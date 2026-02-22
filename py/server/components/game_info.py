"""Game rules panel — auto-generated from CompiledGame DSL definitions.

Uses mcp/mechanics.py to produce human-readable descriptions of deals,
votes, speech acts, and victory conditions.
"""

from __future__ import annotations

from nicegui import ui

from engine.runtime.state import CompiledGame, GameState
from mcp.mechanics import (
    describe_deal_mechanics,
    describe_speech_act_mechanics,
    describe_vote_mechanics,
)


def render_game_info(
    compiled: CompiledGame,
    player_id: str | None = None,
    state: GameState | None = None,
):
    """Render game rules from CompiledGame DSL definitions."""
    ctx = compiled.context

    # --- Overview ---
    with (
        ui.expansion("Overview", icon="info").classes("w-full").props("default-opened")
    ):
        if ctx.game_summary:
            ui.label(ctx.game_summary).classes("text-sm text-gray-600")
        if ctx.score_explanation:
            with ui.row().classes("items-center gap-1 mt-1"):
                ui.icon("emoji_events").classes("text-xs text-amber-600")
                ui.label(f"Scoring: {ctx.score_explanation}").classes(
                    "text-xs text-gray-500"
                )
        ui.label(f"{compiled.min_players}\u2013{compiled.max_players} players").classes(
            "text-xs text-gray-400 mt-1"
        )

    # --- Resources ---
    if compiled.resources:
        with ui.expansion("Resources", icon="account_balance_wallet").classes("w-full"):
            for rid, rdef in compiled.resources.items():
                with ui.row().classes("items-center gap-2"):
                    vis_badge = {
                        "public": ("visibility", "positive"),
                        "private": ("lock", "grey-7"),
                        "hidden": ("visibility_off", "negative"),
                    }.get(rdef.visibility, ("help", "grey"))
                    ui.icon(vis_badge[0]).classes("text-xs text-gray-400")
                    ui.label(rid).classes("text-sm font-medium")
                    if rdef.bounds[0] is not None or rdef.bounds[1] is not None:
                        lo = rdef.bounds[0] if rdef.bounds[0] is not None else "?"
                        hi = rdef.bounds[1] if rdef.bounds[1] is not None else "?"
                        ui.label(f"[{lo}\u2013{hi}]").classes(
                            "text-xs text-gray-400 font-mono"
                        )
                    if rdef.transferable:
                        ui.badge("transferable").props("rounded outline").classes(
                            "text-xs px-1"
                        )

    # --- Roles ---
    if compiled.roles:
        with ui.expansion("Roles", icon="people").classes("w-full"):
            for role_id, role_def in compiled.roles.items():
                with ui.column().classes("gap-0.5 mb-2"):
                    with ui.row().classes("items-center gap-2"):
                        ui.label(role_id).classes("text-sm font-semibold")
                        if role_def.team:
                            ui.badge(role_def.team).props(
                                "rounded color=secondary"
                            ).classes("text-xs px-1.5")
                    if role_def.doc:
                        ui.label(role_def.doc).classes("text-xs text-gray-500")
                    # Role hints for current player
                    if player_id and state and ctx.role_hints:
                        me = state.entities.get(player_id)
                        if me and me.attrs_.get("role") == role_id:
                            rh = ctx.role_hints.get(role_id)
                            if rh:
                                if rh.strategy:
                                    ui.label(f"Strategy: {rh.strategy}").classes(
                                        "text-xs text-cyan-600 italic"
                                    )
                                if rh.allies:
                                    ui.label(f"Allies: {', '.join(rh.allies)}").classes(
                                        "text-xs text-green-600"
                                    )
                                if rh.threats:
                                    ui.label(
                                        f"Threats: {', '.join(rh.threats)}"
                                    ).classes("text-xs text-red-600")

    # --- Phases ---
    if compiled.phases:
        with ui.expansion("Phases", icon="timeline").classes("w-full"):
            for phase_def in compiled.phases:
                with ui.row().classes("items-center gap-2"):
                    is_current = state and state.phase == phase_def.id
                    weight = "font-semibold" if is_current else ""
                    color = "text-violet-600" if is_current else "text-gray-600"
                    ui.label(phase_def.id).classes(f"text-sm {weight} {color}")
                    if phase_def.automatic:
                        ui.badge("auto").props("rounded outline").classes(
                            "text-xs px-1"
                        )
                    if phase_def.allows:
                        ui.label(f"({', '.join(phase_def.allows)})").classes(
                            "text-xs text-gray-400"
                        )
                # Phase hint
                if ctx.phase_hints:
                    ph = ctx.phase_hints.get(phase_def.id)
                    if ph and ph.summary:
                        ui.label(ph.summary).classes("text-xs text-gray-400 ml-4")

    # --- Deals & Actions ---
    if compiled.deals:
        with ui.expansion("Deals & Actions", icon="handshake").classes("w-full"):
            for deal_id, deal_def in compiled.deals.items():
                with ui.column().classes("gap-0.5 mb-3"):
                    ui.label(deal_id.replace("_", " ").title()).classes(
                        "text-sm font-semibold"
                    )
                    desc = describe_deal_mechanics(deal_id, deal_def)
                    # Show as preformatted block for readability
                    ui.label(desc).classes(
                        "text-xs text-gray-500 whitespace-pre-wrap font-mono"
                    ).style("line-height: 1.4")

    # --- Votes ---
    if compiled.votes:
        with ui.expansion("Votes", icon="how_to_vote").classes("w-full"):
            for vote_id, vote_def in compiled.votes.items():
                with ui.column().classes("gap-0.5 mb-3"):
                    ui.label(vote_id.replace("_", " ").title()).classes(
                        "text-sm font-semibold"
                    )
                    desc = describe_vote_mechanics(vote_id, vote_def)
                    ui.label(desc).classes(
                        "text-xs text-gray-500 whitespace-pre-wrap font-mono"
                    ).style("line-height: 1.4")

    # --- Speech Acts ---
    if compiled.speech_acts:
        with ui.expansion("Speech Acts", icon="record_voice_over").classes("w-full"):
            for sa_id, sa_def in compiled.speech_acts.items():
                with ui.column().classes("gap-0.5 mb-3"):
                    ui.label(sa_id.replace("_", " ").title()).classes(
                        "text-sm font-semibold"
                    )
                    desc = describe_speech_act_mechanics(sa_id, sa_def)
                    ui.label(desc).classes(
                        "text-xs text-gray-500 whitespace-pre-wrap font-mono"
                    ).style("line-height: 1.4")

    # --- Commitments (Automatic Triggers) ---
    if compiled.commitments:
        with ui.expansion("Commitments", icon="auto_fix_high").classes("w-full"):
            ui.label("Automatic triggers that fire when conditions are met.").classes(
                "text-xs text-gray-400 mb-1"
            )
            for cid, cdef in compiled.commitments.items():
                with ui.row().classes("items-center gap-2 mb-1"):
                    ui.label(cid.replace("_", " ").title()).classes(
                        "text-sm font-semibold"
                    )
                    ui.badge(f"on {cdef.trigger}").props("rounded outline").classes(
                        "text-xs px-1"
                    )
                    if cdef.once:
                        ui.badge("once").props("rounded outline color=warning").classes(
                            "text-xs px-1"
                        )
                if cdef.doc:
                    ui.label(cdef.doc).classes("text-xs text-gray-500 ml-4")

    # --- Victory Conditions ---
    if compiled.victories:
        with ui.expansion("Victory Conditions", icon="emoji_events").classes("w-full"):
            for i, vdef in enumerate(compiled.victories):
                with ui.column().classes("gap-0.5 mb-2"):
                    vtype = getattr(vdef, "type", "elimination")
                    ui.label(f"Condition {i + 1} ({vtype})").classes(
                        "text-sm font-semibold"
                    )
                    if hasattr(vdef, "message") and vdef.message:
                        ui.label(vdef.message).classes("text-xs text-gray-500")
                    if hasattr(vdef, "doc") and vdef.doc:
                        ui.label(vdef.doc).classes("text-xs text-gray-400")

    # --- Channels ---
    if compiled.channels:
        with ui.expansion("Channels", icon="forum").classes("w-full"):
            for ch_id, ch_def in compiled.channels.items():
                with ui.row().classes("items-center gap-2"):
                    type_icon = {
                        "public": "chat",
                        "broadcast": "campaign",
                        "group": "group",
                        "private": "lock",
                    }.get(ch_def.type, "chat")
                    ui.icon(type_icon).classes("text-sm text-gray-400")
                    ui.label(ch_id).classes("text-sm font-medium")
                    ui.badge(ch_def.type).props("rounded outline").classes(
                        "text-xs px-1"
                    )
                if ch_def.description:
                    ui.label(ch_def.description).classes("text-xs text-gray-400 ml-6")
                if ctx.channel_hints:
                    ch_hint = ctx.channel_hints.get(ch_id)
                    if ch_hint:
                        if ch_hint.when_to_use:
                            ui.label(ch_hint.when_to_use).classes(
                                "text-xs text-cyan-600 ml-6 italic"
                            )
                        if ch_hint.risk:
                            ui.label(f"Risk: {ch_hint.risk}").classes(
                                "text-xs text-red-500 ml-6"
                            )
