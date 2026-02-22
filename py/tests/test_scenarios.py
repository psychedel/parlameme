"""Tests for strategy/scenarios.py — scenario extraction and testing."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from engine.archive import create_archive, save_archive
from engine.runtime.core import GameRuntime
from games import REGISTRY
from strategy.scenarios import (
    DeterministicResult,
    LLMTestResult,
    Scenario,
    _decision_player,
    _interestingness,
    evaluate_deterministic,
    evaluate_with_llm,
    extract_scenarios,
    generate_synthetic_scenarios,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def auction_compiled():
    return REGISTRY["auction"]


@pytest.fixture
def archive_dir(auction_compiled):
    """Create a temp dir with an auction archive using deals (not votes)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        d = Path(tmpdir)
        rt = GameRuntime(auction_compiled)
        players = ["alice", "bob", "charlie"]
        state = rt.start_game(players, seed=42)
        state = rt.run_setup(state)
        state = rt.advance_phase(state)  # → preview
        state = rt.advance_phase(state)  # → format_vote

        # format_vote is a vote-type action in auction — use start_deal
        for voter in players:
            result = rt.start_deal(
                state,
                "format_vote",
                actor_id=voter,
                params={"format": "sealed"},
            )
            if result["ok"]:
                state = result["state"]

        state = rt.advance_phase(state)  # → bidding

        # Place sealed bids
        for bidder in players:
            result = rt.start_deal(
                state,
                "sealed_bid",
                actor_id=bidder,
                params={"amount": 50 + players.index(bidder) * 10},
            )
            if result["ok"]:
                state = result["state"]

        archive = create_archive(auction_compiled, state)
        save_archive(archive, d / "test-game.json")
        yield d


# ---------------------------------------------------------------------------
# Scenario data structure tests
# ---------------------------------------------------------------------------


class TestScenario:
    def test_create(self):
        s = Scenario(
            id="test@0",
            game_id="auction",
            archive_id="test",
            step=0,
            player_id="alice",
            phase="bidding",
            round=1,
            description="Game state...",
            available_actions="sealed_bid, pass_bid",
            actual_decision={"type": "deal", "deal": "sealed_bid"},
            category="deal",
        )
        assert s.id == "test@0"
        assert s.category == "deal"
        assert s.player_id == "alice"

    def test_frozen(self):
        s = Scenario(
            id="test@0",
            game_id="auction",
            archive_id="test",
            step=0,
            player_id="alice",
            phase="bidding",
            round=1,
            description="",
            available_actions="",
            actual_decision={},
            category="deal",
        )
        with pytest.raises(AttributeError):
            s.phase = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Decision player extraction
# ---------------------------------------------------------------------------


class TestDecisionPlayer:
    def test_proposer(self):
        assert _decision_player({"type": "deal", "proposer": "alice"}) == "alice"

    def test_voter(self):
        assert _decision_player({"type": "vote", "voter": "bob"}) == "bob"

    def test_responder(self):
        assert (
            _decision_player({"type": "respond", "responder": "charlie"}) == "charlie"
        )

    def test_sender(self):
        assert _decision_player({"type": "message", "sender": "dave"}) == "dave"

    def test_actor(self):
        assert _decision_player({"type": "speech_act", "actor": "eve"}) == "eve"

    def test_empty(self):
        assert _decision_player({"type": "advance_phase"}) == ""


# ---------------------------------------------------------------------------
# Interestingness scoring
# ---------------------------------------------------------------------------


class TestInterestingness:
    def test_deals_score_higher_than_votes(self):
        deal = Scenario(
            id="a@0",
            game_id="auction",
            archive_id="a",
            step=0,
            player_id="x",
            phase="bidding",
            round=1,
            description="",
            available_actions="abc",
            actual_decision={},
            category="deal",
        )
        vote = Scenario(
            id="a@1",
            game_id="auction",
            archive_id="a",
            step=1,
            player_id="x",
            phase="trial",
            round=1,
            description="",
            available_actions="abc",
            actual_decision={},
            category="vote",
        )
        assert _interestingness(deal) > _interestingness(vote)

    def test_later_rounds_score_higher(self):
        early = Scenario(
            id="a@0",
            game_id="auction",
            archive_id="a",
            step=0,
            player_id="x",
            phase="bidding",
            round=1,
            description="",
            available_actions="abc",
            actual_decision={},
            category="deal",
        )
        late = Scenario(
            id="a@1",
            game_id="auction",
            archive_id="a",
            step=1,
            player_id="x",
            phase="bidding",
            round=5,
            description="",
            available_actions="abc",
            actual_decision={},
            category="deal",
        )
        assert _interestingness(late) > _interestingness(early)


# ---------------------------------------------------------------------------
# Scenario extraction
# ---------------------------------------------------------------------------


class TestExtractScenarios:
    def test_extract_from_archives(self, auction_compiled, archive_dir):
        scenarios = extract_scenarios("auction", limit=5, archive_dir=archive_dir)
        # Should find at least some decision points
        assert isinstance(scenarios, list)
        for s in scenarios:
            assert isinstance(s, Scenario)
            assert s.game_id == "auction"
            assert s.player_id  # non-empty
            assert s.description  # non-empty
            assert s.available_actions  # non-empty

    def test_extract_unknown_game(self, archive_dir):
        scenarios = extract_scenarios("nonexistent", archive_dir=archive_dir)
        assert scenarios == []

    def test_extract_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            scenarios = extract_scenarios("auction", archive_dir=Path(tmpdir))
            assert scenarios == []

    def test_limit_respected(self, auction_compiled, archive_dir):
        scenarios = extract_scenarios("auction", limit=2, archive_dir=archive_dir)
        assert len(scenarios) <= 2

    def test_scenarios_sorted_by_interestingness(self, auction_compiled, archive_dir):
        scenarios = extract_scenarios("auction", limit=10, archive_dir=archive_dir)
        if len(scenarios) >= 2:
            scores = [_interestingness(s) for s in scenarios]
            assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# Deterministic testing
# ---------------------------------------------------------------------------


class TestDeterministicTesting:
    def _make_scenario(self, phase="bidding", category="deal"):
        return Scenario(
            id="test@0",
            game_id="auction",
            archive_id="test",
            step=0,
            player_id="alice",
            phase=phase,
            round=2,
            description="Round 2, bidding phase. Alice has 100 gold.",
            available_actions="sealed_bid (place a sealed bid), pass_bid (skip this lot)",
            actual_decision={"type": "deal", "deal": "sealed_bid", "proposer": "alice"},
            category=category,
        )

    def test_matching_phase_tactic(self):
        scenario = self._make_scenario()
        strategy = {
            "phase_tactics": {"bidding": "Bid conservatively on early lots"},
            "personality": {"aggression": 0.3},
        }
        result = evaluate_deterministic(scenario, strategy)
        assert isinstance(result, DeterministicResult)
        assert "phase_tactic[bidding]" in result.matches
        assert result.confidence in ("high", "medium")

    def test_matching_deal_rule(self):
        scenario = self._make_scenario()
        strategy = {
            "deal_rules": {"sealed_bid": "Bid 70% of value"},
            "phase_tactics": {},
            "personality": {},
        }
        result = evaluate_deterministic(scenario, strategy)
        assert "deal_rule[sealed_bid]" in result.matches

    def test_both_match_high_confidence(self):
        scenario = self._make_scenario()
        strategy = {
            "phase_tactics": {"bidding": "Be careful"},
            "deal_rules": {"sealed_bid": "Bid low"},
            "personality": {},
        }
        result = evaluate_deterministic(scenario, strategy)
        assert result.confidence == "high"
        assert len(result.matches) >= 2

    def test_no_match_low_confidence(self):
        scenario = self._make_scenario()
        strategy = {
            "phase_tactics": {},
            "deal_rules": {},
            "role_overrides": {},
            "personality": {"aggression": 0.5, "risk_tolerance": 0.5},
            "persona": "",
        }
        result = evaluate_deterministic(scenario, strategy)
        assert result.confidence == "low"
        assert len(result.matches) == 0

    def test_aggressive_personality_suggestion(self):
        scenario = self._make_scenario()
        strategy = {
            "phase_tactics": {},
            "deal_rules": {},
            "role_overrides": {},
            "personality": {"aggression": 0.9, "risk_tolerance": 0.8},
            "persona": "",
        }
        result = evaluate_deterministic(scenario, strategy)
        assert "aggressive" in result.suggestion.lower()

    def test_respond_scenario_risk_tolerance(self):
        scenario = self._make_scenario(category="respond")
        # Override available_actions to include "accept"
        scenario = Scenario(
            id=scenario.id,
            game_id=scenario.game_id,
            archive_id=scenario.archive_id,
            step=scenario.step,
            player_id=scenario.player_id,
            phase=scenario.phase,
            round=scenario.round,
            description=scenario.description,
            available_actions="accept (agree to the deal), reject (refuse)",
            actual_decision={"type": "respond", "response": "accept"},
            category="respond",
        )
        strategy = {
            "phase_tactics": {},
            "deal_rules": {},
            "role_overrides": {},
            "personality": {"risk_tolerance": 0.8},
            "persona": "",
        }
        result = evaluate_deterministic(scenario, strategy)
        assert "accept" in result.suggestion.lower()

    def test_actual_field_populated(self):
        scenario = self._make_scenario()
        strategy = {"phase_tactics": {}, "deal_rules": {}, "personality": {}}
        result = evaluate_deterministic(scenario, strategy)
        assert "sealed_bid" in result.actual


# ---------------------------------------------------------------------------
# LLM-based testing (mock)
# ---------------------------------------------------------------------------


class TestLLMTesting:
    @pytest.mark.asyncio
    async def test_llm_test_with_mock(self):
        from dataclasses import dataclass, field

        @dataclass
        class MockResponse:
            content: str = "ACTION: sealed_bid\nREASON: Good value"
            tool_calls: list = field(default_factory=list)
            stop_reason: str = "stop"
            raw: object = None

        class MockProvider:
            async def complete(self, messages, tools=None, system=""):
                return MockResponse()

        scenario = Scenario(
            id="test@0",
            game_id="auction",
            archive_id="test",
            step=0,
            player_id="alice",
            phase="bidding",
            round=1,
            description="State...",
            available_actions="sealed_bid, pass",
            actual_decision={"type": "deal", "deal": "sealed_bid"},
            category="deal",
        )

        result = await evaluate_with_llm(scenario, "You are a player", MockProvider())
        assert isinstance(result, LLMTestResult)
        assert result.chosen_action == "sealed_bid"
        assert result.reasoning == "Good value"
        assert result.matches_actual is True
        assert result.error == ""

    @pytest.mark.asyncio
    async def test_llm_test_error_handling(self):
        class ErrorProvider:
            async def complete(self, messages, tools=None, system=""):
                raise ConnectionError("API down")

        scenario = Scenario(
            id="test@0",
            game_id="auction",
            archive_id="test",
            step=0,
            player_id="alice",
            phase="bidding",
            round=1,
            description="State...",
            available_actions="sealed_bid",
            actual_decision={"type": "deal", "deal": "sealed_bid"},
            category="deal",
        )

        result = await evaluate_with_llm(scenario, "prompt", ErrorProvider())
        assert result.error == "API down"
        assert result.matches_actual is False

    @pytest.mark.asyncio
    async def test_llm_test_no_match(self):
        from dataclasses import dataclass, field

        @dataclass
        class MockResponse:
            content: str = "ACTION: pass_bid\nREASON: Too expensive"
            tool_calls: list = field(default_factory=list)
            stop_reason: str = "stop"
            raw: object = None

        class MockProvider:
            async def complete(self, messages, tools=None, system=""):
                return MockResponse()

        scenario = Scenario(
            id="test@0",
            game_id="auction",
            archive_id="test",
            step=0,
            player_id="alice",
            phase="bidding",
            round=1,
            description="State...",
            available_actions="sealed_bid, pass_bid",
            actual_decision={"type": "deal", "deal": "sealed_bid"},
            category="deal",
        )

        result = await evaluate_with_llm(scenario, "prompt", MockProvider())
        assert result.chosen_action == "pass_bid"
        assert result.matches_actual is False


# ---------------------------------------------------------------------------
# Smarter deterministic eval
# ---------------------------------------------------------------------------


class TestDeterministicAvailableDeals:
    """Deal rules should match against available_actions text, not just actual."""

    def test_matches_available_deal_ids(self):
        scenario = Scenario(
            id="test@0",
            game_id="auction",
            archive_id="test",
            step=0,
            player_id="alice",
            phase="bidding",
            round=2,
            description="Round 2, bidding phase.",
            available_actions="sealed_bid (place a sealed bid), pass_bid (skip)",
            actual_decision={"type": "deal", "deal": "sealed_bid"},
            category="deal",
        )
        strategy = {
            "phase_tactics": {},
            "deal_rules": {
                "sealed_bid": "Bid 70% of value",
                "pass_bid": "Pass on overpriced lots",
            },
            "role_overrides": {},
            "personality": {},
        }
        result = evaluate_deterministic(scenario, strategy)
        # Both deal rules should match since both appear in available_actions
        deal_matches = [m for m in result.matches if m.startswith("deal_rule")]
        assert len(deal_matches) == 2
        assert "deal_rule[sealed_bid]" in result.matches
        assert "deal_rule[pass_bid]" in result.matches

    def test_priority_hint_in_suggestion(self):
        scenario = Scenario(
            id="test@0",
            game_id="auction",
            archive_id="test",
            step=0,
            player_id="alice",
            phase="bidding",
            round=1,
            description="State...",
            available_actions="sealed_bid",
            actual_decision={"type": "deal", "deal": "sealed_bid"},
            category="deal",
        )
        strategy = {
            "phase_tactics": {},
            "deal_rules": {},
            "role_overrides": {},
            "personality": {},
            "priorities": ("wealth", "survival"),
        }
        result = evaluate_deterministic(scenario, strategy)
        assert "Top priority: wealth" in result.suggestion


# ---------------------------------------------------------------------------
# Synthetic scenario generation
# ---------------------------------------------------------------------------


class TestSyntheticGeneration:
    def test_generate_auction(self):
        scenarios = generate_synthetic_scenarios("auction", count=3, seed=42)
        assert isinstance(scenarios, list)
        assert len(scenarios) <= 3
        for s in scenarios:
            assert isinstance(s, Scenario)
            assert s.game_id == "auction"
            assert s.player_id.startswith("bot_")
            assert s.archive_id == "synthetic-42"

    def test_generate_unknown_game(self):
        scenarios = generate_synthetic_scenarios("nonexistent", count=5)
        assert scenarios == []

    def test_generate_deterministic(self):
        """Same seed produces same scenarios."""
        a = generate_synthetic_scenarios("auction", count=3, seed=99)
        b = generate_synthetic_scenarios("auction", count=3, seed=99)
        assert len(a) == len(b)
        for sa, sb in zip(a, b):
            assert sa.id == sb.id
            assert sa.player_id == sb.player_id
            assert sa.phase == sb.phase
