"""Tests for Arena — multi-agent LLM game orchestrator.

Uses a mock LLM provider that always calls 'act' with no args (observe),
then 'act' with random valid actions. This tests the Arena wiring without
real LLM API calls.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from typing import Any

import pytest

from agent.providers import LLMResponse, ToolCall
from games import REGISTRY
from strategy.arena import Arena, ArenaReport, GameResult, _aggregate, ArenaConfig
from strategy.schema import Strategy


# ---------------------------------------------------------------------------
# Mock LLM provider — makes random valid tool calls
# ---------------------------------------------------------------------------


class MockProvider:
    """LLM provider mock that calls 'act' to observe, then picks actions."""

    def __init__(self, seed: int = 42):
        self._rng = random.Random(seed)
        self._turn = 0

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        system: str = "",
    ) -> LLMResponse:
        self._turn += 1

        # First call: observe (act with no action)
        if self._turn <= 1:
            return LLMResponse(
                content="Let me observe the current state.",
                tool_calls=[ToolCall(id=f"tc-{self._turn}", name="act", args={})],
                stop_reason="tool_use",
                raw={},
            )

        # Subsequent calls: call 'act' with no specific action
        # (let the game auto-respond) or advance_phase
        if self._turn % 3 == 0:
            return LLMResponse(
                content="I'll advance the phase.",
                tool_calls=[
                    ToolCall(id=f"tc-{self._turn}", name="advance_phase", args={})
                ],
                stop_reason="tool_use",
                raw={},
            )

        return LLMResponse(
            content="Let me check the situation.",
            tool_calls=[ToolCall(id=f"tc-{self._turn}", name="act", args={})],
            stop_reason="tool_use",
            raw={},
        )


# ---------------------------------------------------------------------------
# Test strategies
# ---------------------------------------------------------------------------


def _test_strategy(name: str, sid: str | None = None, game_id: str = "auction") -> Strategy:
    """Create a minimal test strategy."""
    return Strategy(
        id=sid or f"test-{name}",
        name=name,
        game_id=game_id,
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
# Arena with mock provider
# ---------------------------------------------------------------------------


class MockArena(Arena):
    """Arena subclass that uses MockProvider instead of real LLM."""

    def __init__(self, seed: int = 42, **kwargs):
        super().__init__(**kwargs)
        self._mock_seed = seed

    def _create_provider(self):
        return MockProvider(seed=self._mock_seed)


# ---------------------------------------------------------------------------
# Tests: aggregation logic (pure, no async)
# ---------------------------------------------------------------------------


class TestAggregation:
    """Test ArenaReport aggregation from GameResults."""

    def test_empty_results(self):
        config = ArenaConfig(
            game_id="auction",
            strategy_ids=("s1", "s2"),
        )
        report = _aggregate(config, (), 0.0)
        assert report.total_games == 0
        assert report.per_strategy["s1"]["games"] == 0
        assert report.per_strategy["s2"]["games"] == 0

    def test_single_game_winner(self):
        config = ArenaConfig(
            game_id="auction",
            strategy_ids=("s1", "s2"),
        )
        result = GameResult(
            game_index=0,
            seed=42,
            winner="s1",
            scores={},
            strategy_map={"agent-0": "s1", "agent-1": "s2"},
            archive_path="",
            decisions_count=10,
            final_resources={},
        )
        report = _aggregate(config, (result,), 1.0)
        assert report.per_strategy["s1"]["wins"] == 1
        assert report.per_strategy["s1"]["losses"] == 0
        assert report.per_strategy["s2"]["wins"] == 0
        assert report.per_strategy["s2"]["losses"] == 1
        assert report.per_strategy["s1"]["win_rate"] == 1.0
        assert report.per_strategy["s2"]["win_rate"] == 0.0

    def test_draw(self):
        config = ArenaConfig(
            game_id="auction",
            strategy_ids=("s1", "s2"),
        )
        result = GameResult(
            game_index=0,
            seed=42,
            winner=None,
            scores={},
            strategy_map={"agent-0": "s1", "agent-1": "s2"},
            archive_path="",
            decisions_count=5,
            final_resources={},
        )
        report = _aggregate(config, (result,), 1.0)
        assert report.per_strategy["s1"]["draws"] == 1
        assert report.per_strategy["s2"]["draws"] == 1

    def test_head_to_head(self):
        config = ArenaConfig(
            game_id="auction",
            strategy_ids=("s1", "s2"),
        )
        results = (
            GameResult(0, 1, "s1", {}, {"a-0": "s1", "a-1": "s2"}, "", 10, {}),
            GameResult(1, 2, "s1", {}, {"a-0": "s1", "a-1": "s2"}, "", 10, {}),
            GameResult(2, 3, "s2", {}, {"a-0": "s1", "a-1": "s2"}, "", 10, {}),
        )
        report = _aggregate(config, results, 3.0)
        key = "s1_vs_s2"
        assert key in report.head_to_head
        assert report.head_to_head[key]["s1"] == 2
        assert report.head_to_head[key]["s2"] == 1

    def test_multiple_strategies(self):
        config = ArenaConfig(
            game_id="auction",
            strategy_ids=("s1", "s2", "s3"),
        )
        result = GameResult(
            game_index=0,
            seed=42,
            winner="s2",
            scores={},
            strategy_map={"a-0": "s1", "a-1": "s2", "a-2": "s3"},
            archive_path="",
            decisions_count=15,
            final_resources={},
        )
        report = _aggregate(config, (result,), 1.0)
        assert report.per_strategy["s2"]["wins"] == 1
        assert report.per_strategy["s1"]["losses"] == 1
        assert report.per_strategy["s3"]["losses"] == 1
        assert report.total_decisions == 15

    def test_total_decisions_sums(self):
        config = ArenaConfig(
            game_id="auction",
            strategy_ids=("s1",),
        )
        results = (
            GameResult(0, 1, "s1", {}, {"a-0": "s1"}, "", 10, {}),
            GameResult(1, 2, "s1", {}, {"a-0": "s1"}, "", 20, {}),
        )
        report = _aggregate(config, results, 2.0)
        assert report.total_decisions == 30
        assert report.elapsed_seconds == 2.0


# ---------------------------------------------------------------------------
# Tests: Arena integration with mock provider
# ---------------------------------------------------------------------------


class TestArenaIntegration:
    """Integration tests using MockProvider (no real LLM calls)."""

    @pytest.mark.asyncio
    async def test_run_with_strategies_returns_report(self):
        """Arena should run a game and return an ArenaReport."""
        strategies = [
            _test_strategy("Alpha", "s-alpha"),
            _test_strategy("Beta", "s-beta"),
            _test_strategy("Gamma", "s-gamma"),
        ]
        arena = MockArena()
        report = await arena.run_with_strategies(
            "auction",
            strategies,
            num_games=1,
            seeds=[42],
            phase_timeout=0,
            game_timeout=30,
        )
        assert isinstance(report, ArenaReport)
        assert report.total_games == 1
        assert len(report.results) == 1
        # All strategies should appear in per_strategy
        for s in strategies:
            assert s.id in report.per_strategy

    @pytest.mark.asyncio
    async def test_strategy_metadata_in_archive(self):
        """Arena should tag archives with strategy metadata."""
        strategies = [
            _test_strategy("Alpha", "s-alpha"),
            _test_strategy("Beta", "s-beta"),
            _test_strategy("Gamma", "s-gamma"),
        ]
        arena = MockArena()
        report = await arena.run_with_strategies(
            "auction",
            strategies,
            num_games=1,
            seeds=[42],
            phase_timeout=0,
            game_timeout=30,
        )
        result = report.results[0]
        # strategy_map should map player IDs to strategy IDs
        assert len(result.strategy_map) == 3
        assert set(result.strategy_map.values()) == {"s-alpha", "s-beta", "s-gamma"}

    @pytest.mark.asyncio
    async def test_position_rotation(self):
        """Strategies should rotate positions across games."""
        strategies = [
            _test_strategy("Alpha", "s-alpha"),
            _test_strategy("Beta", "s-beta"),
            _test_strategy("Gamma", "s-gamma"),
        ]
        arena = MockArena()
        report = await arena.run_with_strategies(
            "auction",
            strategies,
            num_games=3,
            seeds=[1, 2, 3],
            phase_timeout=0,
            game_timeout=30,
        )
        # Check that agent-0 is assigned to different strategies across games
        agent0_strategies = [r.strategy_map.get("agent-0") for r in report.results]
        # With 3 games and 3 strategies, rotation should give each strategy a turn
        assert len(set(agent0_strategies)) >= 2  # at least 2 different assignments

    @pytest.mark.asyncio
    async def test_game_timeout_stops_agents(self):
        """Games should stop when timeout expires."""
        strategies = [
            _test_strategy("Alpha", "s-alpha"),
            _test_strategy("Beta", "s-beta"),
            _test_strategy("Gamma", "s-gamma"),
        ]
        arena = MockArena()
        report = await arena.run_with_strategies(
            "auction",
            strategies,
            num_games=1,
            seeds=[42],
            phase_timeout=0,
            game_timeout=5,  # Short timeout
        )
        assert report.total_games == 1
        # Game should have produced a result (even if incomplete)
        assert len(report.results) == 1

    @pytest.mark.asyncio
    async def test_bot_filler_when_fewer_strategies(self):
        """BotRunner should fill remaining slots when fewer strategies than min_players."""
        # Auction needs 3 players, provide only 2 strategies
        strategies = [
            _test_strategy("Alpha", "s-alpha"),
            _test_strategy("Beta", "s-beta"),
        ]
        arena = MockArena()
        report = await arena.run_with_strategies(
            "auction",
            strategies,
            num_games=1,
            seeds=[42],
            phase_timeout=0,
            game_timeout=30,
        )
        assert report.total_games == 1
        result = report.results[0]
        # Only 2 strategies in map (3rd player is a bot, not in strategy_map)
        assert len(result.strategy_map) == 2

    @pytest.mark.asyncio
    async def test_final_resources_captured(self):
        """Game result should capture final resource state."""
        strategies = [
            _test_strategy("Alpha", "s-alpha"),
            _test_strategy("Beta", "s-beta"),
            _test_strategy("Gamma", "s-gamma"),
        ]
        arena = MockArena()
        report = await arena.run_with_strategies(
            "auction",
            strategies,
            num_games=1,
            seeds=[42],
            phase_timeout=0,
            game_timeout=30,
        )
        result = report.results[0]
        # Should have resources for at least the agents
        assert len(result.final_resources) >= 3

    @pytest.mark.asyncio
    async def test_error_does_not_crash_arena(self):
        """A single game error should not crash the whole arena run."""

        class FailingProvider:
            async def complete(self, **kwargs):
                raise ConnectionError("API down")

        class FailingArena(Arena):
            def _create_provider(self):
                return FailingProvider()

        strategies = [
            _test_strategy("Alpha", "s-alpha"),
            _test_strategy("Beta", "s-beta"),
            _test_strategy("Gamma", "s-gamma"),
        ]
        arena = FailingArena()
        # Should not raise, even though provider fails
        report = await arena.run_with_strategies(
            "auction",
            strategies,
            num_games=1,
            seeds=[42],
            phase_timeout=0,
            game_timeout=10,
        )
        assert report.total_games == 1
