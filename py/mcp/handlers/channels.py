"""Channel communication tools — list, read, read-all.

Agents can send messages via dynamic ``{game}/send_{channel}`` tools,
but need dedicated query tools to discover channels and read history.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from engine.runtime.state import can_read_channel, can_write_channel
from mcp.schema import Tool

if TYPE_CHECKING:
    from mcp.agents import AgentState

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS: list[Tool] = [
    Tool(
        name="list_channels",
        description="List communication channels available to you with permissions",
        _meta={"type": "query"},
    ),
    Tool(
        name="get_messages",
        description="Read messages from a specific channel",
        inputSchema={
            "type": "object",
            "properties": {
                "channel": {"type": "string", "description": "Channel ID"},
                "limit": {
                    "type": "integer",
                    "default": 20,
                    "description": "Max messages to return",
                },
            },
            "required": ["channel"],
        },
        _meta={"type": "query"},
    ),
    Tool(
        name="get_all_messages",
        description="Read all visible messages across all channels",
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "default": 50,
                    "description": "Max messages to return",
                },
            },
        },
        _meta={"type": "query"},
    ),
]

# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def handle_list_channels(server: Any, agent: AgentState, args: dict) -> dict:
    session = server._get_session(agent.session_id)
    if not session:
        return _error("Session not found.")

    state = session.state
    compiled = session.compiled
    player_id = agent.player_id

    lines = ["## Channels"]
    for ch_id, cdef in compiled.channels.items():
        can_read = can_read_channel(state, player_id, ch_id, compiled)
        if not can_read:
            continue  # Hide channels the player cannot read
        can_write = can_write_channel(state, player_id, ch_id, compiled)

        perm_str = "read, write" if can_write else "read"
        desc = cdef.description or cdef.type
        # Append channel hints from context annotations
        ctx = compiled.context
        if ctx.channel_hints:
            hint = ctx.channel_hints.get(ch_id)
            if hint:
                if hint.strategy:
                    desc += f" — {hint.strategy}"
                if hint.risk:
                    desc += f" (Risk: {hint.risk})"
        lines.append(f"- **{ch_id}** ({cdef.type}): {desc} [{perm_str}]")

    if len(lines) == 1:
        return _content("No channels in this game.")
    return _content("\n".join(lines))


async def handle_get_messages(server: Any, agent: AgentState, args: dict) -> dict:
    session = server._get_session(agent.session_id)
    if not session:
        return _error("Session not found.")

    channel = args.get("channel", "")
    limit = args.get("limit", 20)

    state = session.state
    compiled = session.compiled
    player_id = agent.player_id

    if not can_read_channel(state, player_id, channel, compiled):
        return _error(f"You cannot read channel '{channel}'.")

    messages = [m for m in state.messages if m.channel == channel]
    messages = messages[-limit:]
    return _content(_format_messages(messages, channel))


async def handle_get_all_messages(server: Any, agent: AgentState, args: dict) -> dict:
    session = server._get_session(agent.session_id)
    if not session:
        return _error("Session not found.")

    limit = args.get("limit", 50)
    state = session.state
    compiled = session.compiled
    player_id = agent.player_id

    visible = [
        m
        for m in state.messages
        if can_read_channel(state, player_id, m.channel, compiled)
    ]
    visible = visible[-limit:]
    return _content(_format_messages(visible))


# ---------------------------------------------------------------------------
# Routing table
# ---------------------------------------------------------------------------

HANDLERS: dict[str, Any] = {
    "list_channels": handle_list_channels,
    "get_messages": handle_get_messages,
    "get_all_messages": handle_get_all_messages,
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_messages(messages: list, channel: str | None = None) -> str:
    if not messages:
        label = f" in {channel}" if channel else ""
        return f"No messages{label}."

    header = f"## Messages" + (f" — {channel}" if channel else "")
    lines = [header]
    for m in messages:
        prefix = f"[{m.channel}] " if channel is None else ""
        lines.append(f"- {prefix}**{m.sender}** (R{m.round}/{m.phase}): {m.content}")
    return "\n".join(lines)


def _content(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


def _error(message: str) -> dict:
    return {"content": [{"type": "text", "text": f"Error: {message}"}], "isError": True}
