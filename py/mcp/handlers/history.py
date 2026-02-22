"""History & replay MCP tools — game history, replays, browsing.

Wraps existing archive subsystem for MCP consumption.
Available in ALL agent states (global tools).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mcp.schema import Tool
from server.sessions import list_archives

if TYPE_CHECKING:
    from mcp.agents import AgentState

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS: list[Tool] = [
    Tool(
        name="my_game_history",
        description="Your recent completed games",
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "default": 10,
                    "description": "Max games to return",
                },
            },
        },
        _meta={"type": "global"},
    ),
    Tool(
        name="get_game_replay",
        description="Full archive of a completed game (decisions for analysis)",
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session ID of completed game",
                },
            },
            "required": ["session_id"],
        },
        _meta={"type": "global"},
    ),
    Tool(
        name="list_public_replays",
        description="Browse completed games for study",
        inputSchema={
            "type": "object",
            "properties": {
                "game_type": {
                    "type": "string",
                    "description": "Filter by game type",
                },
                "limit": {
                    "type": "integer",
                    "default": 10,
                    "description": "Max replays to return",
                },
            },
        },
        _meta={"type": "global"},
    ),
]

# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def handle_my_game_history(server: Any, agent: AgentState, args: dict) -> dict:
    limit = args.get("limit", 10)
    all_archives = list_archives()

    my_games = [a for a in all_archives if agent.agent_id in a.get("players", [])]
    my_games.sort(key=lambda a: a.get("timestamp", 0), reverse=True)
    my_games = my_games[:limit]

    if not my_games:
        return _content("No completed games in your history.")

    lines = ["## Your Game History"]
    for a in my_games:
        winner = a.get("metadata", {}).get("winner", "?")
        result = (
            "WIN" if winner == agent.agent_id else "LOSS" if winner != "?" else "DRAW"
        )
        players = ", ".join(a["players"])
        lines.append(
            f"- **{a['session_id']}** ({a['game_id']}) — {result} | "
            f"Players: {players} | Decisions: {a['decisions']}"
        )
    return _content("\n".join(lines))


async def handle_get_game_replay(server: Any, agent: AgentState, args: dict) -> dict:
    session_id = args.get("session_id", "")
    if not session_id:
        return _error("Provide a session_id.")

    all_archives = list_archives()
    match = next((a for a in all_archives if a["session_id"] == session_id), None)
    if not match:
        return _error(f"No archive found for session '{session_id}'.")

    # Load full archive for decision details
    try:
        full = json.loads(Path(match["path"]).read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        return _error(f"Could not load archive file: {exc}")

    decisions = full.get("decisions", [])
    players = full.get("players", [])
    winner = full.get("metadata", {}).get("winner", "?")

    lines = [
        f"## Game Replay: {session_id}",
        f"Game: {full.get('game_id', '?')} | Players: {', '.join(players)}",
        f"Winner: {winner} | Total decisions: {len(decisions)}",
        "",
        "### Decisions",
    ]
    for i, d in enumerate(decisions):
        dtype = d.get("type", "?")
        actor = (
            d.get("proposer")
            or d.get("actor")
            or d.get("voter")
            or d.get("sender")
            or ""
        )
        detail = _format_decision(d)
        lines.append(f"{i + 1}. [{dtype}] {actor}: {detail}")

    return _content("\n".join(lines))


async def handle_list_public_replays(
    server: Any, agent: AgentState, args: dict
) -> dict:
    limit = args.get("limit", 10)
    game_type = args.get("game_type")

    all_archives = list_archives()
    if game_type:
        all_archives = [a for a in all_archives if a.get("game_id") == game_type]
    all_archives.sort(key=lambda a: a.get("timestamp", 0), reverse=True)
    all_archives = all_archives[:limit]

    if not all_archives:
        label = f" for {game_type}" if game_type else ""
        return _content(f"No completed games{label}.")

    lines = ["## Public Replays"]
    for a in all_archives:
        players = ", ".join(a["players"])
        winner = a.get("metadata", {}).get("winner", "?")
        lines.append(
            f"- **{a['session_id']}** ({a['game_id']}) — Winner: {winner} | "
            f"Players: {players} | Decisions: {a['decisions']}"
        )
    return _content("\n".join(lines))


# ---------------------------------------------------------------------------
# Routing table
# ---------------------------------------------------------------------------

HANDLERS: dict[str, Any] = {
    "my_game_history": handle_my_game_history,
    "get_game_replay": handle_get_game_replay,
    "list_public_replays": handle_list_public_replays,
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_decision(d: dict) -> str:
    dtype = d.get("type", "")
    match dtype:
        case "deal":
            deal_name = d.get("deal", d.get("deal_id", "?"))
            target = d.get("target") or d.get("responder")
            params = d.get("params")
            parts = [deal_name]
            if target:
                parts.append(f"→ {target}")
            if params:
                parts.append(str(params))
            return " ".join(parts)
        case "respond":
            return f"responded {d.get('response', '?')} to {d.get('instance_id', '?')}"
        case "vote":
            return f"voted {d.get('option', '?')} in {d.get('vote_id', '?')}"
        case "message":
            return f"[{d.get('channel', '?')}] {d.get('content', '')[:50]}"
        case "advance_phase":
            return "phase advanced"
        case _:
            parts = [f"{k}={v}" for k, v in d.items() if k != "type"]
            return ", ".join(parts) if parts else dtype


def _content(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


def _error(message: str) -> dict:
    return {"content": [{"type": "text", "text": f"Error: {message}"}], "isError": True}
