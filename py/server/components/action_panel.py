"""Action panel — deals, votes, speech acts, pending responses.

DSL-enriched: phase hints, usage limits, guard filtering, outcome previews,
vote progress, advance readiness, speech act execution + status.
Uses the same helpers as MCP formatters so humans and AI agents see
identical information.
"""

from __future__ import annotations

from typing import Any

from nicegui import ui

from mcp.formatters import (
    build_context_line,
    can_player_start_vote,
    can_player_use_deal,
    can_player_use_speech_act,
    compute_advance_readiness,
    format_usage_limit,
    get_phase_def,
    is_usage_exhausted,
)
from mcp.mechanics import outcome_summary
from server.sessions import GameSession


def render_action_panel(
    session: GameSession,
    player_id: str,
    on_result: Any,
):
    """Render the full action panel: deals, votes, speech acts, advance."""
    s = session.state
    compiled = session.compiled
    ctx = compiled.context

    if s.status != "active":
        _render_game_over(s)
        return

    phase_def = get_phase_def(compiled, s.phase)

    # --- Phase context banner ---
    _render_phase_context(s, compiled, phase_def, ctx)

    # --- Role guidance (collapsible) ---
    _render_role_guidance(s, compiled, player_id)

    # --- Automatic phase ---
    if phase_def and phase_def.automatic:
        with ui.row().classes("items-center gap-2 text-gray-400"):
            ui.icon("autorenew").classes("text-lg")
            ui.label("Automatic phase — advance to continue").classes("text-sm")

    # --- Pending responses (URGENT) ---
    my_pending = [
        (iid, pd)
        for iid, pd in s.pending_deals.items()
        if player_id in pd.responders and pd.responders[player_id] is None
    ]
    if my_pending:
        with ui.row().classes("items-center gap-2"):
            ui.icon("priority_high").classes("text-lg text-amber-600")
            ui.label("Respond Now").classes("font-display text-base font-semibold")
        for iid, pending in my_pending:
            deal_def = compiled.deals.get(pending.deal_id)
            _render_pending_deal(iid, pending, deal_def, session, player_id, on_result)

    # --- Pending inquire responses (for targeted player) ---
    my_inquire_responses = [
        (iid, sa)
        for iid, sa in s.pending_speech_acts.items()
        if sa.act_type == "inquire"
        and sa.target == player_id
        and sa.status == "pending"
        and sa.inquire_response is None
    ]
    if my_inquire_responses:
        with ui.row().classes("items-center gap-2 mt-3"):
            ui.icon("question_answer").classes("text-lg text-amber-600")
            ui.label("Inquiries to Answer").classes(
                "font-display text-base font-semibold"
            )
        for iid, sa in my_inquire_responses:
            _render_inquire_response(iid, sa, compiled, session, player_id, on_result)

    # --- Pending votes ---
    my_votes = [
        (iid, pv)
        for iid, pv in s.pending_votes.items()
        if player_id in pv.eligible and player_id not in pv.votes
    ]
    if my_votes:
        with ui.row().classes("items-center gap-2 mt-3"):
            ui.icon("how_to_vote").classes("text-lg text-violet-600")
            ui.label("Votes").classes("font-display text-base font-semibold")
        for iid, pv in my_votes:
            _render_pending_vote(iid, pv, compiled, session, player_id, on_result)

    # --- Endorsable speech acts ---
    endorsable = [
        (iid, sa)
        for iid, sa in s.pending_speech_acts.items()
        if sa.status == "pending"
        and sa.actor != player_id
        and sa.endorsers is not None
        and player_id not in sa.endorsers
        and compiled.speech_acts.get(sa.speech_act_id)
        and compiled.speech_acts[sa.speech_act_id].endorsable
    ]
    if endorsable:
        with ui.row().classes("items-center gap-2 mt-3"):
            ui.icon("thumb_up").classes("text-lg text-blue-600")
            ui.label("Endorse").classes("font-display text-base font-semibold")
        for iid, sa in endorsable:
            _render_endorse_card(iid, sa, session, player_id, on_result)

    # --- Available actions: deals + votes + speech acts (filtered + sorted) ---
    allowed = phase_def.allows if phase_def else ()

    # Deals
    deal_ids = [
        d
        for d in allowed
        if d in compiled.deals and can_player_use_deal(s, compiled, d, player_id)
    ]
    deal_ids.sort(key=lambda d: -ctx.deal_priorities.get(d, 50))

    # Votes (initiation)
    vote_ids = [
        v
        for v in allowed
        if v in compiled.votes and can_player_start_vote(s, compiled, v, player_id)
    ]

    # Speech acts
    sa_ids = [
        sa_id
        for sa_id in allowed
        if sa_id in compiled.speech_acts
        and can_player_use_speech_act(s, compiled, sa_id, player_id)
    ]

    if deal_ids:
        with ui.row().classes("items-center gap-2 mt-3"):
            ui.icon("bolt").classes("text-lg text-gray-500")
            ui.label("Actions").classes("font-display text-base font-semibold")
        with ui.row().classes("w-full gap-3 flex-wrap"):
            for deal_id in deal_ids:
                _render_deal_card(deal_id, s, compiled, session, player_id, on_result)

    if vote_ids:
        with ui.row().classes("items-center gap-2 mt-3"):
            ui.icon("how_to_vote").classes("text-lg text-gray-500")
            ui.label("Start a Vote").classes("font-display text-base font-semibold")
        with ui.row().classes("w-full gap-3 flex-wrap"):
            for vote_id in vote_ids:
                _render_vote_start_card(
                    vote_id, s, compiled, session, player_id, on_result
                )

    if sa_ids:
        with ui.row().classes("items-center gap-2 mt-3"):
            ui.icon("campaign").classes("text-lg text-gray-500")
            ui.label("Speech Acts").classes("font-display text-base font-semibold")
        with ui.row().classes("w-full gap-3 flex-wrap"):
            for sa_id in sa_ids:
                _render_speech_act_card(
                    sa_id, s, compiled, session, player_id, on_result
                )

    # --- Speech act status ---
    _render_speech_act_status(s, compiled, player_id)

    # --- Advance readiness + button ---
    _render_advance_section(s, compiled, player_id, session)


# ---------------------------------------------------------------------------
# Sub-components
# ---------------------------------------------------------------------------


def _render_game_over(s):
    with ui.card().classes("glass-static w-full items-center py-8").props("flat"):
        ui.icon("emoji_events").classes("text-5xl text-amber-600")
        if s.victory_result:
            winner = s.victory_result.get("winner", "?")
            cond = s.victory_result.get("condition", "?")
            ui.label(f"Game Over! {winner} wins").classes(
                "font-display text-xl font-semibold"
            )
            ui.label(cond).classes("text-sm text-gray-500")
        else:
            ui.label("Game Over").classes("font-display text-xl font-semibold")


def _render_phase_context(s, compiled, phase_def, ctx):
    """Phase hint banner + context variables."""
    parts = build_context_line(s, compiled, phase_def)
    phase_hint = ctx.phase_hints.get(s.phase) if ctx.phase_hints else None

    if not parts and not phase_hint:
        return

    with (
        ui.card()
        .classes("glass-static w-full")
        .props("flat")
        .style("border-left: 3px solid #0891B2")
    ):
        if phase_hint and phase_hint.summary:
            urgency_cls = (
                "font-semibold text-red-600"
                if phase_hint.urgency == "critical"
                else "font-medium text-gray-700"
            )
            ui.label(phase_hint.summary).classes(f"text-sm {urgency_cls}")
            if phase_hint.tips:
                for tip in phase_hint.tips:
                    with ui.row().classes("items-start gap-1 mt-0.5"):
                        ui.icon("lightbulb").classes("text-xs text-amber-500 mt-0.5")
                        ui.label(tip).classes("text-xs text-gray-500")
        if parts:
            with ui.row().classes("gap-2 flex-wrap mt-1"):
                for part in parts:
                    ui.badge(part).props("rounded outline").classes(
                        "text-xs text-gray-500 px-2"
                    )


def _render_role_guidance(state, compiled, player_id):
    """Collapsible role guidance card — same data as MCP role_guidance tool."""
    entity = state.entities.get(player_id)
    if not entity:
        return

    role_id = entity.get_attr("role")
    if not role_id:
        return

    ctx = compiled.context
    role_hint = ctx.role_hints.get(role_id) if ctx.role_hints else None
    role_def = compiled.roles.get(role_id)

    # Nothing to show if no hint and no role def
    if not role_hint and not role_def:
        return

    with (
        ui.expansion(
            f"Role: {role_id.replace('_', ' ').title()}",
            icon="psychology",
        )
        .classes("w-full")
        .props("dense header-class=text-sm")
    ):
        if role_def:
            if role_def.team:
                ui.badge(role_def.team).props(
                    "rounded outline color=secondary"
                ).classes("text-xs px-1.5")
            if role_def.doc:
                ui.label(role_def.doc).classes("text-xs text-gray-500 mt-1")
            if role_def.appears_as:
                ui.label(f"Appears as: {role_def.appears_as}").classes(
                    "text-xs text-gray-400 italic"
                )

        if role_hint:
            if role_hint.strategy:
                with ui.row().classes("items-start gap-1 mt-1"):
                    ui.icon("stars").classes("text-xs text-amber-500 mt-0.5")
                    ui.label(role_hint.strategy).classes("text-xs text-gray-600")
            if role_hint.allies:
                ui.label(f"Allies: {', '.join(role_hint.allies)}").classes(
                    "text-xs text-green-600 mt-0.5"
                )
            if role_hint.threats:
                ui.label(f"Threats: {', '.join(role_hint.threats)}").classes(
                    "text-xs text-red-500 mt-0.5"
                )
            if role_hint.key_actions:
                ui.label(f"Key actions: {', '.join(role_hint.key_actions)}").classes(
                    "text-xs text-gray-500 mt-0.5"
                )
            # Phase-specific tip
            phase_tip = role_hint.phase_tips.get(state.phase)
            if phase_tip:
                with ui.row().classes("items-start gap-1 mt-1"):
                    ui.icon("lightbulb").classes("text-xs text-cyan-600 mt-0.5")
                    ui.label(f"Now: {phase_tip}").classes(
                        "text-xs text-cyan-700 font-medium"
                    )

        # Victory conditions relevant to this role
        relevant_victories = [
            v
            for v in compiled.victories
            if not v.team or (role_def and v.team == role_def.team)
        ]
        if relevant_victories:
            ui.separator().classes("my-1")
            ui.label("Win conditions").classes("text-xs font-semibold text-gray-500")
            for v in relevant_victories:
                ui.label(f"• {v.message or v.id}").classes("text-xs text-gray-500")


def _render_pending_deal(iid, pending, deal_def, session, player_id, on_result):
    with (
        ui.card()
        .classes("glass-static w-full")
        .props("flat")
        .style("border-left: 3px solid #D97706")
    ):
        with ui.row().classes("items-center gap-2"):
            ui.label(f"{pending.deal_id.replace('_', ' ').title()}").classes(
                "font-semibold text-sm"
            )
            ui.label(f"from {pending.proposer}").classes("text-xs text-gray-400")
        # Outcome preview
        if deal_def and deal_def.outcomes and list(deal_def.outcomes.keys()) != ["ok"]:
            ui.label(outcome_summary(deal_def.outcomes)).classes(
                "text-xs text-gray-400 mt-0.5"
            )
        if deal_def and deal_def.response_options:
            with ui.row().classes("gap-2 mt-2"):
                for opt in deal_def.response_options:

                    async def _respond(i=iid, o=opt):
                        result = await session.respond_deal(i, player_id, o)
                        on_result(result)

                    color = (
                        "positive"
                        if opt.lower() in ("accept", "yes", "approve")
                        else "grey-7"
                    )
                    ui.button(opt.title(), on_click=_respond).props(
                        f"unelevated no-caps dense color={color}"
                    ).classes("rounded-lg")


def _render_pending_vote(iid, pv, compiled, session, player_id, on_result):
    cast = len(pv.votes)
    total = len(pv.eligible)
    vote_def = compiled.votes.get(pv.vote_id)
    threshold = vote_def.threshold if vote_def else "majority"

    with (
        ui.card()
        .classes("glass-static w-full")
        .props("flat")
        .style("border-left: 3px solid #6D28D9")
    ):
        with ui.row().classes("items-center gap-2"):
            ui.label(pv.vote_id.replace("_", " ").title()).classes(
                "font-semibold text-sm"
            )
            ui.badge(f"{cast}/{total}").props("rounded color=secondary").classes(
                "text-xs px-1.5"
            )
            ui.badge(threshold).props("rounded outline").classes(
                "text-xs text-gray-400 px-1"
            )
        if pv.subject:
            ui.label(f"Subject: {pv.subject}").classes("text-xs text-gray-500")
        with ui.row().classes("gap-2 mt-2"):
            for opt in pv.options:

                async def _vote(i=iid, o=opt):
                    result = await session.cast_vote(i, player_id, o)
                    on_result(result)

                ui.button(opt.title(), on_click=_vote).props(
                    "unelevated no-caps dense"
                ).classes("rounded-lg")


def _render_vote_start_card(vote_id, state, compiled, session, player_id, on_result):
    """Card to initiate a new vote."""
    vote_def = compiled.votes[vote_id]

    with ui.card().classes("glass-static w-full sm:w-60").props("flat"):
        with ui.row().classes("items-center gap-2"):
            ui.icon("how_to_vote").classes("text-sm text-violet-500")
            ui.label(vote_id.replace("_", " ").title()).classes(
                "font-display font-semibold text-sm"
            )
        if vote_def.doc:
            ui.label(vote_def.doc).classes("text-xs text-gray-400 mt-0.5")
        # Options preview
        if vote_def.options:
            ui.label(f"Options: {', '.join(vote_def.options)}").classes(
                "text-xs text-gray-500 mt-0.5"
            )

        # Subject selector if vote has subject
        subject_input = None
        if vote_def.subject:
            active_others = [
                e for e in state.entities if state.entities[e].active and e != player_id
            ]
            subject_input = (
                ui.select(options=active_others, label="Subject")
                .props("outlined dense")
                .classes("w-full mt-2")
            )

        async def _start(v=vote_id, subj=subject_input):
            kw: dict[str, Any] = {"proposer_id": player_id}
            if subj and subj.value:
                kw["subject_id"] = subj.value
            result = await session.start_vote(v, **kw)
            on_result(result)

        ui.button("Start Vote", on_click=_start, icon="how_to_vote").props(
            "unelevated no-caps dense color=secondary"
        ).classes("w-full rounded-lg mt-2")


def _render_speech_act_card(sa_id, state, compiled, session, player_id, on_result):
    """Card to execute a speech act (claim, accuse, promise, etc.)."""
    sa_def = compiled.speech_acts[sa_id]
    act_icon = {
        "claim": "record_voice_over",
        "accuse": "gavel",
        "promise": "handshake",
        "predict": "auto_awesome",
        "inquire": "help_outline",
    }.get(sa_def.act_type, "campaign")
    act_color = {
        "claim": "#0891B2",
        "accuse": "#DC2626",
        "promise": "#16A34A",
        "predict": "#D97706",
        "inquire": "#6D28D9",
    }.get(sa_def.act_type, "#6D28D9")

    with (
        ui.card()
        .classes("glass-static w-full sm:w-60")
        .props("flat")
        .style(f"border-left: 3px solid {act_color}")
    ):
        with ui.row().classes("items-center gap-2"):
            ui.icon(act_icon).classes("text-sm").style(f"color: {act_color}")
            ui.label(sa_id.replace("_", " ").title()).classes(
                "font-display font-semibold text-sm"
            )
            ui.badge(sa_def.act_type).props("rounded outline").classes("text-xs px-1.5")

        # Cost display
        if sa_def.cost:
            cost_parts = [f"{r}: {a}" for r, a in sa_def.cost.items()]
            ui.label(f"Cost: {', '.join(cost_parts)}").classes(
                "text-xs text-gray-500 mt-0.5"
            )

        # Usage limit
        usage_str = _format_sa_usage(state, sa_id, player_id, sa_def)
        if usage_str:
            ui.badge(usage_str).props("rounded outline").classes(
                "text-xs text-gray-400 px-1.5"
            )

        # Target selector (for accuse, inquire)
        target_input = None
        if sa_def.target_filter is not None or sa_def.act_type in ("accuse", "inquire"):
            active_others = [
                e for e in state.entities if state.entities[e].active and e != player_id
            ]
            target_input = (
                ui.select(options=active_others, label="Target")
                .props("outlined dense")
                .classes("w-full mt-2")
            )

        # Param inputs
        param_inputs = _render_param_inputs(sa_def.params)

        async def _execute(sid=sa_id, tgt=target_input, pinputs=param_inputs):
            kw: dict[str, Any] = {"actor_id": player_id}
            if tgt and tgt.value:
                kw["target_id"] = tgt.value
            if pinputs:
                kw["params"] = {pk: _param_value(pinp) for pk, pinp in pinputs.items()}
            result = await session.execute_speech_act(sid, **kw)
            on_result(result)

        ui.button("Execute", on_click=_execute).props(
            "unelevated no-caps dense"
        ).classes("w-full rounded-lg mt-2").style(f"background: {act_color} !important")


def _render_endorse_card(iid, sa, session, player_id, on_result):
    """Card to endorse a pending speech act."""
    sa_def_ref = sa.speech_act_id
    act_icon = {
        "claim": "record_voice_over",
        "accuse": "gavel",
        "promise": "handshake",
        "predict": "auto_awesome",
    }.get(sa.act_type, "campaign")

    with (
        ui.card()
        .classes("glass-static w-full")
        .props("flat")
        .style("border-left: 3px solid #2563EB")
    ):
        with ui.row().classes("items-center gap-2"):
            ui.icon(act_icon).classes("text-sm text-blue-600")
            ui.label(f"{sa.act_type.title()} by {sa.actor}").classes(
                "font-semibold text-sm"
            )
            if sa.endorsers:
                ui.badge(f"{len(sa.endorsers)} endorsed").props(
                    "rounded color=positive"
                ).classes("text-xs px-1.5")
        if sa.params:
            params_str = ", ".join(f"{k}={v}" for k, v in sa.params.items())
            ui.label(params_str).classes("text-xs text-gray-500 font-mono")

        async def _endorse(i=iid):
            result = await session.endorse_speech_act(i, player_id)
            on_result(result)

        ui.button("Endorse", on_click=_endorse, icon="thumb_up").props(
            "unelevated no-caps dense color=primary"
        ).classes("rounded-lg mt-2")


def _render_inquire_response(iid, sa, compiled, session, player_id, on_result):
    """Card to respond to an inquire speech act."""
    sa_def = compiled.speech_acts.get(sa.speech_act_id)

    with (
        ui.card()
        .classes("glass-static w-full")
        .props("flat")
        .style("border-left: 3px solid #D97706")
    ):
        with ui.row().classes("items-center gap-2"):
            ui.icon("help_outline").classes("text-lg text-amber-600")
            ui.label(f"Inquiry from {sa.actor}").classes("font-semibold text-sm")
        if sa.params:
            params_str = ", ".join(f"{k}={v}" for k, v in sa.params.items())
            ui.label(params_str).classes("text-xs text-gray-500 font-mono")

        # Response options or free text
        if sa_def and sa_def.inquire_response_options:
            with ui.row().classes("gap-2 mt-2"):
                for opt in sa_def.inquire_response_options:

                    async def _resp(i=iid, o=opt):
                        result = await session.respond_to_inquire(i, player_id, o)
                        on_result(result)

                    ui.button(opt.title(), on_click=_resp).props(
                        "unelevated no-caps dense"
                    ).classes("rounded-lg")
        else:
            resp_input = (
                ui.input("Your response").props("outlined dense").classes("w-full mt-2")
            )

            async def _resp_free(i=iid, inp=resp_input):
                if not inp.value:
                    ui.notify("Enter a response", type="warning")
                    return
                result = await session.respond_to_inquire(i, player_id, inp.value)
                on_result(result)

            ui.button("Respond", on_click=_resp_free).props(
                "unelevated no-caps dense color=accent"
            ).classes("rounded-lg mt-1")


def _render_speech_act_status(state, compiled, player_id):
    """Show visible speech acts (claims, accusations, promises) and their status."""
    # Collect visible speech acts
    all_acts = list(state.pending_speech_acts.values()) + list(
        state.resolved_speech_acts
    )
    if not all_acts:
        return

    visible = []
    for sa in all_acts:
        sa_def = compiled.speech_acts.get(sa.speech_act_id)
        vis = sa_def.visibility if sa_def else "public"
        if vis == "public":
            visible.append(sa)
        elif vis == "private" and player_id in (sa.actor, sa.target):
            visible.append(sa)

    if not visible:
        return

    with ui.row().classes("items-center gap-2 mt-3"):
        ui.icon("campaign").classes("text-lg text-gray-400")
        ui.label("Speech Acts").classes("font-display text-sm font-semibold")

    status_colors = {
        "pending": "#E5E4EA",
        "verified_true": "#BBF7D0",
        "verified_false": "#FECACA",
        "expired": "#FDE68A",
    }
    status_icons = {
        "pending": "hourglass_empty",
        "verified_true": "check_circle",
        "verified_false": "cancel",
        "expired": "timer_off",
    }

    for sa in visible:
        border = status_colors.get(sa.status, "#E5E4EA")
        icon = status_icons.get(sa.status, "info")

        with (
            ui.element("div")
            .classes("w-full rounded-lg p-2 mt-1")
            .style(f"background: #FAFAFE; border: 1px solid {border}")
        ):
            with ui.row().classes("items-center gap-2"):
                ui.icon(icon).classes("text-sm text-gray-500")
                ui.label(sa.act_type.title()).classes("text-xs font-semibold")
                ui.label(f"by {sa.actor}").classes("text-xs text-gray-400")
                if sa.target:
                    ui.label(f"→ {sa.target}").classes("text-xs text-gray-400")
                ui.badge(sa.status.replace("_", " ")).props("rounded outline").classes(
                    "text-xs px-1.5"
                )
                if sa.endorsers:
                    ui.badge(f"{len(sa.endorsers)} endorsed").props(
                        "rounded outline"
                    ).classes("text-xs px-1")
            if sa.params:
                params_str = ", ".join(f"{k}={v}" for k, v in sa.params.items())
                ui.label(params_str).classes("text-xs text-gray-500 font-mono ml-6")
            if sa.inquire_response is not None:
                ui.label(f"Response: {sa.inquire_response}").classes(
                    "text-xs text-gray-600 ml-6"
                )


def _render_deal_card(deal_id, state, compiled, session, player_id, on_result):
    deal_def = compiled.deals[deal_id]
    has_target = "target" in deal_def.parties
    has_responder = "responder" in deal_def.parties
    needs_target = has_target or has_responder

    # Usage limit badge text
    usage_str = format_usage_limit(state, deal_id, player_id, deal_def)

    with ui.card().classes("glass-static w-full sm:w-60").props("flat"):
        # Title + usage badge
        with ui.row().classes("items-center gap-2"):
            ui.label(deal_id.replace("_", " ").title()).classes(
                "font-display font-semibold text-sm"
            )
            if usage_str:
                ui.badge(usage_str).props("rounded outline").classes(
                    "text-xs text-gray-400 px-1.5"
                )

        # Doc
        if deal_def.doc:
            ui.label(deal_def.doc).classes("text-xs text-gray-400 mt-0.5")

        # Outcome preview
        if deal_def.outcomes and list(deal_def.outcomes.keys()) != ["ok"]:
            ui.label(outcome_summary(deal_def.outcomes)).classes(
                "text-xs text-cyan-600 mt-0.5 italic"
            )

        # Param inputs (rendered for ALL deals, before target selector)
        param_inputs = _render_param_inputs(deal_def.params)

        # Target selector
        tgt_select = None
        if needs_target:
            active_others = [
                e for e in state.entities if state.entities[e].active and e != player_id
            ]
            tgt_select = (
                ui.select(options=active_others, label="Target")
                .props("outlined dense")
                .classes("w-full mt-2")
            )

        async def _do_deal(
            d=deal_id,
            sel=tgt_select,
            resp=has_responder,
            tgt_needed=needs_target,
            pinputs=param_inputs,
        ):
            if tgt_needed:
                if not sel or not sel.value:
                    ui.notify("Select a target", type="warning")
                    return
            kw: dict[str, Any] = {"actor_id": player_id}
            if tgt_needed and sel and sel.value:
                if resp:
                    kw["responder_id"] = sel.value
                else:
                    kw["target_id"] = sel.value
            if pinputs:
                kw["params"] = {pk: _param_value(pinp) for pk, pinp in pinputs.items()}
            result = await session.execute_deal(d, **kw)
            on_result(result)

        ui.button("Execute", on_click=_do_deal).props(
            "unelevated no-caps dense"
        ).classes("w-full rounded-lg mt-2")


def _render_advance_section(state, compiled, player_id, session):
    readiness = compute_advance_readiness(state, compiled, player_id)
    phase_def = get_phase_def(compiled, state.phase)
    is_auto = phase_def and phase_def.automatic

    with ui.column().classes("mt-4 gap-1"):
        if readiness == "BLOCKED":
            with ui.row().classes("items-center gap-1.5"):
                ui.icon("block").classes("text-sm text-red-500")
                ui.label("Pending actions must be resolved first").classes(
                    "text-xs text-gray-500"
                )
        elif readiness == "READY":
            with ui.row().classes("items-center gap-1.5"):
                ui.icon("check_circle").classes("text-sm text-green-600")
                ui.label("No more actions available — advance when ready").classes(
                    "text-xs text-gray-500"
                )
        else:
            with ui.row().classes("items-center gap-1.5"):
                ui.icon("info").classes("text-sm text-gray-400")
                ui.label("You can still take actions, or advance phase").classes(
                    "text-xs text-gray-500"
                )

        if not is_auto:

            async def _advance():
                await session.advance_phase()

            btn = ui.button("Advance Phase", on_click=_advance, icon="skip_next")
            if readiness == "BLOCKED":
                btn.props("unelevated no-caps color=grey-4 disable")
            else:
                btn.props("unelevated no-caps color=primary")
            btn.classes("rounded-lg")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _render_param_inputs(params: dict | None) -> dict[str, Any]:
    """Render input widgets for ParamDef objects. Returns {name: widget}."""
    if not params:
        return {}
    inputs: dict[str, Any] = {}
    for pname, pdef in params.items():
        label = pdef.label or pname.replace("_", " ").title()
        if pdef.type in ("number", "int", "integer"):
            pmin = pdef.min if pdef.min is not None else 0
            pmax = pdef.max if pdef.max is not None else 9999
            inp = (
                ui.number(label, value=pmin, min=pmin, max=pmax)
                .props("outlined dense")
                .classes("w-full mt-1")
            )
            inputs[pname] = inp
        elif pdef.type == "keyword" and pdef.options:
            inp = (
                ui.select(options=list(pdef.options), label=label)
                .props("outlined dense")
                .classes("w-full mt-1")
            )
            inputs[pname] = inp
        elif pdef.type == "player":
            # Player selector handled separately by target logic;
            # only render if it's a param, not the deal's target party
            inp = (
                ui.input(label, placeholder=pdef.placeholder or "player id")
                .props("outlined dense")
                .classes("w-full mt-1")
            )
            inputs[pname] = inp
        else:
            inp = (
                ui.input(label, placeholder=pdef.placeholder or "")
                .props("outlined dense")
                .classes("w-full mt-1")
            )
            inputs[pname] = inp
    return inputs


def _param_value(widget) -> Any:
    """Extract value from a param input widget, casting numbers."""
    val = widget.value
    if isinstance(val, (int, float)):
        return int(val) if isinstance(val, float) and val == int(val) else val
    return val


def _format_sa_usage(state, sa_id, player_id, sa_def) -> str:
    """Format usage limit for a speech act (same logic as format_usage_limit)."""
    key = f"{player_id}:{sa_id}"
    usage = state.usage.get(key, {})
    parts = []
    if sa_def.per_round is not None:
        used = usage.get(f"round:{state.round}", 0)
        parts.append(f"{used}/{sa_def.per_round} round")
    if sa_def.per_phase is not None:
        used = usage.get(f"phase:{state.phase}", 0)
        parts.append(f"{used}/{sa_def.per_phase} phase")
    if sa_def.per_game is not None:
        used = usage.get("game", 0)
        parts.append(f"{used}/{sa_def.per_game} game")
    return f"[{', '.join(parts)}]" if parts else ""
