"""Feedback — analyze evaluation results and suggest strategy improvements.

Heuristic analysis that produces actionable suggestions for improving
an LLM agent's strategy (system prompt). No LLM calls — instant results.

Dimensions analyzed:
1. Win rate and trend
2. Worst matchups (which opponents beat us?)
3. Resource efficiency (ending with low resources?)
4. Phase activity (are we using all phases?)
5. Personality distance to winning archetypes
"""

from __future__ import annotations

import math
from typing import Any

import attrs

from strategy.schema import PERSONALITY_AXES, Strategy

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@attrs.frozen
class Suggestion:
    """A specific, actionable suggestion for strategy improvement."""

    category: str  # "personality", "phase_tactic", "deal_rule", "priority"
    field: str  # e.g. "aggression", "bidding", "open_market"
    current: str  # what it is now
    suggested: str  # what to change it to
    reason: str  # why this change would help
    confidence: str  # "high", "medium", "low"


@attrs.frozen
class FeedbackReport:
    """Analysis of strategy performance with improvement suggestions."""

    strategy_id: str
    game_id: str
    based_on: int  # number of games analyzed
    win_rate: float

    strengths: tuple[str, ...]
    weaknesses: tuple[str, ...]
    suggestions: tuple[Suggestion, ...]

    closest_archetype: str | None
    archetype_distance: float  # 0 = identical, 1 = very different


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def analyze(
    strategy: Strategy,
    eval_result: Any,  # EvalResult (avoid circular import)
    compiled: Any | None = None,  # CompiledGame
) -> FeedbackReport:
    """Analyze strategy performance and generate improvement suggestions.

    Args:
        strategy: the Strategy being evaluated
        eval_result: EvalResult from the evaluation pipeline
        compiled: optional CompiledGame for phase/deal analysis

    Returns:
        FeedbackReport with strengths, weaknesses, and actionable suggestions.
    """
    suggestions: list[Suggestion] = []
    strengths: list[str] = []
    weaknesses: list[str] = []

    # 1. Win rate analysis
    _analyze_win_rate(eval_result, strengths, weaknesses, suggestions)

    # 2. Matchup analysis
    _analyze_matchups(strategy, eval_result, suggestions)

    # 3. Resource efficiency
    _analyze_resources(eval_result, suggestions)

    # 4. Phase activity
    _analyze_phase_activity(strategy, eval_result, compiled, suggestions, weaknesses)

    # 5. Personality distance to archetypes
    closest, distance = _find_closest_archetype(strategy)

    if closest and distance < 0.15:
        strengths.append(f"Personality closely matches '{closest}' archetype")
    elif closest and distance > 0.6:
        suggestions.append(Suggestion(
            category="personality",
            field="archetype_alignment",
            current=f"distance={distance:.2f}",
            suggested=f"Consider starting from '{closest}' archetype as a base",
            reason="Strategy personality is far from any proven archetype",
            confidence="low",
        ))

    # 6. Check for empty strategy fields
    _check_empty_fields(strategy, compiled, suggestions)

    return FeedbackReport(
        strategy_id=strategy.id,
        game_id=strategy.game_id,
        based_on=eval_result.games_played,
        win_rate=eval_result.win_rate,
        strengths=tuple(strengths),
        weaknesses=tuple(weaknesses),
        suggestions=tuple(suggestions),
        closest_archetype=closest,
        archetype_distance=distance,
    )


# ---------------------------------------------------------------------------
# Analysis dimensions
# ---------------------------------------------------------------------------


def _analyze_win_rate(
    eval_result: Any,
    strengths: list[str],
    weaknesses: list[str],
    suggestions: list[Suggestion],
) -> None:
    """Analyze overall win rate."""
    wr = eval_result.win_rate

    if wr >= 0.6:
        strengths.append(f"Strong win rate ({wr:.0%})")
    elif wr >= 0.4:
        strengths.append(f"Competitive win rate ({wr:.0%})")
    elif wr >= 0.2:
        weaknesses.append(f"Below-average win rate ({wr:.0%})")
        suggestions.append(Suggestion(
            category="priority",
            field="overall",
            current=f"win_rate={wr:.0%}",
            suggested="Consider adjusting priorities or switching archetype",
            reason=f"Win rate of {wr:.0%} suggests the overall approach needs revision",
            confidence="medium",
        ))
    else:
        weaknesses.append(f"Very low win rate ({wr:.0%})")
        suggestions.append(Suggestion(
            category="priority",
            field="overall",
            current=f"win_rate={wr:.0%}",
            suggested="Try a proven archetype as starting point and customize from there",
            reason=f"Win rate of {wr:.0%} indicates fundamental strategy issues",
            confidence="high",
        ))


def _analyze_matchups(
    strategy: Strategy,
    eval_result: Any,
    suggestions: list[Suggestion],
) -> None:
    """Find worst matchup and suggest counter-tactics."""
    matchups = eval_result.matchups
    if not matchups:
        return

    worst_opponent = None
    worst_loss_rate = 0.0

    for opp_id, record in matchups.items():
        total = record.get("wins", 0) + record.get("losses", 0) + record.get("draws", 0)
        if total == 0:
            continue
        loss_rate = record.get("losses", 0) / total
        if loss_rate > worst_loss_rate:
            worst_loss_rate = loss_rate
            worst_opponent = opp_id

    if worst_opponent and worst_loss_rate > 0.6:
        record = matchups[worst_opponent]
        suggestions.append(Suggestion(
            category="deal_rule",
            field=f"vs_{worst_opponent}",
            current=f"W:{record.get('wins', 0)} L:{record.get('losses', 0)}",
            suggested=f"Study '{worst_opponent}' strategy and add counter-tactics",
            reason=f"Losing {worst_loss_rate:.0%} of games against this opponent",
            confidence="high",
        ))


def _analyze_resources(
    eval_result: Any,
    suggestions: list[Suggestion],
) -> None:
    """Analyze resource efficiency from final game states."""
    efficiency = eval_result.resource_efficiency
    if not efficiency:
        return

    for resource, avg_final in efficiency.items():
        if resource in ("credits", "gold", "money", "coins"):
            if avg_final < 20:
                suggestions.append(Suggestion(
                    category="deal_rule",
                    field=resource,
                    current=f"avg_final={avg_final:.0f}",
                    suggested=f"Add deal rules to conserve {resource}",
                    reason=f"Ending games with very low {resource} ({avg_final:.0f} avg)",
                    confidence="medium",
                ))


def _analyze_phase_activity(
    strategy: Strategy,
    eval_result: Any,
    compiled: Any | None,
    suggestions: list[Suggestion],
    weaknesses: list[str],
) -> None:
    """Check if strategy has tactics for all non-automatic phases."""
    if not compiled:
        return

    for phase in compiled.phases:
        if phase.automatic or phase.id == "setup":
            continue
        if phase.id not in strategy.phase_tactics or not strategy.phase_tactics[phase.id]:
            suggestions.append(Suggestion(
                category="phase_tactic",
                field=phase.id,
                current="(empty)",
                suggested=f"Add specific tactical guidance for the '{phase.id}' phase",
                reason="Missing phase tactics reduce LLM agent effectiveness",
                confidence="medium",
            ))


def _check_empty_fields(
    strategy: Strategy,
    compiled: Any | None,
    suggestions: list[Suggestion],
) -> None:
    """Check for important empty strategy fields."""
    if not strategy.persona:
        suggestions.append(Suggestion(
            category="personality",
            field="persona",
            current="(empty)",
            suggested="Add a persona description to give the LLM agent a consistent character",
            reason="Empty persona means the LLM has no character guidance",
            confidence="high",
        ))

    if not strategy.deal_rules and compiled:
        deal_count = len([d for d in compiled.deals if not getattr(compiled.deals[d], 'automatic', False)])
        if deal_count > 0:
            suggestions.append(Suggestion(
                category="deal_rule",
                field="general",
                current="(no deal rules)",
                suggested=f"Add rules for at least the most important deals ({deal_count} available)",
                reason="Without deal rules the LLM agent must guess how to handle each deal",
                confidence="high",
            ))


# ---------------------------------------------------------------------------
# Archetype distance
# ---------------------------------------------------------------------------


def _find_closest_archetype(strategy: Strategy) -> tuple[str | None, float]:
    """Find which archetype is most similar to this strategy."""
    from strategy.archetypes import get_archetypes

    templates = get_archetypes(strategy.game_id)
    if not templates:
        return None, 1.0

    best_name: str | None = None
    best_dist = float("inf")

    for t in templates:
        dist = _personality_distance(strategy.personality, t.personality)
        if dist < best_dist:
            best_dist = dist
            best_name = t.name

    return best_name, min(best_dist, 1.0)


def _personality_distance(a: dict[str, float], b: dict[str, float]) -> float:
    """Euclidean distance between personality vectors, normalized to [0, 1]."""
    axes = list(PERSONALITY_AXES)
    if not axes:
        return 0.0
    sq_sum = sum((a.get(ax, 0.5) - b.get(ax, 0.5)) ** 2 for ax in axes)
    return math.sqrt(sq_sum / len(axes))
