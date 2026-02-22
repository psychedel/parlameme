"""Tests for tournament system — state, generators, runtime, sessions."""

from __future__ import annotations

import asyncio

import attrs
import pytest

from server.sessions import get_session, list_sessions, remove_session
from tournament.generator import MatchGenerator
from tournament.runtime import TournamentRuntime
from tournament.sessions import (
    TournamentSession,
    create_tournament,
    get_tournament,
)
from tournament.sessions import (
    reset_all as reset_tournaments,
)
from tournament.state import Match, Standing, TournamentState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok(result):
    """Unwrap Result, assert success, return state."""
    assert result["ok"], f"Expected ok, got: {result}"
    return result["state"]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runtime():
    return TournamentRuntime()


@pytest.fixture
def players_4():
    return ["alice", "bob", "carol", "dave"]


@pytest.fixture
def players_8():
    return ["p1", "p2", "p3", "p4", "p5", "p6", "p7", "p8"]


# ===========================================================================
# Match Generator — Round Robin
# ===========================================================================


class TestRoundRobin:
    def test_generates_correct_match_count(self, players_4):
        matches = MatchGenerator.round_robin(players_4, seed=42)
        # n*(n-1)/2 = 4*3/2 = 6 matches
        assert len(matches) == 6

    def test_every_pair_plays(self, players_4):
        matches = MatchGenerator.round_robin(players_4, seed=42)
        pairs = {frozenset(m.participants) for m in matches}
        expected = {
            frozenset({a, b})
            for i, a in enumerate(players_4)
            for b in players_4[i + 1 :]
        }
        assert pairs == expected

    def test_deterministic_with_same_seed(self, players_4):
        m1 = MatchGenerator.round_robin(players_4, seed=42)
        m2 = MatchGenerator.round_robin(players_4, seed=42)
        assert [m.participants for m in m1] == [m.participants for m in m2]

    def test_different_seeds_different_order(self, players_4):
        m1 = MatchGenerator.round_robin(players_4, seed=42)
        m2 = MatchGenerator.round_robin(players_4, seed=99)
        # Same pairs but different order/assignment
        pairs1 = {frozenset(m.participants) for m in m1}
        pairs2 = {frozenset(m.participants) for m in m2}
        assert pairs1 == pairs2  # same pairs regardless of seed

    def test_odd_number_of_players(self):
        players = ["a", "b", "c"]
        matches = MatchGenerator.round_robin(players, seed=42)
        # 3 players → 3 matches
        assert len(matches) == 3

    def test_two_players(self):
        matches = MatchGenerator.round_robin(["a", "b"], seed=42)
        assert len(matches) == 1
        assert set(matches[0].participants) == {"a", "b"}

    def test_match_ids_unique(self, players_8):
        matches = MatchGenerator.round_robin(players_8, seed=42)
        ids = [m.id for m in matches]
        assert len(ids) == len(set(ids))

    def test_rounds_assigned(self, players_4):
        matches = MatchGenerator.round_robin(players_4, seed=42)
        rounds = {m.round for m in matches}
        # 4 players → 3 rounds
        assert rounds == {1, 2, 3}


# ===========================================================================
# Match Generator — Single Elimination
# ===========================================================================


class TestSingleElimination:
    def test_generates_first_round(self, players_4):
        matches = MatchGenerator.single_elimination(players_4, seed=42)
        # 4 players → 2 matches in round 1
        assert len(matches) == 2
        assert all(m.round == 1 for m in matches)

    def test_all_players_in_matches(self, players_4):
        matches = MatchGenerator.single_elimination(players_4, seed=42)
        all_participants = set()
        for m in matches:
            all_participants.update(m.participants)
        assert all_participants == set(players_4)

    def test_8_players(self, players_8):
        matches = MatchGenerator.single_elimination(players_8, seed=42)
        assert len(matches) == 4  # first round: 4 matches

    def test_power_of_2(self):
        matches = MatchGenerator.single_elimination(
            ["a", "b", "c", "d", "e", "f", "g", "h"], seed=42
        )
        assert len(matches) == 4

    def test_deterministic(self, players_4):
        m1 = MatchGenerator.single_elimination(players_4, seed=42)
        m2 = MatchGenerator.single_elimination(players_4, seed=42)
        assert [m.participants for m in m1] == [m.participants for m in m2]

    def test_next_round(self):
        completed = [
            Match(id="r1-0", participants=("a", "b"), status="completed", winner="a"),
            Match(id="r1-1", participants=("c", "d"), status="completed", winner="d"),
        ]
        next_round = MatchGenerator.next_elimination_round(completed, 2)
        assert len(next_round) == 1
        assert set(next_round[0].participants) == {"a", "d"}
        assert next_round[0].round == 2


# ===========================================================================
# Match Generator — Swiss
# ===========================================================================


class TestSwiss:
    def test_first_round(self, players_4):
        standings = {p: Standing(participant=p) for p in players_4}
        matches = MatchGenerator.swiss_pairing(standings, set(), 1)
        assert len(matches) == 2
        all_players = set()
        for m in matches:
            all_players.update(m.participants)
        assert all_players == set(players_4)

    def test_avoids_rematches(self, players_4):
        standings = {p: Standing(participant=p) for p in players_4}
        r1 = MatchGenerator.swiss_pairing(standings, set(), 1)

        # Record r1 pairings
        history = {frozenset(m.participants) for m in r1}

        r2 = MatchGenerator.swiss_pairing(standings, history, 2)
        for m in r2:
            if len(m.participants) < 2:
                continue  # bye match
            pair = frozenset(m.participants)
            assert pair not in history, f"Rematch: {pair}"

    def test_pairs_by_score(self):
        standings = {
            "a": Standing(participant="a", points=6),
            "b": Standing(participant="b", points=6),
            "c": Standing(participant="c", points=0),
            "d": Standing(participant="d", points=0),
        }
        matches = MatchGenerator.swiss_pairing(standings, set(), 2)
        # Top players should be paired together
        top_match = next(m for m in matches if "a" in m.participants)
        assert "b" in top_match.participants


# ===========================================================================
# Tournament Runtime — Lifecycle
# ===========================================================================


class TestTournamentRuntime:
    def test_create(self, runtime):
        state = runtime.create("t1", "round_robin", "alice", "duel", name="Test Cup")
        assert state.tournament_id == "t1"
        assert state.status == "registration"
        assert state.host == "alice"

    def test_register(self, runtime):
        state = runtime.create("t1", "round_robin", "alice", "duel")
        state = _ok(runtime.register(state, "alice"))
        state = _ok(runtime.register(state, "bob"))
        assert "alice" in state.participants
        assert "bob" in state.participants
        assert len(state.standings) == 2

    def test_register_duplicate_fails(self, runtime):
        state = runtime.create("t1", "round_robin", "alice", "duel")
        state = _ok(runtime.register(state, "alice"))
        result = runtime.register(state, "alice")
        assert not result["ok"]
        assert result["error"]["code"] == "already_registered"

    def test_register_when_full(self, runtime):
        state = runtime.create("t1", "round_robin", "alice", "duel", max_participants=2)
        state = _ok(runtime.register(state, "alice"))
        state = _ok(runtime.register(state, "bob"))
        result = runtime.register(state, "carol")
        assert not result["ok"]
        assert result["error"]["code"] == "tournament_full"

    def test_unregister(self, runtime):
        state = runtime.create("t1", "round_robin", "alice", "duel")
        state = _ok(runtime.register(state, "alice"))
        state = _ok(runtime.register(state, "bob"))
        state = _ok(runtime.unregister(state, "bob"))
        assert "bob" not in state.participants

    def test_start_requires_min_participants(self, runtime):
        state = runtime.create("t1", "round_robin", "alice", "duel", min_participants=3)
        state = _ok(runtime.register(state, "alice"))
        state = _ok(runtime.register(state, "bob"))
        result = runtime.start(state)
        assert not result["ok"]
        assert "at least 3" in result["error"]["message"]

    def test_start_round_robin(self, runtime, players_4):
        state = runtime.create("t1", "round_robin", "alice", "duel")
        for p in players_4:
            state = _ok(runtime.register(state, p))
        state = _ok(runtime.start(state))

        assert state.status == "in_progress"
        assert len(state.matches) == 6  # 4*(4-1)/2

    def test_start_single_elimination(self, runtime, players_4):
        state = runtime.create("t1", "single_elimination", "alice", "duel")
        for p in players_4:
            state = _ok(runtime.register(state, p))
        state = _ok(runtime.start(state))

        assert state.status == "in_progress"
        assert len(state.matches) == 2  # first round only

    def test_start_swiss(self, runtime, players_4):
        state = runtime.create("t1", "swiss", "alice", "duel")
        for p in players_4:
            state = _ok(runtime.register(state, p))
        state = _ok(runtime.start(state))

        assert state.status == "in_progress"
        assert len(state.matches) == 2  # first round

    def test_register_after_start_fails(self, runtime):
        state = runtime.create("t1", "round_robin", "alice", "duel")
        state = _ok(runtime.register(state, "alice"))
        state = _ok(runtime.register(state, "bob"))
        state = _ok(runtime.start(state))
        result = runtime.register(state, "carol")
        assert not result["ok"]
        assert result["error"]["code"] == "registration_closed"


# ===========================================================================
# Tournament Runtime — Round Robin Completion
# ===========================================================================


class TestRoundRobinCompletion:
    def test_full_tournament(self, runtime):
        """Play complete round-robin with 3 players."""
        state = runtime.create("t1", "round_robin", "a", "duel", min_participants=2)
        for p in ["a", "b", "c"]:
            state = _ok(runtime.register(state, p))
        state = _ok(runtime.start(state))

        assert state.status == "in_progress"
        assert len(state.matches) == 3  # 3*(3-1)/2

        # Play all matches: a beats b, a beats c, b beats c
        matches = sorted(state.matches.values(), key=lambda m: m.id)
        for match in matches:
            p1, p2 = match.participants
            winner = p1  # first player always wins (for simplicity)
            state = _ok(runtime.report_result(state, match.id, winner))

        assert state.status == "completed"
        assert state.winner is not None

    def test_standings_update(self, runtime):
        state = runtime.create("t1", "round_robin", "a", "duel", min_participants=2)
        state = _ok(runtime.register(state, "a"))
        state = _ok(runtime.register(state, "b"))
        state = _ok(runtime.start(state))

        match = list(state.matches.values())[0]
        state = _ok(
            runtime.report_result(state, match.id, "a", scores={"a": 3, "b": 1})
        )

        assert state.standings["a"].points == 3
        assert state.standings["a"].wins == 1
        assert state.standings["a"].goal_diff == 2
        assert state.standings["b"].losses == 1
        assert state.standings["b"].goal_diff == -2


# ===========================================================================
# Tournament Runtime — Single Elimination Progression
# ===========================================================================


class TestSingleEliminationProgression:
    def test_advances_to_next_round(self, runtime, players_4):
        state = runtime.create("t1", "single_elimination", "a", "duel")
        for p in players_4:
            state = _ok(runtime.register(state, p))
        state = _ok(runtime.start(state))

        # Complete first round
        r1_matches = list(state.matches.values())
        assert len(r1_matches) == 2

        for m in r1_matches:
            winner = m.participants[0]
            state = _ok(runtime.report_result(state, m.id, winner))

        # Should have generated round 2 (final)
        r2_matches = [m for m in state.matches.values() if m.round == 2]
        assert len(r2_matches) == 1

    def test_completes_on_final(self, runtime, players_4):
        state = runtime.create("t1", "single_elimination", "a", "duel")
        for p in players_4:
            state = _ok(runtime.register(state, p))
        state = _ok(runtime.start(state))

        # Complete round 1
        for m in list(state.matches.values()):
            state = _ok(runtime.report_result(state, m.id, m.participants[0]))

        # Complete final
        final = [m for m in state.matches.values() if m.round == 2][0]
        state = _ok(runtime.report_result(state, final.id, final.participants[0]))

        assert state.status == "completed"
        assert state.winner == final.participants[0]

    def test_six_players_bye_advances(self, runtime):
        """6 players SE: 2 bye players must appear in round 2."""
        state = runtime.create(
            "t-bye", "single_elimination", "a", "duel", min_participants=2
        )
        for p in ["a", "b", "c", "d", "e", "f"]:
            state = _ok(runtime.register(state, p))
        state = _ok(runtime.start(state))

        # Round 1: only 2 matches (4 players), 2 have byes
        r1 = [m for m in state.matches.values() if m.round == 1]
        assert len(r1) == 2
        r1_players = {p for m in r1 for p in m.participants}
        all_players = set(state.participants)
        bye_players = all_players - r1_players
        assert len(bye_players) == 2

        # Complete round 1
        for m in r1:
            state = _ok(runtime.report_result(state, m.id, m.participants[0]))

        # Round 2: should have 4 players (2 winners + 2 byes)
        r2 = [m for m in state.matches.values() if m.round == 2]
        r2_players = {p for m in r2 for p in m.participants}
        assert bye_players.issubset(r2_players), (
            f"Bye players {bye_players} missing from round 2: {r2_players}"
        )
        assert len(r2) == 2  # 4 players = 2 matches

        # Play through to completion
        for m in r2:
            state = _ok(runtime.report_result(state, m.id, m.participants[0]))
        r3 = [m for m in state.matches.values() if m.round == 3]
        assert len(r3) == 1  # final
        state = _ok(runtime.report_result(state, r3[0].id, r3[0].participants[0]))
        assert state.status == "completed"


# ===========================================================================
# Tournament Runtime — Swiss Progression
# ===========================================================================


class TestSwissProgression:
    def test_generates_next_round(self, runtime, players_4):
        state = runtime.create("t1", "swiss", "a", "duel", rounds=2, min_participants=2)
        for p in players_4:
            state = _ok(runtime.register(state, p))
        state = _ok(runtime.start(state))

        # Complete round 1
        r1_matches = [m for m in state.matches.values() if m.round == 1]
        for m in r1_matches:
            state = _ok(runtime.report_result(state, m.id, m.participants[0]))

        # Round 2 should be generated
        r2_matches = [m for m in state.matches.values() if m.round == 2]
        assert len(r2_matches) == 2

    def test_completes_after_max_rounds(self, runtime, players_4):
        state = runtime.create("t1", "swiss", "a", "duel", rounds=2, min_participants=2)
        for p in players_4:
            state = _ok(runtime.register(state, p))
        state = _ok(runtime.start(state))

        # Play all rounds
        for round_num in range(1, 3):
            round_matches = [
                m
                for m in state.matches.values()
                if m.round == round_num and m.status != "completed"
            ]
            for m in round_matches:
                state = _ok(runtime.report_result(state, m.id, m.participants[0]))

        assert state.status == "completed"
        assert state.winner is not None

    def test_buchholz_tiebreaker(self, runtime):
        """Buchholz = sum of opponents' points. Used for Swiss tiebreaker."""
        state = runtime.create(
            "t-buch", "swiss", "a", "duel", rounds=2, min_participants=2
        )
        for p in ["a", "b", "c", "d"]:
            state = _ok(runtime.register(state, p))
        state = _ok(runtime.start(state))

        # Round 1: complete all matches
        r1 = [m for m in state.matches.values() if m.round == 1]
        for m in r1:
            state = _ok(runtime.report_result(state, m.id, m.participants[0]))

        # After round 1, every player who played has opponents with known points
        # Buchholz should be non-trivial (at least some > 0)
        any_nonzero = any(s.buchholz > 0 for s in state.standings.values())
        assert any_nonzero, "Buchholz should be computed after results"

        # Winners have buchholz = losers' points (0), losers have buchholz = winners' points (3)
        for s in state.standings.values():
            if s.wins > 0:
                # Won against someone with 0 points
                assert s.buchholz == 0.0
            elif s.losses > 0:
                # Lost to someone with 3 points
                assert s.buchholz == 3.0


# ===========================================================================
# Tournament Runtime — Edge Cases
# ===========================================================================


class TestEdgeCases:
    def test_report_unknown_match(self, runtime):
        state = runtime.create("t1", "round_robin", "a", "duel", min_participants=2)
        state = _ok(runtime.register(state, "a"))
        state = _ok(runtime.register(state, "b"))
        state = _ok(runtime.start(state))
        result = runtime.report_result(state, "nonexistent", "a")
        assert not result["ok"]
        assert result["error"]["code"] == "match_not_found"

    def test_report_wrong_winner(self, runtime):
        state = runtime.create("t1", "round_robin", "a", "duel", min_participants=2)
        state = _ok(runtime.register(state, "a"))
        state = _ok(runtime.register(state, "b"))
        state = _ok(runtime.start(state))
        match = list(state.matches.values())[0]
        result = runtime.report_result(state, match.id, "charlie")
        assert not result["ok"]
        assert result["error"]["code"] == "winner_not_in_match"

    def test_report_completed_match(self, runtime):
        state = runtime.create("t1", "round_robin", "a", "duel", min_participants=2)
        state = _ok(runtime.register(state, "a"))
        state = _ok(runtime.register(state, "b"))
        state = _ok(runtime.start(state))
        match = list(state.matches.values())[0]
        state = _ok(runtime.report_result(state, match.id, "a"))
        result = runtime.report_result(state, match.id, "b")
        assert not result["ok"]
        assert result["error"]["code"] == "match_completed"

    def test_two_player_tournament(self, runtime):
        state = runtime.create("t1", "round_robin", "a", "duel", min_participants=2)
        state = _ok(runtime.register(state, "a"))
        state = _ok(runtime.register(state, "b"))
        state = _ok(runtime.start(state))
        assert len(state.matches) == 1
        match = list(state.matches.values())[0]
        state = _ok(runtime.report_result(state, match.id, "a"))
        assert state.status == "completed"
        assert state.winner == "a"


# ===========================================================================
# Auto-Report: game end → tournament auto-updated
# ===========================================================================


class TestAutoReport:
    """Test that game completion auto-reports results to tournament."""

    @pytest.fixture(autouse=True)
    def _clean(self):
        reset_tournaments()
        for sid in list(list_sessions()):
            remove_session(sid)
        yield
        reset_tournaments()
        for sid in list(list_sessions()):
            remove_session(sid)

    @pytest.mark.asyncio
    async def test_auto_report_on_game_end(self):
        """Play auction to completion via advance_phase; tournament standings auto-update."""
        from games import REGISTRY

        compiled = REGISTRY["auction"]
        players = ["alice", "bob", "charlie"]

        # Create and start tournament
        ts = create_tournament(
            tournament_id="auto-1",
            tournament_type="round_robin",
            host="alice",
            game_type="auction",
            min_participants=3,
        )
        for p in players:
            await ts.register(p)
        await ts.start(compiled)

        assert ts.state.status == "in_progress"
        assert len(ts.state.matches) == 3  # 3C2

        # Pick first active match
        match = None
        match_id = None
        for mid, m in ts.state.matches.items():
            if m.status == "active":
                match = m
                match_id = mid
                break
        assert match is not None

        # Get the game session spawned for this match
        session_id = ts.get_match_session_id(match_id)
        assert session_id is not None
        game_session = get_session(session_id)
        assert game_session is not None

        # Play the game: advance phases until game ends
        for _ in range(60):
            if game_session.state.status == "ended":
                break
            await game_session.advance_phase()

        assert game_session.state.status == "ended"

        # Auto-report should have fired — check tournament standings
        ts = get_tournament("auto-1")
        updated_match = ts.state.matches[match_id]
        assert updated_match.status == "completed"
        assert updated_match.winner is not None

        # Standings should reflect the result
        winner = updated_match.winner
        assert ts.state.standings[winner].wins == 1

    @pytest.mark.asyncio
    async def test_auto_report_full_tournament_completion(self):
        """Play ALL matches to completion; tournament should auto-complete."""
        from games import REGISTRY

        compiled = REGISTRY["auction"]
        players = ["p1", "p2", "p3"]

        ts = create_tournament(
            tournament_id="auto-full",
            tournament_type="round_robin",
            host="p1",
            game_type="auction",
            min_participants=3,
        )
        for p in players:
            await ts.register(p)
        await ts.start(compiled)

        # Play all 3 matches to completion
        for match_id in list(ts.state.matches.keys()):
            session_id = ts.get_match_session_id(match_id)
            game_session = get_session(session_id)
            assert game_session is not None

            for _ in range(60):
                if game_session.state.status == "ended":
                    break
                await game_session.advance_phase()

            assert game_session.state.status == "ended"

        # Tournament should be completed with a winner
        ts = get_tournament("auto-full")
        assert ts.state.status == "completed"
        assert ts.state.winner is not None
        # All matches completed
        for m in ts.state.matches.values():
            assert m.status == "completed"

    @pytest.mark.asyncio
    async def test_auto_report_no_double_report(self):
        """Manual report after auto-report should raise ValueError."""
        from games import REGISTRY

        compiled = REGISTRY["auction"]
        players = ["a", "b", "c"]

        ts = create_tournament(
            tournament_id="auto-double",
            tournament_type="round_robin",
            host="a",
            game_type="auction",
            min_participants=3,
        )
        for p in players:
            await ts.register(p)
        await ts.start(compiled)

        match_id = next(
            mid for mid, m in ts.state.matches.items() if m.status == "active"
        )
        session_id = ts.get_match_session_id(match_id)
        game_session = get_session(session_id)

        # Play to completion
        for _ in range(60):
            if game_session.state.status == "ended":
                break
            await game_session.advance_phase()

        assert game_session.state.status == "ended"

        # Auto-report already fired
        ts = get_tournament("auto-double")
        assert ts.state.matches[match_id].status == "completed"

        # Manual report should fail (already completed)
        with pytest.raises(ValueError, match="already completed"):
            await ts.report_result(match_id, "a", compiled=compiled)

    @pytest.mark.asyncio
    async def test_phase_timeout_auto_advances(self):
        """Phase timeout expires pending deals and advances phase."""
        from unittest.mock import AsyncMock, patch

        from games import REGISTRY
        from server.sessions import GameSession

        compiled = REGISTRY["auction"]
        session = GameSession("timeout-test", compiled, ["a", "b", "c"], seed=1)

        # Patch asyncio.sleep inside the sessions module so timeout fires instantly
        real_sleep = asyncio.sleep

        async def instant_sleep(duration):
            # Let the real event loop tick but don't actually wait
            await real_sleep(0)

        with patch("server.sessions.asyncio.sleep", side_effect=instant_sleep):
            await session.start()

            initial_phase = session.state.phase
            # Let the event loop run timeout handlers
            for _ in range(20):
                await real_sleep(0.01)
                if (
                    session.state.phase != initial_phase
                    or session.state.status == "ended"
                ):
                    break

        assert session.state.phase != initial_phase or session.state.status == "ended"

    @pytest.mark.asyncio
    async def test_phase_timer_cancelled_on_manual_advance(self):
        """Manual advance_phase cancels the timeout timer."""
        from games import REGISTRY
        from server.sessions import GameSession

        compiled = REGISTRY["auction"]
        session = GameSession(
            "timeout-cancel", compiled, ["a", "b", "c"], seed=1, phase_timeout=60
        )
        await session.start()

        # Timer should be running
        assert session._timeout_task is not None
        old_task = session._timeout_task
        assert not old_task.done()

        # Manual advance cancels old timer and starts new one
        await session.advance_phase()
        # Let cancellation propagate through event loop
        await asyncio.sleep(0)
        assert old_task.cancelled() or old_task.done()
        # New timer running for new phase
        if session.state.status != "ended":
            assert session._timeout_task is not None
            assert session._timeout_task is not old_task

    @pytest.mark.asyncio
    async def test_phase_timeout_expires_pending_deals(self):
        """Phase timeout rejects pending deals before advancing."""
        from unittest.mock import patch

        from games import REGISTRY
        from server.sessions import GameSession

        compiled = REGISTRY["auction"]
        session = GameSession("timeout-deals", compiled, ["a", "b", "c"], seed=1)

        real_sleep = asyncio.sleep

        async def instant_sleep(duration):
            await real_sleep(0)

        # Start normally (real timers)
        await session.start()
        # Cancel any running timer
        session._cancel_phase_timer()

        # Create a pending deal (buy_info is available in preview phase)
        result = await session.execute_deal("buy_info", actor_id="a")

        # Now patch sleep and manually trigger a timeout
        with patch("server.sessions.asyncio.sleep", side_effect=instant_sleep):
            session._start_phase_timer()
            for _ in range(20):
                await real_sleep(0.01)
                if session.state.phase != "preview" or session.state.status == "ended":
                    break

        # Phase should have advanced (pending deals expired)
        assert session.state.phase != "preview" or session.state.status == "ended"
        # No pending deals remaining
        assert len(session.state.pending_deals) == 0

    @pytest.mark.asyncio
    async def test_match_timeout_forces_draw(self):
        """Match timeout in tournament forces a draw result."""
        from games import REGISTRY

        compiled = REGISTRY["auction"]

        from tournament.config import TournamentConfig

        reset_tournaments()
        ts = create_tournament(
            tournament_id="match-timeout",
            tournament_type="round_robin",
            host="host",
            game_type="auction",
            min_participants=3,
            config=TournamentConfig(match_timeout=0),
        )
        for p in ["alice", "bob", "charlie"]:
            await ts.register(p)

        await ts.start(compiled)

        # Let match timeouts fire
        for _ in range(30):
            await asyncio.sleep(0.01)
            if ts.state.status == "completed":
                break

        # Tournament should have completed (all matches timed out as draws)
        assert ts.state.status == "completed"
        # All matches completed
        for m in ts.state.matches.values():
            assert m.status == "completed"
        # Standings should reflect draws
        for s in ts.state.standings.values():
            assert s.draws > 0

    @pytest.mark.asyncio
    async def test_get_status_shows_game_over_hint(self):
        """get_status should show 'Game over' hint when game ended in tournament."""
        from games import REGISTRY
        from mcp.agents import AgentState

        compiled = REGISTRY["auction"]

        ts = create_tournament(
            tournament_id="auto-hint",
            tournament_type="round_robin",
            host="alice",
            game_type="auction",
            min_participants=3,
        )
        for p in ["alice", "bob", "charlie"]:
            await ts.register(p)
        await ts.start(compiled)

        match_id = next(
            mid for mid, m in ts.state.matches.items() if m.status == "active"
        )
        session_id = ts.get_match_session_id(match_id)
        game_session = get_session(session_id)

        # Play to completion
        for _ in range(60):
            if game_session.state.status == "ended":
                break
            await game_session.advance_phase()

        assert game_session.state.status == "ended"

        # Simulate agent state: in_game from tournament
        agent = AgentState(agent_id="alice")
        agent.to_tournament("auto-hint")
        agent.to_game_from_tournament(session_id, "alice", "auction", match_id)

        # Build MCP server and check status
        from mcp.server import MCPServer

        class FakeSessionStore:
            def get(self, sid):
                return get_session(sid)

            def list_all(self):
                return {}

            def create(self, *a, **kw):
                pass

            def remove(self, *a):
                pass

        server = MCPServer(sessions=FakeSessionStore())
        result = server._tool_get_status(agent)
        text = result["content"][0]["text"]
        assert "GAME OVER" in text
        assert "leave_game" in text
        assert "tournament" in text.lower()


    @pytest.mark.asyncio
    async def test_auto_report_draw_on_game_without_winner(self):
        """When a game ends without winner, auto-report calls report_draw."""
        from unittest.mock import AsyncMock, patch

        from games import REGISTRY

        compiled = REGISTRY["auction"]
        players = ["x", "y", "z"]

        ts = create_tournament(
            tournament_id="auto-draw",
            tournament_type="round_robin",
            host="x",
            game_type="auction",
            min_participants=3,
        )
        for p in players:
            await ts.register(p)
        await ts.start(compiled)

        match_id = next(
            mid for mid, m in ts.state.matches.items() if m.status == "active"
        )
        session_id = ts.get_match_session_id(match_id)
        game_session = get_session(session_id)

        # Force game to end without winner by patching victory result
        from engine.runtime.state import GameState

        ended_state = attrs.evolve(
            game_session.state,
            status="ended",
            victory_result={},  # No winner
        )
        game_session._state = ended_state
        await game_session._notify()

        # Give the event loop a moment for the callback to fire
        await asyncio.sleep(0.05)

        # Match should be completed as a draw
        ts = get_tournament("auto-draw")
        updated = ts.state.matches[match_id]
        assert updated.status == "completed"
        assert updated.winner is None  # draw
        # All participants should have 1 point each (draw credit)
        for p in updated.participants:
            assert ts.state.standings[p].draws >= 1


# ===========================================================================
# Draw Support
# ===========================================================================


class TestDrawSupport:
    def test_report_draw_runtime(self, runtime):
        """Draw: each participant gets 1 point, no winner."""
        state = runtime.create("t1", "round_robin", "a", "duel", min_participants=2)
        state = _ok(runtime.register(state, "a"))
        state = _ok(runtime.register(state, "b"))
        state = _ok(runtime.start(state))
        match_id = list(state.matches.keys())[0]

        state = _ok(runtime.report_draw(state, match_id))

        assert state.matches[match_id].status == "completed"
        assert state.matches[match_id].winner is None
        assert state.standings["a"].draws == 1
        assert state.standings["b"].draws == 1
        assert state.standings["a"].points == 1
        assert state.standings["b"].points == 1
        assert state.standings["a"].wins == 0
        assert state.standings["b"].losses == 0

    def test_report_draw_se_eliminates_both(self, runtime):
        """In SE, a draw means no one advances from that match."""
        state = runtime.create("t2", "single_elimination", "a", "duel")
        for p in ["a", "b", "c", "d"]:
            state = _ok(runtime.register(state, p))
        state = _ok(runtime.start(state))

        # Draw first match, win second
        matches = sorted(state.matches.values(), key=lambda m: m.id)
        state = _ok(runtime.report_draw(state, matches[0].id))
        state = _ok(runtime.report_result(state, matches[1].id, matches[1].participants[0]))

        # Only 1 winner from round 1 → no round 2 (< 2 winners)
        r2 = [m for m in state.matches.values() if m.round == 2]
        assert len(r2) == 0

    def test_report_draw_already_completed(self, runtime):
        """Drawing an already completed match fails."""
        state = runtime.create("t3", "round_robin", "a", "duel", min_participants=2)
        state = _ok(runtime.register(state, "a"))
        state = _ok(runtime.register(state, "b"))
        state = _ok(runtime.start(state))
        match_id = list(state.matches.keys())[0]
        state = _ok(runtime.report_result(state, match_id, "a"))

        result = runtime.report_draw(state, match_id)
        assert not result["ok"]
        assert result["error"]["code"] == "match_completed"


# ===========================================================================
# Phase 5: Multi-player match_size
# ===========================================================================


class TestMultiPlayerMatchSize:
    """Tests for SE and Swiss with match_size > 2."""

    def test_se_match_size_3(self):
        """Single elimination with 3-player pods."""
        players = ["a", "b", "c", "d", "e", "f"]
        matches = MatchGenerator.single_elimination(players, seed=42, match_size=3)
        # 6 players / 3 per pod = 2 matches
        assert len(matches) == 2
        assert all(len(m.participants) == 3 for m in matches)
        all_p = set()
        for m in matches:
            all_p.update(m.participants)
        assert all_p == set(players)

    def test_se_match_size_4_with_remainder(self):
        """SE with 7 players, match_size=4 → pod of 4 + pod of 3."""
        players = [f"p{i}" for i in range(7)]
        matches = MatchGenerator.single_elimination(players, seed=42, match_size=4)
        assert len(matches) == 2
        sizes = sorted(len(m.participants) for m in matches)
        assert sizes == [3, 4]

    def test_se_next_round_match_size_3(self):
        """Next elimination round groups winners into pods of 3."""
        completed = [
            Match(
                id="r1-0", participants=("a", "b", "c"), status="completed", winner="a"
            ),
            Match(
                id="r1-1", participants=("d", "e", "f"), status="completed", winner="d"
            ),
            Match(
                id="r1-2", participants=("g", "h", "i"), status="completed", winner="g"
            ),
        ]
        next_round = MatchGenerator.next_elimination_round(completed, 2, match_size=3)
        assert len(next_round) == 1
        assert set(next_round[0].participants) == {"a", "d", "g"}

    def test_swiss_match_size_3(self):
        """Swiss pairing with 3-player groups."""
        players = ["a", "b", "c", "d", "e", "f"]
        standings = {p: Standing(participant=p) for p in players}
        matches = MatchGenerator.swiss_pairing(standings, set(), 1, match_size=3)
        assert len(matches) == 2
        assert all(len(m.participants) == 3 for m in matches)

    def test_runtime_se_match_size(self):
        """Runtime creates SE matches respecting match_size."""
        rt = TournamentRuntime()
        state = rt.create("t-ms", "single_elimination", "host", "auction", match_size=3)
        for p in ["a", "b", "c", "d", "e", "f"]:
            state = _ok(rt.register(state, p))
        state = _ok(rt.start(state))
        assert all(len(m.participants) == 3 for m in state.matches.values())

    def test_runtime_report_result_multi_player(self):
        """report_result handles multi-player match standings correctly."""
        rt = TournamentRuntime()
        state = rt.create("t-mp", "round_robin", "host", "auction", match_size=3)
        for p in ["a", "b", "c"]:
            state = _ok(rt.register(state, p))
        state = _ok(rt.start(state))
        match_id = list(state.matches.keys())[0]
        state = _ok(
            rt.report_result(state, match_id, "a", scores={"a": 3, "b": 1, "c": 0})
        )
        assert state.standings["a"].wins == 1
        assert state.standings["b"].losses == 1
        assert state.standings["c"].losses == 1

    def test_bye_advances_match_size_3(self):
        """Bye advances with match_size=3 and 7 players → 1 bye."""
        players = [f"p{i}" for i in range(7)]
        byes = MatchGenerator.get_bye_advances(players, seed=42, match_size=3)
        assert len(byes) == 1


# ===========================================================================
# Phase 5: Tournament Persistence
# ===========================================================================


class TestTournamentPersistence:
    """Tests for tournament state persistence."""

    def test_round_trip(self, tmp_path):
        """Save and load tournament state."""
        from tournament.persistence import (
            TournamentStore,
            _dict_to_state,
            _state_to_dict,
        )

        store = TournamentStore(path=tmp_path / "tournaments.json")
        rt = TournamentRuntime()
        state = rt.create("t-persist", "round_robin", "host", "duel")
        for p in ["alice", "bob", "charlie"]:
            state = _ok(rt.register(state, p))
        state = _ok(rt.start(state))

        # Save
        store.save(state)
        store.flush()

        # Load in a new store
        store2 = TournamentStore(path=tmp_path / "tournaments.json")
        loaded = store2.load()
        assert "t-persist" in loaded
        restored = loaded["t-persist"]

        assert restored.tournament_id == state.tournament_id
        assert restored.status == state.status
        assert restored.participants == state.participants
        assert len(restored.matches) == len(state.matches)
        for mid in state.matches:
            assert restored.matches[mid].participants == state.matches[mid].participants

    def test_persistence_with_results(self, tmp_path):
        """Persist tournament with completed matches and standings."""
        from tournament.persistence import TournamentStore

        store = TournamentStore(path=tmp_path / "tournaments.json")
        rt = TournamentRuntime()
        state = rt.create("t-results", "round_robin", "host", "duel")
        for p in ["alice", "bob"]:
            state = _ok(rt.register(state, p))
        state = _ok(rt.start(state))
        match_id = list(state.matches.keys())[0]
        state = _ok(
            rt.report_result(state, match_id, "alice", scores={"alice": 3, "bob": 1})
        )

        store.save(state)
        store.flush()

        store2 = TournamentStore(path=tmp_path / "tournaments.json")
        loaded = store2.load()
        restored = loaded["t-results"]
        assert restored.standings["alice"].wins == 1
        assert restored.standings["bob"].losses == 1
        assert restored.matches[match_id].winner == "alice"

    def test_remove(self, tmp_path):
        """Remove tournament from persistence."""
        from tournament.persistence import TournamentStore

        store = TournamentStore(path=tmp_path / "tournaments.json")
        rt = TournamentRuntime()
        state = rt.create("t-remove", "round_robin", "host", "duel")
        store.save(state)
        store.flush()

        store.remove("t-remove")
        store.flush()

        store2 = TournamentStore(path=tmp_path / "tournaments.json")
        loaded = store2.load()
        assert "t-remove" not in loaded


# ===========================================================================
# Match Timeout Cleanup (Fix 3)
# ===========================================================================


class TestMatchTimeoutCleanup:
    @pytest.fixture(autouse=True)
    def _clean(self):
        reset_tournaments()
        for sid in list(list_sessions()):
            remove_session(sid)
        yield
        reset_tournaments()
        for sid in list(list_sessions()):
            remove_session(sid)

    @pytest.mark.asyncio
    async def test_cancel_match_timeout_on_report(self):
        """_cancel_match_timeout removes task from _match_timeouts on report_result."""
        from games import REGISTRY

        compiled = REGISTRY["auction"]
        players = ["alice", "bob", "charlie"]

        ts = create_tournament(
            tournament_id="timeout-cleanup",
            tournament_type="round_robin",
            host="alice",
            game_type="auction",
            min_participants=3,
        )
        for p in players:
            await ts.register(p)
        await ts.start(compiled)

        # Pick first active match
        match_id = None
        for mid, m in ts.state.matches.items():
            if m.status == "active":
                match_id = mid
                break
        assert match_id is not None

        # Inject a fake timeout task
        fake_task = asyncio.ensure_future(asyncio.sleep(9999))
        ts._match_timeouts[match_id] = fake_task
        assert match_id in ts._match_timeouts

        # Report result — should cancel and remove the timeout task
        await ts.report_result(match_id, "alice", compiled=compiled)

        assert match_id not in ts._match_timeouts
        # Task may be in "cancelling" state; check it's no longer pending
        assert fake_task.cancelling() > 0 or fake_task.cancelled() or fake_task.done()

    def test_cancel_match_timeout_nonexistent(self):
        """_cancel_match_timeout is safe for non-existent match IDs."""
        ts = TournamentSession.__new__(TournamentSession)
        ts._match_timeouts = {}
        # Should not raise
        ts._cancel_match_timeout("no-such-match")

    def test_cancel_match_timeout_already_done(self):
        """_cancel_match_timeout handles already-done tasks gracefully."""
        ts = TournamentSession.__new__(TournamentSession)
        ts._match_timeouts = {}

        # Create a task that completes immediately
        loop = asyncio.new_event_loop()
        task = loop.create_future()
        task.set_result(None)
        ts._match_timeouts["done-match"] = task

        # Should not raise, should remove from dict
        ts._cancel_match_timeout("done-match")
        assert "done-match" not in ts._match_timeouts
        loop.close()


# ===========================================================================
# Tournament Config Tests
# ===========================================================================


class TestTournamentConfig:
    """Tests for declarative tournament configuration."""

    def test_defaults_match_old_hardcoded_values(self):
        """TournamentConfig defaults must match the previously hardcoded values."""
        from tournament.config import TournamentConfig

        cfg = TournamentConfig()
        assert cfg.win_points == 3
        assert cfg.draw_points == 1
        assert cfg.loss_points == 0
        assert cfg.tiebreaker == ("points", "goal_diff", "wins")
        assert cfg.match_timeout == 1800
        assert cfg.phase_timeout == 300
        assert cfg.winner_credit == 100
        assert cfg.participation_credit == 10
        assert cfg.draw_credit == 30
        assert cfg.swiss_max_rounds is None

    def test_custom_scoring_flows_to_standings(self, runtime):
        """Custom win_points and draw_points appear in standings."""
        from tournament.config import TournamentConfig

        config = TournamentConfig(win_points=5, draw_points=2)
        state = runtime.create("t-score", "round_robin", "host", "duel", config=config)
        for p in ["a", "b", "c"]:
            state = _ok(runtime.register(state, p))
        state = _ok(runtime.start(state))

        # Report a win
        match_id = next(iter(state.matches))
        state = _ok(runtime.report_result(state, match_id, "a"))
        assert state.standings["a"].points == 5  # custom win_points

        # Report a draw on a different match
        remaining = [mid for mid, m in state.matches.items() if m.status != "completed"]
        if remaining:
            state = _ok(runtime.report_draw(state, remaining[0]))
            # Find a participant of that draw match
            draw_match = state.matches[remaining[0]]
            for p in draw_match.participants:
                # Points should include the draw_points
                assert state.standings[p].points >= 2

    def test_custom_tiebreaker_order(self, runtime):
        """Custom tiebreaker changes standings sort order."""
        from tournament.config import TournamentConfig

        # Sort by wins first, then points
        config = TournamentConfig(tiebreaker=("wins", "points"))
        state = runtime.create("t-tie", "round_robin", "host", "duel", config=config)
        for p in ["a", "b"]:
            state = _ok(runtime.register(state, p))
        state = _ok(runtime.start(state))

        match_id = next(iter(state.matches))
        state = _ok(runtime.report_result(state, match_id, "a"))

        sorted_standings = runtime.get_standings_sorted(state)
        assert sorted_standings[0].participant == "a"

    def test_config_persists_and_restores(self, tmp_path):
        """Config survives persistence round-trip."""
        from tournament.config import TournamentConfig
        from tournament.persistence import TournamentStore

        config = TournamentConfig(win_points=10, winner_credit=500, draw_points=4)
        rt = TournamentRuntime()
        state = rt.create("t-cfg", "round_robin", "host", "duel", config=config)

        store = TournamentStore(path=tmp_path / "tournaments.json")
        store.save(state)
        store.flush()

        store2 = TournamentStore(path=tmp_path / "tournaments.json")
        loaded = store2.load()
        restored = loaded["t-cfg"]
        assert restored.config.win_points == 10
        assert restored.config.winner_credit == 500
        assert restored.config.draw_points == 4

    def test_old_format_without_config_gets_defaults(self):
        """Persisted data without 'config' key deserializes to defaults."""
        from tournament.config import TournamentConfig
        from tournament.persistence import _dict_to_state

        old_dict = {
            "tournament_id": "old",
            "tournament_type": "round_robin",
            "status": "registration",
            "host": "",
            "name": "",
            "game_type": "",
            "min_participants": 2,
            "max_participants": 16,
            "match_size": 2,
            "rounds": None,
            "participants": [],
            "matches": {},
            "standings": {},
            "winner": None,
            "seed": 42,
            # No "config" key
        }
        state = _dict_to_state(old_dict)
        assert state.config == TournamentConfig()

    def test_swiss_max_rounds_from_config(self, runtime):
        """swiss_max_rounds config overrides the auto formula."""
        from tournament.config import TournamentConfig

        # For 4 players, formula gives max(3, int(2*2))=4 rounds
        # Config caps at 2
        config = TournamentConfig(swiss_max_rounds=2)
        state = runtime.create(
            "t-swiss-cfg", "swiss", "host", "duel", config=config
        )
        for p in ["a", "b", "c", "d"]:
            state = _ok(runtime.register(state, p))
        state = _ok(runtime.start(state))

        # Complete round 1 matches
        for mid in list(state.matches):
            if state.matches[mid].status == "pending":
                # Just report as draw
                state = _ok(runtime.report_draw(state, mid))

        # Round 2 should be generated
        round_2 = [m for m in state.matches.values() if m.round == 2]
        assert len(round_2) > 0

        # Complete round 2
        for mid in list(state.matches):
            if state.matches[mid].status == "pending":
                state = _ok(runtime.report_draw(state, mid))

        # Tournament should be completed after 2 rounds (not 4)
        assert state.status == "completed"

    def test_state_gets_default_config(self, runtime):
        """Creating tournament without explicit config gets TournamentConfig defaults."""
        from tournament.config import TournamentConfig

        state = runtime.create("t-default", "round_robin", "host", "duel")
        assert state.config == TournamentConfig()


# ===========================================================================
# Tournament Audit Fixes
# ===========================================================================


class TestCancelTournament:
    """Tests for tournament cancellation."""

    def test_cancel_in_registration(self, runtime):
        """Host can cancel during registration."""
        state = runtime.create("t-cancel", "round_robin", "host", "duel")
        state = _ok(runtime.register(state, "host"))
        state = _ok(runtime.register(state, "alice"))
        result = runtime.cancel(state, "host")
        assert result["ok"]
        assert result["state"].status == "cancelled"

    def test_cancel_in_progress(self, runtime):
        """Host can cancel during in_progress."""
        state = runtime.create(
            "t-cancel2", "round_robin", "host", "duel", min_participants=2
        )
        state = _ok(runtime.register(state, "host"))
        state = _ok(runtime.register(state, "alice"))
        state = _ok(runtime.start(state))
        assert state.status == "in_progress"
        result = runtime.cancel(state, "host")
        assert result["ok"]
        assert result["state"].status == "cancelled"

    def test_cancel_requires_host(self, runtime):
        """Non-host cannot cancel."""
        state = runtime.create("t-cancel3", "round_robin", "host", "duel")
        state = _ok(runtime.register(state, "host"))
        state = _ok(runtime.register(state, "alice"))
        result = runtime.cancel(state, "alice")
        assert not result["ok"]
        assert result["error"]["code"] == "not_host"

    def test_cancel_completed_fails(self, runtime):
        """Cannot cancel a completed tournament."""
        state = runtime.create(
            "t-cancel4", "round_robin", "host", "duel", min_participants=2
        )
        state = _ok(runtime.register(state, "host"))
        state = _ok(runtime.register(state, "alice"))
        state = _ok(runtime.start(state))
        # Complete all matches
        for mid in list(state.matches):
            state = _ok(runtime.report_result(state, mid, "host"))
        assert state.status == "completed"
        result = runtime.cancel(state, "host")
        assert not result["ok"]
        assert result["error"]["code"] == "tournament_cancelled"

    def test_cancel_already_cancelled(self, runtime):
        """Cannot cancel an already cancelled tournament."""
        state = runtime.create("t-cancel5", "round_robin", "host", "duel")
        state = _ok(runtime.cancel(state, "host"))
        result = runtime.cancel(state, "host")
        assert not result["ok"]

    def test_report_result_on_cancelled_fails(self, runtime):
        """Cannot report results on a cancelled tournament."""
        state = runtime.create(
            "t-cancel6", "round_robin", "host", "duel", min_participants=2
        )
        state = _ok(runtime.register(state, "host"))
        state = _ok(runtime.register(state, "alice"))
        state = _ok(runtime.start(state))
        match_id = next(iter(state.matches))
        state = _ok(runtime.cancel(state, "host"))
        # Attempt report_result on cancelled
        result = runtime.report_result(state, match_id, "host")
        assert not result["ok"]
        assert result["error"]["code"] == "tournament_cancelled"
        # Attempt report_draw on cancelled
        result = runtime.report_draw(state, match_id)
        assert not result["ok"]
        assert result["error"]["code"] == "tournament_cancelled"

    @pytest.mark.asyncio
    async def test_session_cancel_stops_timeouts(self):
        """TournamentSession.cancel() clears match timeouts."""
        from games import REGISTRY

        compiled = REGISTRY["auction"]
        from tournament.config import TournamentConfig

        reset_tournaments()
        ts = create_tournament(
            tournament_id="cancel-timeout",
            tournament_type="round_robin",
            host="host",
            game_type="auction",
            min_participants=3,
            config=TournamentConfig(match_timeout=9999),
        )
        for p in ["host", "alice", "bob"]:
            await ts.register(p)
        await ts.start(compiled)

        # Should have active match timeouts
        assert len(ts._match_timeouts) > 0

        await ts.cancel("host")
        assert ts.state.status == "cancelled"
        assert len(ts._match_timeouts) == 0


class TestReportMatchAuth:
    """Tests for report_match_result authorization."""

    @pytest.mark.asyncio
    async def test_non_participant_rejected(self):
        """Non-participant non-host agent cannot report results."""
        from games import REGISTRY
        from mcp.agents import AgentState
        from mcp.server import MCPServer

        compiled = REGISTRY["auction"]
        reset_tournaments()
        ts = create_tournament(
            tournament_id="auth-test",
            tournament_type="round_robin",
            host="host",
            game_type="auction",
            min_participants=3,
        )
        for p in ["host", "alice", "bob"]:
            await ts.register(p)
        await ts.start(compiled)

        match_id = next(iter(ts.state.matches))
        match = ts.state.matches[match_id]

        # Create an agent who is NOT in the match and not host
        outsider = AgentState(agent_id="outsider")
        outsider.to_tournament("auth-test")

        class FakeSessionStore:
            def get(self, sid):
                return None
            def list(self):
                return {}
        class FakeTournamentStore:
            def get(self, tid):
                return get_tournament(tid)
            def list(self):
                return {}
            def create(self, **kw):
                return None

        server = MCPServer(FakeSessionStore(), FakeTournamentStore(), REGISTRY)
        result = await server._tool_report_match_result(
            outsider, {"match_id": match_id, "winner": match.participants[0]}
        )
        assert "error" in result or "Only match participants" in str(result)

    @pytest.mark.asyncio
    async def test_host_can_report(self):
        """Tournament host can report match results."""
        from games import REGISTRY
        from mcp.agents import AgentState
        from mcp.server import MCPServer

        compiled = REGISTRY["auction"]
        reset_tournaments()
        ts = create_tournament(
            tournament_id="auth-host",
            tournament_type="round_robin",
            host="host",
            game_type="auction",
            min_participants=3,
        )
        for p in ["host", "alice", "bob"]:
            await ts.register(p)
        await ts.start(compiled)

        match_id = next(iter(ts.state.matches))
        match = ts.state.matches[match_id]

        host_agent = AgentState(agent_id="host")
        host_agent.to_tournament("auth-host")

        class FakeSessionStore:
            def get(self, sid):
                return None
            def list(self):
                return {}
        class FakeTournamentStore:
            def get(self, tid):
                return get_tournament(tid)
            def list(self):
                return {}
            def create(self, **kw):
                return None

        server = MCPServer(FakeSessionStore(), FakeTournamentStore(), REGISTRY)
        result = await server._tool_report_match_result(
            host_agent, {"match_id": match_id, "winner": match.participants[0]}
        )
        # Host should succeed (no auth error)
        assert "Only match participants" not in str(result)


class TestSwissBye:
    """Tests for Swiss bye handling with odd players."""

    def test_odd_players_bye_match(self):
        """5 players: 2 matches + 1 bye, all players accounted for."""
        players = ["a", "b", "c", "d", "e"]
        standings = {p: Standing(participant=p) for p in players}
        matches = MatchGenerator.swiss_pairing(standings, set(), 1)

        all_players = set()
        bye_matches = []
        regular_matches = []
        for m in matches:
            all_players.update(m.participants)
            if len(m.participants) == 1:
                bye_matches.append(m)
            else:
                regular_matches.append(m)

        assert all_players == set(players), "All players should be in a match"
        assert len(regular_matches) == 2
        assert len(bye_matches) == 1

    def test_bye_gets_win_points(self, runtime):
        """Bye player receives win_points in standings."""
        state = runtime.create(
            "t-bye", "swiss", "host", "duel", min_participants=2, rounds=1
        )
        for p in ["a", "b", "c"]:
            state = _ok(runtime.register(state, p))
        state = _ok(runtime.start(state))

        # Find the bye match (1 participant)
        bye_matches = [
            m for m in state.matches.values() if len(m.participants) == 1
        ]
        assert len(bye_matches) == 1, "Should have exactly 1 bye match"
        bye_player = bye_matches[0].participants[0]

        # Bye should already be auto-completed
        assert bye_matches[0].status == "completed" or state.matches[bye_matches[0].id].status == "completed"
        assert state.standings[bye_player].wins == 1
        assert state.standings[bye_player].points == state.config.win_points

    def test_even_players_no_bye(self):
        """4 players: exactly 2 matches, no bye."""
        players = ["a", "b", "c", "d"]
        standings = {p: Standing(participant=p) for p in players}
        matches = MatchGenerator.swiss_pairing(standings, set(), 1)
        assert all(len(m.participants) == 2 for m in matches)
        assert len(matches) == 2

    def test_swiss_match_size_3_bye(self):
        """7 players with match_size=3: 2 groups of 3 + 1 bye."""
        players = ["a", "b", "c", "d", "e", "f", "g"]
        standings = {p: Standing(participant=p) for p in players}
        matches = MatchGenerator.swiss_pairing(standings, set(), 1, match_size=3)

        regular = [m for m in matches if len(m.participants) >= 2]
        byes = [m for m in matches if len(m.participants) == 1]
        assert len(regular) == 2
        assert len(byes) == 1

        all_players = set()
        for m in matches:
            all_players.update(m.participants)
        assert all_players == set(players)


class TestSwissHistoryMultiPlayer:
    """Tests for Swiss rematch avoidance with match_size > 2."""

    def test_match_size_3_avoids_rematches(self):
        """Swiss match_size=3 tries to avoid exact group rematches."""
        players = ["a", "b", "c", "d", "e", "f"]
        standings = {p: Standing(participant=p) for p in players}
        r1 = MatchGenerator.swiss_pairing(standings, set(), 1, match_size=3)

        # Build history from r1
        history = {frozenset(m.participants) for m in r1 if len(m.participants) >= 2}

        r2 = MatchGenerator.swiss_pairing(standings, history, 2, match_size=3)
        for m in r2:
            if len(m.participants) < 2:
                continue
            group = frozenset(m.participants)
            assert group not in history, f"Exact rematch: {group}"

    def test_frozenset_history_works_for_2_player(self):
        """Frozenset history works for standard 2-player swiss."""
        players = ["a", "b", "c", "d"]
        standings = {p: Standing(participant=p) for p in players}
        r1 = MatchGenerator.swiss_pairing(standings, set(), 1)
        history = {frozenset(m.participants) for m in r1 if len(m.participants) >= 2}

        r2 = MatchGenerator.swiss_pairing(standings, history, 2)
        for m in r2:
            if len(m.participants) < 2:
                continue
            pair = frozenset(m.participants)
            assert pair not in history, f"Rematch: {pair}"

    def test_runtime_swiss_progression_uses_frozenset(self, runtime):
        """Runtime swiss progression correctly tracks multi-player history."""
        state = runtime.create(
            "t-hist", "swiss", "host", "duel",
            min_participants=2, match_size=3, rounds=2
        )
        for p in ["a", "b", "c", "d", "e", "f"]:
            state = _ok(runtime.register(state, p))
        state = _ok(runtime.start(state))

        # Complete round 1
        for mid in list(state.matches):
            m = state.matches[mid]
            if m.status == "pending" and len(m.participants) >= 2:
                state = _ok(runtime.report_result(state, mid, m.participants[0]))

        # Round 2 should be generated
        round_2 = [m for m in state.matches.values() if m.round == 2]
        assert len(round_2) > 0

        # Check round 2 groups differ from round 1
        r1_groups = {
            frozenset(m.participants)
            for m in state.matches.values()
            if m.round == 1 and len(m.participants) >= 2
        }
        for m in round_2:
            if len(m.participants) >= 2:
                assert frozenset(m.participants) not in r1_groups


class TestOnCompleteOutsideLock:
    """Tests that _on_complete runs outside the lock."""

    @pytest.mark.asyncio
    async def test_completion_callback_can_access_state(self):
        """Completion callback should not deadlock (runs outside lock)."""
        reset_tournaments()
        rt = TournamentRuntime()
        state = rt.create(
            "t-lock", "round_robin", "host", "duel", min_participants=2
        )
        state = _ok(rt.register(state, "host"))
        state = _ok(rt.register(state, "alice"))

        ts = TournamentSession(state)
        state = _ok(rt.start(ts._state))
        ts._state = state

        callback_called = False

        async def on_complete(final_state):
            nonlocal callback_called
            # This would deadlock if called inside the lock
            callback_called = True
            assert final_state.status == "completed"

        ts.on_completion(on_complete)

        # Complete all matches
        for mid in list(ts._state.matches):
            m = ts._state.matches[mid]
            if m.status != "completed":
                await ts.report_result(mid, m.participants[0])

        assert ts.state.status == "completed"
        assert callback_called
