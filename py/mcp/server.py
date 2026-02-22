"""Stateful MCP server — JSON-RPC over HTTP.

Agents connect via POST /mcp/agent/{agent_id}, maintaining persistent state
across requests. Tools are dynamically generated from compiled game definitions
and filtered by phase.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

from engine.runtime.state import CompiledGame, view_for

from . import agents as agent_registry
from . import formatters, schema, tokens
from .handlers import analytics as analytics_handlers
from .handlers import channels as channel_handlers
from .handlers import helpers as helper_handlers
from .handlers import history as history_handlers
from .handlers import ledger as ledger_handlers
from .handlers import spectator as spectator_handlers

# Merged global handlers (available in ALL states)
_GLOBAL_HANDLERS: dict[str, Any] = {
    **analytics_handlers.HANDLERS,
    **history_handlers.HANDLERS,
    **ledger_handlers.HANDLERS,
}
_GLOBAL_TOOLS: list[schema.Tool] = (
    analytics_handlers.TOOLS + history_handlers.TOOLS + ledger_handlers.TOOLS
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dependency protocols — MCPServer does NOT import server.sessions directly
# ---------------------------------------------------------------------------


class SessionStore(Protocol):
    """Protocol for game session access. Injected at construction."""

    def get(self, session_id: str) -> Any: ...
    def list_all(self) -> dict[str, Any]: ...
    def create(
        self, session_id: str, compiled: CompiledGame, player_ids: list[str]
    ) -> Any: ...
    def remove(self, session_id: str) -> None: ...


class TournamentStore(Protocol):
    """Protocol for tournament access. Injected at construction."""

    def get(self, tournament_id: str) -> Any: ...
    def list_all(self) -> dict[str, Any]: ...
    def create(self, **kwargs: Any) -> Any: ...
    def remove(self, tournament_id: str) -> None: ...


class MCPServer:
    """Stateful MCP server managing agent sessions and game interactions."""

    def __init__(
        self,
        sessions: SessionStore | None = None,
        tournaments: TournamentStore | None = None,
        ledger: Any = None,
    ) -> None:
        self._games: dict[str, CompiledGame] = {}
        self._tool_cache: dict[str, list[schema.Tool]] = {}
        self._sessions = sessions
        self._tournaments = tournaments
        self._ledger = ledger
        # Lobby: pending games waiting for players before start
        self._pending_games: dict[
            str, dict
        ] = {}  # session_id → {compiled, players, host}

    # ------------------------------------------------------------------
    # Session/tournament access (via injected stores)
    # ------------------------------------------------------------------

    def _get_session(self, session_id: str) -> Any:
        return self._sessions.get(session_id) if self._sessions else None

    def _list_sessions(self) -> dict[str, Any]:
        return self._sessions.list_all() if self._sessions else {}

    def _create_session(
        self, session_id: str, compiled: CompiledGame, player_ids: list[str]
    ) -> Any:
        if not self._sessions:
            raise RuntimeError("No session store configured")
        return self._sessions.create(session_id, compiled, player_ids)

    def _get_tournament(self, tournament_id: str) -> Any:
        return self._tournaments.get(tournament_id) if self._tournaments else None

    def _list_tournaments(self) -> dict[str, Any]:
        return self._tournaments.list_all() if self._tournaments else {}

    def _create_tournament(self, **kwargs: Any) -> Any:
        if not self._tournaments:
            raise RuntimeError("No tournament store configured")
        return self._tournaments.create(**kwargs)

    # ------------------------------------------------------------------
    # Game registry
    # ------------------------------------------------------------------

    def register_game(self, compiled: CompiledGame) -> None:
        """Register a compiled game for discovery by agents."""
        self._games[compiled.id] = compiled
        self._tool_cache[compiled.id] = schema.generate_game_tools(compiled)

    def get_game(self, game_id: str) -> CompiledGame | None:
        return self._games.get(game_id)

    # ------------------------------------------------------------------
    # MCP JSON-RPC handler
    # ------------------------------------------------------------------

    async def handle_request(
        self, agent_id: str, request: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Handle a single MCP JSON-RPC request.

        Returns None for notifications (requests without "id") per MCP spec —
        notifications must not produce a response.
        """
        method = request.get("method", "")
        params = request.get("params", {})
        req_id = request.get("id")

        # Notifications (no "id") are fire-and-forget per JSON-RPC / MCP spec.
        is_notification = "id" not in request

        agent = agent_registry.register_agent(agent_id)

        try:
            match method:
                case "initialize":
                    result = self._handle_initialize(agent)
                case "tools/list":
                    result = self._handle_tools_list(agent)
                case "tools/call":
                    result = await self._handle_tools_call(agent, params)
                case _ if is_notification:
                    # Unknown notification — silently ignore per spec.
                    return None
                case _:
                    result = {
                        "error": {
                            "code": -32601,
                            "message": f"Unknown method: {method}",
                        }
                    }
        except Exception as e:
            log.exception("MCP request failed: %s", method)
            if is_notification:
                return None
            result = {"error": {"code": -32603, "message": str(e)}}

        if is_notification:
            return None

        response: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id}
        if "error" in result:
            response["error"] = result["error"]
        else:
            response["result"] = result
        return response

    # ------------------------------------------------------------------
    # Method handlers
    # ------------------------------------------------------------------

    def _handle_initialize(self, agent: agent_registry.AgentState) -> dict:
        game_list = ", ".join(
            f"{gid} ({g.min_players}-{g.max_players}p)"
            for gid, g in self._games.items()
        )
        return {
            "protocolVersion": "2025-03-26",
            "serverInfo": {
                "name": "parlameme",
                "version": "0.1.0",
                "instructions": (
                    f"Parlameme — multiplayer strategy games for AI agents. "
                    f"Available games: {game_list}. "
                    "Start: call list_games to see details, then "
                    "create_game(game_type=..., players=[...]) to start playing. "
                    "Core loop: act() to observe + execute, "
                    "wait_for_turn() between moves, leave_game when done. "
                    "Help tools: help (contextual guidance), "
                    "simulate (preview actions), "
                    "game_rules (mechanics reference), "
                    "role_guidance (strategy tips)."
                ),
            },
            "capabilities": {"tools": {}},
        }

    def _handle_tools_list(self, agent: agent_registry.AgentState) -> dict:
        tools = self._get_tools_for_agent(agent)
        return {
            "tools": [
                {
                    "name": t.name,
                    "description": t.description,
                    **({"inputSchema": t.inputSchema} if t.inputSchema else {}),
                    **({"annotations": t.annotations} if t.annotations else {}),
                }
                for t in tools
            ]
        }

    async def _handle_tools_call(
        self, agent: agent_registry.AgentState, params: dict
    ) -> dict:
        name = params.get("name", "")
        arguments = params.get("arguments", {})

        # Route to appropriate handler
        if "/" in name:
            return await self._handle_game_tool(agent, name, arguments)
        return await self._handle_platform_tool(agent, name, arguments)

    # ------------------------------------------------------------------
    # Platform tools (lobby)
    # ------------------------------------------------------------------

    async def _handle_platform_tool(
        self, agent: agent_registry.AgentState, name: str, args: dict
    ) -> dict:
        match name:
            case "list_games":
                return self._tool_list_games()
            case "list_sessions":
                return self._tool_list_sessions()
            case "create_game":
                return await self._tool_create_game(agent, args)
            case "join_game":
                return await self._tool_join_game(agent, args)
            case "start_game":
                return await self._tool_start_game(agent, args)
            case "activate_game":
                return await self._tool_activate_game(agent, args)
            case "leave_game":
                return self._tool_leave_game(agent)
            case "my_status":
                return self._tool_my_status(agent)
            case "help":
                return self._tool_help(agent)
            case "simulate":
                return await self._tool_simulate(agent, args)

            # In-game universal tools
            case "get_status":
                return self._tool_get_status(agent)
            case "get_history":
                return self._tool_get_history(agent, args)
            case "available_actions":
                return self._tool_available_actions(agent)
            case "advance_phase":
                return await self._tool_advance_phase(agent)
            case "respond":
                return await self._tool_respond(agent, args)
            case "endorse":
                return await self._tool_endorse(agent, args)
            case "respond_to_inquire":
                return await self._tool_respond_to_inquire(agent, args)
            case "act":
                return await self._tool_act(agent, args)
            case "wait_for_turn":
                return await self._tool_wait_for_turn(agent, args)

            # Global tools (available in ALL states)
            case name if name in _GLOBAL_HANDLERS:
                return await _GLOBAL_HANDLERS[name](self, agent, args)

            # Spectator tools
            case name if name in spectator_handlers.HANDLERS:
                return await spectator_handlers.HANDLERS[name](self, agent, args)

            # Channel query tools (in-game)
            case name if name in channel_handlers.HANDLERS:
                if agent.state != "in_game":
                    return _error(
                    "Not in a game.",
                    code="not_in_game",
                    suggestion="Use create_game or join_game first.",
                )
                return await channel_handlers.HANDLERS[name](self, agent, args)

            # AI helper tools (in-game)
            case name if name in helper_handlers.HANDLERS:
                return await helper_handlers.HANDLERS[name](self, agent, args)

            # Tournament tools
            case "list_tournaments":
                return self._tool_list_tournaments(agent, args)
            case "create_tournament":
                return await self._tool_create_tournament(agent, args)
            case "register_tournament":
                return await self._tool_register_tournament(agent, args)
            case "start_tournament":
                return await self._tool_start_tournament(agent, args)
            case "cancel_tournament":
                return await self._tool_cancel_tournament(agent, args)
            case "get_tournament_status":
                return self._tool_get_tournament_status(agent)
            case "get_standings":
                return self._tool_get_standings(agent, args)
            case "get_my_matches":
                return self._tool_get_my_matches(agent)
            case "report_match_result":
                return await self._tool_report_match_result(agent, args)
            case "join_match":
                return await self._tool_join_match(agent, args)
            case "leave_tournament":
                return await self._tool_leave_tournament(agent)
            case "wait_for_match":
                return await self._tool_wait_for_match(agent, args)

            case _:
                return self._unknown_tool_error(agent, name)

    # Known tools by required state (for helpful error messages)
    _IN_GAME_TOOLS = {
        "get_status", "available_actions", "advance_phase", "act",
        "wait_for_turn", "respond", "endorse", "respond_to_inquire",
        "get_history", "simulate", "game_rules", "role_guidance",
        "game_summary", "deal_mechanics", "list_channels", "get_messages",
        "get_all_messages",
    }
    _LOBBY_TOOLS = {
        "list_games", "list_sessions", "create_game", "join_game",
        "activate_game", "list_tournaments", "create_tournament",
        "register_tournament", "start_tournament", "cancel_tournament",
        "spectate_game",
    }
    _TOURNAMENT_TOOLS = {
        "get_tournament_status", "get_standings", "get_my_matches",
        "join_match", "wait_for_match", "report_match_result",
        "leave_tournament",
    }

    def _unknown_tool_error(self, agent, name: str) -> dict:
        """Return helpful error when agent calls a tool not in their state."""
        state = agent.state
        # Check if tool exists in another state
        if name in self._IN_GAME_TOOLS:
            return _error(
                f"'{name}' requires being in a game.",
                code="wrong_state",
                suggestion="Use create_game or join_game first.",
            )
        if name in self._LOBBY_TOOLS and state != "lobby":
            return _error(
                f"'{name}' requires being in the lobby.",
                code="wrong_state",
                suggestion="Use leave_game to return to the lobby.",
            )
        if name in self._TOURNAMENT_TOOLS and state != "in_tournament":
            return _error(
                f"'{name}' requires being in a tournament.",
                code="wrong_state",
                suggestion="Use register_tournament to join one.",
            )
        return _error(
            f"Unknown tool: '{name}'.",
            code="unknown_tool",
            suggestion="Use help to see available tools.",
        )

    # ------------------------------------------------------------------
    # Game tools (in-game, dynamically generated)
    # ------------------------------------------------------------------

    async def _handle_game_tool(
        self, agent: agent_registry.AgentState, name: str, args: dict
    ) -> dict:
        if agent.state != "in_game":
            return _error(
                "Not in a game. Join a game first.",
                code="not_in_game",
                suggestion="Use create_game or join_game first.",
            )

        session = self._get_session(agent.session_id)
        if not session:
            agent.to_lobby()
            return _error("Game session not found.", code="session_not_found")

        _game_id, tool_id = name.split("/", 1)
        compiled = session.compiled

        # Deal tool
        if tool_id in compiled.deals:
            return await self._exec_deal(session, agent, tool_id, args)

        # Vote tool
        if tool_id.startswith("vote_"):
            vote_id = tool_id[5:]
            if vote_id in compiled.votes:
                return await self._exec_vote(session, agent, vote_id, args)

        # Channel tool
        if tool_id.startswith("send_"):
            ch_id = tool_id[5:]
            if ch_id in compiled.channels:
                return await self._exec_send(session, agent, ch_id, args)

        # Speech act tool
        if tool_id in compiled.speech_acts:
            return await self._exec_speech_act(session, agent, tool_id, args)

        return _error(f"Unknown game tool: {tool_id}", code="unknown_tool")

    # ------------------------------------------------------------------
    # Deal / Vote / Message execution
    # ------------------------------------------------------------------

    async def _exec_deal(self, session, agent, deal_id: str, args: dict) -> dict:
        deal = session.compiled.deals.get(deal_id)
        if not deal:
            return _error(f"Unknown deal: {deal_id}", code="unknown_tool")
        meta_tools = self._tool_cache.get(session.compiled.id, [])
        tool_meta = next(
            (t._meta for t in meta_tools if t._meta.get("deal_id") == deal_id),
            {},
        )
        mapping = tool_meta.get("party_mapping", {})

        # Build kwargs from canonical MCP params → actual party names
        kwargs: dict[str, Any] = {"actor_id": agent.player_id}
        if "target" in args:
            kwargs["target_id"] = args["target"]
        if "responder" in args:
            kwargs["responder_id"] = args["responder"]
        if "responders" in args:
            kwargs["responder_ids"] = args["responders"]

        # Extract deal params (everything not a party param)
        party_keys = {"target", "responder", "responders"}
        params = {k: v for k, v in args.items() if k not in party_keys}
        if params:
            kwargs["params"] = params

        state_before = session.state
        result = await session.execute_deal(deal_id, **kwargs)
        return _content(
            formatters.format_deal_result(
                result, state_before, session.state, agent.player_id
            )
        )

    async def _exec_vote(self, session, agent, vote_id: str, args: dict) -> dict:
        option = args.get("option", "")
        subject_id = args.get("subject")

        # Find existing pending vote or start new one
        pending = None
        for pv in session.state.pending_votes.values():
            if pv.vote_id == vote_id and agent.player_id in pv.eligible:
                if agent.player_id not in pv.votes:
                    pending = pv
                    break

        if pending:
            state_before = session.state
            result = await session.cast_vote(
                pending.instance_id, agent.player_id, option
            )
            return _content(
                formatters.format_vote_result(
                    result, state_before, session.state, agent.player_id,
                    was_start=False,
                )
            )

        # Start new vote
        state_before = session.state
        result = await session.start_vote(
            vote_id, proposer_id=agent.player_id, subject_id=subject_id
        )
        if result["ok"] and "instance_id" in result:
            # Auto-cast if the proposer is also a voter
            cast_result = await session.cast_vote(
                result["instance_id"], agent.player_id, option
            )
            return _content(
                formatters.format_vote_result(
                    cast_result, state_before, session.state, agent.player_id,
                    was_start=True,
                )
            )
        return _content(
            formatters.format_vote_result(
                result, state_before, session.state, agent.player_id,
                was_start=True,
            )
        )

    MAX_MESSAGE_LENGTH = 500

    async def _exec_send(self, session, agent, ch_id: str, args: dict) -> dict:
        # FIX-24: Accept both "content" and "message" param names, validate non-empty
        content = args.get("content") or args.get("message", "")
        if not content.strip():
            return _error(
                "Message content cannot be empty.",
                code="empty_message",
                suggestion="Use 'content' parameter with your message text.",
            )
        if len(content) > self.MAX_MESSAGE_LENGTH:
            return _error(
                f"Message too long ({len(content)} chars). "
                f"Maximum is {self.MAX_MESSAGE_LENGTH}.",
                code="message_too_long",
            )
        result = await session.send_message(ch_id, agent.player_id, content)
        if result["ok"]:
            return _content(f"Message sent to {ch_id}.")
        return _error(result["error"].get("message", "Send failed"))

    async def _exec_speech_act(
        self, session, agent, speech_act_id: str, args: dict
    ) -> dict:
        kwargs: dict[str, Any] = {"actor_id": agent.player_id}
        if "target" in args:
            kwargs["target_id"] = args["target"]
        # Extract params (everything except 'target')
        params = {k: v for k, v in args.items() if k != "target"}
        if params:
            kwargs["params"] = params
        result = await session.execute_speech_act(speech_act_id, **kwargs)
        if result["ok"]:
            sa_def = session.compiled.speech_acts.get(speech_act_id)
            act_type = sa_def.act_type if sa_def else speech_act_id
            msg = f"{act_type.title()} recorded (id: {result.get('instance_id', '?')})"
            if kwargs.get("target_id"):
                msg += f" targeting {kwargs['target_id']}"
            return _content(msg)
        return _error(result["error"].get("message", "Speech act failed"))

    async def _tool_endorse(self, agent, args: dict) -> dict:
        if agent.state != "in_game":
            return _error(
                    "Not in a game.",
                    code="not_in_game",
                    suggestion="Use create_game or join_game first.",
                )
        session = self._get_session(agent.session_id)
        if not session:
            return _error(
                    "Session not found.",
                    code="session_not_found",
                    suggestion="The game may have ended. Use list_sessions to find active games.",
                )
        instance_id = args.get("instance_id", "")
        result = await session.endorse_speech_act(instance_id, agent.player_id)
        if result["ok"]:
            return _content(f"Endorsed speech act {instance_id}.")
        return _error(result["error"].get("message", "Endorse failed"))

    async def _tool_respond_to_inquire(self, agent, args: dict) -> dict:
        if agent.state != "in_game":
            return _error(
                    "Not in a game.",
                    code="not_in_game",
                    suggestion="Use create_game or join_game first.",
                )
        session = self._get_session(agent.session_id)
        if not session:
            return _error(
                    "Session not found.",
                    code="session_not_found",
                    suggestion="The game may have ended. Use list_sessions to find active games.",
                )
        instance_id = args.get("instance_id", "")
        response = args.get("response", "")
        result = await session.respond_to_inquire(
            instance_id, agent.player_id, response
        )
        if result["ok"]:
            return _content(f"Responded to inquire {instance_id} with '{response}'.")
        return _error(result["error"].get("message", "Response failed"))

    # ------------------------------------------------------------------
    # Platform tool implementations
    # ------------------------------------------------------------------

    def _tool_list_games(self) -> dict:
        lines = ["## Available Games\n"]
        for gid, g in self._games.items():
            doc_line = ""
            if hasattr(g, "doc") and g.doc:
                doc_line = g.doc.split("\n")[0]
            elif hasattr(g, "context") and g.context and g.context.game_summary:
                doc_line = g.context.game_summary
            lines.append(f"### {g.name}")
            lines.append(
                f"- **ID**: `{gid}` | **Players**: {g.min_players}-{g.max_players}"
            )
            if doc_line:
                lines.append(f"- {doc_line}")
            lines.append(
                f'- Start: `create_game(game_type="{gid}", players=[...])`'
            )
            lines.append("")
        lines.append(
            'Use `game_rules(game_type="...")` for full mechanics reference.'
        )
        return _content("\n".join(lines))

    def _tool_list_sessions(self) -> dict:
        sessions = self._list_sessions()
        lines: list[str] = []
        # Show pending lobbies
        if self._pending_games:
            lines.append("## Open Lobbies (waiting for players)")
            for sid, pg in self._pending_games.items():
                compiled = pg["compiled"]
                players = ", ".join(pg["players"])
                lines.append(
                    f"- **{sid}**: {compiled.name} | "
                    f"{len(pg['players'])}/{compiled.min_players} players | "
                    f"Players: {players}"
                )
        if sessions:
            lines.append("## Active Sessions")
            for sid, s in sessions.items():
                players = ", ".join(s.player_ids)
                lines.append(
                    f"- **{sid}**: {s.compiled.name} | "
                    f"Phase: {s.state.phase} | Players: {players}"
                )
        if not lines:
            return _content("No active sessions.")
        return _content("\n".join(lines))

    async def _tool_create_game(self, agent, args: dict) -> dict:
        game_type = args.get("game_type", "")
        compiled = self.get_game(game_type)
        if not compiled:
            return _error(
                f"Unknown game: {game_type}. Available: {list(self._games)}",
                code="unknown_game",
            )

        session_id = args.get("session_id", f"game-{agent.agent_id}-{id(args) % 10000}")
        # Accept both "players" and "player_ids" for convenience
        players = args.get("players") or args.get("player_ids")
        player_id = args.get("player_id")

        if players:
            # Agent specified player list; assign host to first player if no player_id
            if player_id is None:
                player_id = players[0]
            elif player_id not in players:
                players = [player_id] + players
        else:
            # No player list — create single-player with agent as player
            player_id = player_id or agent.agent_id
            players = [player_id]

        if len(players) > compiled.max_players:
            return _error(
                f"{compiled.name} requires {compiled.min_players}-{compiled.max_players} "
                f"players, got {len(players)}",
                code="invalid_player_count",
            )

        if len(players) < compiled.min_players:
            # Not enough players yet — create lobby and wait for joins
            self._pending_games[session_id] = {
                "compiled": compiled,
                "players": list(players),
                "host": player_id,
            }
            agent.to_game(session_id, player_id, game_type)
            return _content(
                f"Lobby created: {session_id}\n"
                f"Type: {compiled.name}\n"
                f"Need {compiled.min_players}-{compiled.max_players} players, "
                f"have {len(players)}\n"
                f"You are: {player_id}\n"
                f"Waiting for others to join_game. "
                f"Game starts automatically at {compiled.min_players} players, "
                f"or host can call start_game."
            )

        session = self._create_session(session_id, compiled, players)
        await session.start()
        agent.to_game(session_id, player_id, game_type)

        return _content(
            f"Game created: {session_id}\n"
            f"Type: {compiled.name}\n"
            f"Players: {players}\n"
            f"You are: {player_id}"
        )

    async def _tool_join_game(self, agent, args: dict) -> dict:
        session_id = args.get("session_id", "")

        # Check if this is a pending lobby first
        pending = self._pending_games.get(session_id)
        if pending:
            return await self._join_pending_game(agent, session_id, pending, args)

        session = self._get_session(session_id)
        if not session:
            return _error(f"Session not found: {session_id}", code="session_not_found")

        player_id = args.get("player_id", agent.agent_id)

        # Validate player exists in session
        if player_id not in session.player_ids:
            return _error(
                f"Player '{player_id}' is not in session '{session_id}'. "
                f"Valid players: {session.player_ids}"
            )

        # Prevent impersonation: check no other agent already claims this player
        for other_id, other_agent in agent_registry.list_agents().items():
            if (
                other_id != agent.agent_id
                and other_agent.state == "in_game"
                and other_agent.session_id == session_id
                and other_agent.player_id == player_id
            ):
                return _error(
                    f"Player '{player_id}' is already claimed by another agent.",
                    code="player_conflict",
                )

        agent.to_game(session_id, player_id, session.compiled.id)

        return _content(
            f"Joined game: {session_id}\n"
            f"Type: {session.compiled.name}\n"
            f"You are: {player_id}"
        )

    async def _join_pending_game(
        self, agent, session_id: str, pending: dict, args: dict
    ) -> dict:
        """Join a pending lobby. Auto-starts when min_players reached."""
        compiled = pending["compiled"]
        player_id = args.get("player_id", agent.agent_id)

        if player_id in pending["players"]:
            return _error(f"Player '{player_id}' already in lobby.")
        if len(pending["players"]) >= compiled.max_players:
            return _error("Lobby is full.", code="lobby_full")

        # Prevent impersonation: check no other agent already claims this player_id
        for other_id, other_agent in agent_registry.list_agents().items():
            if (
                other_id != agent.agent_id
                and other_agent.player_id == player_id
                and other_agent.session_id == session_id
            ):
                return _error(
                    f"Player '{player_id}' is already claimed by another agent.",
                    code="player_conflict",
                )

        pending["players"].append(player_id)
        agent.to_game(session_id, player_id, compiled.id)

        if len(pending["players"]) >= compiled.min_players:
            # Auto-start: enough players
            return await self._start_pending_game(session_id, pending)

        return _content(
            f"Joined lobby: {session_id}\n"
            f"Type: {compiled.name}\n"
            f"Players: {len(pending['players'])}/{compiled.min_players} "
            f"(need {compiled.min_players})\n"
            f"You are: {player_id}"
        )

    async def _start_pending_game(self, session_id: str, pending: dict) -> dict:
        """Promote a pending lobby to a live game session."""
        compiled = pending["compiled"]
        players = pending["players"]

        try:
            session = self._create_session(session_id, compiled, players)
            await session.start()
        except Exception:
            log.exception("Failed to start pending game %s", session_id)
            # Restore pending game so agents can retry or leave cleanly
            self._pending_games[session_id] = pending
            return _error("Failed to start game. Lobby restored.", code="start_failed")

        # Only remove pending after successful start
        self._pending_games.pop(session_id, None)

        return _content(
            f"Game started: {session_id}\nType: {compiled.name}\nPlayers: {players}"
        )

    async def _tool_start_game(self, agent, args: dict) -> dict:
        """Manually start a pending lobby (host only)."""
        session_id = agent.session_id
        if not session_id:
            return _error("Not in a game lobby.", code="not_in_game")

        pending = self._pending_games.get(session_id)
        if not pending:
            return _error(
                "No pending lobby for this session. Game may already be started."
            )

        compiled = pending["compiled"]
        if agent.player_id != pending["host"]:
            return _error("Only the host can start the game.", code="permission_denied")
        if len(pending["players"]) < compiled.min_players:
            return _error(
                f"Need at least {compiled.min_players} players, "
                f"have {len(pending['players'])}."
            )

        return await self._start_pending_game(session_id, pending)

    async def _tool_activate_game(self, agent, args: dict) -> dict:
        token = args.get("token", "")
        payload = tokens.verify_token(token, agent.agent_id)
        if not payload:
            return _error("Invalid or expired token.", code="invalid_token")

        session = self._get_session(payload["session_id"])
        if not session:
            return _error("Session from token not found.", code="session_not_found")

        agent.to_game(payload["session_id"], payload["player_id"], payload["game_type"])
        return _content(
            f"Activated game: {payload['session_id']}\nYou are: {payload['player_id']}"
        )

    def _tool_leave_game(self, agent) -> dict:
        if agent.state != "in_game":
            return _error(
                    "Not in a game.",
                    code="not_in_game",
                    suggestion="Use create_game or join_game first.",
                )
        # Handle pending lobby leave
        pending = self._pending_games.get(agent.session_id or "")
        if pending and agent.player_id in pending["players"]:
            pending["players"].remove(agent.player_id)
            if not pending["players"]:
                del self._pending_games[agent.session_id]
            elif pending["host"] == agent.player_id:
                # Reassign host to next player in lobby
                pending["host"] = pending["players"][0]
        if agent.tournament_context:
            agent.back_to_tournament()
            return _content("Left match. Returned to tournament.")
        agent.to_lobby()
        return _content("Left game. Back to lobby.")

    def _tool_my_status(self, agent) -> dict:
        lines = [
            f"State: {agent.state}",
            f"Session: {agent.session_id or 'none'}",
            f"Player: {agent.player_id or 'none'}",
            f"Tournament: {agent.tournament_id or 'none'}",
        ]
        match agent.state:
            case "lobby":
                lines.append("\nNext: list_games, create_game, or join_game")
            case "in_game":
                lines.append("\nNext: act() to observe, wait_for_turn() between moves")
            case "in_tournament":
                lines.append("\nNext: wait_for_match, then join_match")
            case "spectating":
                lines.append("\nNext: spectate_view to see state, leave_spectate to exit")
        return _content("\n".join(lines))

    def _tool_help(self, agent) -> dict:
        """Contextual help based on agent state."""
        match agent.state:
            case "lobby":
                games = ", ".join(self._games.keys())
                return _content(
                    "## Lobby — What to Do\n"
                    f"Available games: {games}\n\n"
                    "**Start playing:**\n"
                    "1. `create_game` — start a new game\n"
                    "2. `join_game` — join an existing session\n"
                    "3. `list_sessions` — see open lobbies\n\n"
                    "**Tournaments:**\n"
                    "- `list_tournaments` -> `register_tournament` -> "
                    "`start_tournament`\n\n"
                    "**Info:**\n"
                    "- `my_stats` — your rating and history\n"
                    "- `leaderboard` — top players\n"
                    "- `opponent_profile` — scout an opponent"
                )
            case "in_game":
                pending = self._pending_games.get(agent.session_id or "")
                if pending:
                    return _content(
                        "## Lobby — Waiting for Players\n"
                        "Game starts when enough players join.\n"
                        "- `get_status` — check who's here\n"
                        "- `start_game` — start manually (host only)"
                    )
                return _content(
                    "## In Game — How to Play\n"
                    "**Core loop:**\n"
                    "1. `act()` — observe (no args) or execute "
                    "(with action+args)\n"
                    "2. `wait_for_turn` — block until something changes\n"
                    "3. Repeat until game ends, then `leave_game`\n\n"
                    "**Strategy tools:**\n"
                    "- `simulate` — preview action without committing\n"
                    "- `game_rules` — full rules reference\n"
                    "- `role_guidance` — tips for your role\n"
                    "- `deal_mechanics` — deep breakdown of any action\n"
                    "- `opponent_profile` — analyze opponent patterns"
                )
            case "in_tournament":
                return _content(
                    "## Tournament — Flow\n"
                    "1. `wait_for_match` — block until match ready\n"
                    "2. `join_match` — enter the game\n"
                    "3. Play the game (see in-game help)\n"
                    "4. `leave_game` -> back to tournament\n"
                    "5. Repeat until tournament ends\n\n"
                    "- `get_standings` — check standings\n"
                    "- `get_my_matches` — your schedule"
                )
            case _:
                return _content(
                    "Use `my_status` to check your current state."
                )

    # ------------------------------------------------------------------
    # In-game universal tools
    # ------------------------------------------------------------------

    def _tool_get_status(self, agent) -> dict:
        if agent.state != "in_game":
            return _error(
                    "Not in a game.",
                    code="not_in_game",
                    suggestion="Use create_game or join_game first.",
                )
        # Handle pending lobby status
        pending = self._pending_games.get(agent.session_id or "")
        if pending:
            compiled = pending["compiled"]
            return _content(
                f"## Lobby: {agent.session_id}\n"
                f"Game: {compiled.name}\n"
                f"Players: {', '.join(pending['players'])} "
                f"({len(pending['players'])}/{compiled.min_players} needed)\n"
                f"Host: {pending['host']}\n"
                f"Status: waiting for players"
            )
        session = self._get_session(agent.session_id)
        if not session:
            return _error(
                    "Session not found.",
                    code="session_not_found",
                    suggestion="The game may have ended. Use list_sessions to find active games.",
                )
        view = view_for(session.state, agent.player_id, session.compiled)
        view["_state"] = session.state  # for ACTION REQUIRED section
        view["_timeout_remaining"] = getattr(session, "phase_timeout_remaining", None)
        text = formatters.format_status(view, session.compiled, agent.player_id)

        # Hint: game is over, agent should leave
        if session.state.status == "ended":
            vr = session.state.victory_result
            winner_msg = formatters._format_victory(vr) if vr else "Game over"
            dest = "tournament" if agent.tournament_context else "lobby"
            text += f"\n\n**{winner_msg}** Use `leave_game` to return to {dest}."

        # Structured data for programmatic access
        my_entity = view.get("entities", {}).get(agent.player_id, {})
        status_data = {
            "phase": session.state.phase,
            "round": session.state.round,
            "status": session.state.status,
            "resources": dict(my_entity.get("resources", {})),
            "pending_actions": _pending_actions_data(session.state, agent.player_id),
            "timeout_remaining": getattr(session, "phase_timeout_remaining", None),
        }
        return _content(text, data=status_data)

    def _tool_get_history(self, agent, args: dict) -> dict:
        if agent.state != "in_game":
            return _error(
                    "Not in a game.",
                    code="not_in_game",
                    suggestion="Use create_game or join_game first.",
                )
        session = self._get_session(agent.session_id)
        if not session:
            return _error(
                    "Session not found.",
                    code="session_not_found",
                    suggestion="The game may have ended. Use list_sessions to find active games.",
                )
        limit = args.get("limit", 10)
        return _content(formatters.format_history(session.state, limit))

    def _tool_available_actions(self, agent) -> dict:
        if agent.state != "in_game":
            return _error(
                    "Not in a game.",
                    code="not_in_game",
                    suggestion="Use create_game or join_game first.",
                )
        session = self._get_session(agent.session_id)
        if not session:
            return _error(
                    "Session not found.",
                    code="session_not_found",
                    suggestion="The game may have ended. Use list_sessions to find active games.",
                )
        text = formatters.format_available_actions(
            session.state, session.compiled, agent.player_id
        )
        actions_data = _available_actions_data(
            session.state, session.compiled, agent.player_id
        )
        return _content(text, data=actions_data)

    async def _tool_advance_phase(self, agent) -> dict:
        if agent.state != "in_game":
            return _error(
                    "Not in a game.",
                    code="not_in_game",
                    suggestion="Use create_game or join_game first.",
                )
        session = self._get_session(agent.session_id)
        if not session:
            return _error(
                    "Session not found.",
                    code="session_not_found",
                    suggestion="The game may have ended. Use list_sessions to find active games.",
                )
        state = await session.advance_phase()
        victory = session.runtime.check_victory(state)
        if victory:
            return _content(
                f"Phase advanced to: {state.phase}\n"
                f"GAME OVER: {victory.get('winner')} wins!"
            )
        return _content(f"Phase advanced to: {state.phase} (round {state.round})")

    async def _tool_respond(self, agent, args: dict) -> dict:
        if agent.state != "in_game":
            return _error(
                    "Not in a game.",
                    code="not_in_game",
                    suggestion="Use create_game or join_game first.",
                )
        session = self._get_session(agent.session_id)
        if not session:
            return _error(
                    "Session not found.",
                    code="session_not_found",
                    suggestion="The game may have ended. Use list_sessions to find active games.",
                )
        state_before = session.state
        result = await session.respond_deal(
            args["instance_id"], agent.player_id, args["response"]
        )
        return _content(
            formatters.format_deal_result(
                result, state_before, session.state, agent.player_id
            )
        )

    # ------------------------------------------------------------------
    # Tournament tools (delegated to tournament module)
    # ------------------------------------------------------------------

    def _tool_list_tournaments(self, agent, args: dict) -> dict:
        tournaments = self._list_tournaments()
        if not tournaments:
            return _content("No tournaments.")
        lines = ["## Tournaments"]
        for tid, ts in tournaments.items():
            s = ts.state
            lines.append(
                f"- **{tid}**: {s.name or s.tournament_type} | "
                f"Status: {s.status} | Players: {len(s.participants)}"
            )
        return _content("\n".join(lines))

    async def _tool_create_tournament(self, agent, args: dict) -> dict:
        from tournament.config import TournamentConfig

        t_type = args.get("tournament_type", "round_robin")
        game_type = args.get("game_type", "")
        if game_type not in self._games:
            return _error(f"Unknown game: {game_type}. Available: {list(self._games)}", code="unknown_game")
        name = args.get("name", f"{t_type} tournament")
        tid = args.get("tournament_id", f"t-{agent.agent_id}-{id(args) % 10000}")

        compiled = self._games[game_type]
        min_part = args.get("min_participants", compiled.min_players)
        if min_part < compiled.min_players:
            min_part = compiled.min_players

        # Build optional config from args
        config_fields = (
            "win_points", "draw_points", "match_timeout", "phase_timeout",
            "winner_credit", "participation_credit", "draw_credit",
            "swiss_max_rounds",
        )
        config_kwargs = {f: args[f] for f in config_fields if f in args}
        if "tiebreaker" in args:
            config_kwargs["tiebreaker"] = tuple(args["tiebreaker"])
        config = TournamentConfig(**config_kwargs) if config_kwargs else None

        create_kwargs: dict = dict(
            tournament_id=tid,
            tournament_type=t_type,
            host=agent.agent_id,
            game_type=game_type,
            name=name,
            min_participants=min_part,
            max_participants=args.get("max_participants", 16),
            match_size=compiled.min_players,
        )
        if config:
            create_kwargs["config"] = config
        ts = self._create_tournament(**create_kwargs)
        await ts.register(agent.agent_id)
        agent.to_tournament(tid)
        return _content(
            f"Tournament created: {tid}\nFormat: {t_type}\nGame: {game_type}"
        )

    async def _tool_register_tournament(self, agent, args: dict) -> dict:
        tid = args.get("tournament_id", "")
        ts = self._get_tournament(tid)
        if not ts:
            return _error(f"Tournament not found: {tid}", code="tournament_not_found")
        try:
            await ts.register(agent.agent_id)
        except ValueError as exc:
            return _error(str(exc))
        agent.to_tournament(tid)
        return _content(f"Registered for tournament: {tid}")

    async def _tool_start_tournament(self, agent, args: dict) -> dict:
        tid = args.get("tournament_id", agent.tournament_id or "")
        ts = self._get_tournament(tid)
        if not ts:
            return _error(f"Tournament not found: {tid}", code="tournament_not_found")
        if ts.state.host != agent.agent_id:
            return _error("Only the host can start the tournament.", code="permission_denied")
        try:
            await ts.start(self._games.get(ts.state.game_type))
        except ValueError as exc:
            return _error(str(exc))
        return _content(f"Tournament started: {tid}\nMatches: {len(ts.state.matches)}")

    async def _tool_cancel_tournament(self, agent, args: dict) -> dict:
        tid = args.get("tournament_id", agent.tournament_id or "")
        ts = self._get_tournament(tid)
        if not ts:
            return _error(f"Tournament not found: {tid}", code="tournament_not_found")
        try:
            await ts.cancel(agent.agent_id)
        except ValueError as exc:
            return _error(str(exc))
        return _content(f"Tournament cancelled: {tid}")

    def _tool_get_tournament_status(self, agent) -> dict:
        if not agent.tournament_id:
            return _error(
                    "Not in a tournament.",
                    code="not_in_tournament",
                    suggestion="Use register_tournament to join a tournament.",
                )
        ts = self._get_tournament(agent.tournament_id)
        if not ts:
            return _error("Tournament not found.", code="tournament_not_found")
        s = ts.state
        lines = [
            f"## Tournament: {s.name or s.tournament_id}",
            f"Format: {s.tournament_type} | Status: {s.status}",
            f"Game: {s.game_type} | Participants: {len(s.participants)}",
        ]
        if s.winner:
            lines.append(f"Winner: {s.winner}")
        return _content("\n".join(lines))

    def _tool_get_standings(self, agent, args: dict) -> dict:
        from tournament.runtime import TournamentRuntime as _TRT

        tid = args.get("tournament_id", agent.tournament_id or "")
        ts = self._get_tournament(tid)
        if not ts:
            return _error("Tournament not found.", code="tournament_not_found")
        standings = _TRT().get_standings_sorted(ts.state)
        lines = ["## Standings"]
        for i, s in enumerate(standings, 1):
            lines.append(
                f"{i}. {s.participant}: {s.points}pts "
                f"(W{s.wins} L{s.losses} D{s.draws})"
            )
        return _content("\n".join(lines))

    def _tool_get_my_matches(self, agent) -> dict:
        if not agent.tournament_id:
            return _error(
                    "Not in a tournament.",
                    code="not_in_tournament",
                    suggestion="Use register_tournament to join a tournament.",
                )
        ts = self._get_tournament(agent.tournament_id)
        if not ts:
            return _error("Tournament not found.", code="tournament_not_found")
        matches = []
        for mid, m in ts.state.matches.items():
            if agent.agent_id in m.participants:
                matches.append(m)
        if not matches:
            return _content("No matches found for you.")
        lines = ["## Your Matches"]
        for m in sorted(matches, key=lambda m: m.round):
            opp = [p for p in m.participants if p != agent.agent_id]
            opp_str = ", ".join(opp) if opp else "?"
            session_id = ts.get_match_session_id(m.id) or ""

            # Readiness signal
            if m.status == "completed":
                status_str = f"COMPLETED (winner: {m.winner})"
            elif m.status == "active" and session_id:
                game_session = self._get_session(session_id)
                if game_session and game_session.state.status == "ended":
                    status_str = f"COMPLETED (winner: {m.winner or '?'})"
                else:
                    status_str = "READY — use `join_match` to play"
            else:
                status_str = "PENDING — waiting for earlier matches"

            lines.append(f"- **{m.id}** (round {m.round}): vs {opp_str} | {status_str}")
        return _content("\n".join(lines))

    async def _tool_report_match_result(self, agent, args: dict) -> dict:
        if not agent.tournament_id:
            return _error(
                    "Not in a tournament.",
                    code="not_in_tournament",
                    suggestion="Use register_tournament to join a tournament.",
                )
        ts = self._get_tournament(agent.tournament_id)
        if not ts:
            return _error("Tournament not found.", code="tournament_not_found")
        match_id = args.get("match_id", "")
        winner = args.get("winner", "")
        if not match_id or not winner:
            return _error("Provide match_id and winner.", code="invalid_args")
        match = ts.state.matches.get(match_id)
        if not match:
            return _error(f"Match not found: {match_id}", code="match_not_found")
        if agent.agent_id not in match.participants and agent.agent_id != ts.state.host:
            return _error("Only match participants or the tournament host can report results.", code="permission_denied")
        compiled = self._games.get(ts.state.game_type)
        try:
            await ts.report_result(match_id, winner, compiled=compiled)
        except ValueError as e:
            return _error(str(e))
        return _content(f"Result reported: {winner} wins match {match_id}.")

    async def _tool_join_match(self, agent, args: dict) -> dict:
        """Join a specific match's game session from a tournament."""
        if not agent.tournament_id:
            return _error(
                    "Not in a tournament.",
                    code="not_in_tournament",
                    suggestion="Use register_tournament to join a tournament.",
                )
        ts = self._get_tournament(agent.tournament_id)
        if not ts:
            return _error("Tournament not found.", code="tournament_not_found")
        match_id = args.get("match_id", "")
        match = ts.state.matches.get(match_id)
        if not match:
            return _error(f"Match not found: {match_id}", code="match_not_found")
        if agent.agent_id not in match.participants:
            return _error("You are not a participant in this match.", code="permission_denied")
        session_id = ts.get_match_session_id(match_id)
        if not session_id:
            return _error("Match game session not ready.", code="match_not_ready")
        # Prevent impersonation: check no other agent already claims this player
        for other_id, other_agent in agent_registry.list_agents().items():
            if (
                other_id != agent.agent_id
                and other_agent.state == "in_game"
                and other_agent.session_id == session_id
                and other_agent.player_id == agent.agent_id
            ):
                return _error(
                    f"Player '{agent.agent_id}' is already claimed by another agent.",
                    code="player_conflict",
                )
        session = self._get_session(session_id)
        if not session:
            return _error("Match game session not found.", code="session_not_found")
        agent.to_game_from_tournament(
            session_id, agent.agent_id, ts.state.game_type, match_id
        )
        return _content(
            f"Joined match: {match_id}\n"
            f"Session: {session_id}\n"
            f"You are: {agent.agent_id}"
        )

    async def _tool_leave_tournament(self, agent) -> dict:
        if agent.state != "in_tournament":
            return _error(
                    "Not in a tournament.",
                    code="not_in_tournament",
                    suggestion="Use register_tournament to join a tournament.",
                )
        # Unregister from tournament if still in registration phase
        if agent.tournament_id:
            ts = self._get_tournament(agent.tournament_id)
            if ts and ts.state.status == "registration":
                try:
                    await ts.unregister(agent.agent_id)
                except ValueError:
                    pass  # already unregistered or tournament started
        agent.to_lobby()
        return _content("Left tournament. Back to lobby.")

    # ------------------------------------------------------------------
    # Combined act + wait tools (Phase 6)
    # ------------------------------------------------------------------

    async def _tool_act(self, agent, args: dict) -> dict:
        """Combined observe + execute tool. Reduces 3 calls to 1.

        No args → observe (status + available_actions).
        With action → execute action + return result + updated status.
        """
        if agent.state != "in_game":
            return _error(
                    "Not in a game.",
                    code="not_in_game",
                    suggestion="Use create_game or join_game first.",
                )
        session = self._get_session(agent.session_id)
        if not session:
            return _error(
                    "Session not found.",
                    code="session_not_found",
                    suggestion="The game may have ended. Use list_sessions to find active games.",
                )

        action = args.get("action")
        action_args = args.get("args", {})

        parts: list[str] = []

        if action:
            # Execute the action first
            result = await self._dispatch_action(agent, session, action, action_args)
            result_text = result.get("content", [{}])[0].get("text", "")
            if result.get("isError"):
                result_text = result.get("content", [{}])[0].get("text", "")
                parts.append(f"## Action Result: ERROR\n{result_text}")
            else:
                parts.append(f"## Action Result\n{result_text}")

        # Always include current status + available actions
        view = view_for(session.state, agent.player_id, session.compiled)
        view["_state"] = session.state
        view["_timeout_remaining"] = getattr(session, "phase_timeout_remaining", None)
        status_text = formatters.format_status(view, session.compiled, agent.player_id)
        actions_text = formatters.format_available_actions(
            session.state, session.compiled, agent.player_id
        )

        if session.state.status == "ended":
            vr = session.state.victory_result
            winner_msg = formatters._format_victory(vr) if vr else "Game over"
            dest = "tournament" if agent.tournament_context else "lobby"
            status_text += f"\n\n**{winner_msg}** Use `leave_game` to return to {dest}."

        parts.append(status_text)
        parts.append(actions_text)

        # Structured data for programmatic access
        act_data: dict = {
            "phase": session.state.phase,
            "round": session.state.round,
            "status": session.state.status,
            "pending_actions": _pending_actions_data(session.state, agent.player_id),
            "timeout_remaining": getattr(session, "phase_timeout_remaining", None),
        }
        if action:
            act_data["action_ok"] = not result.get("isError", False)

        return _content("\n\n---\n\n".join(parts), data=act_data)

    async def _tool_simulate(self, agent, args: dict) -> dict:
        """Simulate an action without committing. Shows expected result."""
        if agent.state != "in_game":
            return _error(
                    "Not in a game.",
                    code="not_in_game",
                    suggestion="Use create_game or join_game first.",
                )
        session = self._get_session(agent.session_id)
        if not session:
            return _error(
                    "Session not found.",
                    code="session_not_found",
                    suggestion="The game may have ended. Use list_sessions to find active games.",
                )
        action = args.get("action", "")
        action_args = args.get("args", {})
        if not action:
            return _error("Provide an action to simulate.", code="invalid_args")

        result = self._simulate_action(session, agent, action, action_args)
        result_text = result.get("content", [{}])[0].get("text", "")
        prefix = "SIMULATION (not committed)"
        if result.get("isError"):
            return _content(f"## {prefix}: ERROR\n{result_text}")
        return _content(f"## {prefix}\n{result_text}")

    def _simulate_action(self, session, agent, action: str, args: dict) -> dict:
        """Run action against current state without mutating session."""
        if "/" in action:
            action = action.split("/", 1)[1]
        compiled = session.compiled
        state = session.state
        rt = session.runtime
        pid = agent.player_id

        # Deal simulation
        if action in compiled.deals:
            kwargs: dict[str, Any] = {"actor_id": pid}
            if "target" in args:
                kwargs["target_id"] = args["target"]
            if "responder" in args:
                kwargs["responder_id"] = args["responder"]
            if "responders" in args:
                kwargs["responder_ids"] = args["responders"]
            party_keys = {"target", "responder", "responders"}
            params = {k: v for k, v in args.items() if k not in party_keys}
            if params:
                kwargs["params"] = params
            result = rt.start_deal(state, action, **kwargs)
            return _content(
                formatters.format_deal_result(
                    result, state, result.get("state", state), pid
                )
            )

        # Vote simulation
        if action.startswith("vote_"):
            vote_id = action[5:]
            if vote_id in compiled.votes:
                option = args.get("option", "")
                # Check for existing pending vote first
                pending = None
                for pv in state.pending_votes.values():
                    if (
                        pv.vote_id == vote_id
                        and pid in pv.eligible
                        and pid not in pv.votes
                    ):
                        pending = pv
                        break
                if pending:
                    result = rt.cast_vote(state, pending.instance_id, pid, option)
                else:
                    result = rt.start_vote(
                        state, vote_id,
                        proposer_id=pid,
                        subject_id=args.get("subject"),
                    )
                    if result["ok"] and "instance_id" in result:
                        result = rt.cast_vote(
                            result["state"], result["instance_id"], pid, option
                        )
                return _content(
                    formatters.format_vote_result(
                        result, state, result.get("state", state), pid
                    )
                )

        # Respond simulation
        if action == "respond":
            iid = args.get("instance_id", "")
            response = args.get("response", "")
            if not iid or not response:
                return _error("Provide instance_id and response to simulate.", code="invalid_args")
            result = rt.respond_to_deal(state, iid, pid, response)
            return _content(
                formatters.format_deal_result(
                    result, state, result.get("state", state), pid
                )
            )

        # Advance phase simulation
        if action == "advance_phase":
            try:
                new_state = rt.advance_phase(state)
                return _content(
                    f"Phase would advance: {state.phase} -> {new_state.phase}"
                )
            except Exception as e:
                return _error(str(e))

        return _error(f"Cannot simulate: {action}")

    async def _dispatch_action(self, agent, session, action: str, args: dict) -> dict:
        """Route an action name to the appropriate handler."""
        # Strip game prefix if agent passed qualified name (e.g. "auction/buy_info")
        if "/" in action:
            action = action.split("/", 1)[1]
        # Game-specific tools (deal/vote/send/speech_act)
        compiled = session.compiled
        if action in compiled.deals:
            return await self._exec_deal(session, agent, action, args)
        if action.startswith("vote_"):
            vote_id = action[5:]
            if vote_id in compiled.votes:
                return await self._exec_vote(session, agent, vote_id, args)
        if action.startswith("send_"):
            ch_id = action[5:]
            if ch_id in compiled.channels:
                return await self._exec_send(session, agent, ch_id, args)
        if action in compiled.speech_acts:
            return await self._exec_speech_act(session, agent, action, args)

        # Platform tools
        match action:
            case "advance_phase":
                return await self._tool_advance_phase(agent)
            case "respond":
                return await self._tool_respond(agent, args)
            case "endorse":
                return await self._tool_endorse(agent, args)
            case "respond_to_inquire":
                return await self._tool_respond_to_inquire(agent, args)
            case _:
                return _error(f"Unknown action: {action}")

    async def _tool_wait_for_turn(self, agent, args: dict) -> dict:
        """Block until game state changes relevant to this agent.

        Returns immediately if agent has pending actions.
        Max timeout: 60 seconds (configurable via args).
        """
        if agent.state != "in_game":
            return _error("Not in a game.", code="not_in_game")
        session = self._get_session(agent.session_id)
        if not session:
            return _error("Session not found.", code="session_not_found")

        timeout = min(args.get("timeout", 60), 60)

        # Return immediately if there are pending actions for this player
        if self._has_pending_actions(session, agent.player_id):
            return _with_trigger("pending_actions", self._tool_get_status(agent))

        # Snapshot state before waiting
        prev_phase = session.state.phase
        prev_round = session.state.round
        prev_deals = len(session.state.pending_deals)
        prev_votes = len(session.state.pending_votes)
        prev_status = session.state.status
        prev_history_len = len(session.state.history)

        # Subscribe and wait for state change
        event = asyncio.Event()
        timed_out = False

        async def on_change(state):
            event.set()

        session.subscribe(on_change)
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            timed_out = True
        finally:
            session.unsubscribe(on_change)

        # Detect what triggered the wake
        trigger = _detect_trigger(
            session,
            prev_phase,
            prev_round,
            prev_deals,
            prev_votes,
            prev_status,
            timed_out,
        )

        # Append recent history events to trigger text
        new_entries = session.state.history[prev_history_len:]
        if new_entries and not timed_out:
            changes = []
            for e in new_entries[-5:]:
                data_str = ", ".join(f"{k}={v}" for k, v in e.data.items())
                changes.append(f"  - [{e.type}] {data_str}")
            trigger += "\nRecent events:\n" + "\n".join(changes)

        return _with_trigger(trigger, self._tool_get_status(agent))

    def _has_pending_actions(self, session, player_id: str) -> bool:
        """Check if player has any pending deals or votes to respond to."""
        state = session.state
        for pd in state.pending_deals.values():
            if player_id in pd.responders and pd.responders[player_id] is None:
                return True
        for pv in state.pending_votes.values():
            if player_id in pv.eligible and player_id not in pv.votes:
                return True
        return False

    async def _tool_wait_for_match(self, agent, args: dict) -> dict:
        """Block until a match is ready for this agent in the tournament.

        Returns immediately if a match is already active.
        Max timeout: 60 seconds.
        """
        if agent.state != "in_tournament":
            return _error(
                    "Not in a tournament.",
                    code="not_in_tournament",
                    suggestion="Use register_tournament to join a tournament.",
                )
        ts = self._get_tournament(agent.tournament_id)
        if not ts:
            return _error("Tournament not found.", code="tournament_not_found")

        timeout = min(args.get("timeout", 60), 60)

        # Return immediately if there's an active match for this agent
        for mid, m in ts.state.matches.items():
            if m.status == "active" and agent.agent_id in m.participants:
                return self._tool_get_my_matches(agent)

        # Subscribe and wait for tournament state change
        event = asyncio.Event()

        async def on_change(state):
            event.set()

        ts.subscribe(on_change)
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        finally:
            ts.unsubscribe(on_change)

        # Check if tournament completed while waiting
        if ts.state.status == "completed":
            from tournament.runtime import TournamentRuntime as _TRT

            standings = _TRT().get_standings_sorted(ts.state)
            lines = [
                "## Tournament Complete!",
                f"Winner: {ts.state.winner or 'N/A'}",
                "",
                "### Final Standings",
            ]
            for i, s in enumerate(standings, 1):
                marker = " (you)" if s.participant == agent.agent_id else ""
                lines.append(
                    f"{i}. {s.participant}{marker}: {s.points}pts "
                    f"(W{s.wins} L{s.losses} D{s.draws})"
                )
            return _content("\n".join(lines))

        return self._tool_get_my_matches(agent)

    # ------------------------------------------------------------------
    # Tool list generation
    # ------------------------------------------------------------------

    def _get_tools_for_agent(
        self, agent: agent_registry.AgentState
    ) -> list[schema.Tool]:
        match agent.state:
            case "lobby":
                return self._lobby_tools()
            case "in_game":
                return self._in_game_tools(agent)
            case "in_tournament":
                return self._tournament_tools()
            case "spectating":
                return self._spectating_tools()
        return []

    def _lobby_tools(self) -> list[schema.Tool]:
        game_enum = list(self._games.keys())
        return _GLOBAL_TOOLS + [
            schema.Tool(
                name="list_games",
                description="List available game types",
                annotations={"readOnlyHint": True},
            ),
            schema.Tool(
                name="list_sessions",
                description="List open game sessions",
                annotations={"readOnlyHint": True},
            ),
            schema.Tool(
                name="create_game",
                description=(
                    "Create a new game session. "
                    "If all players listed, game starts immediately. "
                    "Otherwise creates a lobby — others join via join_game, "
                    "game auto-starts when enough players join."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "game_type": {
                            "type": "string",
                            **({"enum": game_enum} if game_enum else {}),
                        },
                        "session_id": {"type": "string"},
                        "player_id": {"type": "string"},
                        "players": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "All player IDs. If omitted, creates a lobby.",
                        },
                    },
                    "required": ["game_type"],
                },
            ),
            schema.Tool(
                name="join_game",
                description=(
                    "Join an existing game session or open lobby. "
                    "If joining a lobby, game auto-starts when min players reached."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string"},
                        "player_id": {"type": "string"},
                    },
                    "required": ["session_id"],
                },
            ),
            schema.Tool(
                name="activate_game",
                description="Join game with invite token",
                inputSchema={
                    "type": "object",
                    "properties": {"token": {"type": "string"}},
                    "required": ["token"],
                },
            ),
            schema.Tool(
                name="my_status",
                description="Your current status",
                annotations={"readOnlyHint": True},
            ),
            schema.Tool(
                name="list_tournaments",
                description="List tournaments",
            ),
            schema.Tool(
                name="create_tournament",
                description="Create a tournament",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "tournament_type": {
                            "type": "string",
                            "enum": ["round_robin", "single_elimination", "swiss"],
                        },
                        "game_type": {
                            "type": "string",
                            **({"enum": game_enum} if game_enum else {}),
                        },
                        "name": {"type": "string"},
                        "tournament_id": {"type": "string"},
                        "min_participants": {"type": "integer", "default": 2},
                        "max_participants": {"type": "integer", "default": 16},
                        "win_points": {"type": "integer", "description": "Points for a win (default 3)"},
                        "draw_points": {"type": "integer", "description": "Points for a draw (default 1)"},
                        "match_timeout": {"type": "integer", "description": "Match timeout seconds (default 1800)"},
                        "phase_timeout": {"type": "integer", "description": "Phase timeout seconds (default 300)"},
                        "winner_credit": {"type": "integer", "description": "Ledger credits to winner (default 100)"},
                        "participation_credit": {"type": "integer", "description": "Credits to non-winners (default 10)"},
                        "draw_credit": {"type": "integer", "description": "Credits per player on draw (default 30)"},
                        "swiss_max_rounds": {"type": "integer", "description": "Override swiss max rounds"},
                        "tiebreaker": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Tiebreaker fields: points, goal_diff, wins",
                        },
                    },
                    "required": ["tournament_type", "game_type"],
                },
            ),
            schema.Tool(
                name="register_tournament",
                description="Register for a tournament",
                inputSchema={
                    "type": "object",
                    "properties": {"tournament_id": {"type": "string"}},
                    "required": ["tournament_id"],
                },
            ),
            schema.Tool(
                name="start_tournament",
                description="Start tournament (host only)",
                inputSchema={
                    "type": "object",
                    "properties": {"tournament_id": {"type": "string"}},
                    "required": ["tournament_id"],
                },
            ),
            schema.Tool(
                name="cancel_tournament",
                description="Cancel tournament (host only)",
                inputSchema={
                    "type": "object",
                    "properties": {"tournament_id": {"type": "string"}},
                },
            ),
            spectator_handlers.LOBBY_TOOL,
            schema.Tool(
                name="help",
                description="What to do next — contextual guidance",
            ),
        ]

    def _in_game_tools(self, agent: agent_registry.AgentState) -> list[schema.Tool]:
        # Check pending lobby first
        pending = self._pending_games.get(agent.session_id or "")
        if pending:
            tools = _GLOBAL_TOOLS + [
                schema.Tool(
                    name="get_status",
                    description="Get lobby status",
                ),
                schema.Tool(
                    name="leave_game",
                    description="Leave the lobby",
                ),
            ]
            if agent.player_id == pending["host"]:
                tools.append(
                    schema.Tool(
                        name="start_game",
                        description="Start the game (host only, needs min players)",
                    )
                )
            return tools

        session = self._get_session(agent.session_id)
        if not session:
            return self._lobby_tools()

        all_tools = self._tool_cache.get(session.compiled.id)
        if not all_tools:
            all_tools = schema.generate_game_tools(session.compiled)
            self._tool_cache[session.compiled.id] = all_tools

        return schema.filter_tools_for_phase(
            all_tools, session.state, session.compiled, agent.player_id
        )

    def _tournament_tools(self) -> list[schema.Tool]:
        return _GLOBAL_TOOLS + [
            schema.Tool(name="get_tournament_status", description="Tournament status"),
            schema.Tool(name="get_standings", description="Current standings"),
            schema.Tool(
                name="get_my_matches",
                description="List your matches in the tournament",
            ),
            schema.Tool(
                name="join_match",
                description="Join a match's game session to play",
                inputSchema={
                    "type": "object",
                    "properties": {"match_id": {"type": "string"}},
                    "required": ["match_id"],
                },
            ),
            schema.Tool(
                name="report_match_result",
                description="Report who won a match",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "match_id": {"type": "string"},
                        "winner": {"type": "string"},
                    },
                    "required": ["match_id", "winner"],
                },
            ),
            schema.Tool(
                name="cancel_tournament",
                description="Cancel tournament (host only)",
                inputSchema={
                    "type": "object",
                    "properties": {"tournament_id": {"type": "string"}},
                },
            ),
            schema.Tool(name="leave_tournament", description="Leave tournament"),
            schema.Tool(
                name="my_status",
                description="Your current status",
                annotations={"readOnlyHint": True},
            ),
            schema.Tool(
                name="wait_for_match",
                description=(
                    "Block until a match is ready for you. "
                    "Returns immediately if a match is already active. "
                    "Use instead of polling get_my_matches. Max 60s."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "timeout": {
                            "type": "number",
                            "description": "Max seconds to wait (default 60, max 60)",
                        },
                    },
                },
            ),
            schema.Tool(
                name="help",
                description="What to do next — contextual guidance",
            ),
        ]

    def _spectating_tools(self) -> list[schema.Tool]:
        return _GLOBAL_TOOLS + spectator_handlers.SPECTATING_TOOLS


# ---------------------------------------------------------------------------
# Helpers


def _pending_actions_data(state, player_id: str) -> list[dict]:
    """Build structured list of pending actions for a player."""
    items: list[dict] = []
    for iid, pd in state.pending_deals.items():
        if player_id in pd.responders and pd.responders[player_id] is None:
            items.append({
                "type": "respond",
                "instance_id": iid,
                "deal_id": pd.deal_id,
            })
    for iid, pv in state.pending_votes.items():
        if player_id in pv.eligible and player_id not in pv.votes:
            items.append({
                "type": "vote",
                "action": f"vote_{pv.vote_id}",
                "instance_id": iid,
                "vote_id": pv.vote_id,
                "options": list(pv.options),
                "subject": pv.subject,
            })
    return items


def _available_actions_data(state, compiled, player_id: str) -> dict:
    """Build structured dict of available actions for a player."""
    deals = [
        d for d in compiled.deals
        if formatters.can_player_use_deal(state, compiled, d, player_id)
    ]
    votes = [f"vote_{v}" for v in compiled.votes]
    speech_acts = [
        s for s in compiled.speech_acts
        if formatters.can_player_use_speech_act(state, compiled, s, player_id)
    ]
    return {"deals": deals, "votes": votes, "speech_acts": speech_acts}


def _detect_trigger(
    session,
    prev_phase,
    prev_round,
    prev_deals,
    prev_votes,
    prev_status,
    timed_out,
) -> str:
    """Compare state before/after wait to produce a human-readable trigger."""
    if timed_out:
        return "timeout (no changes)"
    cur = session.state
    parts: list[str] = []
    if cur.status != prev_status and cur.status == "ended":
        parts.append("game_ended")
    if cur.phase != prev_phase:
        parts.append(f"phase_changed ({prev_phase} -> {cur.phase})")
    if cur.round != prev_round:
        parts.append(f"new_round ({prev_round} -> {cur.round})")
    if len(cur.pending_deals) > prev_deals:
        new_deals = [pd.deal_id for pd in cur.pending_deals.values()]
        parts.append(f"deal_proposed ({', '.join(new_deals)})")
    elif len(cur.pending_deals) < prev_deals:
        parts.append("deal_resolved")
    if len(cur.pending_votes) > prev_votes:
        new_votes = [pv.vote_id for pv in cur.pending_votes.values()]
        parts.append(f"vote_started ({', '.join(new_votes)})")
    elif len(cur.pending_votes) < prev_votes:
        parts.append("vote_completed")
    return "; ".join(parts) if parts else "state_changed"


# ---------------------------------------------------------------------------


def _content(text: str, *, data: dict | None = None) -> dict:
    blocks: list[dict] = [{"type": "text", "text": text}]
    if data is not None:
        import json as _json

        blocks.append(
            {"type": "text", "text": f"\n```json\n{_json.dumps(data, default=str)}\n```"}
        )
    return {"content": blocks}


def _with_trigger(trigger: str, status: dict) -> dict:
    """Prepend trigger info to a status response without mutating the original."""
    content = status.get("content", [])
    if not content or status.get("isError"):
        return status
    original_text = content[0].get("text", "")
    return {
        "content": [
            {"type": "text", "text": f"Trigger: {trigger}\n\n{original_text}"},
            *content[1:],  # preserve JSON data blocks
        ]
    }


def _error(message: str, *, code: str = "", suggestion: str = "") -> dict:
    text = f"Error: [{code}] {message}" if code else f"Error: {message}"
    if suggestion:
        text += f"\nSuggestion: {suggestion}"
    return {"content": [{"type": "text", "text": text}], "isError": True}
