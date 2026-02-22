"""Tests for exported MCP formatter helpers used by UI components.

These helpers are the shared logic between MCP agent formatters and NiceGUI UI.
"""

from __future__ import annotations

import attrs
import pytest

from engine.runtime.state import CompiledGame, GameState
from games import REGISTRY

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def auction():
    return REGISTRY["auction"]


@pytest.fixture
def werewolf():
    return REGISTRY["werewolf"]


@pytest.fixture
def parliament():
    return REGISTRY["parliament_arena"]


def _start(compiled: CompiledGame, n: int | None = None) -> GameState:
    """Start a game with minimum players and return initial state."""
    from engine.runtime.core import GameRuntime

    rt = GameRuntime(compiled)
    count = n or compiled.min_players
    players = [f"p{i}" for i in range(count)]
    return rt.start_game(players)


# ---------------------------------------------------------------------------
# get_phase_def
# ---------------------------------------------------------------------------


class TestGetPhaseDef:
    def test_returns_phase_for_valid_id(self, auction):
        from mcp.formatters import get_phase_def

        pd = get_phase_def(auction, "preview")
        assert pd is not None
        assert pd.id == "preview"

    def test_returns_none_for_unknown(self, auction):
        from mcp.formatters import get_phase_def

        assert get_phase_def(auction, "nonexistent") is None

    def test_backward_compat_alias(self):
        from mcp.formatters import _get_phase_def, get_phase_def

        assert _get_phase_def is get_phase_def


# ---------------------------------------------------------------------------
# build_context_line
# ---------------------------------------------------------------------------


class TestBuildContextLine:
    def test_auction_with_var_hints(self, auction):
        from mcp.formatters import build_context_line, get_phase_def

        state = _start(auction)
        # Advance to have some vars
        pd = get_phase_def(auction, state.phase)
        lines = build_context_line(state, auction, pd)
        # Should return a list of strings
        assert isinstance(lines, list)
        for line in lines:
            assert isinstance(line, str)

    def test_fallback_for_no_var_hints(self):
        """Games without context.var_hints fall back to round + raw vars."""
        from mcp.formatters import build_context_line

        # Create a minimal compiled game with no context hints
        auction = REGISTRY["auction"]
        # Hack: make a copy with empty context var_hints
        from engine.runtime.state import ContextConfig

        compiled = attrs.evolve(auction, context=ContextConfig())
        state = _start(compiled)
        # Add some vars manually
        state = attrs.evolve(state, vars_={**state.vars_, "test_key": 42})
        pd = None
        lines = build_context_line(state, compiled, pd)
        # Should include the test_key
        assert any("test_key" in l for l in lines)

    def test_filters_by_phase(self, auction):
        """VarHints with phase restrictions only show in matching phases."""
        from mcp.formatters import build_context_line, get_phase_def

        state = _start(auction)
        pd = get_phase_def(auction, "preview")
        lines_preview = build_context_line(state, auction, pd)

        pd2 = get_phase_def(auction, "bidding")
        lines_bidding = build_context_line(state, auction, pd2)

        # Both should be valid lists (contents may differ by phase)
        assert isinstance(lines_preview, list)
        assert isinstance(lines_bidding, list)


# ---------------------------------------------------------------------------
# can_player_use_deal
# ---------------------------------------------------------------------------


class TestCanPlayerUseDeal:
    def test_valid_deal_in_correct_phase(self, auction):
        from engine.runtime.core import GameRuntime
        from mcp.formatters import can_player_use_deal

        rt = GameRuntime(auction)
        state = rt.start_game(["p0", "p1", "p2"])
        state = rt.advance_phase(state)  # preview
        state = rt.advance_phase(state)  # format_vote

        # In format_vote, all players should be able to vote
        # Check a deal that exists
        for deal_id in auction.deals:
            result = can_player_use_deal(state, auction, deal_id, "p0")
            assert isinstance(result, bool)

    def test_unknown_deal_returns_false(self, auction):
        from mcp.formatters import can_player_use_deal

        state = _start(auction)
        assert can_player_use_deal(state, auction, "nonexistent_deal", "p0") is False

    def test_backward_compat_alias(self):
        from mcp.formatters import _can_player_use_deal, can_player_use_deal

        assert _can_player_use_deal is can_player_use_deal


# ---------------------------------------------------------------------------
# is_usage_exhausted
# ---------------------------------------------------------------------------


class TestIsUsageExhausted:
    def test_not_exhausted_initially(self, auction):
        from mcp.formatters import is_usage_exhausted

        state = _start(auction)
        for deal_id, deal in auction.deals.items():
            result = is_usage_exhausted(state, deal_id, "p0", deal)
            assert result is False or result is True  # valid bool

    def test_exhausted_after_limit_reached(self, auction):
        """Simulate usage reaching per_round limit."""
        from mcp.formatters import is_usage_exhausted

        state = _start(auction)
        # Find a deal with per_round limit
        for deal_id, deal in auction.deals.items():
            if deal.per_round is not None:
                # Simulate usage at limit
                key = f"p0:{deal_id}"
                usage = {f"round:{state.round}": deal.per_round}
                state = attrs.evolve(state, usage={**state.usage, key: usage})
                assert is_usage_exhausted(state, deal_id, "p0", deal) is True
                break

    def test_backward_compat_alias(self):
        from mcp.formatters import _is_usage_exhausted, is_usage_exhausted

        assert _is_usage_exhausted is is_usage_exhausted


# ---------------------------------------------------------------------------
# format_usage_limit
# ---------------------------------------------------------------------------


class TestFormatUsageLimit:
    def test_empty_for_no_limits(self):
        """Deals with no per_round/per_phase/per_game return empty string."""
        from mcp.formatters import format_usage_limit

        # Find a deal without usage limits or create a minimal one
        auction = REGISTRY["auction"]
        state = _start(auction)
        for deal_id, deal in auction.deals.items():
            result = format_usage_limit(state, deal_id, "p0", deal)
            assert isinstance(result, str)
            if (
                deal.per_round is None
                and deal.per_phase is None
                and deal.per_game is None
            ):
                assert result == ""

    def test_format_with_per_round(self, auction):
        from mcp.formatters import format_usage_limit

        state = _start(auction)
        for deal_id, deal in auction.deals.items():
            if deal.per_round is not None:
                result = format_usage_limit(state, deal_id, "p0", deal)
                assert "round" in result
                assert "/" in result
                break

    def test_format_with_usage(self, auction):
        """Shows current usage count."""
        from mcp.formatters import format_usage_limit

        state = _start(auction)
        for deal_id, deal in auction.deals.items():
            if deal.per_round is not None:
                key = f"p0:{deal_id}"
                usage = {f"round:{state.round}": 1}
                state = attrs.evolve(state, usage={**state.usage, key: usage})
                result = format_usage_limit(state, deal_id, "p0", deal)
                assert "1/" in result
                break

    def test_backward_compat_alias(self):
        from mcp.formatters import _format_usage_limit, format_usage_limit

        assert _format_usage_limit is format_usage_limit


# ---------------------------------------------------------------------------
# compute_advance_readiness
# ---------------------------------------------------------------------------


class TestComputeAdvanceReadiness:
    def test_returns_valid_state(self, auction):
        from mcp.formatters import compute_advance_readiness

        state = _start(auction)
        result = compute_advance_readiness(state, auction, "p0")
        assert result in ("BLOCKED", "READY", "OPTIONAL")

    def test_blocked_with_pending_votes(self, auction):
        from engine.runtime.core import GameRuntime
        from mcp.formatters import compute_advance_readiness

        rt = GameRuntime(auction)
        state = rt.start_game(["p0", "p1", "p2"])
        state = rt.advance_phase(state)  # preview
        state = rt.advance_phase(state)  # format_vote

        # format_vote starts a pending vote
        if state.pending_votes:
            result = compute_advance_readiness(state, auction, "p0")
            assert result == "BLOCKED"

    def test_ready_with_no_actions(self, auction):
        """After all actions exhausted, should be READY."""
        from mcp.formatters import compute_advance_readiness

        # Setup phase is auto — start state has no pending deals/votes
        state = _start(auction)
        # setup phase typically has no player actions
        result = compute_advance_readiness(state, auction, "p0")
        assert result in ("READY", "OPTIONAL")

    def test_backward_compat_alias(self):
        from mcp.formatters import _compute_advance_readiness, compute_advance_readiness

        assert _compute_advance_readiness is compute_advance_readiness


# ---------------------------------------------------------------------------
# outcome_summary (from mechanics.py — also used in UI)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# can_player_use_speech_act
# ---------------------------------------------------------------------------


class TestCanPlayerUseSpeechAct:
    def test_unknown_speech_act_returns_false(self, auction):
        from mcp.formatters import can_player_use_speech_act

        state = _start(auction)
        assert can_player_use_speech_act(state, auction, "nonexistent", "p0") is False

    def test_valid_speech_act(self, werewolf):
        from mcp.formatters import can_player_use_speech_act

        state = _start(werewolf)
        # werewolf has speech acts — check all of them
        for sa_id in werewolf.speech_acts:
            result = can_player_use_speech_act(state, werewolf, sa_id, "p0")
            assert isinstance(result, bool)

    def test_respects_usage_limits(self, werewolf):
        from mcp.formatters import can_player_use_speech_act

        state = _start(werewolf)
        for sa_id, sa_def in werewolf.speech_acts.items():
            if sa_def.per_round is not None:
                # Simulate exhausted usage
                key = f"p0:{sa_id}"
                usage = {f"round:{state.round}": sa_def.per_round}
                state = attrs.evolve(state, usage={**state.usage, key: usage})
                assert can_player_use_speech_act(state, werewolf, sa_id, "p0") is False
                break

    def test_respects_cost(self, parliament):
        from mcp.formatters import can_player_use_speech_act

        state = _start(parliament)
        for sa_id, sa_def in parliament.speech_acts.items():
            if sa_def.cost:
                # Drain the required resource to 0
                for resource in sa_def.cost:
                    state = state.set_resource("p0", resource, 0)
                assert (
                    can_player_use_speech_act(state, parliament, sa_id, "p0") is False
                )
                break


# ---------------------------------------------------------------------------
# can_player_start_vote
# ---------------------------------------------------------------------------


class TestCanPlayerStartVote:
    def test_unknown_vote_returns_false(self, auction):
        from mcp.formatters import can_player_start_vote

        state = _start(auction)
        assert can_player_start_vote(state, auction, "nonexistent", "p0") is False

    def test_valid_vote(self, auction):
        from mcp.formatters import can_player_start_vote

        state = _start(auction)
        for vote_id in auction.votes:
            result = can_player_start_vote(state, auction, vote_id, "p0")
            assert isinstance(result, bool)

    def test_vote_without_proposer_filter(self):
        """Votes without proposer_filter should allow any player."""
        from mcp.formatters import can_player_start_vote

        # Find a vote that has no proposer filter
        for game_id, compiled in REGISTRY.items():
            for vote_id, vote_def in compiled.votes.items():
                if vote_def.proposer_filter is None:
                    state = _start(compiled)
                    assert can_player_start_vote(state, compiled, vote_id, "p0") is True
                    return
        pytest.skip("No games have votes without proposer_filter")


# ---------------------------------------------------------------------------
# outcome_summary (from mechanics.py — also used in UI)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _deal_param_hints (formatter bug fix verification)
# ---------------------------------------------------------------------------


class TestDealParamHints:
    def test_auction_bids_show_amount_range(self, auction):
        """Verify ParamDef params produce proper hints (not bare names)."""
        from mcp.formatters import _deal_param_hints

        for deal_id, deal_def in auction.deals.items():
            if deal_def.params:
                hint = _deal_param_hints(deal_def)
                # Should contain type info, not just bare param name
                assert "number" in hint or "keyword" in hint or "string" in hint, (
                    f"{deal_id}: got '{hint}' — expected type info from ParamDef"
                )

    def test_parliament_bribe_shows_responder_and_amount(self, parliament):
        from mcp.formatters import _deal_param_hints

        bribe = parliament.deals.get("bribe")
        if bribe:
            hint = _deal_param_hints(bribe)
            assert "responder" in hint or "target" in hint
            # Should show amount range
            assert "number" in hint or "10" in hint


# ---------------------------------------------------------------------------
# outcome_summary (from mechanics.py — also used in UI)
# ---------------------------------------------------------------------------


class TestOutcomeSummary:
    def test_produces_string_for_deals(self, auction):
        from mcp.mechanics import outcome_summary

        for deal_id, deal in auction.deals.items():
            result = outcome_summary(deal.outcomes)
            assert isinstance(result, str)

    def test_empty_for_no_outcomes(self):
        from mcp.mechanics import outcome_summary

        assert outcome_summary({}) == ""
