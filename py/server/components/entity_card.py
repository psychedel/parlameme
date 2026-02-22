"""Entity card component — shows player resources, attrs, groups.

Enriched with resource bounds display, resource deltas, and prominent role badges.
"""

from nicegui import ui

from engine.runtime.state import CompiledGame

# Compact color map for resource bars
_RES_COLORS = {
    "gold": "#D97706",
    "coins": "#D97706",
    "money": "#D97706",
    "influence": "#6D28D9",
    "power": "#DC2626",
    "votes": "#0891B2",
    "health": "#16A34A",
    "reputation": "#7C3AED",
    "intel": "#2563EB",
    # Exchange game resources
    "credits": "#D97706",
    "alpha": "#DC2626",
    "beta": "#2563EB",
    "gamma": "#16A34A",
    "delta": "#9333EA",
    "research": "#0891B2",
    "suspicion": "#EA580C",
}


def render_entity_card(
    eid: str,
    entity: dict,
    is_self: bool,
    compiled: CompiledGame,
    prev_resources: dict | None = None,
):
    """Render a single entity card from a view_for() entity dict.

    Args:
        prev_resources: previous resource values for delta display (optional).
    """
    active = entity["active"]

    card_cls = "w-full sm:w-72"
    if not active:
        card_cls += " opacity-40"
    if is_self:
        card_cls += " glow-self"

    with ui.card().classes(f"glass-static {card_cls}").props("flat"):
        # Header row: name + badges
        with ui.row().classes("items-center gap-2 w-full"):
            ui.label(eid).classes("font-display text-base font-semibold tracking-tight")
            if is_self:
                ui.badge("YOU").props("color=primary rounded").classes(
                    "text-xs font-semibold px-2"
                )
            ui.badge("ACTIVE" if active else "OUT").props(
                f"color={'positive' if active else 'negative'} rounded"
            ).classes("text-xs px-2")

        # Role badge (prominent)
        attrs = entity.get("attrs", {})
        role = attrs.get("role")
        if role:
            team = attrs.get("team") or attrs.get("faction", "")
            role_text = f"{role}" + (f" ({team})" if team else "")
            ui.badge(role_text).props("rounded color=secondary").classes(
                "text-xs px-2 mt-1"
            )

        # Resources as thin colored bars with bounds + deltas
        resources = entity.get("resources", {})
        if resources:
            with ui.column().classes("gap-1.5 w-full mt-2"):
                for res_id, val in resources.items():
                    rdef = compiled.resources.get(res_id)
                    upper = (
                        rdef.bounds[1] if rdef and rdef.bounds[1] is not None else None
                    )
                    bounded = upper is not None and upper > 0
                    color = _RES_COLORS.get(res_id, "#6D28D9")

                    # Compute delta
                    delta = None
                    if prev_resources and res_id in prev_resources:
                        prev_val = prev_resources[res_id]
                        if prev_val != val:
                            delta = val - prev_val

                    with ui.row().classes("items-center gap-2 w-full"):
                        ui.label(res_id).classes("text-xs text-gray-400 w-16 truncate")
                        if bounded:
                            pct = max(0.0, min(1.0, float(val) / float(upper)))
                            with (
                                ui.element("div")
                                .classes(
                                    "flex-grow h-1.5 rounded-full overflow-hidden"
                                )
                                .style("background: #F1F0F5")
                            ):
                                ui.element("div").classes(
                                    "h-full rounded-full transition-all duration-500"
                                ).style(
                                    f"width: {pct * 100:.0f}%; background: {color}"
                                )
                        else:
                            # Unbounded: spacer keeps row alignment
                            ui.element("div").classes("flex-grow")
                        # Value label
                        if bounded and upper < 10000:
                            ui.label(f"{val:.0f}/{upper:.0f}").classes(
                                "text-xs text-gray-500 w-14 text-right font-mono"
                            )
                        else:
                            ui.label(f"{val:.0f}").classes(
                                "text-xs text-gray-500 w-8 text-right font-mono"
                            )
                        # Delta badge
                        if delta is not None:
                            sign = "+" if delta > 0 else ""
                            d_color = "text-green-600" if delta > 0 else "text-red-600"
                            ui.label(f"{sign}{delta:.0f}").classes(
                                f"text-xs font-semibold {d_color} w-8"
                            )

        # Attributes as pills (excluding role/team already shown)
        extra_attrs = {
            k: v for k, v in attrs.items() if k not in ("role", "team", "faction")
        }
        if extra_attrs:
            with ui.row().classes("gap-1.5 flex-wrap mt-2"):
                for attr_id, val in extra_attrs.items():
                    ui.badge(f"{attr_id}: {val}").props("rounded outline").classes(
                        "text-xs text-gray-500 px-2"
                    )

        # Groups as colored tags
        groups = entity.get("groups", [])
        if groups:
            with ui.row().classes("gap-1.5 flex-wrap mt-1"):
                for g in groups:
                    ui.badge(g).props("rounded color=secondary outline").classes(
                        "text-xs px-2"
                    )
