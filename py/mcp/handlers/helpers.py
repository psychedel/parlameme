"""AI helper tools — game summary, role guidance, game rules.

Higher-level game understanding for agents beyond raw state dumps.
Available in-game only (universal tools).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from engine.runtime.state import view_for
from mcp.schema import Tool

if TYPE_CHECKING:
    from mcp.agents import AgentState

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS: list[Tool] = [
    Tool(
        name="game_summary",
        description="Concise game summary: phase, round, alive, pending actions, your role/team",
        _meta={"type": "query"},
    ),
    Tool(
        name="role_guidance",
        description="Strategic guidance for your role: win condition, abilities, tips",
        _meta={"type": "query"},
    ),
    Tool(
        name="game_rules",
        description="Full game rules: resources, phases, deals, votes, speech acts, commitments, victory conditions",
        _meta={"type": "query"},
    ),
    Tool(
        name="deal_mechanics",
        description="Deep mechanical breakdown of any deal, vote, or speech act — shows all outcomes, effects, stakes, guards",
        inputSchema={
            "type": "object",
            "properties": {
                "action_id": {
                    "type": "string",
                    "description": "Deal, vote, or speech act ID",
                }
            },
            "required": ["action_id"],
        },
        _meta={"type": "query"},
    ),
]

# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def handle_game_summary(server: Any, agent: AgentState, args: dict) -> dict:
    if agent.state != "in_game":
        return _error("Not in a game.")

    session = server._get_session(agent.session_id)
    if not session:
        return _error("Session not found.")

    state = session.state
    compiled = session.compiled
    pid = agent.player_id
    view = view_for(state, pid, compiled)

    me = view["entities"].get(pid, {})
    alive = sum(1 for e in state.entities.values() if e.active)
    total = len(state.entities)

    # Pending actions for this player
    pending = []
    for pd in state.pending_deals.values():
        if pid in pd.responders and pd.responders[pid] is None:
            pending.append(f"respond to {pd.deal_id}")
    for pv in state.pending_votes.values():
        if pid in pv.eligible and pid not in pv.votes:
            pending.append(f"vote in {pv.vote_id}")
    pending_str = ", ".join(pending) if pending else "none"

    # Role/team from visible attrs
    role = me.get("attrs", {}).get("role", "unknown")
    team = me.get("attrs", {}).get("team") or me.get("attrs", {}).get("faction", "")

    lines = [
        f"{compiled.name} — Round {state.round}, Phase: {state.phase}",
        f"Players: {alive}/{total} active | Status: {state.status}",
        f"You: {pid} | Role: {role}" + (f" | Team: {team}" if team else ""),
        f"Pending: {pending_str}",
    ]

    # Game summary from context annotations
    ctx = compiled.context
    if ctx.game_summary:
        lines.insert(1, ctx.game_summary)
    if ctx.score_explanation:
        lines.append(f"Scoring: {ctx.score_explanation}")

    # Key resources
    if me.get("resources"):
        res = ", ".join(f"{k}: {v}" for k, v in me["resources"].items())
        lines.append(f"Resources: {res}")

    return _content("\n".join(lines))


async def handle_role_guidance(server: Any, agent: AgentState, args: dict) -> dict:
    if agent.state != "in_game":
        return _error("Not in a game.")

    session = server._get_session(agent.session_id)
    if not session:
        return _error("Session not found.")

    compiled = session.compiled
    state = session.state
    pid = agent.player_id
    entity = state.entities.get(pid)
    if not entity:
        return _error("Your entity not found.")

    role_id = entity.get_attr("role")
    role_def = compiled.roles.get(role_id) if role_id else None

    # Data-driven guidance from ContextConfig
    ctx = compiled.context
    role_hint = ctx.role_hints.get(role_id) if role_id else None

    if role_id:
        lines = [f"## Role Guidance: {role_id}"]
    else:
        lines = [f"## Strategy Guidance: {compiled.name}"]

    if role_hint:
        if role_hint.strategy:
            lines.append(f"\n**Strategy:** {role_hint.strategy}")
        if role_hint.allies:
            lines.append(f"**Allies:** {', '.join(role_hint.allies)}")
        if role_hint.threats:
            lines.append(f"**Threats:** {', '.join(role_hint.threats)}")
        if role_hint.key_actions:
            lines.append(f"**Key actions:** {', '.join(role_hint.key_actions)}")
        phase_tip = role_hint.phase_tips.get(state.phase)
        if phase_tip:
            lines.append(f"\n**Now ({state.phase}):** {phase_tip}")
    elif role_def:
        if role_def.doc:
            lines.append(role_def.doc)
    elif not role_id:
        # No roles in this game — show game-level guidance
        if ctx.game_summary:
            lines.append(f"\n{ctx.game_summary}")
        if ctx.score_explanation:
            lines.append(f"**Scoring:** {ctx.score_explanation}")
        phase_hint = ctx.phase_hints.get(state.phase)
        if phase_hint:
            if phase_hint.summary:
                lines.append(f"\n**Now ({state.phase}):** {phase_hint.summary}")
            if phase_hint.tips:
                for tip in phase_hint.tips:
                    lines.append(f"  - {tip}")

    if role_def:
        lines.append(f"Team: {role_def.team}")
        if role_def.appears_as:
            lines.append(f"Appears as: {role_def.appears_as} (deception)")

    # Victory conditions relevant to this player
    lines.append("\n### Win Conditions")
    for v in compiled.victories:
        if v.team and role_def and v.team != role_def.team:
            continue  # skip victory conditions for other teams
        lines.append(f"- **{v.id}**: {v.message or v.type}")

    # Deals available to this role
    phase_def = None
    for p in compiled.phases:
        if p.id == state.phase:
            phase_def = p
            break

    if phase_def:
        from mcp.formatters import _can_player_use_deal

        lines.append(f"\n### Your Actions (in {state.phase})")
        for did in phase_def.allows:
            deal = compiled.deals.get(did)
            if deal:
                if not _can_player_use_deal(state, compiled, did, pid):
                    continue
                lines.append(f"- **{did}**: {deal.doc or 'deal'}")
            vote = compiled.votes.get(did)
            if vote:
                lines.append(f"- **vote_{did}**: {vote.doc or 'vote'}")

    # Groups this player belongs to
    if entity.groups:
        lines.append(f"\nGroups: {', '.join(sorted(entity.groups))}")

    return _content("\n".join(lines))


async def handle_game_rules(server: Any, agent: AgentState, args: dict) -> dict:
    if agent.state != "in_game":
        return _error("Not in a game.")

    session = server._get_session(agent.session_id)
    if not session:
        return _error("Session not found.")

    compiled = session.compiled
    lines = [f"## Rules: {compiled.name}"]
    lines.append(f"Players: {compiled.min_players}-{compiled.max_players}")

    # Resources
    if compiled.resources:
        lines.append("\n### Resources")
        for rid, rdef in compiled.resources.items():
            vis = rdef.visibility.value
            bounds = ""
            if rdef.bounds != (None, None):
                bounds = f" (bounds: {rdef.bounds[0]}-{rdef.bounds[1]})"
            lines.append(f"- **{rid}**: initial={rdef.initial}, {vis}{bounds}")

    # Roles
    if compiled.roles:
        lines.append("\n### Roles")
        for rid, rdef in compiled.roles.items():
            desc = rdef.doc or ""
            lines.append(f"- **{rid}** (team: {rdef.team}): {desc}")

    # Phases
    if compiled.phases:
        lines.append("\n### Phases")
        for p in compiled.phases:
            auto = " [auto]" if p.automatic else ""
            allows = ", ".join(p.allows) if p.allows else "none"
            lines.append(f"- **{p.id}**{auto}: allows [{allows}]")

    # Deals (with outcome docs and stakes)
    if compiled.deals:
        from mcp.mechanics import describe_outcome, describe_stakes

        lines.append("\n### Deals")
        for did, deal in compiled.deals.items():
            desc = deal.doc or did
            parties = ", ".join(deal.parties.keys())
            lines.append(f"- **{did}** ({parties}): {desc}")
            if deal.stakes:
                lines.append(f"  Stakes: {describe_stakes(deal.stakes)}")
            usage_parts = []
            if deal.per_round is not None:
                usage_parts.append(f"{deal.per_round}/round")
            if deal.per_phase is not None:
                usage_parts.append(f"{deal.per_phase}/phase")
            if deal.per_game is not None:
                usage_parts.append(f"{deal.per_game}/game")
            if usage_parts:
                lines.append(f"  Limits: {', '.join(usage_parts)}")
            if deal.outcomes:
                for oid, odef in deal.outcomes.items():
                    odoc = describe_outcome(oid, odef)
                    lines.append(f"  {oid}: {odoc}")

    # Votes (with outcome docs)
    if compiled.votes:
        from mcp.mechanics import describe_outcome

        lines.append("\n### Votes")
        for vid, vote in compiled.votes.items():
            desc = vote.doc or vid
            options = ", ".join(vote.options) if vote.options else "dynamic"
            lines.append(f"- **{vid}**: {desc} (options: {options})")
            if vote.outcomes:
                for oid, odef in vote.outcomes.items():
                    if odef.doc:
                        lines.append(f"  {oid}: {odef.doc}")

    # Speech Acts (with verification mechanics)
    if compiled.speech_acts:
        from mcp.mechanics import describe_effects

        lines.append("\n### Speech Acts")
        for sa_id, sa in compiled.speech_acts.items():
            desc = sa.doc or sa_id
            cost_parts = [f"{amt} {res}" for res, amt in sa.cost.items()]
            cost_str = ", ".join(cost_parts) if cost_parts else "free"
            lines.append(f"- **{sa_id}** ({sa.act_type}): {desc}")
            lines.append(f"  Cost: {cost_str}")
            if sa.verify_triggers:
                lines.append(f"  Verified on: {', '.join(sa.verify_triggers)}")
            if sa.verify_true_effects:
                true_desc = describe_effects(sa.verify_true_effects)
                if true_desc:
                    lines.append(
                        f"  If true: {'; '.join(d.strip() for d in true_desc)}"
                    )
            if sa.verify_false_effects:
                false_desc = describe_effects(sa.verify_false_effects)
                if false_desc:
                    lines.append(
                        f"  If false: {'; '.join(d.strip() for d in false_desc)}"
                    )
            if sa.endorsement_cost:
                e_parts = [f"{amt} {res}" for res, amt in sa.endorsement_cost.items()]
                lines.append(f"  Endorsement cost: {', '.join(e_parts)}")
            if sa.promise_deadline is not None:
                lines.append(f"  Promise deadline: {sa.promise_deadline} rounds")
            if sa.inquire_deadline:
                lines.append(f"  Response deadline: {sa.inquire_deadline} phases")
            if sa.inquire_silence_effects:
                silence_desc = describe_effects(sa.inquire_silence_effects)
                if silence_desc:
                    lines.append(
                        f"  Silence penalty: {'; '.join(d.strip() for d in silence_desc)}"
                    )
            usage_parts = []
            if sa.per_round is not None:
                usage_parts.append(f"{sa.per_round}/round")
            if sa.per_phase is not None:
                usage_parts.append(f"{sa.per_phase}/phase")
            if sa.per_game is not None:
                usage_parts.append(f"{sa.per_game}/game")
            if usage_parts:
                lines.append(f"  Limits: {', '.join(usage_parts)}")

    # Commitments (automatic triggers)
    if compiled.commitments:
        lines.append("\n### Commitments (Automatic Triggers)")
        for cid, cdef in compiled.commitments.items():
            freq = "once" if cdef.once else "recurring"
            desc = cdef.doc or cid
            lines.append(f"- **{cid}** (on {cdef.trigger}) [{freq}]: {desc}")

    # Victory conditions
    if compiled.victories:
        lines.append("\n### Victory Conditions")
        for v in compiled.victories:
            team = f" (team: {v.team})" if v.team else ""
            lines.append(f"- **{v.id}**{team}: {v.message or v.type}")

    # Channels (with effects)
    if compiled.channels:
        from mcp.mechanics import describe_effects

        lines.append("\n### Channels")
        for cid, cdef in compiled.channels.items():
            desc = cdef.description or ""
            if cdef.effects:
                eff_desc = describe_effects(cdef.effects)
                if eff_desc:
                    desc += f" [Effect: {'; '.join(d.strip() for d in eff_desc)}]"
            lines.append(f"- **{cid}** ({cdef.type}): {desc}")

    return _content("\n".join(lines))


async def handle_deal_mechanics(server: Any, agent: AgentState, args: dict) -> dict:
    if agent.state != "in_game":
        return _error("Not in a game.")

    session = server._get_session(agent.session_id)
    if not session:
        return _error("Session not found.")

    action_id = args.get("action_id", "")
    if not action_id:
        return _error("action_id is required.")

    compiled = session.compiled

    # Search across deals, votes, and speech acts
    from mcp.mechanics import (
        describe_deal_mechanics,
        describe_speech_act_mechanics,
        describe_vote_mechanics,
    )

    if action_id in compiled.deals:
        text = describe_deal_mechanics(action_id, compiled.deals[action_id])
        return _content(text)

    if action_id in compiled.votes:
        text = describe_vote_mechanics(action_id, compiled.votes[action_id])
        return _content(text)

    if action_id in compiled.speech_acts:
        text = describe_speech_act_mechanics(action_id, compiled.speech_acts[action_id])
        return _content(text)

    # Not found — list available action IDs
    available = (
        sorted(compiled.deals.keys())
        + sorted(compiled.votes.keys())
        + sorted(compiled.speech_acts.keys())
    )
    return _error(f"Action '{action_id}' not found. Available: {', '.join(available)}")


# ---------------------------------------------------------------------------
# Routing table
# ---------------------------------------------------------------------------

HANDLERS: dict[str, Any] = {
    "game_summary": handle_game_summary,
    "role_guidance": handle_role_guidance,
    "game_rules": handle_game_rules,
    "deal_mechanics": handle_deal_mechanics,
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _content(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


def _error(message: str) -> dict:
    return {"content": [{"type": "text", "text": f"Error: {message}"}], "isError": True}
