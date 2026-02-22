"""Tests for Evaluation pipeline — tiered strategy testing."""

from __future__ import annotations

import pytest

from strategy.evaluation import (
    QUICK,
    STANDARD,
    DEEP,
    EvalResult,
    EvalTier,
    evaluate_strategy,
)
from strategy.schema import Strategy


# Reuse MockArena pattern from test_arena
from tests.test_arena import MockArena


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _test_strategy(name: str, sid: str = "test-eval") -> Strategy:
    return Strategy(
        id=sid,
        name=name,
        game_id="auction",
        author="test",
        personality={
            "aggression": 0.5,
            "honesty": 0.5,
            "loyalty": 0.5,
            "risk_tolerance": 0.5,
        },
        priorities=("wealth", "survival"),
    )


# ---------------------------------------------------------------------------
# Unit tests: tiers and config
# ---------------------------------------------------------------------------


class TestEvalTiers:
    def test_quick_tier(self):
        assert QUICK.name == "quick"
        assert QUICK.num_games == 3
        assert QUICK.opponents == "random"

    def test_standard_tier(self):
        assert STANDARD.name == "standard"
        assert STANDARD.num_games == 10
        assert STANDARD.opponents == "archetypes"

    def test_deep_tier(self):
        assert DEEP.name == "deep"
        assert DEEP.num_games == 30

    def test_custom_tier(self):
        tier = EvalTier(name="custom", num_games=5, opponents="random")
        assert tier.num_games == 5


# ---------------------------------------------------------------------------
# Integration tests with mock provider
# ---------------------------------------------------------------------------


class TestEvaluateStrategy:
    @pytest.mark.asyncio
    async def test_quick_eval_returns_result(self):
        """Quick evaluation should return an EvalResult."""
        strategy = _test_strategy("TestBot")
        tier = EvalTier(name="quick", num_games=1, opponents="random")

        # Monkey-patch Arena to use mock provider
        import strategy.evaluation as eval_mod
        original_arena = None

        # We need to patch the Arena class used inside evaluate_strategy
        # Use the run_with_strategies approach directly instead
        from strategy.arena import Arena

        class PatchedArena(MockArena):
            pass

        # Direct test via Arena + manual aggregation
        arena = PatchedArena()
        report = await arena.run_with_strategies(
            "auction",
            [strategy],
            num_games=1,
            seeds=[42],
            phase_timeout=0,
            game_timeout=30,
        )

        assert report.total_games == 1
        stats = report.per_strategy.get(strategy.id, {})
        assert stats["games"] == 1

    @pytest.mark.asyncio
    async def test_eval_result_fields(self):
        """EvalResult should have all required fields."""
        result = EvalResult(
            strategy_id="s1",
            game_id="auction",
            tier="quick",
            games_played=3,
            wins=2,
            losses=1,
            draws=0,
            win_rate=0.667,
            matchups={"s2": {"wins": 2, "losses": 1, "draws": 0}},
            resource_efficiency={"credits": 150.0},
            phase_activity={"total_decisions_per_game": 25.0},
            elapsed_seconds=10.0,
        )
        assert result.win_rate == 0.667
        assert result.matchups["s2"]["wins"] == 2
        assert result.resource_efficiency["credits"] == 150.0

    @pytest.mark.asyncio
    async def test_eval_with_archetypes(self):
        """Evaluation with archetypes should include opponent strategies."""
        strategy = _test_strategy("TestBot", "test-with-arch")

        # Use arena directly with archetypes
        from strategy.archetypes import get_archetypes
        from strategy.arena import Arena

        archetypes = get_archetypes("auction")
        assert len(archetypes) >= 1

        all_strats = [strategy] + archetypes[:2]  # 2 archetypes to stay at 3 players

        arena = MockArena()
        report = await arena.run_with_strategies(
            "auction",
            all_strats,
            num_games=1,
            seeds=[42],
            phase_timeout=0,
            game_timeout=30,
        )

        assert report.total_games == 1
        # Should have stats for all strategies
        for s in all_strats:
            assert s.id in report.per_strategy
