"""Status and result formatting for AI agent consumption.

Key design: ACTION REQUIRED at top, categorized actions, vote progress,
usage limits, resource deltas, advance readiness.

Public helpers (also used by NiceGUI UI components):
  get_phase_def, can_player_use_deal, is_usage_exhausted,
  format_usage_limit, compute_advance_readiness, build_context_line
"""

from __future__ import annotations

import logging
from typing import Any

from engine.expr.evaluator import Context, evaluate
from engine.runtime.state import CompiledGame, GameState
from mcp.mechanics import outcome_summary
from mcp.schema import classify_parties

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# format_status — the main game state view
# ---------------------------------------------------------------------------


def format_status(view: dict[str, Any], compiled: CompiledGame, player_id: str) -> str:
    """Format game state for AI agent, using the filtered view."""
    state_obj = view.get("_state")  # injected by caller if available
    lines = [f"## {compiled.name}"]
    ctx = compiled.context
    if ctx.game_summary:
        lines.append(ctx.game_summary)
    lines.append(
        f"Phase: {view['phase']} | Round: {view['round']} | "
        f"Players: {_count_active(view)}/{len(view['entities'])} active | "
        f"Status: {view['status']}"
    )
    if ctx.score_explanation:
        lines.append(f"Scoring: {ctx.score_explanation}")

    timeout_remaining = view.get("_timeout_remaining")
    if timeout_remaining is not None:
        minutes, secs = divmod(timeout_remaining, 60)
        lines.append(f"Phase timeout: {minutes}m {secs}s remaining")

    # ACTION REQUIRED — top priority
    action_lines = _format_action_required(view, compiled, player_id, state_obj)
    if action_lines:
        lines.append("")
        lines.append("### ! ACTION REQUIRED")
        lines.extend(action_lines)

    # Your entity
    lines.append("")
    me = view["entities"].get(player_id)
    if me:
        lines.append(f"### You ({player_id})")
        status = "ACTIVE" if me["active"] else "ELIMINATED"
        role = me.get("attrs", {}).get("role", "")
        team = me.get("attrs", {}).get("team") or me.get("attrs", {}).get("faction", "")
        parts = [f"Status: {status}"]
        if role:
            parts.append(f"Role: {role}")
        if team:
            parts.append(f"Team: {team}")
        lines.append(" | ".join(parts))

        if me["resources"]:
            res_parts = [f"{k}: {v}" for k, v in me["resources"].items()]
            lines.append(f"Resources: {', '.join(res_parts)}")
        # Non-role/team attrs
        extra_attrs = {
            k: v
            for k, v in me.get("attrs", {}).items()
            if k not in ("role", "team", "faction")
        }
        if extra_attrs:
            attr_parts = [f"{k}: {v}" for k, v in extra_attrs.items()]
            lines.append(f"Attributes: {', '.join(attr_parts)}")
        if me.get("groups"):
            lines.append(f"Groups: {', '.join(me['groups'])}")

        # Compact role context from ContextConfig hints
        role_id = me.get("attrs", {}).get("role")
        if role_id and ctx.role_hints:
            rh = ctx.role_hints.get(role_id)
            if rh:
                lines.append(f"\n### Your Role: {role_id}")
                if rh.strategy:
                    lines.append(f"Strategy: {rh.strategy}")
                role_parts = []
                if rh.allies:
                    role_parts.append(f"Allies: {', '.join(rh.allies)}")
                if rh.threats:
                    role_parts.append(f"Threats: {', '.join(rh.threats)}")
                if role_parts:
                    lines.append(" | ".join(role_parts))
                if state_obj:
                    phase_tip = rh.phase_tips.get(state_obj.phase)
                    if phase_tip:
                        lines.append(f"Now ({state_obj.phase}): {phase_tip}")

    # Other players
    active_others = [
        (eid, e)
        for eid, e in view["entities"].items()
        if eid != player_id and e["active"]
    ]
    eliminated = [
        (eid, e)
        for eid, e in view["entities"].items()
        if eid != player_id and not e["active"]
    ]

    if active_others:
        lines.append("\n### Other Players")
        for eid, entity in active_others:
            parts = [eid]
            if entity["resources"]:
                res_str = ", ".join(f"{k}={v}" for k, v in entity["resources"].items())
                parts.append(res_str)
            if entity.get("attrs"):
                attr_str = ", ".join(f"{k}={v}" for k, v in entity["attrs"].items())
                parts.append(attr_str)
            if entity.get("groups"):
                parts.append(f"groups: {','.join(entity['groups'])}")
            if entity.get("hidden_fields"):
                parts.append(f"[hidden: {', '.join(entity['hidden_fields'])}]")
            lines.append("  " + " | ".join(parts))

    if eliminated:
        elim_names = ", ".join(f"~~{eid}~~" for eid, _ in eliminated)
        lines.append(f"  Eliminated: {elim_names}")

    # Game variables
    if view.get("vars"):
        lines.append("\n### Game State")
        for k, v in view["vars"].items():
            lines.append(f"  {k}: {v}")

    # Speech acts
    if view.get("speech_acts"):
        lines.append("\n### Speech Acts")
        for sa in view["speech_acts"]:
            status = sa.get("status", "pending")
            actor = sa.get("actor", "?")
            target = sa.get("target", "")
            sa_type = sa.get("act_type", "?")
            sa_id = sa.get("speech_act_id", "")
            target_str = f" -> {target}" if target else ""
            lines.append(f"  [{status}] {sa_type}: {actor}{target_str} ({sa_id})")

    return "\n".join(lines)


def _count_active(view: dict[str, Any]) -> int:
    return sum(1 for e in view["entities"].values() if e["active"])


def _format_action_required(
    view: dict[str, Any],
    compiled: CompiledGame,
    player_id: str,
    state: GameState | None,
) -> list[str]:
    """Build ACTION REQUIRED lines from pending deals/votes needing this player."""
    lines: list[str] = []
    if not state:
        return lines

    # Pending deals requiring response
    for iid, pd in state.pending_deals.items():
        if player_id in pd.responders and pd.responders[player_id] is None:
            deal = compiled.deals.get(pd.deal_id)
            opts = deal.response_options if deal else ()
            lines.append(
                f"- Respond to **{pd.deal_id}** from {pd.proposer} "
                f"(instance: {iid})"
            )
            if deal and deal.outcomes:
                for opt in opts:
                    odef = deal.outcomes.get(opt)
                    desc = odef.doc if odef and odef.doc else opt
                    lines.append(f"  - **{opt}**: {desc}")

    # Pending votes needing this player's vote
    for iid, pv in state.pending_votes.items():
        if player_id in pv.eligible and player_id not in pv.votes:
            cast = len(pv.votes)
            total = len(pv.eligible)
            subject_str = f" on **{pv.subject}**" if pv.subject else ""
            lines.append(
                f"- **vote_{pv.vote_id}**{subject_str} "
                f"(instance: {iid}, options: {', '.join(pv.options)}) "
                f"[{cast}/{total} voted]"
            )

    return lines


# ---------------------------------------------------------------------------
# format_available_actions — categorized, with usage limits and progress
# ---------------------------------------------------------------------------


def format_available_actions(
    state: GameState, compiled: CompiledGame, player_id: str
) -> str:
    """List available actions for the player, categorized with usage info."""
    phase_def = _get_phase_def(compiled, state.phase)

    if not phase_def:
        return "## Available Actions\nNo phase active."

    if phase_def.automatic:
        return (
            f"## Available Actions (Phase: {state.phase})\n"
            "This is an automatic phase. Use `advance_phase` to proceed."
        )

    lines = [f"## Available Actions (Phase: {state.phase})"]

    # Context line — key game variables that affect available actions
    context_parts = _build_context_line(state, compiled, phase_def)
    if context_parts:
        lines.append(f"Context: {' | '.join(context_parts)}")

    # Phase hint — strategic summary from ContextConfig
    ctx = compiled.context
    if ctx.phase_hints:
        ph = ctx.phase_hints.get(state.phase)
        if ph:
            urgency_prefix = "! " if ph.urgency == "critical" else ""
            if ph.summary:
                lines.append(f"*{urgency_prefix}{ph.summary}*")
            for tip in ph.tips:
                lines.append(f"  Tip: {tip}")

    # Categorize allowed actions
    deal_entries: list[tuple[int, str]] = []  # (priority, line)
    votes_section: list[str] = []
    responses_section: list[str] = []
    pending_votes_section: list[str] = []

    # Phase-allowed deals, votes, and speech acts
    speech_acts_section: list[str] = []

    for action_id in phase_def.allows:
        deal = compiled.deals.get(action_id)
        if deal:
            if not _can_player_use_deal(state, compiled, action_id, player_id):
                continue
            desc = deal.doc or action_id
            usage_str = _format_usage_limit(state, action_id, player_id, deal)
            param_hints = _deal_param_hints(deal)
            entry = f"- **{action_id}**: {desc}"
            if param_hints:
                entry += f" ({param_hints})"
            if usage_str:
                entry += f" {usage_str}"
            # Outcome preview for bilateral deals (skip single "ok" outcomes)
            if deal.outcomes and list(deal.outcomes.keys()) != ["ok"]:
                entry += f"\n  Outcomes: {outcome_summary(deal.outcomes)}"
            priority = ctx.deal_priorities.get(action_id, 50)
            deal_entries.append((priority, entry))
            continue

        vote = compiled.votes.get(action_id)
        if vote:
            desc = vote.doc or f"Vote: {action_id}"
            opts = ", ".join(vote.options) if vote.options else "dynamic"
            votes_section.append(f"- **vote_{action_id}**: {desc} (options: {opts})")
            continue

        sa = compiled.speech_acts.get(action_id)
        if sa and can_player_use_speech_act(state, compiled, action_id, player_id):
            desc = sa.doc or action_id
            cost_str = (
                ", ".join(f"{r}: {a}" for r, a in sa.cost.items()) if sa.cost else ""
            )
            entry = f"- **{action_id}**: {desc}"
            if cost_str:
                entry += f" (cost: {cost_str})"
            speech_acts_section.append(entry)

    # Pending deals requiring response (URGENT)
    for iid, pd in state.pending_deals.items():
        if player_id in pd.responders and pd.responders[player_id] is None:
            deal = compiled.deals.get(pd.deal_id)
            opts = deal.response_options if deal else ()
            responses_section.append(
                f"- **respond** to {pd.deal_id} from {pd.proposer} "
                f"(instance: {iid})"
            )
            if deal and deal.outcomes:
                for opt in opts:
                    odef = deal.outcomes.get(opt)
                    desc = odef.doc if odef and odef.doc else opt
                    responses_section.append(f"  - **{opt}**: {desc}")

    # Pending votes
    for iid, pv in state.pending_votes.items():
        if player_id in pv.eligible and player_id not in pv.votes:
            cast = len(pv.votes)
            total = len(pv.eligible)
            pending_votes_section.append(
                f"- **vote_{pv.vote_id}** "
                f"(instance: {iid}, options: {', '.join(pv.options)}) "
                f"[{cast}/{total} voted]"
            )

    # Build output with categories
    if responses_section:
        lines.append("\n### Responses (URGENT)")
        lines.extend(responses_section)

    if pending_votes_section:
        lines.append("\n### Pending Votes")
        lines.extend(pending_votes_section)

    if deal_entries:
        deal_entries.sort(key=lambda x: -x[0])
        lines.append("\n### Deals")
        lines.extend(entry for _, entry in deal_entries)

    if votes_section:
        lines.append("\n### Votes")
        lines.extend(votes_section)

    if speech_acts_section:
        lines.append("\n### Speech Acts")
        lines.extend(speech_acts_section)

    # Advance readiness
    readiness = _compute_advance_readiness(state, compiled, player_id)
    lines.append(f"\n### Phase Control")
    if readiness == "BLOCKED":
        blockers = _advance_blockers(state)
        if blockers:
            lines.append("Cannot advance — pending actions:")
            lines.extend(f"  - {b}" for b in blockers)
        else:
            lines.append("Cannot advance: pending actions must be resolved first.")
    elif readiness == "READY":
        lines.append("No more actions available. Use `advance_phase` to proceed.")
    else:
        lines.append("You can still take actions, or use `advance_phase` to proceed.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# format_deal_result — with resource deltas
# ---------------------------------------------------------------------------


def format_deal_result(
    result: dict[str, Any],
    state_before: GameState | None = None,
    state_after: GameState | None = None,
    player_id: str | None = None,
) -> str:
    """Format deal execution result, optionally showing resource deltas."""
    if not result["ok"]:
        err = result["error"]
        msg = err.get("message", "Unknown error")
        detail = err.get("detail", "")
        if detail:
            msg += f"\n{detail}"
        # Add contextual tips for common errors
        code = err.get("code", "")
        if code == "usage_limit":
            msg += "\nTip: try a different action or advance_phase."
        elif code == "insufficient_resources":
            msg += "\nTip: check your resources with get_status."
        elif code == "guard_failed":
            msg += "\nTip: this action's precondition is not met. Use `available_actions` to see what you CAN do."
        elif code == "deal_not_allowed":
            msg += "\nTip: this action is not available in the current phase."
        return f"Error: {msg}"

    parts = ["Action executed successfully."]
    if "outcome" in result:
        parts.append(f"Outcome: {result['outcome']}")
    if "instance_id" in result:
        parts.append(f"Pending deal: {result['instance_id']} (awaiting response)")

    # Resource deltas
    if state_before and state_after and player_id:
        delta = _format_resource_delta(state_before, state_after, player_id)
        if delta:
            parts.append(delta)

    if "victory" in result:
        parts.append(_format_victory(result["victory"]))
    return "\n".join(parts)


def format_vote_result(
    result: dict[str, Any],
    state_before: GameState | None = None,
    state_after: GameState | None = None,
    player_id: str | None = None,
    was_start: bool | None = None,
) -> str:
    """Format vote result with optional resource deltas."""
    if not result["ok"]:
        return f"Error: {result['error'].get('message', 'Unknown error')}"

    if was_start is True:
        parts = ["Vote **started** and your vote cast."]
    elif was_start is False:
        parts = ["Vote **cast** on existing vote."]
    else:
        parts = ["Vote cast successfully."]
    if result.get("auto_completed"):
        parts.append("All votes are in — vote completed automatically.")
        if "tally" in result:
            parts.append(f"Tally: {result['tally']}")
        if "outcome" in result:
            parts.append(f"Outcome: {result['outcome']}")

    # Resource deltas
    if state_before and state_after and player_id:
        delta = _format_resource_delta(state_before, state_after, player_id)
        if delta:
            parts.append(delta)

    if result.get("auto_advanced"):
        parts.append("Phase auto-advanced.")

    if result.get("victory"):
        parts.append(_format_victory(result["victory"]))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# format_history (unchanged)
# ---------------------------------------------------------------------------


def format_history(state: GameState, limit: int = 10) -> str:
    """Format recent history events."""
    entries = state.history[-limit:]
    if not entries:
        return "No events yet."

    lines = ["## Recent Events"]
    for entry in reversed(entries):
        data_str = ", ".join(f"{k}={v}" for k, v in entry.data.items())
        lines.append(f"- [{entry.type}] {data_str}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def get_phase_def(compiled: CompiledGame, phase_id: str):
    """Return the PhaseDef for *phase_id*, or None."""
    for p in compiled.phases:
        if p.id == phase_id:
            return p
    return None


_get_phase_def = get_phase_def  # backward compat


def _advance_blockers(state: GameState) -> list[str]:
    """List specific actions blocking phase advance."""
    blockers: list[str] = []
    for iid, pd in state.pending_deals.items():
        waiting = [r for r, v in pd.responders.items() if v is None]
        if waiting:
            blockers.append(
                f"pending {pd.deal_id} ({iid}) — waiting on: {', '.join(waiting)}"
            )
    for iid, pv in state.pending_votes.items():
        not_voted = [v for v in pv.eligible if v not in pv.votes]
        if not_voted:
            blockers.append(
                f"pending vote {pv.vote_id} ({iid}) — {len(not_voted)} haven't voted"
            )
    return blockers


def build_context_line(
    state: GameState, compiled: CompiledGame, phase_def: Any
) -> list[str]:
    """Build context from VarHints (data-driven) with fallback for unannotated games."""
    ctx = compiled.context
    parts: list[str] = []

    if ctx.var_hints:
        # Data-driven: use VarHints sorted by priority
        hints = sorted(ctx.var_hints.values(), key=lambda h: -h.priority)
        for hint in hints:
            if hint.phases and (not phase_def or phase_def.id not in hint.phases):
                continue
            val = state.vars_.get(hint.id)
            if val is None:
                continue
            label = hint.label or hint.id
            if hint.format == "progress" and hint.max_var:
                max_val = state.vars_.get(hint.max_var, "?")
                parts.append(f"{label} {int(val)}/{int(max_val)}")
            elif hint.format == "currency":
                parts.append(f"{label}: {int(val)}")
            elif hint.format == "player":
                if val:
                    parts.append(f"{label}: {val}")
            elif hint.format == "table" and isinstance(val, dict):
                items = " ".join(f"{k}={v}" for k, v in val.items())
                parts.append(f"{label}: {items}")
            elif hint.format == "list" and isinstance(val, (list, tuple)):
                # Show last 3 items
                shown = val[-3:] if len(val) > 3 else val
                parts.append(f"{label}: {', '.join(str(x) for x in shown)}")
            else:
                parts.append(f"{label}: {val}")
    else:
        # Fallback: show round + non-underscore vars
        if state.round > 1:
            parts.append(f"Round {state.round}")
        for k, v in state.vars_.items():
            if not k.startswith("_") and v is not None and v != "" and v != 0:
                parts.append(f"{k}: {v}")

    return parts


_build_context_line = build_context_line  # backward compat


def can_player_use_deal(
    state: GameState, compiled: CompiledGame, deal_id: str, player_id: str
) -> bool:
    """Check if player can use a deal (actor filter + guard + usage limits)."""
    deal = compiled.deals.get(deal_id)
    if not deal:
        return False

    # Check usage limits first (cheap)
    if _is_usage_exhausted(state, deal_id, player_id, deal):
        return False

    try:
        classification = classify_parties(deal.parties)
        initiator_key = classification.initiator

        bindings = {"actor": player_id}
        if initiator_key:
            bindings[initiator_key] = player_id

        ctx = Context(state=state, compiled=compiled, bindings=bindings)

        # Check initiator party filter
        if initiator_key and initiator_key in deal.parties:
            party_def = deal.parties[initiator_key]
            if party_def.filter is not None:
                if not evaluate(party_def.filter, ctx):
                    return False

        # Check deal guard
        if deal.guard is not None and not evaluate(deal.guard, ctx):
            return False
    except Exception:
        log.warning("can_player_use_deal: filter eval failed for %s/%s", deal_id, player_id, exc_info=True)
        return False

    return True


_can_player_use_deal = can_player_use_deal  # backward compat


def is_usage_exhausted(
    state: GameState, action_id: str, player_id: str, action_def: Any
) -> bool:
    """Check if all usage limits are exhausted for this action."""
    key = f"{player_id}:{action_id}"
    usage = state.usage.get(key, {})

    if (
        action_def.per_round is not None
        and usage.get(f"round:{state.round}", 0) >= action_def.per_round
    ):
        return True
    if (
        action_def.per_phase is not None
        and usage.get(f"phase:{state.phase}", 0) >= action_def.per_phase
    ):
        return True
    if action_def.per_game is not None and usage.get("game", 0) >= action_def.per_game:
        return True
    return False


_is_usage_exhausted = is_usage_exhausted  # backward compat


def format_usage_limit(
    state: GameState, action_id: str, player_id: str, deal_def: Any
) -> str:
    """Format usage limit info like '[1/2 this round]' or '[EXHAUSTED: 2/2 round]'."""
    key = f"{player_id}:{action_id}"
    usage = state.usage.get(key, {})
    parts = []
    any_exhausted = False

    if deal_def.per_round is not None:
        used = usage.get(f"round:{state.round}", 0)
        if used >= deal_def.per_round:
            any_exhausted = True
        parts.append(f"{used}/{deal_def.per_round} round")
    if deal_def.per_phase is not None:
        used = usage.get(f"phase:{state.phase}", 0)
        if used >= deal_def.per_phase:
            any_exhausted = True
        parts.append(f"{used}/{deal_def.per_phase} phase")
    if deal_def.per_game is not None:
        used = usage.get("game", 0)
        if used >= deal_def.per_game:
            any_exhausted = True
        parts.append(f"{used}/{deal_def.per_game} game")

    if not parts:
        return ""
    prefix = "EXHAUSTED: " if any_exhausted else ""
    return f"[{prefix}{', '.join(parts)}]"


_format_usage_limit = format_usage_limit  # backward compat


def _deal_param_hints(deal_def: Any) -> str:
    """Build concise param description from deal parties and params with types."""
    hints = []
    for name, party in deal_def.parties.items():
        if name in ("proposer", "actor"):
            continue
        hints.append(f"{name}: player")
    if deal_def.params:
        for pname, pdef in deal_def.params.items():
            ptype = getattr(pdef, "type", "any")
            pmin = getattr(pdef, "min", None)
            pmax = getattr(pdef, "max", None)
            label = getattr(pdef, "label", "") or pname
            if pmin is not None and pmax is not None:
                hints.append(f"{label}: {ptype} {pmin}-{pmax}")
            elif pmin is not None:
                hints.append(f"{label}: {ptype} min {pmin}")
            else:
                hints.append(f"{label}: {ptype}")
    return ", ".join(hints)


def compute_advance_readiness(
    state: GameState, compiled: CompiledGame, player_id: str
) -> str:
    """Return 'BLOCKED', 'READY', or 'OPTIONAL'."""
    # Blocked if pending responses or votes exist for anyone
    if state.pending_deals or state.pending_votes:
        return "BLOCKED"

    # Check if any player has usable deals left in this phase
    # (accounts for both usage limits AND guards)
    phase_def = _get_phase_def(compiled, state.phase)
    if not phase_def:
        return "READY"

    for eid in state.get_active_entity_ids():
        for action_id in phase_def.allows:
            deal = compiled.deals.get(action_id)
            if deal and _can_player_use_deal(state, compiled, action_id, eid):
                return "OPTIONAL"
            vote = compiled.votes.get(action_id)
            if vote:
                return "OPTIONAL"

    return "READY"


_compute_advance_readiness = compute_advance_readiness  # backward compat


def can_player_use_speech_act(
    state: GameState, compiled: CompiledGame, sa_id: str, player_id: str
) -> bool:
    """Check if player can perform a speech act (actor filter + usage + cost)."""
    sa = compiled.speech_acts.get(sa_id)
    if not sa:
        return False

    # Check usage limits
    key = f"{player_id}:{sa_id}"
    usage = state.usage.get(key, {})
    if (
        sa.per_round is not None
        and usage.get(f"round:{state.round}", 0) >= sa.per_round
    ):
        return False
    if (
        sa.per_phase is not None
        and usage.get(f"phase:{state.phase}", 0) >= sa.per_phase
    ):
        return False
    if sa.per_game is not None and usage.get("game", 0) >= sa.per_game:
        return False

    # Actor filter
    if sa.actor_filter is not None:
        try:
            ctx = Context(state=state, compiled=compiled, bindings={"actor": player_id})
            if not evaluate(sa.actor_filter, ctx):
                return False
        except Exception:
            log.warning("can_player_use_speech_act: filter eval failed for %s/%s", sa_id, player_id, exc_info=True)
            return False

    # Cost check
    for resource, amount in sa.cost.items():
        if state.get_resource(player_id, resource) < amount:
            return False

    return True


def can_player_start_vote(
    state: GameState, compiled: CompiledGame, vote_id: str, player_id: str
) -> bool:
    """Check if player can initiate a vote (proposer filter)."""
    vote = compiled.votes.get(vote_id)
    if not vote:
        return False

    # Proposer filter
    if vote.proposer_filter is not None:
        try:
            ctx = Context(state=state, compiled=compiled, bindings={"actor": player_id})
            if not evaluate(vote.proposer_filter, ctx):
                return False
        except Exception:
            log.warning("can_player_start_vote: filter eval failed for %s/%s", vote_id, player_id, exc_info=True)
            return False

    return True


def _format_resource_delta(before: GameState, after: GameState, player_id: str) -> str:
    """Show resource changes: 'gold: 100 -> 90 (-10)'."""
    e_before = before.entities.get(player_id)
    e_after = after.entities.get(player_id)
    if not e_before or not e_after:
        return ""

    changes = []
    all_keys = set(e_before.resources) | set(e_after.resources)
    for key in sorted(all_keys):
        v_before = e_before.resources.get(key, 0)
        v_after = e_after.resources.get(key, 0)
        if v_before != v_after:
            diff = v_after - v_before
            sign = "+" if diff > 0 else ""
            changes.append(f"{key}: {v_before} -> {v_after} ({sign}{diff})")

    return f"Changes: {', '.join(changes)}" if changes else ""


def _format_victory(victory: dict[str, Any]) -> str:
    """Format victory result consistently."""
    if victory.get("type") == "distribution" and victory.get("scores"):
        sorted_scores = sorted(victory["scores"].items(), key=lambda x: -x[1])
        top = ", ".join(f"{p}: {s:.0f}" for p, s in sorted_scores[:3])
        return f"GAME OVER: {victory.get('condition', '?')} — Top scores: {top}"
    return (
        f"GAME OVER: {victory.get('winner', '?')} wins! "
        f"({victory.get('condition', '?')})"
    )
