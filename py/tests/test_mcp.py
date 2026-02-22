"""Tests for MCP schema generation, agents, tokens, and server."""

from __future__ import annotations

import pytest

from engine.runtime.core import GameRuntime
from engine.runtime.state import (
    AttrDef,
    ChannelDef,
    CompiledGame,
    DealDef,
    Entity,
    GameState,
    OutcomeDef,
    ParamDef,
    PartyDef,
    PhaseDef,
    ResourceDef,
    VictoryDef,
    Visibility,
    VoteDef,
)
from mcp.agents import (
    STALE_TIMEOUT,
    AgentState,
    cleanup_stale,
    get_agent,
    register_agent,
    remove_agent,
)
from mcp.agents import (
    reset_all as reset_agents,
)
from mcp.formatters import (
    _compute_advance_readiness,
    _format_resource_delta,
    _format_usage_limit,
    format_available_actions,
    format_deal_result,
    format_history,
    format_status,
    format_vote_result,
)
from mcp.schema import (
    PartyClassification,
    channel_to_tool,
    classify_parties,
    deal_to_tool,
    filter_tools_for_phase,
    generate_game_tools,
    vote_to_tool,
)
from mcp.tokens import create_token, set_secret, verify_token

# Deterministic secret for tests
set_secret(b"test-secret-for-unit-tests")

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_agents():
    reset_agents()
    yield
    reset_agents()


def _mini_game() -> CompiledGame:
    """Minimal compiled game for testing."""
    return CompiledGame(
        id="test",
        name="Test Game",
        min_players=2,
        max_players=4,
        resources={
            "gold": ResourceDef(id="gold", initial=100, visibility=Visibility.PRIVATE),
            "health": ResourceDef(
                id="health", initial=10, visibility=Visibility.PUBLIC
            ),
        },
        attrs_defs={
            "role": AttrDef(id="role", visibility=Visibility.PRIVATE),
        },
        deals={
            "attack": DealDef(
                id="attack",
                parties={
                    "actor": PartyDef(),
                    "target": PartyDef(),
                },
                doc="Attack another player",
            ),
            "bribe": DealDef(
                id="bribe",
                parties={
                    "proposer": PartyDef(),
                    "responder": PartyDef(),
                },
                params={
                    "amount": ParamDef(
                        type="number", min=1, max=100, label="Bribe amount"
                    ),
                },
                response_options=("accept", "reject"),
                outcomes={
                    "accept": OutcomeDef(doc="Bribe accepted"),
                    "reject": OutcomeDef(doc="Bribe rejected"),
                },
                doc="Offer a bribe",
            ),
            "rally": DealDef(
                id="rally",
                parties={"actor": PartyDef()},
                doc="Hold a rally (night only)",
            ),
        },
        votes={
            "lynch": VoteDef(
                id="lynch",
                options=("guilty", "innocent"),
                threshold="majority",
                doc="Vote to lynch",
            ),
        },
        phases=(
            PhaseDef(id="day", allows=("attack", "bribe")),
            PhaseDef(id="night", allows=("rally",)),
            PhaseDef(id="vote", allows=("lynch",)),
        ),
        victories=(VictoryDef(id="last_standing", message="Last one standing wins"),),
        channels={
            "town": ChannelDef(id="town", type="public", description="Town square"),
        },
    )


# ===========================================================================
# Agent State Machine
# ===========================================================================


class TestAgentState:
    def test_register_and_get(self):
        agent = register_agent("alice")
        assert agent.agent_id == "alice"
        assert agent.state == "lobby"

        found = get_agent("alice")
        assert found is agent

    def test_state_transitions(self):
        agent = AgentState(agent_id="bob")
        assert agent.state == "lobby"

        agent.to_game("session-1", "player-bob", "duel")
        assert agent.state == "in_game"
        assert agent.session_id == "session-1"
        assert agent.player_id == "player-bob"

        agent.to_lobby()
        assert agent.state == "lobby"
        assert agent.session_id is None

    def test_tournament_transitions(self):
        agent = AgentState(agent_id="carol")

        agent.to_tournament("tourney-1")
        assert agent.state == "in_tournament"
        assert agent.tournament_id == "tourney-1"

        # Enter match from tournament
        agent.to_game_from_tournament("session-2", "player-carol", "duel", "match-0")
        assert agent.state == "in_game"
        assert agent.tournament_context is not None
        assert agent.tournament_context.tournament_id == "tourney-1"

        # Return to tournament
        agent.back_to_tournament()
        assert agent.state == "in_tournament"
        assert agent.tournament_id == "tourney-1"
        assert agent.tournament_context is None

    def test_remove_agent(self):
        register_agent("temp")
        assert get_agent("temp") is not None
        remove_agent("temp")
        assert get_agent("temp") is None


# ===========================================================================
# Agent Cleanup (Fix 1)
# ===========================================================================


class TestAgentCleanup:
    def test_cleanup_stale_removes_old_agents(self):
        """Agents idle beyond STALE_TIMEOUT are removed."""
        import time

        a1 = register_agent("fresh")
        a2 = register_agent("stale")
        # Backdate stale agent
        a2.last_seen = time.time() - STALE_TIMEOUT - 1

        removed = cleanup_stale()
        assert removed == 1
        assert get_agent("fresh") is not None
        assert get_agent("stale") is None

    def test_cleanup_stale_no_stale(self):
        """No removals when all agents are fresh."""
        register_agent("a")
        register_agent("b")
        removed = cleanup_stale()
        assert removed == 0

    def test_cleanup_stale_empty_registry(self):
        """Cleanup on empty registry returns 0."""
        removed = cleanup_stale()
        assert removed == 0


# ===========================================================================
# Party Classification
# ===========================================================================


class TestPartyClassification:
    def test_immediate_single_party(self):
        result = classify_parties({"actor": PartyDef()})
        assert result.type == "immediate"
        assert result.mapping["actor"] == "actor"

    def test_bilateral(self):
        result = classify_parties(
            {
                "proposer": PartyDef(),
                "responder": PartyDef(),
            }
        )
        assert result.type == "bilateral"
        assert result.mapping["proposer"] == "actor"
        assert result.mapping["responder"] == "responder"

    def test_with_target(self):
        result = classify_parties(
            {
                "actor": PartyDef(),
                "target": PartyDef(),
            }
        )
        assert result.type == "bilateral"
        assert result.mapping["actor"] == "actor"
        assert result.mapping["target"] == "target"

    def test_multilateral(self):
        result = classify_parties(
            {
                "leader": PartyDef(),
                "partners": PartyDef(count=(2, 5)),
            }
        )
        assert result.type == "multilateral"
        assert result.mapping["leader"] == "actor"
        assert result.mapping["partners"] == "responders"

    def test_empty_parties(self):
        result = classify_parties({})
        assert result.type == "immediate"

    def test_custom_party_names(self):
        result = classify_parties(
            {
                "investigator": PartyDef(),
                "suspect": PartyDef(),
            }
        )
        assert result.type == "bilateral"
        assert result.mapping["investigator"] == "actor"
        assert result.mapping["suspect"] == "responder"


# ===========================================================================
# Tool Schema Generation
# ===========================================================================


class TestToolGeneration:
    def test_deal_to_tool_simple(self):
        game = _mini_game()
        tool = deal_to_tool("test", "attack", game.deals["attack"])
        assert tool.name == "test/attack"
        assert "target" in tool.inputSchema["properties"]
        assert tool._meta["type"] == "deal"
        assert tool._meta["deal_id"] == "attack"

    def test_deal_to_tool_with_params(self):
        game = _mini_game()
        tool = deal_to_tool("test", "bribe", game.deals["bribe"])
        assert tool.name == "test/bribe"
        props = tool.inputSchema["properties"]
        assert "responder" in props
        assert "amount" in props
        assert props["amount"]["type"] == "number"
        assert props["amount"]["minimum"] == 1
        assert props["amount"]["maximum"] == 100

    def test_vote_to_tool(self):
        game = _mini_game()
        tool = vote_to_tool("test", "lynch", game.votes["lynch"])
        assert tool.name == "test/vote_lynch"
        assert "option" in tool.inputSchema["properties"]
        assert tool.inputSchema["properties"]["option"]["enum"] == [
            "guilty",
            "innocent",
        ]

    def test_channel_to_tool(self):
        game = _mini_game()
        tool = channel_to_tool("test", "town", game.channels["town"])
        assert tool.name == "test/send_town"
        assert "content" in tool.inputSchema["properties"]

    def test_generate_all_tools(self):
        game = _mini_game()
        tools = generate_game_tools(game)
        names = {t.name for t in tools}
        # Deals
        assert "test/attack" in names
        assert "test/bribe" in names
        assert "test/rally" in names
        # Votes
        assert "test/vote_lynch" in names
        # Channels
        assert "test/send_town" in names
        # Universal
        assert "get_status" in names
        assert "advance_phase" in names
        assert "respond" in names


# ===========================================================================
# Tool Filtering
# ===========================================================================


class TestToolFiltering:
    def test_filter_by_phase(self):
        game = _mini_game()
        all_tools = generate_game_tools(game)

        # Simulate day phase state
        from engine.runtime.state import Entity, GameState

        state = GameState(
            phase="day",
            entities={"alice": Entity(id="alice"), "bob": Entity(id="bob")},
        )

        filtered = filter_tools_for_phase(all_tools, state, game, "alice")
        names = {t.name for t in filtered}

        # Day allows attack, bribe — not rally, not lynch
        assert "test/attack" in names
        assert "test/bribe" in names
        assert "test/rally" not in names
        assert "test/vote_lynch" not in names

        # Universal tools always present
        assert "get_status" in names
        assert "advance_phase" in names

    def test_filter_night_phase(self):
        game = _mini_game()
        all_tools = generate_game_tools(game)

        from engine.runtime.state import Entity, GameState

        state = GameState(
            phase="night",
            entities={"alice": Entity(id="alice")},
        )

        filtered = filter_tools_for_phase(all_tools, state, game, "alice")
        names = {t.name for t in filtered}

        assert "test/rally" in names
        assert "test/attack" not in names
        assert "test/bribe" not in names


# ===========================================================================
# Tokens
# ===========================================================================


class TestTokens:
    def test_create_and_verify(self):
        token = create_token(
            agent_id="alice",
            session_id="game-1",
            player_id="player-alice",
            game_type="duel",
        )
        payload = verify_token(token, "alice")
        assert payload is not None
        assert payload["session_id"] == "game-1"
        assert payload["player_id"] == "player-alice"
        assert payload["game_type"] == "duel"

    def test_wrong_agent(self):
        token = create_token("alice", "game-1", "player-alice", "duel")
        payload = verify_token(token, "bob")
        assert payload is None  # agent binding mismatch

    def test_tampered_token(self):
        token = create_token("alice", "game-1", "player-alice", "duel")
        # Tamper with payload
        tampered = "X" + token[1:]
        payload = verify_token(tampered, "alice")
        assert payload is None

    def test_expired_token(self):
        token = create_token("alice", "game-1", "player-alice", "duel", expiry_hours=0)
        payload = verify_token(token, "alice")
        assert payload is None  # expired immediately


# ===========================================================================
# Formatters
# ===========================================================================


class TestFormatters:
    def test_format_status(self):
        view = {
            "phase": "day",
            "round": 2,
            "status": "active",
            "entities": {
                "alice": {
                    "active": True,
                    "resources": {"gold": 100, "health": 8},
                    "attrs": {"role": "detective"},
                    "groups": [],
                },
                "bob": {
                    "active": True,
                    "resources": {"health": 10},
                    "attrs": {},
                    "groups": [],
                },
            },
            "messages": [],
            "vars": {},
        }
        game = _mini_game()
        result = format_status(view, game, "alice")
        assert "Test Game" in result
        assert "Phase: day" in result
        assert "gold: 100" in result
        assert "Role: detective" in result
        assert "bob" in result

    def test_format_deal_result_success(self):
        result = format_deal_result({"ok": True, "outcome": "accept"})
        assert "successfully" in result
        assert "accept" in result

    def test_format_deal_result_error(self):
        result = format_deal_result(
            {"ok": False, "error": {"message": "Not enough gold"}}
        )
        assert "Error" in result
        assert "Not enough gold" in result

    def test_format_history_empty(self):
        from engine.runtime.state import GameState

        state = GameState()
        result = format_history(state)
        assert "No events" in result

    def test_format_status_action_required(self):
        """ACTION REQUIRED appears when pending deals/votes exist."""
        from engine.runtime.state import GameState, PendingDeal, PendingVote

        state = GameState(
            phase="day",
            round=1,
            status="active",
            pending_deals={
                "deal-0": PendingDeal(
                    instance_id="deal-0",
                    deal_id="bribe",
                    proposer="bob",
                    responders={"alice": None},
                )
            },
            pending_votes={
                "vote-0": PendingVote(
                    instance_id="vote-0",
                    vote_id="lynch",
                    eligible=("alice", "bob", "charlie"),
                    options=("guilty", "innocent"),
                    votes={"bob": "guilty"},
                )
            },
        )
        view = {
            "phase": "day",
            "round": 1,
            "status": "active",
            "entities": {
                "alice": {"active": True, "resources": {}, "attrs": {}, "groups": []},
            },
            "vars": {},
            "_state": state,
        }
        game = _mini_game()
        result = format_status(view, game, "alice")
        assert "ACTION REQUIRED" in result
        assert "bribe" in result
        assert "bob" in result
        assert "lynch" in result
        assert "[1/3 voted]" in result

    def test_format_status_eliminated_separate(self):
        """Eliminated players shown separately."""
        view = {
            "phase": "day",
            "round": 1,
            "status": "active",
            "entities": {
                "alice": {"active": True, "resources": {}, "attrs": {}, "groups": []},
                "bob": {"active": True, "resources": {}, "attrs": {}, "groups": []},
                "charlie": {
                    "active": False,
                    "resources": {},
                    "attrs": {},
                    "groups": [],
                },
            },
            "vars": {},
        }
        game = _mini_game()
        result = format_status(view, game, "alice")
        assert "~~charlie~~" in result
        assert "Eliminated" in result

    def test_format_status_active_count(self):
        """Player count shows active/total."""
        view = {
            "phase": "day",
            "round": 1,
            "status": "active",
            "entities": {
                "a": {"active": True, "resources": {}, "attrs": {}, "groups": []},
                "b": {"active": True, "resources": {}, "attrs": {}, "groups": []},
                "c": {"active": False, "resources": {}, "attrs": {}, "groups": []},
            },
            "vars": {},
        }
        game = _mini_game()
        result = format_status(view, game, "a")
        assert "2/3 active" in result

    def test_format_available_actions_categorized(self):
        """Actions are categorized: Deals, Votes, Responses, Phase Control."""
        from engine.runtime.state import GameState

        state = GameState(
            phase="day",
            round=1,
            status="active",
            entities={"alice": Entity(id="alice", resources={"gold": 100})},
        )
        game = _mini_game()
        result = format_available_actions(state, game, "alice")
        assert "### Deals" in result
        assert "attack" in result
        assert "bribe" in result
        assert "### Phase Control" in result

    def test_format_available_actions_with_responses(self):
        """Pending responses shown as URGENT."""
        from engine.runtime.state import GameState, PendingDeal

        state = GameState(
            phase="day",
            round=1,
            status="active",
            entities={"alice": Entity(id="alice", resources={"gold": 100})},
            pending_deals={
                "deal-0": PendingDeal(
                    instance_id="deal-0",
                    deal_id="bribe",
                    proposer="bob",
                    responders={"alice": None},
                )
            },
        )
        game = _mini_game()
        result = format_available_actions(state, game, "alice")
        assert "### Responses (URGENT)" in result
        assert "bribe" in result
        assert "bob" in result

    def test_format_available_actions_vote_progress(self):
        """Pending votes show progress [N/M voted]."""
        from engine.runtime.state import GameState, PendingVote

        state = GameState(
            phase="vote",
            round=1,
            status="active",
            entities={"alice": Entity(id="alice")},
            pending_votes={
                "v-0": PendingVote(
                    instance_id="v-0",
                    vote_id="lynch",
                    eligible=("alice", "bob", "charlie"),
                    options=("guilty", "innocent"),
                    votes={"bob": "guilty"},
                )
            },
        )
        game = _mini_game()
        result = format_available_actions(state, game, "alice")
        assert "### Pending Votes" in result
        assert "[1/3 voted]" in result

    def test_format_available_actions_usage_limits(self):
        """Usage limits shown inline."""
        from engine.runtime.state import DealDef, GameState, PartyDef

        deal_with_limit = DealDef(
            id="limited",
            parties={"actor": PartyDef()},
            doc="Limited action",
            per_round=2,
        )
        game = _mini_game()
        # Inject deal with limit
        import attrs

        game = attrs.evolve(
            game,
            deals={**game.deals, "limited": deal_with_limit},
            phases=(PhaseDef(id="day", allows=("limited",)),),
        )
        state = GameState(
            phase="day",
            round=1,
            status="active",
            entities={"alice": Entity(id="alice")},
            usage={"alice:limited": {"round:1": 1}},
        )
        result = format_available_actions(state, game, "alice")
        assert "[1/2 round]" in result

    def test_format_deal_result_with_deltas(self):
        """Resource deltas shown when state_before/after provided."""
        from engine.runtime.state import GameState

        before = GameState(
            entities={
                "alice": Entity(id="alice", resources={"gold": 100, "health": 10})
            }
        )
        after = GameState(
            entities={"alice": Entity(id="alice", resources={"gold": 90, "health": 10})}
        )
        result = format_deal_result(
            {"ok": True, "outcome": "accept"},
            state_before=before,
            state_after=after,
            player_id="alice",
        )
        assert "gold: 100 -> 90 (-10)" in result

    def test_format_deal_result_error_tips(self):
        """Error messages include contextual tips."""
        result = format_deal_result(
            {"ok": False, "error": {"message": "Limit reached", "code": "usage_limit"}}
        )
        assert "advance_phase" in result

    def test_format_vote_result_with_auto_advance(self):
        """Vote result shows auto-advance notification."""
        result = format_vote_result(
            {"ok": True, "auto_completed": True, "auto_advanced": True}
        )
        assert "auto-advanced" in result

    def test_advance_readiness_blocked(self):
        """BLOCKED when pending deals/votes exist."""
        from engine.runtime.state import GameState, PendingVote

        state = GameState(
            phase="day",
            pending_votes={
                "v-0": PendingVote(
                    instance_id="v-0",
                    vote_id="lynch",
                    eligible=("alice",),
                    options=("yes",),
                )
            },
        )
        game = _mini_game()
        assert _compute_advance_readiness(state, game, "alice") == "BLOCKED"

    def test_advance_readiness_ready(self):
        """READY when no pending actions and no active entities with usable deals."""
        from engine.runtime.state import GameState

        # No entities → nothing can act → READY
        state = GameState(phase="night", round=1, status="active")
        game = _mini_game()
        assert _compute_advance_readiness(state, game, "alice") == "READY"

    def test_advance_readiness_optional(self):
        """OPTIONAL when deals available but none pending."""
        from engine.runtime.state import GameState

        state = GameState(
            phase="day",
            round=1,
            status="active",
            entities={"alice": Entity(id="alice", resources={"gold": 100})},
        )
        game = _mini_game()
        assert _compute_advance_readiness(state, game, "alice") == "OPTIONAL"

    def test_resource_delta_no_change(self):
        """No delta text when resources unchanged."""
        from engine.runtime.state import GameState

        state = GameState(
            entities={"alice": Entity(id="alice", resources={"gold": 100})}
        )
        result = _format_resource_delta(state, state, "alice")
        assert result == ""

    def test_resource_delta_with_change(self):
        """Delta shows changes with +/- signs."""
        from engine.runtime.state import GameState

        before = GameState(
            entities={
                "alice": Entity(id="alice", resources={"gold": 100, "health": 10})
            }
        )
        after = GameState(
            entities={"alice": Entity(id="alice", resources={"gold": 120, "health": 7})}
        )
        result = _format_resource_delta(before, after, "alice")
        assert "gold: 100 -> 120 (+20)" in result
        assert "health: 10 -> 7 (-3)" in result


# ===========================================================================
# Helpers for MCP server tests
# ===========================================================================


class _SimpleSessionStore:
    """Minimal session store for testing MCPServer."""

    def __init__(self):
        from server.sessions import (
            create_session as _create,
        )
        from server.sessions import (
            get_session as _get,
        )
        from server.sessions import (
            list_sessions as _list,
        )
        from server.sessions import (
            remove_session as _remove,
        )

        self._create = _create
        self._get = _get
        self._list = _list
        self._remove = _remove

    def get(self, sid):
        return self._get(sid)

    def list_all(self):
        return self._list()

    def create(self, sid, compiled, player_ids):
        return self._create(sid, compiled, player_ids)

    def remove(self, sid):
        self._remove(sid)


# ===========================================================================
# Message Length Validation (Fix 4)
# ===========================================================================


class TestMessageLengthValidation:
    @pytest.mark.asyncio
    async def test_message_too_long_rejected(self):
        """Messages exceeding MAX_MESSAGE_LENGTH are rejected."""
        from mcp.server import MCPServer
        from server.sessions import remove_session

        server = MCPServer(sessions=_SimpleSessionStore())
        from games import REGISTRY

        for gid, compiled in REGISTRY.items():
            server.register_game(compiled)

        # Create game — agent enters in_game state
        await server.handle_request(
            "host",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "create_game",
                    "arguments": {
                        "game_type": "auction",
                        "session_id": "msg-len-test",
                        "player_id": "p1",
                        "players": ["p1", "p2", "p3"],
                    },
                },
            },
        )

        # Try sending a too-long message via auction/send_lobby (channel tool)
        long_msg = "x" * 501
        resp = await server.handle_request(
            "host",
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "auction/send_auction_floor",
                    "arguments": {"content": long_msg},
                },
            },
        )
        text = resp["result"]["content"][0]["text"]
        assert "too long" in text.lower() or "Maximum" in text
        remove_session("msg-len-test")

    @pytest.mark.asyncio
    async def test_message_exact_limit_accepted(self):
        """Message at exactly MAX_MESSAGE_LENGTH is not rejected for length."""
        from mcp.server import MCPServer
        from server.sessions import remove_session

        server = MCPServer(sessions=_SimpleSessionStore())
        from games import REGISTRY

        for gid, compiled in REGISTRY.items():
            server.register_game(compiled)

        await server.handle_request(
            "host",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "create_game",
                    "arguments": {
                        "game_type": "auction",
                        "session_id": "msg-ok-test",
                        "player_id": "p1",
                        "players": ["p1", "p2", "p3"],
                    },
                },
            },
        )

        ok_msg = "x" * 500
        resp = await server.handle_request(
            "host",
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "auction/send_auction_floor",
                    "arguments": {"content": ok_msg},
                },
            },
        )
        text = resp["result"]["content"][0]["text"]
        # Should NOT contain the "too long" error
        assert "too long" not in text.lower()
        remove_session("msg-ok-test")

    def test_max_message_length_constant(self):
        """MAX_MESSAGE_LENGTH is set to a reasonable value."""
        from mcp.server import MCPServer

        assert MCPServer.MAX_MESSAGE_LENGTH == 500


# ===========================================================================
# Dead Player Deal Response (Fix 5)
# ===========================================================================


class TestDeadPlayerDealResponse:
    def test_respond_to_deal_rejects_dead_responder(self):
        """respond_to_deal() rejects responses from eliminated players."""
        import attrs

        from engine.runtime.state import PendingDeal

        game = _mini_game()
        rt = GameRuntime(game)
        state = rt.start_game(["alice", "bob"], seed=42)

        # Manually inject a pending bilateral deal
        pending = PendingDeal(
            instance_id="deal-1",
            deal_id="bribe",
            proposer="alice",
            responders={"bob": None},
            params={"amount": 10},
            stakes={},
        )
        state = attrs.evolve(
            state, pending_deals={**state.pending_deals, "deal-1": pending}
        )

        # Kill bob
        bob_entity = state.entities["bob"]
        dead_bob = attrs.evolve(bob_entity, active=False)
        state = attrs.evolve(state, entities={**state.entities, "bob": dead_bob})

        # Bob tries to respond — should fail with sender_inactive
        result = rt.respond_to_deal(state, "deal-1", "bob", "accept")
        assert not result["ok"]
        assert result["error"]["code"] == "sender_inactive"

    def test_respond_to_deal_allows_alive_responder(self):
        """respond_to_deal() works normally for alive responders."""
        import attrs

        from engine.runtime.state import PendingDeal

        game = _mini_game()
        rt = GameRuntime(game)
        state = rt.start_game(["alice", "bob"], seed=42)

        # Manually inject a pending bilateral deal
        pending = PendingDeal(
            instance_id="deal-1",
            deal_id="bribe",
            proposer="alice",
            responders={"bob": None},
            params={"amount": 10},
            stakes={},
        )
        state = attrs.evolve(
            state, pending_deals={**state.pending_deals, "deal-1": pending}
        )

        # Bob responds normally — should work
        result = rt.respond_to_deal(state, "deal-1", "bob", "accept")
        assert result["ok"]


class TestErrorCodes:
    """_error() produces structured error messages with codes and suggestions."""

    def test_error_with_code(self):
        from mcp.server import _error

        result = _error("Game not found", code="unknown_game")
        text = result["content"][0]["text"]
        assert "[unknown_game]" in text
        assert "Game not found" in text
        assert result["isError"] is True

    def test_error_with_suggestion(self):
        from mcp.server import _error

        result = _error(
            "Not in a game", code="not_in_game", suggestion="Use create_game first"
        )
        text = result["content"][0]["text"]
        assert "[not_in_game]" in text
        assert "Suggestion: Use create_game first" in text

    def test_error_without_code(self):
        from mcp.server import _error

        result = _error("Something went wrong")
        text = result["content"][0]["text"]
        assert text == "Error: Something went wrong"
        assert "[" not in text

    def test_error_code_in_game_tool_dispatch(self):
        """_handle_game_tool returns error with code when not in game."""
        from mcp.server import MCPServer

        server = MCPServer(sessions=_SimpleSessionStore())
        from games import REGISTRY

        for gid, compiled in REGISTRY.items():
            server.register_game(compiled)

        import asyncio

        resp = asyncio.get_event_loop().run_until_complete(
            server.handle_request(
                "agent1",
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "auction/sealed_bid",
                        "arguments": {},
                    },
                },
            )
        )
        text = resp["result"]["content"][0]["text"]
        assert "[not_in_game]" in text


class TestDetectTrigger:
    """_detect_trigger correctly identifies state change type."""

    def test_timeout_trigger(self):
        from mcp.server import _detect_trigger

        class FakeSession:
            class state:
                phase = "action"
                round = 1
                pending_deals = {}
                pending_votes = {}
                status = "active"

        result = _detect_trigger(FakeSession(), "action", 1, 0, 0, "active", True)
        assert result == "timeout (no changes)"

    def test_new_phase_trigger(self):
        from mcp.server import _detect_trigger

        class FakeSession:
            class state:
                phase = "voting"
                round = 1
                pending_deals = {}
                pending_votes = {}
                status = "active"

        result = _detect_trigger(FakeSession(), "action", 1, 0, 0, "active", False)
        assert "phase_changed" in result
        assert "action" in result
        assert "voting" in result

    def test_new_round_trigger(self):
        from mcp.server import _detect_trigger

        class FakeSession:
            class state:
                phase = "action"
                round = 2
                pending_deals = {}
                pending_votes = {}
                status = "active"

        result = _detect_trigger(FakeSession(), "action", 1, 0, 0, "active", False)
        assert "new_round" in result

    def test_deal_proposed_trigger(self):
        from mcp.server import _detect_trigger

        class FakeDeal:
            deal_id = "bribe"

        class FakeSession:
            class state:
                phase = "action"
                round = 1
                pending_deals = {"d1": FakeDeal()}
                pending_votes = {}
                status = "active"

        result = _detect_trigger(FakeSession(), "action", 1, 0, 0, "active", False)
        assert "deal_proposed" in result
        assert "bribe" in result

    def test_vote_started_trigger(self):
        from mcp.server import _detect_trigger

        class FakeVote:
            vote_id = "expulsion"

        class FakeSession:
            class state:
                phase = "action"
                round = 1
                pending_deals = {}
                pending_votes = {"v1": FakeVote()}
                status = "active"

        result = _detect_trigger(FakeSession(), "action", 1, 0, 0, "active", False)
        assert "vote_started" in result
        assert "expulsion" in result

    def test_game_ended_trigger(self):
        from mcp.server import _detect_trigger

        class FakeSession:
            class state:
                phase = "action"
                round = 1
                pending_deals = {}
                pending_votes = {}
                status = "ended"

        result = _detect_trigger(FakeSession(), "action", 1, 0, 0, "active", False)
        assert result == "game_ended"

    def test_unknown_change_trigger(self):
        from mcp.server import _detect_trigger

        class FakeSession:
            class state:
                phase = "action"
                round = 1
                pending_deals = {}
                pending_votes = {}
                status = "active"

        result = _detect_trigger(FakeSession(), "action", 1, 0, 0, "active", False)
        assert result == "state_changed"


# ===========================================================================
# Fix: except→False + logging in filter eval (formatters.py)
# ===========================================================================


class TestFilterEvalFallback:
    """Verify that filter eval errors return False (not True) and log warnings."""

    def test_can_player_use_deal_filter_error_returns_false(self):
        """If Expr evaluation raises, can_player_use_deal returns False."""
        import attrs

        from engine.expr.core import Call
        from mcp.formatters import can_player_use_deal

        # Create a deal with a guard that will raise
        broken_guard = Call("nonexistent_function_xyz", ())
        game = _mini_game()
        broken_deal = attrs.evolve(game.deals["attack"], guard=broken_guard)
        game = attrs.evolve(game, deals={**game.deals, "attack": broken_deal})

        state = GameState(
            phase="day",
            entities={"alice": Entity(id="alice")},
        )
        # Should return False (not True) when eval raises
        assert can_player_use_deal(state, game, "attack", "alice") is False

    def test_can_player_use_speech_act_filter_error_returns_false(self):
        """If Expr evaluation raises, can_player_use_speech_act returns False."""
        import attrs

        from engine.expr.core import Call
        from engine.runtime.state import SpeechActDef
        from mcp.formatters import can_player_use_speech_act

        broken_filter = Call("nonexistent_function_xyz", ())
        sa = SpeechActDef(
            id="broken_sa",
            act_type="claim",
            actor_filter=broken_filter,
        )
        game = attrs.evolve(_mini_game(), speech_acts={"broken_sa": sa})
        state = GameState(
            phase="day",
            entities={"alice": Entity(id="alice")},
        )
        assert can_player_use_speech_act(state, game, "broken_sa", "alice") is False

    def test_can_player_start_vote_filter_error_returns_false(self):
        """If Expr evaluation raises, can_player_start_vote returns False."""
        import attrs

        from engine.expr.core import Call
        from mcp.formatters import can_player_start_vote

        broken_filter = Call("nonexistent_function_xyz", ())
        vote = attrs.evolve(
            _mini_game().votes["lynch"], proposer_filter=broken_filter
        )
        game = attrs.evolve(_mini_game(), votes={"lynch": vote})
        state = GameState(
            phase="vote",
            entities={"alice": Entity(id="alice")},
        )
        assert can_player_start_vote(state, game, "lynch", "alice") is False


# ===========================================================================
# Fix: TournamentContext typed dataclass (agents.py)
# ===========================================================================


class TestTournamentContext:
    def test_tournament_context_is_frozen_dataclass(self):
        from mcp.agents import TournamentContext

        ctx = TournamentContext(tournament_id="t1", match_id="m0")
        assert ctx.tournament_id == "t1"
        assert ctx.match_id == "m0"
        with pytest.raises(AttributeError):
            ctx.tournament_id = "t2"  # frozen

    def test_tournament_context_roundtrip(self):
        """Agent transitions preserve typed context."""
        from mcp.agents import TournamentContext

        agent = AgentState(agent_id="test")
        agent.to_tournament("t1")
        agent.to_game_from_tournament("s1", "p1", "auction", "m0")

        assert isinstance(agent.tournament_context, TournamentContext)
        assert agent.tournament_context.tournament_id == "t1"
        assert agent.tournament_context.match_id == "m0"

        agent.back_to_tournament()
        assert agent.tournament_context is None
        assert agent.tournament_id == "t1"


# ===========================================================================
# Fix: EXHAUSTED label in usage limits (formatters.py)
# ===========================================================================


class TestUsageLimitExhausted:
    def test_exhausted_label_when_limit_reached(self):
        """Usage limit shows EXHAUSTED prefix when fully used."""
        state = GameState(
            phase="day",
            round=1,
            usage={"alice:attack": {"round:1": 2}},
        )
        deal = DealDef(id="attack", per_round=2, parties={"actor": PartyDef()})
        result = _format_usage_limit(state, "attack", "alice", deal)
        assert "EXHAUSTED" in result
        assert "2/2 round" in result

    def test_no_exhausted_when_under_limit(self):
        """No EXHAUSTED prefix when usage is under limit."""
        state = GameState(
            phase="day",
            round=1,
            usage={"alice:attack": {"round:1": 1}},
        )
        deal = DealDef(id="attack", per_round=2, parties={"actor": PartyDef()})
        result = _format_usage_limit(state, "attack", "alice", deal)
        assert "EXHAUSTED" not in result
        assert "1/2 round" in result

    def test_exhausted_any_limit_type(self):
        """EXHAUSTED shown even if only one of multiple limits is hit."""
        state = GameState(
            phase="day",
            round=1,
            usage={"alice:x": {"phase:day": 3, "game": 1}},
        )

        class FakeDeal:
            per_round = None
            per_phase = 3
            per_game = 5

        result = _format_usage_limit(state, "x", "alice", FakeDeal())
        assert "EXHAUSTED" in result  # per_phase exhausted
        assert "3/3 phase" in result
        assert "1/5 game" in result


# ===========================================================================
# Second-pass: Lobby impersonation, atomic start, host reassignment, dispatch
# ===========================================================================


class TestLobbyImpersonation:
    """_join_pending_game rejects duplicate player_id from different agents."""

    @pytest.mark.asyncio
    async def test_pending_lobby_rejects_impersonation(self):
        from mcp.server import MCPServer
        from server.sessions import remove_session

        server = MCPServer(sessions=_SimpleSessionStore())
        from games import REGISTRY

        for gid, compiled in REGISTRY.items():
            server.register_game(compiled)

        # Agent "host" creates a lobby (auction needs 3 players)
        await server.handle_request(
            "host",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "create_game",
                    "arguments": {
                        "game_type": "auction",
                        "session_id": "impersonation-test",
                        "player_id": "host",
                    },
                },
            },
        )

        # Agent "alice" joins as player "p2"
        await server.handle_request(
            "alice",
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "join_game",
                    "arguments": {
                        "session_id": "impersonation-test",
                        "player_id": "p2",
                    },
                },
            },
        )

        # Agent "evil" tries to join also as "p2" — should be rejected
        resp = await server.handle_request(
            "evil",
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "join_game",
                    "arguments": {
                        "session_id": "impersonation-test",
                        "player_id": "p2",
                    },
                },
            },
        )
        text = resp["result"]["content"][0]["text"]
        assert "already" in text.lower()

        # Clean up
        server._pending_games.pop("impersonation-test", None)
        reset_agents()


class TestPendingGameAtomicStart:
    """_start_pending_game restores pending on failure."""

    @pytest.mark.asyncio
    async def test_pending_restored_on_start_failure(self):
        from mcp.server import MCPServer

        # Create a session store that always fails on create
        class FailingStore:
            def get(self, sid):
                return None

            def list_all(self):
                return {}

            def create(self, sid, compiled, player_ids):
                raise RuntimeError("Simulated failure")

            def remove(self, sid):
                pass

        server = MCPServer(sessions=FailingStore())
        from games import REGISTRY

        for gid, compiled in REGISTRY.items():
            server.register_game(compiled)

        # Create lobby
        await server.handle_request(
            "host",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "create_game",
                    "arguments": {
                        "game_type": "auction",
                        "session_id": "fail-start-test",
                        "player_id": "host",
                    },
                },
            },
        )
        assert "fail-start-test" in server._pending_games

        # Agent "p2" joins
        await server.handle_request(
            "p2",
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "join_game",
                    "arguments": {
                        "session_id": "fail-start-test",
                        "player_id": "p2",
                    },
                },
            },
        )

        # Agent "p3" joins — triggers auto-start which fails
        resp = await server.handle_request(
            "p3",
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "join_game",
                    "arguments": {
                        "session_id": "fail-start-test",
                        "player_id": "p3",
                    },
                },
            },
        )
        text = resp["result"]["content"][0]["text"]
        assert "Failed" in text or "Error" in text

        # Pending game should be restored, not lost
        assert "fail-start-test" in server._pending_games
        assert len(server._pending_games["fail-start-test"]["players"]) == 3

        reset_agents()


class TestHostReassignment:
    """Host is reassigned when original host leaves lobby."""

    @pytest.mark.asyncio
    async def test_host_reassigned_on_leave(self):
        from mcp.server import MCPServer

        server = MCPServer(sessions=_SimpleSessionStore())
        from games import REGISTRY

        for gid, compiled in REGISTRY.items():
            server.register_game(compiled)

        # Host creates lobby
        await server.handle_request(
            "host",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "create_game",
                    "arguments": {
                        "game_type": "auction",
                        "session_id": "host-leave-test",
                        "player_id": "host_player",
                    },
                },
            },
        )

        # p2 joins
        await server.handle_request(
            "p2",
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "join_game",
                    "arguments": {
                        "session_id": "host-leave-test",
                        "player_id": "p2",
                    },
                },
            },
        )

        pending = server._pending_games["host-leave-test"]
        assert pending["host"] == "host_player"

        # Host leaves
        await server.handle_request(
            "host",
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "leave_game", "arguments": {}},
            },
        )

        # Host should be reassigned to p2
        pending = server._pending_games["host-leave-test"]
        assert pending["host"] == "p2"
        assert "host_player" not in pending["players"]

        # Clean up
        server._pending_games.pop("host-leave-test", None)
        reset_agents()


class TestDispatchRespondToInquire:
    """_dispatch_action routes 'respond_to_inquire' via act tool."""

    @pytest.mark.asyncio
    async def test_respond_to_inquire_via_act(self):
        """Dispatching respond_to_inquire doesn't return 'Unknown action'."""
        from mcp.server import MCPServer
        from server.sessions import remove_session

        server = MCPServer(sessions=_SimpleSessionStore())
        from games import REGISTRY

        for gid, compiled in REGISTRY.items():
            server.register_game(compiled)

        # Create a game
        await server.handle_request(
            "host",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "create_game",
                    "arguments": {
                        "game_type": "auction",
                        "session_id": "inquire-dispatch-test",
                        "player_id": "p1",
                        "players": ["p1", "p2", "p3"],
                    },
                },
            },
        )

        # Use act with respond_to_inquire — should NOT return "Unknown action"
        resp = await server.handle_request(
            "host",
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "act",
                    "arguments": {
                        "action": "respond_to_inquire",
                        "args": {"instance_id": "fake", "response": "yes"},
                    },
                },
            },
        )
        text = resp["result"]["content"][0]["text"]
        # Should get a meaningful error (no such instance), not "Unknown action"
        assert "Unknown action" not in text

        remove_session("inquire-dispatch-test")
        reset_agents()


# ===========================================================================
# Change 1: Structured JSON in _content / get_status / available_actions
# ===========================================================================


class TestStructuredJSON:
    """_content() emits JSON data blocks alongside text."""

    def test_content_without_data(self):
        from mcp.server import _content

        resp = _content("hello")
        assert resp == {"content": [{"type": "text", "text": "hello"}]}

    def test_content_with_data(self):
        from mcp.server import _content

        resp = _content("hello", data={"phase": "action", "round": 1})
        assert len(resp["content"]) == 2
        assert resp["content"][0]["text"] == "hello"
        json_block = resp["content"][1]["text"]
        assert "```json" in json_block
        assert '"phase": "action"' in json_block

    @pytest.mark.asyncio
    async def test_get_status_returns_structured_data(self):
        """get_status includes a JSON data block with phase/round/resources."""
        from mcp.server import MCPServer
        from server.sessions import remove_session

        server = MCPServer(sessions=_SimpleSessionStore())
        from games import REGISTRY

        for gid, compiled in REGISTRY.items():
            server.register_game(compiled)

        await server.handle_request(
            "host",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "create_game",
                    "arguments": {
                        "game_type": "auction",
                        "session_id": "json-test",
                        "player_id": "p1",
                        "players": ["p1", "p2", "p3"],
                    },
                },
            },
        )

        resp = await server.handle_request(
            "host",
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "get_status", "arguments": {}},
            },
        )
        content = resp["result"]["content"]
        # First block is text, second is JSON
        assert len(content) >= 2
        json_block = content[1]["text"]
        assert "```json" in json_block
        assert '"phase"' in json_block
        assert '"resources"' in json_block

        remove_session("json-test")
        reset_agents()

    @pytest.mark.asyncio
    async def test_available_actions_returns_structured_data(self):
        """available_actions includes a JSON data block."""
        from mcp.server import MCPServer
        from server.sessions import remove_session

        server = MCPServer(sessions=_SimpleSessionStore())
        from games import REGISTRY

        for gid, compiled in REGISTRY.items():
            server.register_game(compiled)

        await server.handle_request(
            "host",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "create_game",
                    "arguments": {
                        "game_type": "auction",
                        "session_id": "actions-json-test",
                        "player_id": "p1",
                        "players": ["p1", "p2", "p3"],
                    },
                },
            },
        )

        resp = await server.handle_request(
            "host",
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "available_actions", "arguments": {}},
            },
        )
        content = resp["result"]["content"]
        assert len(content) >= 2
        json_block = content[1]["text"]
        assert '"deals"' in json_block

        remove_session("actions-json-test")
        reset_agents()


class TestPendingActionsData:
    """_pending_actions_data builds structured list of pending items."""

    def test_empty_state(self):
        from mcp.server import _pending_actions_data

        class FakeState:
            pending_deals = {}
            pending_votes = {}

        assert _pending_actions_data(FakeState(), "p1") == []

    def test_pending_deal_response(self):
        from mcp.server import _pending_actions_data

        class FakeDeal:
            deal_id = "bribe"
            responders = {"p1": None, "p2": "accept"}

        class FakeState:
            pending_deals = {"d1": FakeDeal()}
            pending_votes = {}

        result = _pending_actions_data(FakeState(), "p1")
        assert len(result) == 1
        assert result[0]["type"] == "respond"
        assert result[0]["deal_id"] == "bribe"
        assert result[0]["instance_id"] == "d1"

    def test_pending_vote(self):
        from mcp.server import _pending_actions_data

        class FakeVote:
            vote_id = "expulsion"
            eligible = {"p1", "p2"}
            votes = {}
            options = ("yes", "no")
            subject = "p3"

        class FakeState:
            pending_deals = {}
            pending_votes = {"v1": FakeVote()}

        result = _pending_actions_data(FakeState(), "p1")
        assert len(result) == 1
        assert result[0]["type"] == "vote"
        assert result[0]["action"] == "vote_expulsion"
        assert result[0]["vote_id"] == "expulsion"
        assert result[0]["subject"] == "p3"


# ===========================================================================
# Change 2: Simulate tool
# ===========================================================================


class TestSimulateTool:
    """simulate tool previews actions without committing."""

    @pytest.mark.asyncio
    async def test_simulate_not_in_game(self):
        from mcp.server import MCPServer

        server = MCPServer(sessions=_SimpleSessionStore())
        from games import REGISTRY

        for gid, compiled in REGISTRY.items():
            server.register_game(compiled)

        # Agent in lobby — simulate should fail
        resp = await server.handle_request(
            "agent1",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "simulate",
                    "arguments": {"action": "advance_phase"},
                },
            },
        )
        text = resp["result"]["content"][0]["text"]
        assert "Not in a game" in text
        reset_agents()

    @pytest.mark.asyncio
    async def test_simulate_no_action(self):
        from mcp.server import MCPServer
        from server.sessions import remove_session

        server = MCPServer(sessions=_SimpleSessionStore())
        from games import REGISTRY

        for gid, compiled in REGISTRY.items():
            server.register_game(compiled)

        await server.handle_request(
            "host",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "create_game",
                    "arguments": {
                        "game_type": "auction",
                        "session_id": "sim-test",
                        "player_id": "p1",
                        "players": ["p1", "p2", "p3"],
                    },
                },
            },
        )

        resp = await server.handle_request(
            "host",
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "simulate",
                    "arguments": {},
                },
            },
        )
        text = resp["result"]["content"][0]["text"]
        assert "Provide an action" in text

        remove_session("sim-test")
        reset_agents()

    @pytest.mark.asyncio
    async def test_simulate_advance_phase(self):
        """Simulate advance_phase shows transition without changing state."""
        from mcp.server import MCPServer
        from server.sessions import remove_session, get_session

        server = MCPServer(sessions=_SimpleSessionStore())
        from games import REGISTRY

        for gid, compiled in REGISTRY.items():
            server.register_game(compiled)

        await server.handle_request(
            "host",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "create_game",
                    "arguments": {
                        "game_type": "auction",
                        "session_id": "sim-adv-test",
                        "player_id": "p1",
                        "players": ["p1", "p2", "p3"],
                    },
                },
            },
        )

        session = get_session("sim-adv-test")
        phase_before = session.state.phase

        resp = await server.handle_request(
            "host",
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "simulate",
                    "arguments": {"action": "advance_phase"},
                },
            },
        )
        text = resp["result"]["content"][0]["text"]
        assert "SIMULATION" in text
        assert "would advance" in text.lower() or "Phase would advance" in text

        # State should be unchanged
        assert session.state.phase == phase_before

        remove_session("sim-adv-test")
        reset_agents()

    @pytest.mark.asyncio
    async def test_simulate_unknown_action(self):
        from mcp.server import MCPServer
        from server.sessions import remove_session

        server = MCPServer(sessions=_SimpleSessionStore())
        from games import REGISTRY

        for gid, compiled in REGISTRY.items():
            server.register_game(compiled)

        await server.handle_request(
            "host",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "create_game",
                    "arguments": {
                        "game_type": "auction",
                        "session_id": "sim-unk-test",
                        "player_id": "p1",
                        "players": ["p1", "p2", "p3"],
                    },
                },
            },
        )

        resp = await server.handle_request(
            "host",
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "simulate",
                    "arguments": {"action": "nonexistent_action"},
                },
            },
        )
        text = resp["result"]["content"][0]["text"]
        assert "SIMULATION" in text
        assert "Cannot simulate" in text

        remove_session("sim-unk-test")
        reset_agents()


# ===========================================================================
# Change 3: Opponent profile (analytics handler)
# ===========================================================================


class TestOpponentProfile:
    """opponent_profile handler analyzes player decision patterns."""

    def test_build_profile_empty(self):
        from mcp.handlers.analytics import _build_opponent_profile

        result = _build_opponent_profile([], "target", "me")
        assert result["games"] == 0
        assert result["win_rate"] == 0
        assert result["avg_decisions"] == 0

    def test_build_profile_with_wins(self):
        from mcp.handlers.analytics import _build_opponent_profile

        archives = [
            {
                "session_id": "nonexistent-1",
                "players": ["target", "me"],
                "metadata": {"winner": "target"},
            },
            {
                "session_id": "nonexistent-2",
                "players": ["target", "me"],
                "metadata": {"winner": "me"},
            },
        ]
        result = _build_opponent_profile(archives, "target", "me")
        assert result["games"] == 2
        assert result["win_rate"] == 50.0
        assert result["win_rate_vs"] == 50.0

    @pytest.mark.asyncio
    async def test_opponent_profile_no_player_id(self):
        from mcp.handlers.analytics import handle_opponent_profile

        agent = AgentState("tester")
        result = await handle_opponent_profile(None, agent, {})
        assert result.get("isError")
        assert "player_id" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_opponent_profile_self_rejected(self):
        from mcp.handlers.analytics import handle_opponent_profile

        agent = AgentState("tester")
        result = await handle_opponent_profile(
            None, agent, {"player_id": "tester"}
        )
        assert result.get("isError")
        assert "my_stats" in result["content"][0]["text"]


# ===========================================================================
# Change 4: Help tool (contextual guidance)
# ===========================================================================


class TestHelpTool:
    """help tool returns contextual guidance based on agent state."""

    @pytest.mark.asyncio
    async def test_help_in_lobby(self):
        from mcp.server import MCPServer

        server = MCPServer(sessions=_SimpleSessionStore())
        from games import REGISTRY

        for gid, compiled in REGISTRY.items():
            server.register_game(compiled)

        resp = await server.handle_request(
            "agent1",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "help", "arguments": {}},
            },
        )
        text = resp["result"]["content"][0]["text"]
        assert "Lobby" in text
        assert "create_game" in text
        assert "join_game" in text
        reset_agents()

    @pytest.mark.asyncio
    async def test_help_in_game(self):
        from mcp.server import MCPServer
        from server.sessions import remove_session

        server = MCPServer(sessions=_SimpleSessionStore())
        from games import REGISTRY

        for gid, compiled in REGISTRY.items():
            server.register_game(compiled)

        await server.handle_request(
            "host",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "create_game",
                    "arguments": {
                        "game_type": "auction",
                        "session_id": "help-test",
                        "player_id": "p1",
                        "players": ["p1", "p2", "p3"],
                    },
                },
            },
        )

        resp = await server.handle_request(
            "host",
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "help", "arguments": {}},
            },
        )
        text = resp["result"]["content"][0]["text"]
        assert "In Game" in text
        assert "act()" in text or "act" in text
        assert "simulate" in text

        remove_session("help-test")
        reset_agents()

    @pytest.mark.asyncio
    async def test_help_in_pending_lobby(self):
        """help in pending lobby shows waiting guidance."""
        from mcp.server import MCPServer

        server = MCPServer(sessions=_SimpleSessionStore())
        from games import REGISTRY

        for gid, compiled in REGISTRY.items():
            server.register_game(compiled)

        # Create a lobby (not enough players)
        await server.handle_request(
            "host",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "create_game",
                    "arguments": {
                        "game_type": "auction",
                        "session_id": "help-lobby-test",
                        "player_id": "host",
                    },
                },
            },
        )

        resp = await server.handle_request(
            "host",
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "help", "arguments": {}},
            },
        )
        text = resp["result"]["content"][0]["text"]
        assert "Waiting" in text or "Lobby" in text

        reset_agents()


# ===========================================================================
# Change 5: Vote feedback (was_start) + subject in ACTION REQUIRED
# ===========================================================================


class TestVoteFeedback:
    """format_vote_result distinguishes start vs cast."""

    def test_vote_started_message(self):
        result = {"ok": True}
        text = format_vote_result(result, was_start=True)
        assert "started" in text.lower()

    def test_vote_cast_message(self):
        result = {"ok": True}
        text = format_vote_result(result, was_start=False)
        assert "cast" in text.lower()

    def test_vote_no_start_hint(self):
        result = {"ok": True}
        text = format_vote_result(result)
        assert "successfully" in text.lower() or "cast" in text.lower()

    def test_vote_error_ignores_was_start(self):
        result = {"ok": False, "error": {"message": "bad vote"}}
        text = format_vote_result(result, was_start=True)
        assert "bad vote" in text


# ===========================================================================
# Change 6: Enhanced _detect_trigger (multi-event, deal/vote names)
# ===========================================================================


class TestDetectTriggerEnhanced:
    """Extended _detect_trigger tests for multi-event and named items."""

    def test_multiple_triggers_combined(self):
        """Phase change + new vote produces combined trigger."""
        from mcp.server import _detect_trigger

        class FakeVote:
            vote_id = "bailout"

        class FakeSession:
            class state:
                phase = "voting"
                round = 2
                pending_deals = {}
                pending_votes = {"v1": FakeVote()}
                status = "active"

        result = _detect_trigger(
            FakeSession(), "action", 1, 0, 0, "active", False
        )
        assert "phase_changed" in result
        assert "new_round" in result
        assert "vote_started" in result
        assert "bailout" in result
        # Multiple events joined by semicolons
        assert ";" in result

    def test_deal_resolved_trigger(self):
        from mcp.server import _detect_trigger

        class FakeSession:
            class state:
                phase = "action"
                round = 1
                pending_deals = {}
                pending_votes = {}
                status = "active"

        # prev_deals=1 > current 0 → deal_resolved
        result = _detect_trigger(
            FakeSession(), "action", 1, 1, 0, "active", False
        )
        assert "deal_resolved" in result

    def test_vote_completed_trigger(self):
        from mcp.server import _detect_trigger

        class FakeSession:
            class state:
                phase = "action"
                round = 1
                pending_deals = {}
                pending_votes = {}
                status = "active"

        # prev_votes=1 > current 0 → vote_completed
        result = _detect_trigger(
            FakeSession(), "action", 1, 0, 1, "active", False
        )
        assert "vote_completed" in result


# ===========================================================================
# _with_trigger preserves JSON data blocks
# ===========================================================================


class TestWithTrigger:
    """_with_trigger prepends trigger without losing data blocks."""

    def test_with_trigger_prepends(self):
        from mcp.server import _with_trigger

        status = {"content": [{"type": "text", "text": "Status info"}]}
        result = _with_trigger("phase_changed", status)
        assert "Trigger: phase_changed" in result["content"][0]["text"]
        assert "Status info" in result["content"][0]["text"]

    def test_with_trigger_preserves_json_blocks(self):
        from mcp.server import _with_trigger, _content

        status = _content("Status info", data={"phase": "action"})
        assert len(status["content"]) == 2
        result = _with_trigger("deal_proposed", status)
        # Should still have 2 blocks: text + json
        assert len(result["content"]) == 2
        assert "Trigger: deal_proposed" in result["content"][0]["text"]
        assert "```json" in result["content"][1]["text"]

    def test_with_trigger_on_error_passthrough(self):
        from mcp.server import _with_trigger

        err = {"content": [{"type": "text", "text": "err"}], "isError": True}
        result = _with_trigger("anything", err)
        # Should return error unchanged
        assert result is err


# ===========================================================================
# Initialize instructions include game list
# ===========================================================================


class TestInitializeInstructions:
    """Server initialize includes onboarding instructions."""

    @pytest.mark.asyncio
    async def test_initialize_has_instructions(self):
        from mcp.server import MCPServer

        server = MCPServer(sessions=_SimpleSessionStore())
        from games import REGISTRY

        for gid, compiled in REGISTRY.items():
            server.register_game(compiled)

        resp = await server.handle_request(
            "agent1",
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
        info = resp["result"]["serverInfo"]
        assert "instructions" in info
        assert "auction" in info["instructions"]
        assert "act()" in info["instructions"] or "act" in info["instructions"]
        reset_agents()


# ===========================================================================
# Vote tool description includes "Casts on pending" hint
# ===========================================================================


class TestVoteToolDescription:
    """vote_to_tool description mentions pending vote behavior."""

    def test_vote_tool_desc_mentions_pending(self):
        vote = VoteDef(
            id="bill",
            options=("yes", "no"),
            doc="Vote on bill",
        )
        tool = vote_to_tool("game", "bill", vote)
        assert "pending" in tool.description.lower()
        assert "Casts on pending" in tool.description or "pending vote" in tool.description.lower()


# ===========================================================================
# Schema: help and simulate tools in universal tools
# ===========================================================================


class TestSchemaNewTools:
    """help and simulate appear in universal tools."""

    def test_help_in_universal_tools(self):
        from mcp.schema import _universal_tools

        tools = _universal_tools()
        names = [t.name for t in tools]
        assert "help" in names

    def test_simulate_in_universal_tools(self):
        from mcp.schema import _universal_tools

        tools = _universal_tools()
        names = [t.name for t in tools]
        assert "simulate" in names
        sim_tool = next(t for t in tools if t.name == "simulate")
        assert sim_tool.inputSchema is not None
        assert "action" in sim_tool.inputSchema["properties"]


# ===========================================================================
# Simulate respond (friction fix 1)
# ===========================================================================


class TestSimulateRespond:
    """simulate tool supports respond action for deal preview."""

    @pytest.mark.asyncio
    async def test_simulate_respond_missing_args(self):
        from mcp.server import MCPServer
        from server.sessions import remove_session

        server = MCPServer(sessions=_SimpleSessionStore())
        from games import REGISTRY

        for gid, compiled in REGISTRY.items():
            server.register_game(compiled)

        await server.handle_request(
            "host",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "create_game",
                    "arguments": {
                        "game_type": "auction",
                        "session_id": "sim-resp-test",
                        "player_id": "p1",
                        "players": ["p1", "p2", "p3"],
                    },
                },
            },
        )

        resp = await server.handle_request(
            "host",
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "simulate",
                    "arguments": {"action": "respond"},
                },
            },
        )
        text = resp["result"]["content"][0]["text"]
        assert "instance_id" in text.lower() or "Provide" in text

        remove_session("sim-resp-test")
        reset_agents()

    @pytest.mark.asyncio
    async def test_simulate_respond_no_such_deal(self):
        """Simulate respond on nonexistent instance returns error, not crash."""
        from mcp.server import MCPServer
        from server.sessions import remove_session

        server = MCPServer(sessions=_SimpleSessionStore())
        from games import REGISTRY

        for gid, compiled in REGISTRY.items():
            server.register_game(compiled)

        await server.handle_request(
            "host",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "create_game",
                    "arguments": {
                        "game_type": "auction",
                        "session_id": "sim-resp-test2",
                        "player_id": "p1",
                        "players": ["p1", "p2", "p3"],
                    },
                },
            },
        )

        resp = await server.handle_request(
            "host",
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "simulate",
                    "arguments": {
                        "action": "respond",
                        "args": {"instance_id": "nonexistent", "response": "accept"},
                    },
                },
            },
        )
        text = resp["result"]["content"][0]["text"]
        # Should show SIMULATION prefix and an error about the deal not found
        assert "SIMULATION" in text

        remove_session("sim-resp-test2")
        reset_agents()


# ===========================================================================
# Role guidance fallback (friction fix 2)
# ===========================================================================


class TestRoleGuidanceFallback:
    """role_guidance shows game-level guidance when no roles exist."""

    @pytest.mark.asyncio
    async def test_auction_shows_game_summary(self):
        from mcp.server import MCPServer
        from server.sessions import remove_session

        server = MCPServer(sessions=_SimpleSessionStore())
        from games import REGISTRY

        for gid, compiled in REGISTRY.items():
            server.register_game(compiled)

        await server.handle_request(
            "host",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "create_game",
                    "arguments": {
                        "game_type": "auction",
                        "session_id": "role-test",
                        "player_id": "p1",
                        "players": ["p1", "p2", "p3"],
                    },
                },
            },
        )

        resp = await server.handle_request(
            "host",
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "role_guidance", "arguments": {}},
            },
        )
        text = resp["result"]["content"][0]["text"]
        # Should show "Strategy Guidance" not "unknown"
        assert "unknown" not in text.lower()
        assert "Strategy Guidance" in text
        # Should include game summary from context config
        assert "auction" in text.lower() or "Art Auction" in text
        # Should include scoring explanation
        assert "Score" in text or "gold" in text.lower()

        remove_session("role-test")
        reset_agents()


# ===========================================================================
# Vote tool name consistency (friction fixes 3+4)
# ===========================================================================


class TestVoteToolNameConsistency:
    """Vote tool names use vote_ prefix consistently everywhere."""

    def test_action_required_uses_vote_prefix(self):
        """ACTION REQUIRED shows vote_{id} not just vote_id."""
        from engine.runtime.state import PendingVote
        from mcp.formatters import _format_action_required

        state = GameState(
            phase="action",
            round=1,
            entities={"p1": Entity(id="p1", active=True)},
            pending_votes={
                "v1": PendingVote(
                    instance_id="v1",
                    vote_id="expulsion",
                    eligible={"p1", "p2"},
                    votes={},
                    options=("yes", "no"),
                )
            },
        )
        compiled = _mini_game()
        lines = _format_action_required({}, compiled, "p1", state)
        text = "\n".join(lines)
        assert "vote_expulsion" in text
        # Should NOT have old format "Vote in **expulsion**"
        assert "Vote in **expulsion**" not in text

    def test_available_actions_pending_votes_uses_prefix(self):
        """Pending votes section in available_actions uses vote_{id}."""
        from engine.runtime.state import PendingVote

        state = GameState(
            phase="vote",
            round=1,
            entities={"p1": Entity(id="p1", active=True)},
            pending_votes={
                "v1": PendingVote(
                    instance_id="v1",
                    vote_id="lynch",
                    eligible={"p1"},
                    votes={},
                    options=("guilty", "innocent"),
                )
            },
        )
        text = format_available_actions(state, _mini_game(), "p1")
        assert "vote_lynch" in text
        # Should NOT have old format "**vote** in lynch"
        assert "**vote** in" not in text


# ===========================================================================
# Structured JSON vote prefix (friction fix 5)
# ===========================================================================


class TestAvailableActionsDataVotePrefix:
    """JSON data returns vote names with vote_ prefix."""

    def test_votes_prefixed(self):
        from mcp.server import _available_actions_data

        compiled = _mini_game()
        state = GameState(
            phase="action",
            round=1,
            entities={"p1": Entity(id="p1", active=True)},
        )
        data = _available_actions_data(state, compiled, "p1")
        for v in data["votes"]:
            assert v.startswith("vote_"), f"Vote '{v}' missing vote_ prefix"

    def test_pending_vote_has_action_field(self):
        from mcp.server import _pending_actions_data
        from engine.runtime.state import PendingVote

        state = GameState(
            phase="action",
            round=1,
            entities={"p1": Entity(id="p1", active=True)},
            pending_votes={
                "v1": PendingVote(
                    instance_id="v1",
                    vote_id="expulsion",
                    eligible={"p1"},
                    votes={},
                    options=("yes", "no"),
                )
            },
        )
        items = _pending_actions_data(state, "p1")
        assert len(items) == 1
        assert items[0]["action"] == "vote_expulsion"


# ===========================================================================
# start_game — manual lobby start (host only)
# ===========================================================================


class TestStartGame:
    """start_game tool for manually starting pending lobbies."""

    @pytest.mark.asyncio
    async def test_start_game_not_in_lobby(self):
        """start_game fails when agent has no session."""
        from mcp.server import MCPServer

        server = MCPServer(sessions=_SimpleSessionStore())
        from games import REGISTRY

        for gid, compiled in REGISTRY.items():
            server.register_game(compiled)

        resp = await server.handle_request(
            "agent1",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "start_game", "arguments": {}},
            },
        )
        text = resp["result"]["content"][0]["text"]
        assert "Not in a game" in text or "lobby" in text.lower()
        reset_agents()

    @pytest.mark.asyncio
    async def test_start_game_not_host(self):
        """Only host can start the game."""
        from mcp.server import MCPServer

        server = MCPServer(sessions=_SimpleSessionStore())
        from games import REGISTRY

        for gid, compiled in REGISTRY.items():
            server.register_game(compiled)

        # Host creates lobby (1 player, needs 3)
        await server.handle_request(
            "host",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "create_game",
                    "arguments": {
                        "game_type": "auction",
                        "session_id": "start-host-test",
                        "player_id": "host",
                    },
                },
            },
        )

        # Another player joins
        await server.handle_request(
            "p2",
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "join_game",
                    "arguments": {
                        "session_id": "start-host-test",
                        "player_id": "p2",
                    },
                },
            },
        )

        # Non-host tries to start
        resp = await server.handle_request(
            "p2",
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "start_game", "arguments": {}},
            },
        )
        text = resp["result"]["content"][0]["text"]
        assert "host" in text.lower()
        reset_agents()

    @pytest.mark.asyncio
    async def test_start_game_insufficient_players(self):
        """start_game fails when below minimum player count."""
        from mcp.server import MCPServer

        server = MCPServer(sessions=_SimpleSessionStore())
        from games import REGISTRY

        for gid, compiled in REGISTRY.items():
            server.register_game(compiled)

        # Create lobby with 1 player (auction needs 3)
        await server.handle_request(
            "host",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "create_game",
                    "arguments": {
                        "game_type": "auction",
                        "session_id": "start-min-test",
                        "player_id": "host",
                    },
                },
            },
        )

        # Try to start with only 1 player
        resp = await server.handle_request(
            "host",
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "start_game", "arguments": {}},
            },
        )
        text = resp["result"]["content"][0]["text"]
        assert "Need at least" in text or "players" in text.lower()
        reset_agents()

    @pytest.mark.asyncio
    async def test_start_game_success(self):
        """Host can start game with enough players."""
        from mcp.server import MCPServer
        from server.sessions import remove_session

        server = MCPServer(sessions=_SimpleSessionStore())
        from games import REGISTRY

        for gid, compiled in REGISTRY.items():
            server.register_game(compiled)

        # Create lobby
        await server.handle_request(
            "host",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "create_game",
                    "arguments": {
                        "game_type": "auction",
                        "session_id": "start-ok-test",
                        "player_id": "host",
                    },
                },
            },
        )

        # Add enough players
        for pid in ["p2", "p3"]:
            await server.handle_request(
                pid,
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "join_game",
                        "arguments": {
                            "session_id": "start-ok-test",
                            "player_id": pid,
                        },
                    },
                },
            )

        # At 3 players (auction min), auto-start fires.
        # Verify the game is running via get_status
        resp = await server.handle_request(
            "host",
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "get_status", "arguments": {}},
            },
        )
        text = resp["result"]["content"][0]["text"]
        assert "Art Auction" in text or "Phase" in text
        assert resp["result"].get("isError") is not True

        remove_session("start-ok-test")
        reset_agents()


# ===========================================================================
# activate_game — token-based game join
# ===========================================================================


class TestActivateGame:
    """activate_game joins a game via HMAC invite token."""

    @pytest.mark.asyncio
    async def test_activate_invalid_token(self):
        from mcp.server import MCPServer

        server = MCPServer(sessions=_SimpleSessionStore())
        from games import REGISTRY

        for gid, compiled in REGISTRY.items():
            server.register_game(compiled)

        resp = await server.handle_request(
            "agent1",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "activate_game",
                    "arguments": {"token": "garbage-token"},
                },
            },
        )
        text = resp["result"]["content"][0]["text"]
        assert "Invalid" in text or "expired" in text.lower()
        assert resp["result"].get("isError")
        reset_agents()

    @pytest.mark.asyncio
    async def test_activate_empty_token(self):
        from mcp.server import MCPServer

        server = MCPServer(sessions=_SimpleSessionStore())
        from games import REGISTRY

        for gid, compiled in REGISTRY.items():
            server.register_game(compiled)

        resp = await server.handle_request(
            "agent1",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "activate_game",
                    "arguments": {"token": ""},
                },
            },
        )
        text = resp["result"]["content"][0]["text"]
        assert "Invalid" in text or "expired" in text.lower()
        reset_agents()

    @pytest.mark.asyncio
    async def test_activate_valid_token(self):
        """Valid token transitions agent to in_game state."""
        from mcp.server import MCPServer
        from mcp.tokens import create_token
        from server.sessions import remove_session

        server = MCPServer(sessions=_SimpleSessionStore())
        from games import REGISTRY

        for gid, compiled in REGISTRY.items():
            server.register_game(compiled)

        # First create a real game session so the session exists
        await server.handle_request(
            "creator",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "create_game",
                    "arguments": {
                        "game_type": "auction",
                        "session_id": "token-test",
                        "player_id": "creator",
                        "players": ["creator", "p2", "p3"],
                    },
                },
            },
        )

        # Create a valid token for a different agent
        token = create_token(
            agent_id="joiner",
            session_id="token-test",
            player_id="p2",
            game_type="auction",
        )

        # Activate with the token
        resp = await server.handle_request(
            "joiner",
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "activate_game",
                    "arguments": {"token": token},
                },
            },
        )
        text = resp["result"]["content"][0]["text"]
        assert "token-test" in text
        assert "p2" in text
        assert not resp["result"].get("isError")

        # Verify agent is now in_game
        resp = await server.handle_request(
            "joiner",
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "my_status", "arguments": {}},
            },
        )
        text = resp["result"]["content"][0]["text"]
        assert "in_game" in text

        remove_session("token-test")
        reset_agents()

    @pytest.mark.asyncio
    async def test_activate_wrong_agent(self):
        """Token for agent A rejected when used by agent B."""
        from mcp.server import MCPServer
        from mcp.tokens import create_token

        server = MCPServer(sessions=_SimpleSessionStore())
        from games import REGISTRY

        for gid, compiled in REGISTRY.items():
            server.register_game(compiled)

        token = create_token(
            agent_id="alice",
            session_id="any-session",
            player_id="p1",
            game_type="auction",
        )

        # Bob tries to use Alice's token
        resp = await server.handle_request(
            "bob",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "activate_game",
                    "arguments": {"token": token},
                },
            },
        )
        text = resp["result"]["content"][0]["text"]
        assert "Invalid" in text or "expired" in text.lower()
        reset_agents()

    @pytest.mark.asyncio
    async def test_activate_session_not_found(self):
        """Valid token but session doesn't exist."""
        from mcp.server import MCPServer
        from mcp.tokens import create_token

        server = MCPServer(sessions=_SimpleSessionStore())
        from games import REGISTRY

        for gid, compiled in REGISTRY.items():
            server.register_game(compiled)

        token = create_token(
            agent_id="agent1",
            session_id="nonexistent-session",
            player_id="p1",
            game_type="auction",
        )

        resp = await server.handle_request(
            "agent1",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "activate_game",
                    "arguments": {"token": token},
                },
            },
        )
        text = resp["result"]["content"][0]["text"]
        assert "not found" in text.lower()
        reset_agents()


# ===========================================================================
# Information Design: Context-aware errors, annotations, enriched responses
# ===========================================================================


class TestUnknownToolContextual:
    """Unknown tool errors should explain why and suggest next steps."""

    @pytest.mark.asyncio
    async def test_in_game_tool_from_lobby(self):
        """Calling an in-game tool from lobby gets not_in_game error with suggestion."""
        from mcp.server import MCPServer

        server = MCPServer(sessions=_SimpleSessionStore())
        server.register_game(_mini_game())
        resp = await server.handle_request(
            "agent1",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "get_status", "arguments": {}},
            },
        )
        text = resp["result"]["content"][0]["text"]
        # Should have error code and recovery suggestion
        assert "not_in_game" in text
        assert "create_game" in text or "join_game" in text
        reset_agents()

    @pytest.mark.asyncio
    async def test_truly_unknown_tool(self):
        """Truly unknown tool gets a helpful error."""
        from mcp.server import MCPServer

        server = MCPServer(sessions=_SimpleSessionStore())
        server.register_game(_mini_game())
        resp = await server.handle_request(
            "a1",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "foobar_nonexistent", "arguments": {}},
            },
        )
        text = resp["result"]["content"][0]["text"]
        assert "unknown_tool" in text
        assert "help" in text.lower()
        reset_agents()

    @pytest.mark.asyncio
    async def test_unknown_tool_similar_to_in_game(self):
        """Unknown tool name that's in _IN_GAME_TOOLS set gets state hint."""
        from mcp.server import MCPServer

        server = MCPServer(sessions=_SimpleSessionStore())
        server.register_game(_mini_game())
        # "deal_mechanics" is in helper_handlers.HANDLERS which checks in-game
        # Calling from lobby when handler has no state check returns its own error
        # Test the _unknown_tool_error path with a fabricated tool name
        # by calling a tool that doesn't exist in any handler
        resp = await server.handle_request(
            "a1",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "xyz_not_a_tool", "arguments": {}},
            },
        )
        text = resp["result"]["content"][0]["text"]
        assert "unknown_tool" in text
        assert "help" in text.lower()
        reset_agents()


class TestErrorCodesStandardized:
    """All common errors should have codes."""

    @pytest.mark.asyncio
    async def test_not_in_game_has_code(self):
        from mcp.server import MCPServer

        server = MCPServer(sessions=_SimpleSessionStore())
        server.register_game(_mini_game())
        resp = await server.handle_request(
            "a1",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "advance_phase", "arguments": {}},
            },
        )
        text = resp["result"]["content"][0]["text"]
        assert "[not_in_game]" in text
        reset_agents()

    @pytest.mark.asyncio
    async def test_unknown_game_has_code(self):
        from mcp.server import MCPServer

        server = MCPServer(sessions=_SimpleSessionStore())
        server.register_game(_mini_game())
        resp = await server.handle_request(
            "a1",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "create_game",
                    "arguments": {"game_type": "nonexistent"},
                },
            },
        )
        text = resp["result"]["content"][0]["text"]
        assert "[unknown_game]" in text
        reset_agents()


class TestListGamesEnriched:
    """list_games should show descriptions and quick-start hints."""

    @pytest.mark.asyncio
    async def test_list_games_shows_create_hint(self):
        from mcp.server import MCPServer

        server = MCPServer(sessions=_SimpleSessionStore())
        from games import REGISTRY

        for gid, compiled in REGISTRY.items():
            server.register_game(compiled)
        resp = await server.handle_request(
            "a1",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "list_games", "arguments": {}},
            },
        )
        text = resp["result"]["content"][0]["text"]
        assert "create_game" in text
        assert "game_rules" in text
        # Should have structured sections per game
        assert "### " in text
        reset_agents()


class TestToolAnnotations:
    """Tools should have readOnlyHint annotations."""

    @pytest.mark.asyncio
    async def test_lobby_tools_have_annotations(self):
        from mcp.server import MCPServer

        server = MCPServer(sessions=_SimpleSessionStore())
        server.register_game(_mini_game())
        resp = await server.handle_request(
            "a1",
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )
        tools = resp["result"]["tools"]
        by_name = {t["name"]: t for t in tools}
        # Read-only tools should have annotation
        assert by_name["list_games"].get("annotations", {}).get("readOnlyHint") is True
        assert by_name["list_sessions"].get("annotations", {}).get("readOnlyHint") is True
        # create_game should NOT have readOnlyHint=True (or no annotations)
        cg_ann = by_name["create_game"].get("annotations", {})
        assert cg_ann.get("readOnlyHint") is not True
        reset_agents()

    @pytest.mark.asyncio
    async def test_in_game_tools_have_annotations(self):
        from mcp.server import MCPServer
        from server.sessions import remove_session

        server = MCPServer(sessions=_SimpleSessionStore())
        server.register_game(_mini_game())
        await server.handle_request(
            "a1",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "create_game",
                    "arguments": {
                        "game_type": "test",
                        "players": ["p1", "p2"],
                        "player_id": "p1",
                    },
                },
            },
        )
        resp = await server.handle_request(
            "a1",
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        tools = resp["result"]["tools"]
        by_name = {t["name"]: t for t in tools}
        # get_status should be read-only
        assert by_name["get_status"].get("annotations", {}).get("readOnlyHint") is True
        # help should be read-only
        assert by_name["help"].get("annotations", {}).get("readOnlyHint") is True
        # advance_phase should NOT be read-only
        assert by_name["advance_phase"].get("annotations", {}).get("readOnlyHint") is False
        sid = register_agent("a1").session_id
        if sid:
            remove_session(sid)
        reset_agents()


class TestMyStatusNextSteps:
    """my_status should include actionable next steps."""

    @pytest.mark.asyncio
    async def test_lobby_status_has_next(self):
        from mcp.server import MCPServer

        server = MCPServer(sessions=_SimpleSessionStore())
        server.register_game(_mini_game())
        resp = await server.handle_request(
            "a1",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "my_status", "arguments": {}},
            },
        )
        text = resp["result"]["content"][0]["text"]
        assert "list_games" in text or "create_game" in text
        reset_agents()

    @pytest.mark.asyncio
    async def test_in_game_status_has_next(self):
        from mcp.server import MCPServer
        from server.sessions import remove_session

        server = MCPServer(sessions=_SimpleSessionStore())
        server.register_game(_mini_game())
        await server.handle_request(
            "a1",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "create_game",
                    "arguments": {
                        "game_type": "test",
                        "players": ["p1", "p2"],
                        "player_id": "p1",
                    },
                },
            },
        )
        resp = await server.handle_request(
            "a1",
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "my_status", "arguments": {}},
            },
        )
        text = resp["result"]["content"][0]["text"]
        assert "act()" in text or "wait_for_turn" in text
        sid = register_agent("a1").session_id
        if sid:
            remove_session(sid)
        reset_agents()
