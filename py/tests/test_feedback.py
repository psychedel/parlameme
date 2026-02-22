"""Tests for Feedback — strategy improvement suggestions."""

from __future__ import annotations

import pytest

from games import REGISTRY
from strategy.evaluation import EvalResult
from strategy.feedback import (
    FeedbackReport,
    Suggestion,
    analyze,
    _find_closest_archetype,
    _personality_distance,
)
from strategy.schema import Strategy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _strategy(**overrides) -> Strategy:
    defaults = dict(
        id="test-feedback",
        name="Test Strategy",
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
    defaults.update(overrides)
    return Strategy(**defaults)


def _eval_result(**overrides) -> EvalResult:
    defaults = dict(
        strategy_id="test-feedback",
        game_id="auction",
        tier="quick",
        games_played=10,
        wins=5,
        losses=3,
        draws=2,
        win_rate=0.5,
        matchups={},
        resource_efficiency={},
        phase_activity={},
        elapsed_seconds=30.0,
    )
    defaults.update(overrides)
    return EvalResult(**defaults)


# ---------------------------------------------------------------------------
# Tests: personality distance
# ---------------------------------------------------------------------------


class TestPersonalityDistance:
    def test_identical_is_zero(self):
        a = {"aggression": 0.5, "honesty": 0.5}
        assert _personality_distance(a, a) == 0.0

    def test_opposite_is_high(self):
        a = {"aggression": 0.0, "honesty": 0.0, "loyalty": 0.0, "risk_tolerance": 0.0}
        b = {"aggression": 1.0, "honesty": 1.0, "loyalty": 1.0, "risk_tolerance": 1.0}
        dist = _personality_distance(a, b)
        assert dist == pytest.approx(1.0, abs=0.01)

    def test_partial_difference(self):
        a = {"aggression": 0.5}
        b = {"aggression": 0.8}
        dist = _personality_distance(a, b)
        assert 0.0 < dist < 1.0


# ---------------------------------------------------------------------------
# Tests: closest archetype
# ---------------------------------------------------------------------------


class TestClosestArchetype:
    def test_finds_archetype(self):
        s = _strategy()
        name, dist = _find_closest_archetype(s)
        assert name is not None
        assert 0.0 <= dist <= 1.0

    def test_unknown_game_returns_none(self):
        s = _strategy(game_id="nonexistent_game")
        name, dist = _find_closest_archetype(s)
        assert name is None
        assert dist == 1.0


# ---------------------------------------------------------------------------
# Tests: analyze
# ---------------------------------------------------------------------------


class TestAnalyze:
    def test_high_win_rate_shows_strength(self):
        s = _strategy()
        er = _eval_result(win_rate=0.8, wins=8, losses=2)
        report = analyze(s, er)
        assert any("strong" in st.lower() or "win rate" in st.lower() for st in report.strengths)

    def test_low_win_rate_shows_weakness(self):
        s = _strategy()
        er = _eval_result(win_rate=0.1, wins=1, losses=9)
        report = analyze(s, er)
        assert any("low" in w.lower() or "win rate" in w.lower() for w in report.weaknesses)
        # Should suggest trying an archetype
        assert len(report.suggestions) > 0

    def test_worst_matchup_suggestion(self):
        s = _strategy()
        er = _eval_result(
            matchups={
                "opponent-1": {"wins": 1, "losses": 4, "draws": 0},
                "opponent-2": {"wins": 3, "losses": 2, "draws": 0},
            }
        )
        report = analyze(s, er)
        # Should suggest counter-tactics for opponent-1 (80% loss rate)
        matchup_suggestions = [
            sg for sg in report.suggestions if "opponent-1" in sg.field
        ]
        assert len(matchup_suggestions) >= 1

    def test_low_resources_suggestion(self):
        s = _strategy()
        er = _eval_result(
            resource_efficiency={"credits": 5.0, "reputation": 50.0}
        )
        report = analyze(s, er)
        credit_suggestions = [
            sg for sg in report.suggestions if sg.field == "credits"
        ]
        assert len(credit_suggestions) >= 1

    def test_empty_persona_suggestion(self):
        s = _strategy(persona="")
        er = _eval_result()
        report = analyze(s, er)
        persona_suggestions = [
            sg for sg in report.suggestions if sg.field == "persona"
        ]
        assert len(persona_suggestions) >= 1

    def test_missing_phase_tactics_suggestion(self):
        s = _strategy(phase_tactics={})
        er = _eval_result()
        compiled = REGISTRY["auction"]
        report = analyze(s, er, compiled)
        phase_suggestions = [
            sg for sg in report.suggestions if sg.category == "phase_tactic"
        ]
        # Auction has non-automatic phases that should be covered
        assert len(phase_suggestions) >= 1

    def test_no_deal_rules_suggestion(self):
        s = _strategy(deal_rules={})
        er = _eval_result()
        compiled = REGISTRY["auction"]
        report = analyze(s, er, compiled)
        deal_suggestions = [
            sg for sg in report.suggestions
            if sg.category == "deal_rule" and sg.field == "general"
        ]
        assert len(deal_suggestions) >= 1

    def test_report_has_archetype_info(self):
        s = _strategy()
        er = _eval_result()
        report = analyze(s, er)
        assert report.closest_archetype is not None
        assert 0.0 <= report.archetype_distance <= 1.0

    def test_complete_strategy_fewer_suggestions(self):
        """A strategy with all fields populated should get fewer suggestions."""
        s = _strategy(
            persona="A cunning trader who maximizes profit.",
            phase_tactics={"preview": "scout lots", "bidding": "bid aggressively"},
            deal_rules={"sealed_bid": "always bid 60% of value"},
        )
        er = _eval_result(win_rate=0.6, wins=6, losses=4)
        compiled = REGISTRY["auction"]
        report = analyze(s, er, compiled)
        # Should still have some suggestions but fewer than empty strategy
        s_empty = _strategy(persona="", phase_tactics={}, deal_rules={})
        report_empty = analyze(s_empty, er, compiled)
        assert len(report.suggestions) <= len(report_empty.suggestions)
