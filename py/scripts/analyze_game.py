#!/usr/bin/env python3
"""Game schema analyzer — validates structure, detects design issues, generates reports.

Usage:
    uv run python scripts/analyze_game.py                # all games
    uv run python scripts/analyze_game.py mafia          # single game
    uv run python scripts/analyze_game.py --json         # machine-readable
    uv run python scripts/analyze_game.py --simulate 100 # run N random games
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from typing import Any

# Ensure py/ is on path
sys.path.insert(0, ".")

from engine.expr.core import Expr
from engine.runtime.core import GameRuntime
from engine.runtime.state import (
    CompiledGame,
    DealDef,
    OutcomeDef,
    PhaseDef,
    SpeechActDef,
    VoteDef,
)
from games import REGISTRY

# ---------------------------------------------------------------------------
# Schema extraction
# ---------------------------------------------------------------------------


def extract_schema(compiled: CompiledGame) -> dict[str, Any]:
    """Extract a complete structural schema from a compiled game."""
    return {
        "id": compiled.id,
        "name": compiled.name,
        "players": {"min": compiled.min_players, "max": compiled.max_players},
        "resources": _resources(compiled),
        "attrs": _attrs(compiled),
        "roles": _roles(compiled),
        "groups": _groups(compiled),
        "channels": _channels(compiled),
        "deals": _deals(compiled),
        "votes": _votes(compiled),
        "speech_acts": _speech_acts(compiled),
        "commitments": _commitments(compiled),
        "phases": _phases(compiled),
        "victories": _victories(compiled),
        "counts": _counts(compiled),
    }


def _resources(c: CompiledGame) -> list[dict]:
    return [
        {
            "id": r.id,
            "initial": r.initial,
            "visibility": r.visibility.value
            if hasattr(r.visibility, "value")
            else str(r.visibility),
            "bounds": list(r.bounds) if r.bounds else [None, None],
        }
        for r in c.resources.values()
    ]


def _attrs(c: CompiledGame) -> list[dict]:
    return [
        {
            "id": a.id,
            "visibility": a.visibility.value
            if hasattr(a.visibility, "value")
            else str(a.visibility),
            "initial": _safe_repr(a.initial),
        }
        for a in c.attrs_defs.values()
    ]


def _roles(c: CompiledGame) -> list[dict]:
    return [
        {
            "id": r.id,
            "team": r.team,
            "filler": r.filler,
            "count": r.count,
            "min_players": r.min_players,
        }
        for r in c.roles.values()
    ]


def _groups(c: CompiledGame) -> list[dict]:
    return [
        {
            "id": g.id,
            "visible": g.visible,
            "exclusive": g.exclusive,
            "knows_members": g.knows_members,
        }
        for g in c.group_types.values()
    ]


def _channels(c: CompiledGame) -> list[dict]:
    return [
        {"id": ch.id, "type": ch.type, "group": ch.group or None}
        for ch in c.channels.values()
    ]


def _deals(c: CompiledGame) -> list[dict]:
    result = []
    for d in c.deals.values():
        # Classify deal type from structure
        has_responses = bool(d.response_options)
        # Check for multilateral by looking for responders with count spec
        is_multilateral = any(
            hasattr(p, "count") and p.count is not None
            for p in d.parties.values()
            if hasattr(p, "count")
        )
        if not has_responses:
            deal_type = "immediate"
        elif is_multilateral:
            deal_type = "multilateral"
        else:
            deal_type = "bilateral"
        result.append(
            {
                "id": d.id,
                "type": deal_type,
                "parties": list(d.parties.keys()),
                "responses": list(d.response_options) if d.response_options else [],
                "outcomes": list(d.outcomes.keys()) if d.outcomes else [],
                "params": list(d.params.keys()) if d.params else [],
                "stakes": _stakes_summary(d.stakes),
                "per_round": d.per_round,
                "per_phase": d.per_phase,
                "per_game": d.per_game,
                "has_guard": d.guard is not None,
                "doc": d.doc[:80] if d.doc else "",
            }
        )
    return result


def _votes(c: CompiledGame) -> list[dict]:
    return [
        {
            "id": v.id,
            "options": list(v.options),
            "threshold": v.threshold,
            "visibility": v.visibility,
            "has_subject": v.subject is not None,
            "outcomes": list(v.outcomes.keys()),
            "doc": v.doc[:80] if v.doc else "",
        }
        for v in c.votes.values()
    ]


def _speech_acts(c: CompiledGame) -> list[dict]:
    return [
        {
            "id": sa.id,
            "act_type": sa.act_type,
            "cost": dict(sa.cost) if sa.cost else {},
            "verify_triggers": list(sa.verify_triggers),
            "per_round": sa.per_round,
            "per_game": sa.per_game,
            "endorsable": sa.endorsable,
            "doc": sa.doc[:80] if sa.doc else "",
        }
        for sa in c.speech_acts.values()
    ]


def _commitments(c: CompiledGame) -> list[dict]:
    return [
        {
            "id": cm.id,
            "trigger": cm.trigger,
            "once": cm.once,
            "has_guard": cm.guard is not None,
            "doc": cm.doc[:80] if cm.doc else "",
        }
        for cm in c.commitments.values()
    ]


def _phases(c: CompiledGame) -> list[dict]:
    return [
        {
            "id": p.id,
            "category": p.category,
            "automatic": p.automatic,
            "once": p.once,
            "allows": list(p.allows),
            "channels": list(p.channels),
            "has_when": p.when is not None,
            "has_effects": len(p.effects) > 0,
            "duration": p.duration if hasattr(p, "duration") else None,
        }
        for p in c.phases
    ]


def _victories(c: CompiledGame) -> list[dict]:
    return [
        {
            "id": v.id,
            "type": v.type,
            "priority": v.priority,
            "team": v.team,
            "has_score": v.score is not None,
            "message": v.message[:60] if v.message else "",
        }
        for v in c.victories
    ]


def _counts(c: CompiledGame) -> dict[str, int]:
    return {
        "resources": len(c.resources),
        "attrs": len(c.attrs_defs),
        "roles": len(c.roles),
        "groups": len(c.group_types),
        "channels": len(c.channels),
        "deals": len(c.deals),
        "votes": len(c.votes),
        "speech_acts": len(c.speech_acts),
        "commitments": len(c.commitments),
        "phases": len(c.phases),
        "victories": len(c.victories),
        "total_primitives": (
            len(c.resources)
            + len(c.attrs_defs)
            + len(c.roles)
            + len(c.group_types)
            + len(c.channels)
            + len(c.deals)
            + len(c.votes)
            + len(c.speech_acts)
            + len(c.commitments)
            + len(c.phases)
            + len(c.victories)
        ),
    }


def _stakes_summary(stakes: dict) -> dict:
    if not stakes:
        return {}
    result = {}
    for party, entries in stakes.items():
        result[party] = [(r, _safe_repr(a)) for r, a in entries]
    return result


def _safe_repr(v: Any) -> Any:
    if isinstance(v, Expr):
        return str(v)
    return v


# ---------------------------------------------------------------------------
# Validation checks
# ---------------------------------------------------------------------------


def validate_game(compiled: CompiledGame) -> list[dict[str, Any]]:
    """Run structural validation checks. Returns list of issues."""
    issues = []

    def issue(severity: str, category: str, msg: str):
        issues.append({"severity": severity, "category": category, "message": msg})

    # 1. Phase allows — check all referenced deals/votes/speech_acts exist
    all_action_ids = (
        set(compiled.deals.keys())
        | set(compiled.votes.keys())
        | set(compiled.speech_acts.keys())
    )
    for phase in compiled.phases:
        for action_id in phase.allows:
            if action_id not in all_action_ids:
                issue(
                    "error",
                    "phase_allows",
                    f"Phase '{phase.id}' allows unknown action '{action_id}'",
                )

    # 2. Orphan actions — deals/votes not in any phase.allows
    allowed_anywhere = set()
    for phase in compiled.phases:
        allowed_anywhere.update(phase.allows)
    for deal_id in compiled.deals:
        if deal_id not in allowed_anywhere:
            issue(
                "warning", "orphan_deal", f"Deal '{deal_id}' not allowed in any phase"
            )
    for vote_id in compiled.votes:
        if vote_id not in allowed_anywhere:
            issue(
                "warning", "orphan_vote", f"Vote '{vote_id}' not allowed in any phase"
            )
    for sa_id in compiled.speech_acts:
        if sa_id not in allowed_anywhere:
            issue(
                "warning",
                "orphan_speech_act",
                f"Speech act '{sa_id}' not allowed in any phase",
            )

    # 3. Votes with subject-targeting effects but no subject definition
    for vote_id, vote in compiled.votes.items():
        has_subject_effect = False
        for outcome in vote.outcomes.values():
            for eff in outcome.effects:
                eff_str = str(eff)
                if "subject" in eff_str.lower():
                    has_subject_effect = True
                    break
        if has_subject_effect and vote.subject is None:
            issue(
                "error",
                "vote_subject",
                f"Vote '{vote_id}' uses 'subject' in effects but has no subject definition",
            )

    # 4. Resources referenced in stakes must exist
    for deal_id, deal in compiled.deals.items():
        for party, entries in (deal.stakes or {}).items():
            for resource, _amount in entries:
                if isinstance(resource, str) and resource not in compiled.resources:
                    issue(
                        "error",
                        "unknown_resource",
                        f"Deal '{deal_id}' stakes unknown resource '{resource}'",
                    )

    # 5. Phase cycle — must have at least one non-setup, non-automatic phase
    interactive_phases = [
        p for p in compiled.phases if not p.automatic and p.category != "setup"
    ]
    if not interactive_phases:
        issue("error", "no_interactive_phase", "Game has no interactive phases")

    # 6. Victory conditions — must have at least one
    if not compiled.victories:
        issue("error", "no_victory", "Game has no victory conditions")

    # 7. Channels referenced in phases must exist
    for phase in compiled.phases:
        for ch_id in phase.channels:
            if ch_id not in compiled.channels:
                issue(
                    "warning",
                    "unknown_channel",
                    f"Phase '{phase.id}' references unknown channel '{ch_id}'",
                )

    # 8. Deal cost check — stakes without guard on resource availability
    # (informational — the runtime checks this, but good to flag)

    # 9. Phase flow — setup must be first
    if compiled.phases and compiled.phases[0].category != "setup":
        issue("warning", "setup_not_first", "First phase is not a setup phase")

    # 10. Speech act promise_action must reference a real deal or vote
    for sa_id, sa in compiled.speech_acts.items():
        if sa.promise_action:
            if (
                sa.promise_action not in compiled.deals
                and sa.promise_action not in compiled.votes
            ):
                issue(
                    "error",
                    "promise_action",
                    f"Speech act '{sa_id}' promise_action '{sa.promise_action}' not found",
                )

    return issues


# ---------------------------------------------------------------------------
# Game theory analysis
# ---------------------------------------------------------------------------


def analyze_game_theory(compiled: CompiledGame) -> dict[str, Any]:
    """Analyze game theory properties."""
    analysis = {}

    # Information structure
    from engine.runtime.state import Visibility

    private_resources = [
        r.id for r in compiled.resources.values() if r.visibility == Visibility.PRIVATE
    ]
    hidden_resources = [
        r.id for r in compiled.resources.values() if r.visibility == Visibility.HIDDEN
    ]
    private_attrs = [
        a.id for a in compiled.attrs_defs.values() if a.visibility == Visibility.PRIVATE
    ]
    hidden_attrs = [
        a.id for a in compiled.attrs_defs.values() if a.visibility == Visibility.HIDDEN
    ]

    analysis["information_asymmetry"] = {
        "private_resources": private_resources,
        "hidden_resources": hidden_resources,
        "private_attrs": private_attrs,
        "hidden_attrs": hidden_attrs,
        "score": len(private_resources)
        + len(hidden_resources) * 2
        + len(private_attrs)
        + len(hidden_attrs) * 2,
    }

    # Commitment devices
    commitment_count = len(compiled.commitments)
    bilateral_deals = sum(1 for d in compiled.deals.values() if d.response_options)
    staked_deals = sum(1 for d in compiled.deals.values() if d.stakes)
    analysis["commitment_depth"] = {
        "commitments": commitment_count,
        "bilateral_deals": bilateral_deals,
        "staked_deals": staked_deals,
        "score": commitment_count * 3 + bilateral_deals * 2 + staked_deals,
    }

    # Communication richness
    public_channels = sum(1 for c in compiled.channels.values() if c.type == "public")
    private_channels = sum(
        1 for c in compiled.channels.values() if c.type in ("private", "group")
    )
    analysis["communication"] = {
        "public_channels": public_channels,
        "private_channels": private_channels,
        "total_channels": len(compiled.channels),
        "speech_acts": len(compiled.speech_acts),
        "score": public_channels + private_channels * 2 + len(compiled.speech_acts) * 2,
    }

    # Decision space (how many meaningful choices per phase)
    max_actions_per_phase = max(
        (len(p.allows) for p in compiled.phases if not p.automatic), default=0
    )
    total_outcomes = sum(
        len(d.outcomes) for d in compiled.deals.values() if d.outcomes
    ) + sum(len(v.outcomes) for v in compiled.votes.values())
    analysis["decision_space"] = {
        "max_actions_per_phase": max_actions_per_phase,
        "total_deal_outcomes": total_outcomes,
        "total_params": sum(len(d.params) for d in compiled.deals.values()),
        "score": max_actions_per_phase * 2 + total_outcomes,
    }

    # Victory diversity
    dist_victories = sum(1 for v in compiled.victories if v.type == "distribution")
    team_victories = sum(1 for v in compiled.victories if v.type == "single")
    analysis["victory_structure"] = {
        "distribution": dist_victories,
        "team_based": team_victories,
        "total": len(compiled.victories),
        "type": "distribution"
        if dist_victories > team_victories
        else "elimination"
        if team_victories > 0
        else "none",
    }

    # Expose/betray mechanics (signals for social deduction depth)
    expose_count = 0
    for d in compiled.deals.values():
        if d.response_options and "expose" in d.response_options:
            expose_count += 1
    analysis["betrayal_mechanics"] = {
        "expose_deals": expose_count,
        "hidden_teams": len(
            [
                r
                for r in compiled.roles.values()
                if r.team
                and any(
                    a.visibility == "private"
                    for a in compiled.attrs_defs.values()
                    if a.id == "team"
                )
            ]
        ),
    }

    # Overall complexity score
    analysis["complexity_score"] = (
        analysis["information_asymmetry"]["score"]
        + analysis["commitment_depth"]["score"]
        + analysis["communication"]["score"]
        + analysis["decision_space"]["score"]
    )

    return analysis


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------


def simulate_game(compiled: CompiledGame, n_games: int = 10) -> dict[str, Any]:
    """Run N random simulations with actual random play.

    Each simulation: start game → for each interactive phase, pick random
    valid deals/votes → advance → repeat until victory or step limit.
    """
    import random
    import traceback

    runtime = GameRuntime(compiled)
    results = {
        "games_run": 0,
        "games_completed": 0,
        "games_stalled": 0,
        "victories": defaultdict(int),
        "avg_rounds": 0,
        "avg_decisions": 0,
        "deals_tried": defaultdict(int),
        "deals_succeeded": defaultdict(int),
        "votes_completed": defaultdict(int),
        "errors": [],
    }

    total_rounds = 0
    total_decisions = 0

    for i in range(n_games):
        seed = random.randint(1, 999999)
        rng = random.Random(seed)
        n_players = rng.randint(
            compiled.min_players, min(compiled.max_players, compiled.min_players + 4)
        )
        player_ids = [f"p{j}" for j in range(n_players)]

        try:
            state = runtime.start_game(player_ids, seed)
            state = runtime.run_setup(state)

            max_steps = 300
            steps = 0
            while state.status != "ended" and steps < max_steps:
                phase = runtime._current_phase(state)
                if not phase:
                    break

                # Check victory before acting
                victory = runtime.check_victory(state)
                if victory:
                    state = runtime.end_game(state, victory)
                    break

                if phase.automatic:
                    state = state.record_decision({"type": "advance_phase"})
                    state = runtime.advance_phase(state)
                    steps += 1
                    continue

                # Interactive phase: try random actions
                alive = state.get_active_entity_ids()
                if not alive:
                    break

                acted = False
                actions_this_phase = rng.randint(1, min(3, len(alive)))

                for _ in range(actions_this_phase):
                    # Try a random deal or vote from phase.allows
                    if not phase.allows:
                        break
                    action_id = rng.choice(list(phase.allows))

                    deal = compiled.deals.get(action_id)
                    vote = compiled.votes.get(action_id)
                    sa = compiled.speech_acts.get(action_id)

                    if deal:
                        state = _try_random_deal(
                            runtime, state, deal, alive, rng, results
                        )
                        acted = True
                    elif vote:
                        state = _try_random_vote(
                            runtime, state, vote, alive, rng, results
                        )
                        acted = True
                    elif sa:
                        state = _try_random_speech_act(runtime, state, sa, alive, rng)
                        acted = True

                # Advance phase
                state = state.record_decision({"type": "advance_phase"})
                state = runtime.advance_phase(state)

                # Post-advance victory check
                victory = runtime.check_victory(state)
                if victory:
                    state = runtime.end_game(state, victory)
                    break

                steps += 1

            results["games_run"] += 1

            if state.status == "ended":
                results["games_completed"] += 1
                v = state.victory_result
                if v:
                    vid = v.get("condition", v.get("type", "unknown"))
                    results["victories"][vid] += 1
                total_rounds += state.round
                total_decisions += len(state.decisions)
            else:
                results["games_stalled"] += 1

        except Exception as e:
            tb = traceback.format_exc().strip().split("\n")[-3:]
            results["errors"].append(
                f"Game {i} (seed={seed}): {type(e).__name__}: {e}\n    {'    '.join(tb)}"
            )
            results["games_run"] += 1

    if results["games_completed"] > 0:
        results["avg_rounds"] = round(total_rounds / results["games_completed"], 1)
        results["avg_decisions"] = round(
            total_decisions / results["games_completed"], 1
        )

    results["victories"] = dict(results["victories"])
    results["deals_tried"] = dict(results["deals_tried"])
    results["deals_succeeded"] = dict(results["deals_succeeded"])
    results["votes_completed"] = dict(results["votes_completed"])
    return results


def _try_random_deal(
    runtime: GameRuntime,
    state: GameState,
    deal: DealDef,
    alive: list[str],
    rng: "random.Random",
    results: dict,
) -> GameState:
    """Try to execute a random deal. Returns state (possibly unchanged on failure)."""
    import random

    results["deals_tried"][deal.id] = results["deals_tried"].get(deal.id, 0) + 1

    parties = deal.parties
    actor_id = None
    target_id = None
    responder_id = None
    responder_ids = None

    # Pick actor/proposer
    actor_key = (
        "actor" if "actor" in parties else "proposer" if "proposer" in parties else None
    )
    if actor_key and alive:
        actor_id = rng.choice(alive)

    # Pick target/responder
    others = [p for p in alive if p != actor_id] if actor_id else list(alive)
    if "target" in parties and others:
        target_id = rng.choice(others)
    if "responder" in parties and others:
        responder_id = rng.choice(others)
    if "responders" in parties and others:
        party_def = parties["responders"]
        if party_def.count:
            lo, hi = party_def.count
            n = rng.randint(lo, min(hi, len(others)))
        else:
            n = min(2, len(others))
        responder_ids = rng.sample(others, min(n, len(others)))

    # Generate random params
    params = _random_params(deal.params, rng, state, actor_id)

    result = runtime.start_deal(
        state,
        deal.id,
        actor_id=actor_id,
        target_id=target_id,
        responder_id=responder_id,
        responder_ids=responder_ids,
        params=params,
    )

    if not result.get("ok"):
        return state

    state = result["state"]
    results["deals_succeeded"][deal.id] = results["deals_succeeded"].get(deal.id, 0) + 1

    # Auto-respond to pending deals
    if deal.response_options:
        for pending in list(state.pending_deals.values()):
            if pending.deal_id != deal.id:
                continue
            for rid, resp in pending.responders.items():
                if resp is None:  # awaiting response
                    response = rng.choice(list(deal.response_options))
                    r = runtime.respond_to_deal(
                        state, pending.instance_id, rid, response
                    )
                    if r.get("ok"):
                        state = r["state"]

    return state


def _try_random_vote(
    runtime: GameRuntime,
    state: GameState,
    vote: VoteDef,
    alive: list[str],
    rng: "random.Random",
    results: dict,
) -> GameState:
    """Try to start and complete a random vote."""
    # Pick subject if needed
    subject_id = None
    if vote.subject:
        subject_id = rng.choice(alive) if alive else None

    # Pick proposer
    proposer_id = rng.choice(alive) if alive else None

    result = runtime.start_vote(
        state, vote.id, proposer_id=proposer_id, subject_id=subject_id
    )
    if not result.get("ok"):
        return state
    state = result["state"]
    instance_id = result.get("instance_id")
    if not instance_id:
        return state

    # All eligible voters cast random votes
    pending = state.pending_votes.get(instance_id)
    if pending:
        for voter_id in pending.eligible:
            option = rng.choice(list(pending.options))
            r = runtime.cast_vote(state, instance_id, voter_id, option)
            if r.get("ok"):
                state = r["state"]

    # Complete vote if still pending
    if instance_id in state.pending_votes:
        r = runtime.complete_vote(state, instance_id)
        if r.get("ok"):
            state = r["state"]
            results["votes_completed"][vote.id] = (
                results["votes_completed"].get(vote.id, 0) + 1
            )

    return state


def _try_random_speech_act(
    runtime: GameRuntime,
    state: GameState,
    sa: SpeechActDef,
    alive: list[str],
    rng: "random.Random",
) -> GameState:
    """Try a random speech act."""
    actor = rng.choice(alive) if alive else None
    if not actor:
        return state

    target = None
    if sa.target_filter is not None:
        others = [p for p in alive if p != actor]
        target = rng.choice(others) if others else None

    params = _random_params(sa.params, rng, state, actor) if sa.params else {}

    result = runtime.execute_speech_act(
        state, sa.id, actor, target_id=target, params=params
    )
    if result.get("ok"):
        return result["state"]
    return state


def _random_params(
    param_defs: dict, rng: "random.Random", state: GameState, actor_id: str | None
) -> dict[str, Any]:
    """Generate random valid params for a deal/speech act."""
    params = {}
    if not param_defs:
        return params

    alive = state.get_active_entity_ids()

    for pid, pdef in param_defs.items():
        if pdef.type == "number":
            lo = pdef.min if pdef.min is not None else 1
            hi = pdef.max if pdef.max is not None else 10
            params[pid] = rng.randint(int(lo), int(hi))
        elif pdef.type == "player":
            others = [p for p in alive if p != actor_id]
            if others:
                params[pid] = rng.choice(others)
        elif pdef.type == "keyword" and pdef.options:
            params[pid] = rng.choice(list(pdef.options))
        elif pdef.type == "string":
            params[pid] = f"random_{rng.randint(0, 999)}"
        elif pdef.type == "players":
            others = [p for p in alive if p != actor_id]
            n = rng.randint(1, min(3, len(others))) if others else 0
            params[pid] = rng.sample(others, n) if others else []
        elif pdef.options:
            params[pid] = rng.choice(list(pdef.options))
        else:
            params[pid] = (
                pdef.default
                if hasattr(pdef, "default") and pdef.default is not None
                else "default"
            )

    return params


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------


def print_report(game_id: str, compiled: CompiledGame, simulate_n: int = 0):
    """Print a human-readable game report."""
    schema = extract_schema(compiled)
    issues = validate_game(compiled)
    theory = analyze_game_theory(compiled)

    counts = schema["counts"]

    print(f"\n{'=' * 70}")
    print(f"  {schema['name']} ({schema['id']})")
    print(f"  Players: {schema['players']['min']}-{schema['players']['max']}")
    print(f"{'=' * 70}")

    # Counts summary
    print(f"\n  Primitives: {counts['total_primitives']}")
    print(
        f"    Resources: {counts['resources']}  Attrs: {counts['attrs']}  Roles: {counts['roles']}"
    )
    print(f"    Groups: {counts['groups']}  Channels: {counts['channels']}")
    print(
        f"    Deals: {counts['deals']}  Votes: {counts['votes']}  Speech Acts: {counts['speech_acts']}"
    )
    print(
        f"    Commitments: {counts['commitments']}  Phases: {counts['phases']}  Victories: {counts['victories']}"
    )

    # Phase flow
    print(f"\n  Phase Flow:")
    for p in schema["phases"]:
        marker = "A" if p["automatic"] else "I"
        once = " (once)" if p["once"] else ""
        guard = " [guarded]" if p["has_when"] else ""
        allows = f" → {', '.join(p['allows'])}" if p["allows"] else ""
        print(f"    [{marker}] {p['id']}{once}{guard}{allows}")

    # Game theory
    print(f"\n  Game Theory Profile:")
    print(f"    Information asymmetry: {theory['information_asymmetry']['score']}")
    print(f"    Commitment depth:      {theory['commitment_depth']['score']}")
    print(f"    Communication:         {theory['communication']['score']}")
    print(f"    Decision space:        {theory['decision_space']['score']}")
    print(f"    Victory type:          {theory['victory_structure']['type']}")
    print(f"    Complexity score:      {theory['complexity_score']}")

    # Issues
    errors = [i for i in issues if i["severity"] == "error"]
    warnings = [i for i in issues if i["severity"] == "warning"]
    if errors:
        print(f"\n  ERRORS ({len(errors)}):")
        for e in errors:
            print(f"    [{e['category']}] {e['message']}")
    if warnings:
        print(f"\n  Warnings ({len(warnings)}):")
        for w in warnings:
            print(f"    [{w['category']}] {w['message']}")
    if not errors and not warnings:
        print(f"\n  Validation: CLEAN")

    # Simulation
    if simulate_n > 0:
        print(f"\n  Smoke Test ({simulate_n} random games):")
        sim = simulate_game(compiled, simulate_n)
        print(f"    Completed: {sim['games_completed']}/{sim['games_run']}")
        print(f"    Stalled:   {sim['games_stalled']}")
        print(f"    Avg rounds:    {sim['avg_rounds']}")
        print(f"    Avg decisions: {sim['avg_decisions']}")
        if sim["victories"]:
            print(f"    Victory distribution:")
            for vid, count in sorted(sim["victories"].items(), key=lambda x: -x[1]):
                pct = count / max(sim["games_completed"], 1) * 100
                print(f"      {vid}: {count} ({pct:.0f}%)")
        if sim["deals_tried"]:
            print(f"    Deal success rates:")
            for did in sorted(sim["deals_tried"]):
                tried = sim["deals_tried"][did]
                ok = sim["deals_succeeded"].get(did, 0)
                pct = ok / tried * 100 if tried else 0
                print(f"      {did}: {ok}/{tried} ({pct:.0f}%)")
        if sim["votes_completed"]:
            print(f"    Votes completed:")
            for vid, count in sorted(sim["votes_completed"].items()):
                print(f"      {vid}: {count}")
        if sim["errors"]:
            print(f"    ERRORS ({len(sim['errors'])}):")
            for err in sim["errors"][:5]:
                print(f"      {err}")

    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Analyze game schemas")
    parser.add_argument("game", nargs="?", help="Game ID (or 'all')")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument(
        "--simulate", type=int, default=0, help="Run N simulations per game"
    )
    parser.add_argument(
        "--validate-only", action="store_true", help="Only show validation issues"
    )
    args = parser.parse_args()

    if args.game and args.game != "all":
        games = {args.game: REGISTRY[args.game]}
    else:
        games = REGISTRY

    if args.json:
        output = {}
        for gid, compiled in games.items():
            output[gid] = {
                "schema": extract_schema(compiled),
                "issues": validate_game(compiled),
                "theory": analyze_game_theory(compiled),
            }
            if args.simulate > 0:
                output[gid]["simulation"] = simulate_game(compiled, args.simulate)
        print(json.dumps(output, indent=2, default=str))
    elif args.validate_only:
        any_issues = False
        for gid, compiled in games.items():
            issues = validate_game(compiled)
            if issues:
                any_issues = True
                print(f"\n{gid}:")
                for i in issues:
                    print(f"  [{i['severity']}] {i['message']}")
        if not any_issues:
            print("All games: CLEAN")
    else:
        for gid, compiled in games.items():
            print_report(gid, compiled, args.simulate)


if __name__ == "__main__":
    main()
