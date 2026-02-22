"""Evaluation pipeline — tiered strategy testing via real LLM games.

Three tiers:
- Quick:    3 games vs random bots (BotRunner fills slots)
- Standard: 10 games vs archetype strategies (LLM agents)
- Deep:     30 games round-robin vs archetypes (LLM agents)

Each tier runs games through the Arena and aggregates results into
an EvalResult with win rate, matchup matrix, resource efficiency,
and phase activity metrics.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import attrs

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Evaluation tiers
# ---------------------------------------------------------------------------


@attrs.frozen
class EvalTier:
    """Configuration for an evaluation tier."""

    name: str
    num_games: int
    opponents: str  # "random" or "archetypes"


QUICK = EvalTier(name="quick", num_games=3, opponents="random")
STANDARD = EvalTier(name="standard", num_games=10, opponents="archetypes")
DEEP = EvalTier(name="deep", num_games=30, opponents="archetypes")


# ---------------------------------------------------------------------------
# Evaluation result
# ---------------------------------------------------------------------------


@attrs.frozen
class EvalResult:
    """Complete evaluation result for a strategy."""

    strategy_id: str
    game_id: str
    tier: str

    # Win/loss aggregates
    games_played: int
    wins: int
    losses: int
    draws: int
    win_rate: float

    # Per-opponent breakdown
    matchups: dict[str, dict[str, int]]  # opponent_sid -> {wins, losses, draws}

    # Resource efficiency: avg final resources / avg starting resources
    resource_efficiency: dict[str, float]

    # Phase activity: avg actions per game per phase
    phase_activity: dict[str, float]

    # Timing
    elapsed_seconds: float
    timestamp: float = attrs.Factory(time.time)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def evaluate(
    strategy_id: str,
    tier: EvalTier = STANDARD,
    *,
    provider_type: str = "anthropic",
    model: str = "",
    api_key: str = "",
    phase_timeout: int = 120,
    game_timeout: int = 600,
) -> EvalResult:
    """Run a full strategy evaluation at the specified tier.

    Args:
        strategy_id: ID of the strategy to evaluate (from StrategyStore)
        tier: evaluation tier (QUICK, STANDARD, or DEEP)
        provider_type: LLM provider ("anthropic" or "ollama")
        model: model override (default from env)
        api_key: API key for the provider
        phase_timeout: seconds per game phase
        game_timeout: max seconds per game

    Returns:
        EvalResult with aggregated metrics.
    """
    from strategy.archetypes import get_archetypes
    from strategy.arena import Arena
    from strategy.schema import Strategy
    from strategy.store import StrategyStore

    store = StrategyStore()
    strategy = store.load(strategy_id)
    if not strategy:
        raise ValueError(f"Strategy '{strategy_id}' not found")
    if not strategy.game_id:
        raise ValueError(f"Strategy '{strategy_id}' has no game_id")

    return await evaluate_strategy(
        strategy,
        tier=tier,
        provider_type=provider_type,
        model=model,
        api_key=api_key,
        phase_timeout=phase_timeout,
        game_timeout=game_timeout,
    )


async def evaluate_strategy(
    strategy: Any,  # Strategy (avoid circular import at type level)
    tier: EvalTier = STANDARD,
    *,
    provider_type: str = "anthropic",
    model: str = "",
    api_key: str = "",
    phase_timeout: int = 120,
    game_timeout: int = 600,
) -> EvalResult:
    """Evaluate a Strategy object directly (no store lookup).

    Use this for programmatic evaluation or testing.
    """
    from strategy.archetypes import get_archetypes
    from strategy.arena import Arena

    # Build opponent list
    if tier.opponents == "archetypes":
        opponents = get_archetypes(strategy.game_id)
        if not opponents:
            log.warning("No archetypes for %s, falling back to random", strategy.game_id)
            opponents = []
    else:
        opponents = []

    # Build list of all strategies (target + opponents)
    all_strategies = [strategy] + opponents

    arena = Arena(
        provider_type=provider_type,
        model=model,
        api_key=api_key,
    )

    start_time = time.monotonic()
    report = await arena.run_with_strategies(
        strategy.game_id,
        all_strategies,
        num_games=tier.num_games,
        phase_timeout=phase_timeout,
        game_timeout=game_timeout,
    )
    elapsed = time.monotonic() - start_time

    # Extract per-opponent matchup breakdown
    matchups: dict[str, dict[str, int]] = {}
    for key, h2h in report.head_to_head.items():
        if strategy.id in key:
            for opponent_id in report.config.strategy_ids:
                if opponent_id != strategy.id and opponent_id in key:
                    matchups[opponent_id] = {
                        "wins": h2h.get(strategy.id, 0),
                        "losses": h2h.get(opponent_id, 0),
                        "draws": h2h.get("draws", 0),
                    }

    # Compute resource efficiency from game results
    resource_efficiency = _compute_resource_efficiency(strategy.id, report)

    # Compute phase activity from decision counts (approximation)
    phase_activity = _compute_phase_activity(report)

    # Get strategy stats from report
    stats = report.per_strategy.get(strategy.id, {})

    return EvalResult(
        strategy_id=strategy.id,
        game_id=strategy.game_id,
        tier=tier.name,
        games_played=stats.get("games", 0),
        wins=stats.get("wins", 0),
        losses=stats.get("losses", 0),
        draws=stats.get("draws", 0),
        win_rate=stats.get("win_rate", 0.0),
        matchups=matchups,
        resource_efficiency=resource_efficiency,
        phase_activity=phase_activity,
        elapsed_seconds=elapsed,
    )


# ---------------------------------------------------------------------------
# Metric helpers
# ---------------------------------------------------------------------------


def _compute_resource_efficiency(
    strategy_id: str, report: Any
) -> dict[str, float]:
    """Compute average final resources for the evaluated strategy.

    Returns {resource_name: avg_final_amount} across all games
    where this strategy participated.
    """
    resource_totals: dict[str, float] = {}
    resource_counts: dict[str, int] = {}

    for result in report.results:
        # Find player_id for this strategy
        pid = None
        for p, sid in result.strategy_map.items():
            if sid == strategy_id:
                pid = p
                break
        if not pid:
            continue

        resources = result.final_resources.get(pid, {})
        for rname, amount in resources.items():
            resource_totals[rname] = resource_totals.get(rname, 0) + amount
            resource_counts[rname] = resource_counts.get(rname, 0) + 1

    return {
        rname: resource_totals[rname] / resource_counts[rname]
        for rname in resource_totals
        if resource_counts.get(rname, 0) > 0
    }


def _compute_phase_activity(report: Any) -> dict[str, float]:
    """Compute average decisions per game (rough measure of activity).

    Without per-phase breakdown in GameResult, we return total activity.
    """
    if not report.results:
        return {}
    total = sum(r.decisions_count for r in report.results)
    return {"total_decisions_per_game": total / len(report.results)}
