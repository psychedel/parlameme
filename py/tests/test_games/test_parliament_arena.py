"""Parliament Arena: The Last Assembly — game-specific integration tests.

Tests faction assignment, position elections, deal lifecycle, bill voting,
victory conditions, commitments, and MCP formatter integration.
"""

from __future__ import annotations

import pytest

from engine.archive import create_archive, verify
from engine.runtime.core import GameRuntime
from engine.runtime.state import GameState, view_for
from games.parliament_arena import parliament_arena
from mcp.formatters import _can_player_use_deal, format_available_actions

# =========================================================================
# Helpers
# =========================================================================

PLAYERS_6 = [f"p{i}" for i in range(6)]
PLAYERS_8 = [f"p{i}" for i in range(8)]

rt = GameRuntime(parliament_arena)


def _setup(players: list[str] = PLAYERS_6, seed: int = 42) -> GameState:
    """Start game and run setup phase."""
    state = rt.start_game(players, seed=seed)
    return rt.run_setup(state)


def _advance(state: GameState) -> GameState:
    """Record advance decision and advance phase."""
    state = state.record_decision({"type": "advance_phase"})
    return rt.advance_phase(state)


def _advance_to(state: GameState, target_phase: str, max_steps: int = 20) -> GameState:
    """Advance phases until reaching target_phase."""
    for _ in range(max_steps):
        if state.phase == target_phase:
            return state
        state = _advance(state)
    raise RuntimeError(f"Failed to reach phase '{target_phase}' after {max_steps} advances")


def _find_by_faction(state: GameState, faction: str) -> list[str]:
    """Find all players with given faction."""
    return [pid for pid in state.entities if state.get_attr(pid, "faction") == faction]


def _elect(state: GameState, subject: str, option: str = "elect") -> GameState:
    """Run a quick elect_position vote."""
    result = rt.start_vote(
        state, "elect_position", proposer_id=PLAYERS_6[0], subject_id=subject
    )
    assert result["ok"], f"start_vote elect_position failed: {result}"
    state = result["state"]
    iid = result["instance_id"]
    for pid in state.entities:
        if state.is_active(pid) and pid not in state.pending_votes[iid].votes:
            result = rt.cast_vote(state, iid, pid, option)
            if result["ok"]:
                state = result["state"]
    return state


# =========================================================================
# Setup & Faction Assignment
# =========================================================================


class TestSetup:
    def test_initial_phase_is_election(self):
        """After setup, game is in election phase."""
        state = _setup()
        assert state.phase == "election"

    def test_all_players_get_faction(self):
        """Every player has a faction assigned."""
        state = _setup()
        for pid in PLAYERS_6:
            faction = state.get_attr(pid, "faction")
            assert faction is not None, f"{pid} has no faction"

    def test_all_players_get_hidden_type(self):
        """Every player gets a hidden_type attr."""
        state = _setup()
        valid_types = {"loyalist", "opportunist", "ideologue", "chaotic"}
        for pid in PLAYERS_6:
            ht = state.get_attr(pid, "hidden_type")
            assert ht in valid_types, f"{pid} hidden_type={ht}"

    def test_initial_resources(self):
        """Players start with correct resources."""
        state = _setup()
        for pid in PLAYERS_6:
            assert state.get_resource(pid, "caps") == 100
            assert state.get_resource(pid, "rations") == 20
            assert state.get_resource(pid, "influence") == 10
            assert state.get_resource(pid, "reputation") == 50

    def test_game_vars_initialized(self):
        """Game variables set in setup."""
        state = _setup()
        assert state.get_game_var("vacant_position") == "speaker"
        assert state.get_game_var("bills_passed") == 0
        assert state.get_game_var("current_bill_type") == "taxation"

    def test_everyone_starts_with_position_none(self):
        """All players start with position=none."""
        state = _setup()
        for pid in PLAYERS_6:
            assert state.get_attr(pid, "position") == "none"
            # role is set by AssignRoles to faction role id (e.g. free_radical)
            role = state.get_attr(pid, "role")
            assert role is not None


# =========================================================================
# Election Phase
# =========================================================================


class TestElections:
    def test_elect_speaker(self):
        """Electing speaker sets position and advances vacant_position."""
        state = _setup()
        assert state.get_game_var("vacant_position") == "speaker"

        state = _elect(state, "p0")
        assert state.get_attr("p0", "position") == "speaker"
        # FIX-3: vacant position should auto-advance to prime_minister
        assert state.get_game_var("vacant_position") == "prime_minister"

    def test_elect_pm_after_speaker(self):
        """After speaker, electing PM sets position and cabinet."""
        state = _setup()
        state = _elect(state, "p0")  # speaker
        assert state.get_game_var("vacant_position") == "prime_minister"

        state = _elect(state, "p1")  # PM
        assert state.get_attr("p1", "position") == "prime_minister"
        assert state.get_attr("p1", "role") == "leader"
        assert state.get_game_var("vacant_position") == "opposition_leader"

    def test_elect_opposition_leader(self):
        """Third election fills opposition leader."""
        state = _setup()
        state = _elect(state, "p0")  # speaker
        state = _elect(state, "p1")  # PM
        state = _elect(state, "p2")  # opposition leader

        assert state.get_attr("p2", "position") == "opposition_leader"
        assert state.get_game_var("vacant_position") == "none"

    def test_oppose_election_keeps_position_vacant(self):
        """If election fails, vacant position stays."""
        state = _setup()
        # All vote oppose
        result = rt.start_vote(
            state, "elect_position", proposer_id="p0", subject_id="p0"
        )
        assert result["ok"]
        state = result["state"]
        iid = result["instance_id"]
        for pid in PLAYERS_6:
            result = rt.cast_vote(state, iid, pid, "oppose")
            if result["ok"]:
                state = result["state"]

        # Election failed — speaker still vacant
        assert state.get_attr("p0", "position") == "none"


# =========================================================================
# Deal Lifecycle
# =========================================================================


class TestDeals:
    def test_promise_deal(self):
        """Promise deal works in caucus phase."""
        state = _setup()
        state = _advance_to(state, "caucus")

        result = rt.start_deal(state, "promise", actor_id="p0", responder_id="p1")
        assert result["ok"], f"promise failed: {result}"
        state = result["state"]
        iid = result["instance_id"]

        result = rt.respond_to_deal(state, iid, "p1", "acknowledge")
        assert result["ok"]
        state = result["state"]
        # Proposer gets reputation boost
        assert state.get_resource("p0", "reputation") > 50

    def test_bribe_accept(self):
        """Bribe deal transfers caps on accept."""
        state = _setup()
        state = _advance_to(state, "floor")

        initial_caps_p1 = state.get_resource("p1", "caps")
        result = rt.start_deal(
            state, "bribe", actor_id="p0", responder_id="p1", params={"amount": 20}
        )
        assert result["ok"], f"bribe failed: {result}"
        state = result["state"]
        iid = result["instance_id"]

        result = rt.respond_to_deal(state, iid, "p1", "accept")
        assert result["ok"]
        state = result["state"]
        # p1 should have received the bribe
        assert state.get_resource("p1", "caps") >= initial_caps_p1 + 20

    def test_bribe_expose_damages_reputation(self):
        """Exposing bribe burns stakes and damages proposer reputation."""
        state = _setup()
        state = _advance_to(state, "floor")

        initial_rep_p0 = state.get_resource("p0", "reputation")
        result = rt.start_deal(
            state, "bribe", actor_id="p0", responder_id="p1", params={"amount": 20}
        )
        assert result["ok"]
        state = result["state"]
        iid = result["instance_id"]

        result = rt.respond_to_deal(state, iid, "p1", "expose")
        assert result["ok"]
        state = result["state"]
        # Proposer reputation damaged
        assert state.get_resource("p0", "reputation") < initial_rep_p0

    def test_speaker_set_agenda(self):
        """Speaker can set bill type."""
        state = _setup()
        # Elect a speaker
        state = _elect(state, "p0")
        state = _advance_to(state, "agenda")

        result = rt.start_deal(
            state, "speaker_set_agenda", actor_id="p0", params={"bill_type": "defense"}
        )
        assert result["ok"], f"speaker_set_agenda failed: {result}"
        state = result["state"]
        assert state.get_game_var("current_bill_type") == "defense"

    def test_only_speaker_can_set_agenda(self):
        """Non-speaker cannot use speaker_set_agenda."""
        state = _setup()
        state = _elect(state, "p0")  # p0 is speaker
        state = _advance_to(state, "agenda")

        # p1 is not speaker
        can = _can_player_use_deal(state, parliament_arena, "speaker_set_agenda", "p1")
        assert not can, "Non-speaker should not use speaker_set_agenda"

        can = _can_player_use_deal(state, parliament_arena, "speaker_set_agenda", "p0")
        assert can, "Speaker should use speaker_set_agenda"

    def test_only_pm_can_appoint(self):
        """Only PM can use appoint_position deal."""
        state = _setup()
        state = _elect(state, "p0")  # speaker
        state = _elect(state, "p1")  # PM

        state = _advance_to(state, "floor")

        can = _can_player_use_deal(state, parliament_arena, "appoint_position", "p1")
        assert can, "PM should be able to appoint"

        can = _can_player_use_deal(state, parliament_arena, "appoint_position", "p0")
        assert not can, "Non-PM should not appoint"


# =========================================================================
# Bill Voting
# =========================================================================


class TestBillVoting:
    def test_taxation_bill_gives_caps(self):
        """Supporting taxation bill boosts everyone's caps."""
        state = _setup()
        state = _advance_to(state, "vote")

        # bill_type is "taxation" by default
        initial_caps = {pid: state.get_resource(pid, "caps") for pid in PLAYERS_6}

        result = rt.start_vote(state, "bill_vote")
        assert result["ok"], f"start bill_vote failed: {result}"
        state = result["state"]
        iid = result["instance_id"]
        for pid in PLAYERS_6:
            if state.is_active(pid):
                result = rt.cast_vote(state, iid, pid, "support")
                if result["ok"]:
                    state = result["state"]

        # All should have more caps
        for pid in PLAYERS_6:
            assert state.get_resource(pid, "caps") >= initial_caps[pid] + 10

    def test_bill_oppose_no_effect(self):
        """Opposing bill has no resource effect."""
        state = _setup()
        state = _advance_to(state, "vote")

        initial_caps = {pid: state.get_resource(pid, "caps") for pid in PLAYERS_6}

        result = rt.start_vote(state, "bill_vote")
        assert result["ok"]
        state = result["state"]
        iid = result["instance_id"]
        for pid in PLAYERS_6:
            if state.is_active(pid):
                result = rt.cast_vote(state, iid, pid, "oppose")
                if result["ok"]:
                    state = result["state"]

        # Caps unchanged (caucus may have given bonuses already)
        for pid in PLAYERS_6:
            assert state.get_resource(pid, "caps") == initial_caps[pid]


# =========================================================================
# Expulsion Vote
# =========================================================================


class TestExpulsion:
    def test_expulsion_eliminates_player(self):
        """Supermajority expulsion eliminates the target."""
        state = _setup()
        state = _advance_to(state, "vote")

        target = "p3"
        result = rt.start_vote(state, "expulsion", proposer_id="p0", subject_id=target)
        assert result["ok"], f"start expulsion failed: {result}"
        state = result["state"]
        iid = result["instance_id"]
        for pid in PLAYERS_6:
            if state.is_active(pid):
                result = rt.cast_vote(state, iid, pid, "expel")
                if result["ok"]:
                    state = result["state"]

        assert not state.is_active(target), f"{target} should be expelled"


# =========================================================================
# Caucus Faction Bonuses
# =========================================================================


class TestFactionBonuses:
    def test_caucus_gives_faction_bonuses(self):
        """Caucus phase applies faction-specific resource bonuses."""
        state = _setup(seed=42)
        state = _advance_to(state, "caucus")

        # Check that some players got faction bonuses
        # All players get +1 achievement in caucus
        for pid in PLAYERS_6:
            assert state.get_resource(pid, "achievements") >= 1


# =========================================================================
# Visibility
# =========================================================================


class TestVisibility:
    def test_hidden_type_not_visible_to_others(self):
        """hidden_type is not visible to other players (visibility=private)."""
        state = _setup()
        view = view_for(state, "p0", parliament_arena)
        # p0 can't see p1's hidden_type (visibility=private — only self can see)
        p1_attrs = view["entities"]["p1"]["attrs"]
        assert "hidden_type" not in p1_attrs

    def test_hidden_type_visible_to_self(self):
        """hidden_type is visible to the player themselves (visibility=private)."""
        state = _setup()
        view = view_for(state, "p0", parliament_arena)
        # p0 can see their own hidden_type
        p0_attrs = view["entities"]["p0"]["attrs"]
        assert "hidden_type" in p0_attrs
        assert p0_attrs["hidden_type"] in (
            "loyalist",
            "opportunist",
            "ideologue",
            "chaotic",
        )

    def test_faction_visible_to_all(self):
        """faction attr is public."""
        state = _setup()
        view = view_for(state, "p0", parliament_arena)
        # p0 can see p1's faction (visibility=public)
        p1_attrs = view["entities"]["p1"]["attrs"]
        assert "faction" in p1_attrs

    def test_caps_private(self):
        """caps resource is private — not visible to others."""
        state = _setup()
        view = view_for(state, "p0", parliament_arena)
        # p0 cannot see p1's caps (visibility=private)
        p1_resources = view["entities"]["p1"]["resources"]
        assert "caps" not in p1_resources

    def test_influence_public(self):
        """influence resource is public."""
        state = _setup()
        view = view_for(state, "p0", parliament_arena)
        p1_resources = view["entities"]["p1"]["resources"]
        assert "influence" in p1_resources


# =========================================================================
# Commitments
# =========================================================================


class TestCommitments:
    def test_dead_hand_damages_all(self):
        """Dead hand commitment damages everyone when player expelled."""
        state = _setup()
        state = _advance_to(state, "vote")

        # Record initial reputations
        initial_rep = {pid: state.get_resource(pid, "reputation") for pid in PLAYERS_6}

        # Expel p3
        target = "p3"
        result = rt.start_vote(state, "expulsion", proposer_id="p0", subject_id=target)
        assert result["ok"]
        state = result["state"]
        iid = result["instance_id"]
        for pid in PLAYERS_6:
            if state.is_active(pid):
                result = rt.cast_vote(state, iid, pid, "expel")
                if result["ok"]:
                    state = result["state"]

        # Dead hand: all surviving players lose 5 reputation
        for pid in PLAYERS_6:
            if pid != target and state.is_active(pid):
                assert state.get_resource(pid, "reputation") < initial_rep[pid], (
                    f"{pid} reputation should decrease from dead hand"
                )


# =========================================================================
# Phase Cycle
# =========================================================================


class TestPhaseCycle:
    def test_full_round_phases(self):
        """A full round cycles through all phases."""
        state = _setup()
        assert state.phase == "election"

        state = _advance(state)
        assert state.phase == "caucus"

        state = _advance(state)
        assert state.phase == "agenda"

        state = _advance(state)
        assert state.phase == "floor"

        state = _advance(state)
        assert state.phase == "vote"

        state = _advance(state)
        # fallout is automatic, should cascade to caucus (next round)
        assert state.phase == "caucus"


# =========================================================================
# Replay
# =========================================================================


class TestReplay:
    def test_election_and_deals_replay(self):
        """Elections + deals replay deterministically."""
        state = _setup(seed=42)

        # Elect speaker
        state = _elect(state, "p0")

        # Advance to caucus
        state = _advance_to(state, "caucus")

        # Promise deal
        result = rt.start_deal(state, "promise", actor_id="p1", responder_id="p2")
        if result["ok"]:
            state = result["state"]
            iid = result["instance_id"]
            result = rt.respond_to_deal(state, iid, "p2", "acknowledge")
            if result["ok"]:
                state = result["state"]

        # Advance to agenda
        state = _advance_to(state, "agenda")

        archive = create_archive(parliament_arena, state)
        result = verify(archive, parliament_arena)
        assert result["valid"], (
            f"Replay mismatch: expected {result['decisions_expected']}, "
            f"got {result['decisions_replayed']}"
        )


# =========================================================================
# Investigate Mutual Exclusivity
# =========================================================================


class TestInvestigate:
    """Tests for investigate deal — verify mutually exclusive real/fake outcomes."""

    def _to_floor_with_intel(
        self, player: str, seed: int = 42
    ) -> GameState:
        """Get to floor phase and give player enough intel to investigate."""
        state = _setup(seed=seed)
        state = _advance_to(state, "floor")
        # Ensure player has at least 3 intel
        state = state.adjust_resource(player, "intel", 10, parliament_arena)
        return state

    def test_investigate_never_produces_both_real_and_fake(self):
        """Investigate should produce either real OR fake reveal, never both.

        Before the fix, two independent Maybe blocks could both fire,
        producing both a real and fake reveal simultaneously.
        """
        # Run investigate across many seeds to check mutual exclusivity
        real_count = 0
        fake_count = 0
        both_count = 0

        for seed in range(100):
            state = self._to_floor_with_intel("p0", seed=seed)
            initial_reveals = dict(state.reveals)

            result = rt.start_deal(
                state, "investigate", actor_id="p0", target_id="p1"
            )
            if not result["ok"]:
                continue  # some seeds may not have enough intel after caucus

            state = result["state"]

            # Check which outcomes fired by looking at emitted events
            events = [
                h for h in state.history
                if h.type == "event"
                and h.data.get("event") in ("investigation_real", "investigation_fake")
            ]
            has_real = any(h.data["event"] == "investigation_real" for h in events)
            has_fake = any(h.data["event"] == "investigation_fake" for h in events)

            if has_real:
                real_count += 1
            if has_fake:
                fake_count += 1
            if has_real and has_fake:
                both_count += 1

        # With 100 seeds, we should see some real and some fake results
        assert real_count > 0, "No real investigation results across 100 seeds"
        assert fake_count > 0, "No fake investigation results across 100 seeds"
        # Critical: should NEVER have both
        assert both_count == 0, (
            f"Investigation produced both real AND fake results in {both_count}/100 seeds. "
            f"Maybe blocks are not mutually exclusive."
        )

    def test_investigate_costs_intel_and_suspicion(self):
        """Investigate always costs 3 intel and +3 suspicion regardless of outcome."""
        state = self._to_floor_with_intel("p0", seed=42)
        initial_intel = state.get_resource("p0", "intel")
        initial_suspicion = state.get_resource("p0", "suspicion")

        result = rt.start_deal(
            state, "investigate", actor_id="p0", target_id="p1"
        )
        assert result["ok"], f"investigate failed: {result}"
        state = result["state"]

        assert state.get_resource("p0", "intel") == initial_intel - 3
        assert state.get_resource("p0", "suspicion") == initial_suspicion + 3


class TestLeakScandal:
    """Tests for leak_scandal deal — verify mutually exclusive success/backfire."""

    def _to_floor_with_intel(
        self, player: str, seed: int = 42
    ) -> GameState:
        """Get to floor phase and give player enough intel to leak."""
        state = _setup(seed=seed)
        state = _advance_to(state, "floor")
        state = state.adjust_resource(player, "intel", 10, parliament_arena)
        return state

    def test_leak_scandal_never_both_success_and_backfire(self):
        """Leak scandal should succeed OR backfire, never both.

        Before the fix, two independent Maybe blocks could both fire.
        """
        success_count = 0
        backfire_count = 0
        both_count = 0

        for seed in range(100):
            state = self._to_floor_with_intel("p0", seed=seed)

            result = rt.start_deal(
                state, "leak_scandal", actor_id="p0", target_id="p1"
            )
            if not result["ok"]:
                continue

            state = result["state"]

            events = [
                h for h in state.history
                if h.type == "event"
                and h.data.get("event") in ("scandal_success", "scandal_backfire")
            ]
            has_success = any(h.data["event"] == "scandal_success" for h in events)
            has_backfire = any(h.data["event"] == "scandal_backfire" for h in events)

            if has_success:
                success_count += 1
            if has_backfire:
                backfire_count += 1
            if has_success and has_backfire:
                both_count += 1

        assert success_count > 0, "No scandal successes across 100 seeds"
        assert backfire_count > 0, "No scandal backfires across 100 seeds"
        assert both_count == 0, (
            f"Scandal produced both success AND backfire in {both_count}/100 seeds."
        )
