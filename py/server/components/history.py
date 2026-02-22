"""History timeline component."""

from nicegui import ui

from engine.runtime.state import GameState

_ICONS = {
    "deal_executed": "handshake",
    "deal_proposed": "pending",
    "deal_response": "reply",
    "vote_started": "how_to_vote",
    "vote_completed": "check_circle",
    "game_ended": "emoji_events",
    "message_sent": "chat",
    "phase_advanced": "skip_next",
}

_COLORS = {
    "deal_executed": "positive",
    "deal_proposed": "accent",
    "deal_response": "secondary",
    "vote_started": "primary",
    "vote_completed": "primary",
    "game_ended": "negative",
    "message_sent": "grey-6",
    "phase_advanced": "secondary",
}


_HISTORY_LIMIT = 50


def render_history(state: GameState):
    """Render event history as timeline entries."""
    total = len(state.history)
    entries = state.history[-_HISTORY_LIMIT:]
    if not entries:
        with ui.column().classes("items-center gap-2 py-8 w-full"):
            ui.icon("history").classes("text-3xl text-gray-300")
            ui.label("No events yet").classes("text-sm text-gray-400")
        return

    with ui.timeline(side="left", layout="dense"):
        if total > _HISTORY_LIMIT:
            ui.timeline_entry(
                body=f"Showing latest {_HISTORY_LIMIT} of {total} events",
                icon="info",
                color="grey-5",
            )
        for entry in reversed(entries):
            icon = _ICONS.get(entry.type, "circle")
            color = _COLORS.get(entry.type, "grey-7")
            detail = ", ".join(f"{k}={v}" for k, v in entry.data.items())
            ui.timeline_entry(
                body=detail,
                title=entry.type.replace("_", " ").title(),
                icon=icon,
                color=color,
            )
