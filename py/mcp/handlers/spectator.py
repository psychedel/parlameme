"""Spectating tools — observe games without participating.

Spectators get a public-only view: PUBLIC resources/attrs, public channel
messages, no reveals, no private data. Critical for AI training — agents
learn by watching.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from engine.runtime.state import SPECTATOR_ID, view_for
from mcp.schema import Tool

if TYPE_CHECKING:
    from mcp.agents import AgentState

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS: list[Tool] = [
    Tool(
        name="spectate_game",
        description="Start spectating a game (read-only observer mode)",
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session ID of game to spectate",
                },
            },
            "required": ["session_id"],
        },
        _meta={"type": "action"},
    ),
    Tool(
        name="leave_spectate",
        description="Stop spectating, return to lobby",
        _meta={"type": "action"},
    ),
    Tool(
        name="spectate_status",
        description="Brief status of the game you are spectating",
        _meta={"type": "query"},
    ),
    Tool(
        name="spectate_view",
        description="Full spectator view of game state (public info only)",
        inputSchema={
            "type": "object",
            "properties": {
                "include_history": {
                    "type": "boolean",
                    "default": False,
                    "description": "Include recent game events",
                },
                "limit": {
                    "type": "integer",
                    "default": 20,
                    "description": "Max history events if included",
                },
            },
        },
        _meta={"type": "query"},
    ),
]

# Lobby-only tool (transitions to spectating)
LOBBY_TOOL = TOOLS[0]  # spectate_game

# Spectating-only tools
SPECTATING_TOOLS = TOOLS[1:]  # leave, status, view

# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def handle_spectate_game(server: Any, agent: AgentState, args: dict) -> dict:
    if agent.state != "lobby":
        return _error(
            f"Cannot spectate from '{agent.state}' state. Return to lobby first."
        )

    session_id = args.get("session_id", "")
    if not session_id:
        return _error("Provide a session_id.")

    session = server._get_session(session_id)
    if not session:
        return _error(f"Session not found: {session_id}")

    agent.to_spectating(session_id)
    state = session.state
    alive = sum(1 for e in state.entities.values() if e.active)
    return _content(
        f"Now spectating: {session_id}\n"
        f"Game: {session.compiled.name}\n"
        f"Phase: {state.phase} | Round: {state.round} | "
        f"Players: {len(state.entities)} ({alive} active)"
    )


async def handle_leave_spectate(server: Any, agent: AgentState, args: dict) -> dict:
    if agent.state != "spectating":
        return _error("Not spectating.")
    agent.to_lobby()
    return _content("Stopped spectating. Back to lobby.")


async def handle_spectate_status(server: Any, agent: AgentState, args: dict) -> dict:
    if agent.state != "spectating":
        return _error("Not spectating.")

    session = server._get_session(agent.session_id)
    if not session:
        agent.to_lobby()
        return _error("Session no longer exists.")

    state = session.state
    alive = sum(1 for e in state.entities.values() if e.active)
    return _content(
        f"Game: {session.compiled.name}\n"
        f"Phase: {state.phase} | Round: {state.round}\n"
        f"Status: {state.status}\n"
        f"Players: {len(state.entities)} ({alive} active)"
    )


async def handle_spectate_view(server: Any, agent: AgentState, args: dict) -> dict:
    if agent.state != "spectating":
        return _error("Not spectating.")

    session = server._get_session(agent.session_id)
    if not session:
        agent.to_lobby()
        return _error("Session no longer exists.")

    view = view_for(session.state, SPECTATOR_ID, session.compiled)
    include_history = args.get("include_history", False)
    limit = args.get("limit", 20)

    lines = [f"## Spectating: {session.compiled.name}"]
    lines.append(
        f"Phase: {view['phase']} | Round: {view['round']} | Status: {view['status']}"
    )

    # Entities
    lines.append("\n### Players")
    for eid, entity in view["entities"].items():
        status = "active" if entity["active"] else "eliminated"
        parts = [f"**{eid}** ({status})"]
        if entity["resources"]:
            res = ", ".join(f"{k}={v}" for k, v in entity["resources"].items())
            parts.append(res)
        if entity["attrs"]:
            attrs = ", ".join(f"{k}={v}" for k, v in entity["attrs"].items())
            parts.append(attrs)
        lines.append("- " + " | ".join(parts))

    # Game vars
    if view.get("vars"):
        lines.append("\n### Game State")
        for k, v in view["vars"].items():
            lines.append(f"  {k}: {v}")

    # Messages (spectator only sees public channels)
    if view.get("messages"):
        lines.append("\n### Messages")
        msgs = view["messages"][-limit:]
        for m in msgs:
            lines.append(
                f"- [{m['channel']}] **{m['sender']}** (R{m['round']}): {m['content']}"
            )

    # History
    if include_history:
        entries = session.state.history[-limit:]
        if entries:
            lines.append("\n### Recent Events")
            for entry in reversed(entries):
                data = ", ".join(f"{k}={v}" for k, v in entry.data.items())
                lines.append(f"- [{entry.type}] {data}")

    return _content("\n".join(lines))


# ---------------------------------------------------------------------------
# Routing table
# ---------------------------------------------------------------------------

HANDLERS: dict[str, Any] = {
    "spectate_game": handle_spectate_game,
    "leave_spectate": handle_leave_spectate,
    "spectate_status": handle_spectate_status,
    "spectate_view": handle_spectate_view,
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _content(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


def _error(message: str) -> dict:
    return {"content": [{"type": "text", "text": f"Error: {message}"}], "isError": True}
