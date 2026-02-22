"""Replay transport controls and state diff rendering."""

from nicegui import ui


def render_replay_transport(ctrl, on_go):
    """Render step transport bar with slider."""
    with (
        ui.row()
        .classes("w-full items-center gap-2 px-3 py-2 rounded-xl")
        .style("background: #F8F8FA; border: 1px solid #E5E4EA")
    ):
        ui.button(icon="skip_previous", on_click=lambda: on_go(0)).props(
            "round flat dense color=dark"
        )
        ui.button(icon="arrow_back", on_click=lambda: on_go(ctrl.step - 1)).props(
            "round flat dense color=dark"
        )
        ui.button(icon="arrow_forward", on_click=lambda: on_go(ctrl.step + 1)).props(
            "round flat dense color=dark"
        )
        ui.button(icon="skip_next", on_click=lambda: on_go(ctrl.total_steps)).props(
            "round flat dense color=dark"
        )
        ui.label(f"Step {ctrl.step} / {ctrl.total_steps}").classes(
            "text-sm text-gray-500 mx-2 font-mono"
        )
        ui.slider(
            min=0,
            max=ctrl.total_steps,
            value=ctrl.step,
            step=1,
            on_change=lambda e: on_go(int(e.value)),
        ).classes("flex-grow")


# Color map for change types
_CHANGE_COLORS = {
    "resource": ("swap_horiz", None),  # dynamic: green/red based on delta
    "attr": ("tune", "#D97706"),
    "active": ("person_off", "#DC2626"),
    "phase": ("skip_next", "#0891B2"),
    "round": ("replay", "#0891B2"),
    "status": ("flag", "#7C3AED"),
    "group_join": ("group_add", "#16A34A"),
    "group_leave": ("group_remove", "#DC2626"),
}


def render_change(ch: dict):
    """Render a single state change entry."""
    ctype = ch["type"]

    if ctype == "resource":
        delta = ch["delta"]
        sign = "+" if delta > 0 else ""
        color = "#16A34A" if delta > 0 else "#DC2626"
        icon = "trending_up" if delta > 0 else "trending_down"
        with ui.row().classes("items-center gap-1.5"):
            ui.icon(icon).classes("text-sm").style(f"color: {color}")
            ui.label(
                f"{ch['entity']}.{ch['resource']}: "
                f"{ch['from']} \u2192 {ch['to']} ({sign}{delta})"
            ).classes("text-xs").style(f"color: {color}")
    elif ctype == "attr":
        with ui.row().classes("items-center gap-1.5"):
            ui.icon("tune").classes("text-sm text-amber-600")
            ui.label(
                f"{ch['entity']}.{ch['attr']}: {ch['from']} \u2192 {ch['to']}"
            ).classes("text-xs text-amber-600")
    elif ctype == "active":
        status = "eliminated" if not ch["to"] else "reactivated"
        with ui.row().classes("items-center gap-1.5"):
            ui.icon("person_off").classes("text-sm text-red-600")
            ui.label(f"{ch['entity']} {status}").classes(
                "text-xs text-red-600 font-semibold"
            )
    elif ctype == "phase":
        with ui.row().classes("items-center gap-1.5"):
            ui.icon("skip_next").classes("text-sm text-cyan-600")
            ui.label(f"Phase: {ch['from']} \u2192 {ch['to']}").classes(
                "text-xs text-cyan-600"
            )
    elif ctype == "round":
        with ui.row().classes("items-center gap-1.5"):
            ui.icon("replay").classes("text-sm text-cyan-600")
            ui.label(f"Round: {ch['from']} \u2192 {ch['to']}").classes(
                "text-xs text-cyan-600"
            )
    elif ctype == "status":
        with ui.row().classes("items-center gap-1.5"):
            ui.icon("flag").classes("text-sm text-violet-600")
            ui.label(f"Status: {ch['from']} \u2192 {ch['to']}").classes(
                "text-xs text-violet-600"
            )
    elif ctype in ("group_join", "group_leave"):
        action = "joined" if ctype == "group_join" else "left"
        icon = "group_add" if ctype == "group_join" else "group_remove"
        color = "text-green-600" if ctype == "group_join" else "text-red-600"
        with ui.row().classes("items-center gap-1.5"):
            ui.icon(icon).classes(f"text-sm {color}")
            ui.label(f"{ch['entity']} {action} {', '.join(ch['groups'])}").classes(
                f"text-xs {color}"
            )
    else:
        ui.label(str(ch)).classes("text-xs text-gray-400")
