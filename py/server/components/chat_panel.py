"""Chat panel — multi-channel messaging with type indicators and write filtering."""

from __future__ import annotations

from nicegui import ui

from engine.runtime.state import can_read_channel, can_write_channel
from server.sessions import GameSession

_CHANNEL_ICONS = {
    "public": "chat",
    "broadcast": "campaign",
    "group": "group",
    "private": "lock",
}


def render_chat_panel(
    view: dict,
    session: GameSession,
    player_id: str,
):
    """Render messages grouped by channel with send input."""
    messages = view.get("messages", [])
    compiled = session.compiled
    state = session.state

    if not messages and not compiled.channels:
        with ui.column().classes("items-center gap-2 py-8 w-full"):
            ui.icon("chat_bubble_outline").classes("text-3xl text-gray-300")
            ui.label("No channels in this game").classes("text-sm text-gray-400")
        return

    # Determine readable channels for this player
    readable_channels = [
        cid
        for cid in compiled.channels
        if can_read_channel(state, player_id, cid, compiled)
    ]
    # Also discover ad-hoc private channels from messages
    for msg in messages:
        if msg["channel"] not in readable_channels:
            readable_channels.append(msg["channel"])

    if not readable_channels:
        readable_channels = ["all"]

    # Single channel — no tabs needed
    if len(readable_channels) <= 1:
        ch_id = readable_channels[0] if readable_channels else "all"
        ch_msgs = (
            [m for m in messages if m["channel"] == ch_id]
            if ch_id != "all"
            else messages
        )
        _render_message_area(ch_msgs, player_id)
        if ch_id != "all" and can_write_channel(state, player_id, ch_id, compiled):
            _render_send_input(ch_id, session, player_id)
        elif ch_id == "all":
            # Fallback: find first writable channel
            for cid in readable_channels:
                if cid != "all" and can_write_channel(state, player_id, cid, compiled):
                    _render_send_input(cid, session, player_id)
                    break
        return

    # Multi-channel — show tabs
    with ui.tabs().classes("w-full").props("no-caps dense inline-label") as tabs:
        for cid in readable_channels:
            cdef = compiled.channels.get(cid)
            ch_type = cdef.type if cdef else "private"
            icon = _CHANNEL_ICONS.get(ch_type, "chat")
            tab = ui.tab(cid, icon=icon)
            # Channel hint tooltip
            ctx = compiled.context
            if ctx.channel_hints:
                ch_hint = ctx.channel_hints.get(cid)
                if ch_hint and ch_hint.when_to_use:
                    tab.tooltip(ch_hint.when_to_use)

    with ui.tab_panels(tabs, value=readable_channels[0]).classes("w-full"):
        for cid in readable_channels:
            with ui.tab_panel(cid):
                ch_msgs = [m for m in messages if m["channel"] == cid]
                _render_message_area(ch_msgs, player_id)
                if can_write_channel(state, player_id, cid, compiled):
                    _render_send_input(cid, session, player_id)
                else:
                    ui.label("Read only").classes("text-xs text-gray-300 mt-1 italic")


def _render_message_area(messages: list, player_id: str):
    """Scrollable message list."""
    with (
        ui.scroll_area()
        .classes("w-full h-56 rounded-xl p-3")
        .style("background: #F8F8FA; border: 1px solid #E5E4EA")
    ):
        if not messages:
            with ui.column().classes("items-center gap-2 py-8 w-full"):
                ui.icon("chat_bubble_outline").classes("text-2xl text-gray-300")
                ui.label("No messages yet").classes("text-sm text-gray-400")
        for msg in messages:
            sent = msg["sender"] == player_id
            ui.chat_message(
                text=msg["content"],
                name=msg["sender"],
                sent=sent,
                stamp=f"R{msg['round']} {msg['phase']}",
            )


def _render_send_input(channel_id: str, session: GameSession, player_id: str):
    """Message input for a specific channel."""
    with ui.row().classes("w-full gap-2 mt-2"):
        msg_input = (
            ui.input(placeholder=f"Message ({channel_id})")
            .props("outlined dense")
            .classes("flex-grow")
        )

        async def send():
            content = msg_input.value
            if not content:
                return
            result = await session.send_message(channel_id, player_id, content)
            if result["ok"]:
                msg_input.value = ""
            else:
                ui.notify(
                    result["error"].get("message", "Send failed"),
                    type="negative",
                )

        msg_input.on("keydown.enter", send)
        ui.button(icon="send", on_click=send).props(
            "unelevated round dense color=primary"
        )
