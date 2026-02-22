"""Werewolf: Strategic Edition — game-specific integration tests.

Tests role assignment, night actions, vote lifecycle, victory conditions,
commitments, actor filters, and MCP formatter integration.
"""

from __future__ import annotations

import pytest

from engine.runtime.core import GameRuntime
from engine.runtime.state import GameState, view_for
from games.werewolf import werewolf
from mcp.formatters import _can_player_use_deal, format_available_actions

# =========================================================================
# Helpers
# =========================================================================

PLAYERS_8 = [f"w{i}" for i in range(8)]
PLAYERS_10 = [f"w{i}" for i in range(10)]
PLAYERS_12 = [f"w{i}" for i in range(12)]

rt = GameRuntime(werewolf)


def _setup(players: list[str] = PLAYERS_8, seed: int = 42) -> GameState:
    """Start game and run setup phase."""
    state = rt.start_game(players, seed=seed)
    return rt.run_setup(state)


def _find_by_role(state: GameState, role: str) -> str | None:
    """Find first player with given role."""
    for pid in state.entities:
        if state.get_attr(pid, "role") == role:
            return pid
    return None


def _find_by_team(state: GameState, team: str) -> list[str]:
    """Find all players on a team."""
    return [pid for pid in state.entities if state.get_attr(pid, "team") == team]


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


# =========================================================================
# Role Assignment
# =========================================================================


class TestRoleAssignment:
    def test_8_players_role_distribution(self):
        """8 players: seer(1), witch(1), hunter(1), werewolf(2), villager(3)."""
        state = _setup(PLAYERS_8)
        roles = [state.get_attr(pid, "role") for pid in PLAYERS_8]
        assert roles.count("seer") == 1
        assert roles.count("witch") == 1
        assert roles.count("hunter") == 1
        assert roles.count("werewolf") == 2
        # No high-min roles at 8 players
        assert roles.count("cupid") == 0
        assert roles.count("tanner") == 0
        assert roles.count("bodyguard") == 0
        assert roles.count("alpha_wolf") == 0
        assert roles.count("elder") == 0
        # Fillers
        assert roles.count("villager") == 3

    def test_10_players_adds_cupid_bodyguard_tanner(self):
        """10 players adds cupid, bodyguard, tanner (min_players=10)."""
        state = _setup(PLAYERS_10)
        roles = [state.get_attr(pid, "role") for pid in PLAYERS_10]
        assert roles.count("seer") == 1
        assert roles.count("witch") == 1
        assert roles.count("hunter") == 1
        assert roles.count("werewolf") == 2
        assert roles.count("cupid") == 1
        assert roles.count("bodyguard") == 1
        assert roles.count("tanner") == 1
        assert roles.count("villager") == 2

    def test_12_players_adds_alpha_elder(self):
        """12 players adds alpha_wolf and elder (min_players=12)."""
        state = _setup(PLAYERS_12)
        roles = [state.get_attr(pid, "role") for pid in PLAYERS_12]
        assert roles.count("seer") == 1
        assert roles.count("witch") == 1
        assert roles.count("hunter") == 1
        assert roles.count("werewolf") == 2
        assert roles.count("alpha_wolf") == 1
        assert roles.count("elder") == 1
        assert roles.count("cupid") == 1
        assert roles.count("bodyguard") == 1
        assert roles.count("tanner") == 1

    def test_teams_assigned_correctly(self):
        """All roles map to correct teams."""
        state = _setup(PLAYERS_8)
        for pid in PLAYERS_8:
            role = state.get_attr(pid, "role")
            team = state.get_attr(pid, "team")
            if role in (
                "villager",
                "seer",
                "witch",
                "hunter",
                "bodyguard",
                "cupid",
                "elder",
            ):
                assert team == "village", (
                    f"{pid} ({role}) should be village, got {team}"
                )
            elif role in ("werewolf", "alpha_wolf"):
                assert team == "wolves", f"{pid} ({role}) should be wolves, got {team}"
            elif role == "tanner":
                assert team == "neutral", (
                    f"{pid} ({role}) should be neutral, got {team}"
                )

    def test_wolf_pack_group_created(self):
        """Wolves are in wolf_pack group after setup."""
        state = _setup(PLAYERS_8)
        wolves = _find_by_team(state, "wolves")
        assert len(wolves) >= 2
        for wolf in wolves:
            assert state.entity_in_group_type(wolf, "wolf_pack"), (
                f"{wolf} should be in wolf_pack"
            )

    def test_witch_gets_potions(self):
        """Witch starts with 1 heal potion and 1 poison potion."""
        state = _setup(PLAYERS_8)
        witch = _find_by_role(state, "witch")
        assert witch is not None
        assert state.get_resource(witch, "heal_potion") == 1
        assert state.get_resource(witch, "poison_potion") == 1

    def test_setup_enters_first_night(self):
        """After setup, game is in first_night phase."""
        state = _setup(PLAYERS_8)
        assert state.phase == "first_night"


# =========================================================================
# Deal Actor Filters (FIX-5 verification)
# =========================================================================


class TestActorFilters:
    def test_seer_vision_only_for_seer(self):
        """Only the seer can use seer_vision."""
        state = _setup(PLAYERS_8)
        seer = _find_by_role(state, "seer")
        for pid in PLAYERS_8:
            can = _can_player_use_deal(state, werewolf, "seer_vision", pid)
            if pid == seer:
                assert can, f"Seer ({pid}) should be able to use seer_vision"
            else:
                assert not can, f"Non-seer ({pid}) should not use seer_vision"

    def test_hunter_aim_only_for_hunter(self):
        """Only the hunter can use hunter_aim."""
        state = _setup(PLAYERS_8)
        hunter = _find_by_role(state, "hunter")
        for pid in PLAYERS_8:
            can = _can_player_use_deal(state, werewolf, "hunter_aim", pid)
            if pid == hunter:
                assert can, f"Hunter ({pid}) should be able to use hunter_aim"
            else:
                assert not can, f"Non-hunter ({pid}) should not use hunter_aim"

    def test_wolf_mark_only_for_wolves(self):
        """Only wolves can use wolf_mark (night phase)."""
        state = _setup(PLAYERS_8)
        state = _advance_to(state, "night")
        wolves = _find_by_team(state, "wolves")
        for pid in PLAYERS_8:
            can = _can_player_use_deal(state, werewolf, "wolf_mark", pid)
            if pid in wolves:
                assert can, f"Wolf ({pid}) should be able to use wolf_mark"
            else:
                assert not can, f"Non-wolf ({pid}) should not use wolf_mark"

    def test_witch_potions_require_role_and_resource(self):
        """Witch needs role=witch AND potion resource > 0."""
        state = _setup(PLAYERS_8)
        state = _advance_to(state, "night")
        witch = _find_by_role(state, "witch")
        non_witch = [p for p in PLAYERS_8 if p != witch][0]

        # Witch can use poison (has potion)
        can = _can_player_use_deal(state, werewolf, "witch_poison", witch)
        assert can, "Witch should be able to use witch_poison"

        # Non-witch cannot
        can = _can_player_use_deal(state, werewolf, "witch_poison", non_witch)
        assert not can, "Non-witch should not use witch_poison"

    def test_format_available_actions_shows_correct_deals(self):
        """format_available_actions shows role-appropriate deals."""
        state = _setup(PLAYERS_8)
        seer = _find_by_role(state, "seer")
        hunter = _find_by_role(state, "hunter")
        villager = [p for p in PLAYERS_8 if state.get_attr(p, "role") == "villager"][0]

        seer_actions = format_available_actions(state, werewolf, seer)
        assert "seer_vision" in seer_actions

        hunter_actions = format_available_actions(state, werewolf, hunter)
        assert "hunter_aim" in hunter_actions

        villager_actions = format_available_actions(state, werewolf, villager)
        assert "seer_vision" not in villager_actions
        assert "hunter_aim" not in villager_actions


# =========================================================================
# Night Actions (Deals)
# =========================================================================


class TestNightActions:
    def test_seer_sees_role(self):
        """Seer vision reveals target's role."""
        state = _setup(PLAYERS_8)
        seer = _find_by_role(state, "seer")
        wolf = _find_by_team(state, "wolves")[0]
        result = rt.start_deal(state, "seer_vision", actor_id=seer, target_id=wolf)
        assert result["ok"], f"seer_vision failed: {result}"
        state = result["state"]
        # Check reveal was recorded
        view = view_for(state, seer, werewolf)
        # The seer should see the wolf's role through reveals
        target_entity = view["entities"].get(wolf, {})
        assert target_entity.get("attrs", {}).get("role") == "werewolf"

    def test_wolf_mark_marks_target(self):
        """Wolf mark sets target's marked attribute."""
        state = _setup(PLAYERS_8)
        state = _advance_to(state, "night")
        wolf = _find_by_team(state, "wolves")[0]
        village = [p for p in PLAYERS_8 if state.get_attr(p, "team") == "village"][0]
        result = rt.start_deal(state, "wolf_mark", actor_id=wolf, target_id=village)
        assert result["ok"], f"wolf_mark failed: {result}"
        state = result["state"]
        assert state.get_attr(village, "marked") is True

    def test_witch_heal_protects(self):
        """Witch heal sets target's protected attribute."""
        state = _setup(PLAYERS_8)
        state = _advance_to(state, "night")
        witch = _find_by_role(state, "witch")
        # First mark someone
        wolf = _find_by_team(state, "wolves")[0]
        village = [
            p
            for p in PLAYERS_8
            if state.get_attr(p, "team") == "village" and p != witch
        ][0]
        result = rt.start_deal(state, "wolf_mark", actor_id=wolf, target_id=village)
        assert result["ok"]
        state = result["state"]
        assert state.get_attr(village, "marked") is True

        # Witch heals
        result = rt.start_deal(state, "witch_heal", actor_id=witch, target_id=village)
        assert result["ok"], f"witch_heal failed: {result}"
        state = result["state"]
        assert state.get_attr(village, "protected") is True
        # Potion consumed
        assert state.get_resource(witch, "heal_potion") == 0

    def test_witch_poison_marks_target(self):
        """Witch poison marks target for elimination."""
        state = _setup(PLAYERS_8)
        state = _advance_to(state, "night")
        witch = _find_by_role(state, "witch")
        victim = [
            p for p in PLAYERS_8 if p != witch and state.get_attr(p, "role") != "witch"
        ][0]
        result = rt.start_deal(state, "witch_poison", actor_id=witch, target_id=victim)
        assert result["ok"], f"witch_poison failed: {result}"
        state = result["state"]
        assert state.get_attr(victim, "marked") is True
        assert state.get_resource(witch, "poison_potion") == 0

    def test_hunter_aim_records_target(self):
        """Hunter aim records aimed_at attribute."""
        state = _setup(PLAYERS_8)
        hunter = _find_by_role(state, "hunter")
        target = [p for p in PLAYERS_8 if p != hunter][0]
        result = rt.start_deal(state, "hunter_aim", actor_id=hunter, target_id=target)
        assert result["ok"], f"hunter_aim failed: {result}"
        state = result["state"]
        assert state.get_attr(hunter, "aimed_at") == target


# =========================================================================
# Phase Transitions
# =========================================================================


class TestPhaseTransitions:
    def test_phase_cycle(self):
        """Phases cycle: first_night -> night -> dawn -> day -> trial -> dusk -> night."""
        state = _setup(PLAYERS_8)
        assert state.phase == "first_night"

        state = _advance(state)
        assert state.phase == "night"

        state = _advance(state)
        # dawn is automatic, should cascade to day
        assert state.phase == "day"

        state = _advance(state)
        assert state.phase == "trial"

        state = _advance(state)
        # dusk is automatic, should cascade to night
        assert state.phase == "night"

    def test_dawn_resolves_marked(self):
        """Dawn phase eliminates marked (unprotected) players."""
        state = _setup(PLAYERS_8)
        state = _advance_to(state, "night")
        wolf = _find_by_team(state, "wolves")[0]
        victim = [p for p in PLAYERS_8 if state.get_attr(p, "team") == "village"][0]

        # Wolf marks victim
        result = rt.start_deal(state, "wolf_mark", actor_id=wolf, target_id=victim)
        assert result["ok"]
        state = result["state"]

        # Advance through night -> dawn (auto) -> day
        state = _advance_to(state, "day")
        # Victim should be eliminated
        assert not state.is_active(victim), f"{victim} should be eliminated"

    def test_dawn_protected_survives(self):
        """Protected player survives dawn resolution."""
        state = _setup(PLAYERS_8)
        state = _advance_to(state, "night")
        wolf = _find_by_team(state, "wolves")[0]
        witch = _find_by_role(state, "witch")
        victim = [
            p
            for p in PLAYERS_8
            if state.get_attr(p, "team") == "village" and p != witch
        ][0]

        # Wolf marks, witch heals
        result = rt.start_deal(state, "wolf_mark", actor_id=wolf, target_id=victim)
        state = result["state"]
        result = rt.start_deal(state, "witch_heal", actor_id=witch, target_id=victim)
        state = result["state"]

        # Advance through dawn
        state = _advance_to(state, "day")
        assert state.is_active(victim), f"{victim} should survive (was protected)"


# =========================================================================
# Vote Lifecycle
# =========================================================================


class TestVoteLifecycle:
    def test_lynch_vote_eliminates(self):
        """Lynch vote with majority 'lynch' eliminates target."""
        state = _setup(PLAYERS_8)
        state = _advance_to(state, "trial")

        target = PLAYERS_8[0]
        result = rt.start_vote(
            state, "lynch", proposer_id=PLAYERS_8[1], subject_id=target
        )
        assert result["ok"], f"start_vote failed: {result}"
        state = result["state"]
        instance_id = result["instance_id"]

        # Majority vote lynch
        for pid in PLAYERS_8:
            if state.is_active(pid):
                result = rt.cast_vote(state, instance_id, pid, "lynch")
                assert result["ok"], f"cast_vote {pid} failed: {result}"
                state = result["state"]

        assert not state.is_active(target), f"{target} should be eliminated"

    def test_lynch_vote_spare_keeps_alive(self):
        """Lynch vote with majority 'spare' keeps target alive."""
        state = _setup(PLAYERS_8)
        state = _advance_to(state, "trial")

        target = PLAYERS_8[0]
        result = rt.start_vote(
            state, "lynch", proposer_id=PLAYERS_8[1], subject_id=target
        )
        assert result["ok"]
        state = result["state"]
        instance_id = result["instance_id"]

        # Majority vote spare
        for pid in PLAYERS_8:
            if state.is_active(pid):
                result = rt.cast_vote(state, instance_id, pid, "spare")
                assert result["ok"]
                state = result["state"]

        assert state.is_active(target), f"{target} should still be alive"

    def test_lynch_reveals_role(self):
        """Lynched player's role is revealed."""
        state = _setup(PLAYERS_8)
        state = _advance_to(state, "trial")

        target = PLAYERS_8[0]
        target_role = state.get_attr(target, "role")

        result = rt.start_vote(
            state, "lynch", proposer_id=PLAYERS_8[1], subject_id=target
        )
        state = result["state"]
        instance_id = result["instance_id"]
        for pid in PLAYERS_8:
            if state.is_active(pid):
                result = rt.cast_vote(state, instance_id, pid, "lynch")
                state = result["state"]

        # Role should be publicly visible after lynch
        other = [p for p in PLAYERS_8 if p != target and state.is_active(p)][0]
        view = view_for(state, other, werewolf)
        lynched_entity = view["entities"].get(target, {})
        assert lynched_entity.get("attrs", {}).get("role") == target_role


# =========================================================================
# Victory Conditions
# =========================================================================


class TestVictoryConditions:
    def test_village_wins_when_wolves_eliminated(self):
        """Village wins when all wolves are eliminated."""
        state = _setup(PLAYERS_8, seed=42)
        wolves = _find_by_team(state, "wolves")
        assert len(wolves) >= 2

        # Skip to trial and lynch all wolves
        state = _advance_to(state, "trial")

        for wolf_id in wolves:
            if not state.is_active(wolf_id):
                continue
            result = rt.start_vote(
                state, "lynch", proposer_id=PLAYERS_8[0], subject_id=wolf_id
            )
            if not result["ok"]:
                continue
            state = result["state"]
            iid = result["instance_id"]
            for pid in PLAYERS_8:
                if (
                    state.is_active(pid)
                    and pid
                    in state.pending_votes.get(
                        iid, type("", (), {"eligible": []})()
                    ).eligible
                ):
                    result = rt.cast_vote(state, iid, pid, "lynch")
                    if result["ok"]:
                        state = result["state"]

            # Check victory
            victory = rt.check_victory(state)
            if victory:
                break

            # If more wolves, advance to next trial
            state = _advance_to(state, "trial")

        # Should have village_wins or all wolves dead
        remaining_wolves = [w for w in wolves if state.is_active(w)]
        if not remaining_wolves:
            victory = rt.check_victory(state)
            assert victory is not None
            assert victory["condition"] == "village_wins"

    def test_wolves_win_when_outnumber_village(self):
        """Wolves win when they equal or outnumber villagers."""
        state = _setup(PLAYERS_8, seed=42)
        wolves = _find_by_team(state, "wolves")
        village = _find_by_team(state, "village")

        # Manually eliminate villagers until wolves >= village
        from engine.expr.evaluator import Context
        from engine.runtime.effects import Eliminate, apply_effects

        ctx = Context(state=state, compiled=werewolf, bindings={})
        # Eliminate all but 2 villagers (wolves have 2)
        to_kill = village[:-2]
        for pid in to_kill:
            state = apply_effects([Eliminate(pid)], state, ctx)

        victory = rt.check_victory(state)
        assert victory is not None
        assert victory["condition"] == "wolves_win"


# =========================================================================
# Commitments
# =========================================================================


class TestCommitments:
    def test_hunter_revenge_on_death(self):
        """Hunter kills aimed_at target when lynched."""
        state = _setup(PLAYERS_8, seed=42)
        hunter = _find_by_role(state, "hunter")
        assert hunter is not None

        # Hunter aims at someone
        aimed_target = [p for p in PLAYERS_8 if p != hunter][0]
        result = rt.start_deal(
            state, "hunter_aim", actor_id=hunter, target_id=aimed_target
        )
        assert result["ok"]
        state = result["state"]
        assert state.get_attr(hunter, "aimed_at") == aimed_target

        # Skip to trial phase to lynch the hunter
        state = _advance_to(state, "trial")

        # Lynch the hunter (commitments fire through runtime's _on_eliminate)
        result = rt.start_vote(
            state, "lynch", proposer_id=PLAYERS_8[1], subject_id=hunter
        )
        assert result["ok"]
        state = result["state"]
        iid = result["instance_id"]
        for pid in PLAYERS_8:
            if state.is_active(pid):
                result = rt.cast_vote(state, iid, pid, "lynch")
                if result["ok"]:
                    state = result["state"]

        # Hunter should be dead
        assert not state.is_active(hunter)
        # Hunter's aimed target should also be dead (revenge commitment)
        assert not state.is_active(aimed_target), (
            f"Hunter's target ({aimed_target}) should be eliminated by revenge"
        )


# =========================================================================
# Day Deals
# =========================================================================


class TestDayDeals:
    def test_accuse_deal(self):
        """Accuse deal works in day phase."""
        state = _setup(PLAYERS_8)
        state = _advance_to(state, "day")

        proposer = PLAYERS_8[0]
        responder = PLAYERS_8[1]
        result = rt.start_deal(
            state, "accuse", actor_id=proposer, responder_id=responder
        )
        assert result["ok"], f"accuse failed: {result}"
        state = result["state"]
        instance_id = result["instance_id"]

        # Responder defends
        result = rt.respond_to_deal(state, instance_id, responder, "defend")
        assert result["ok"], f"respond failed: {result}"
        state = result["state"]

        # Responder gets trust
        assert state.get_resource(responder, "trust") > 50  # started at 50

    def test_claim_role_broadcasts(self):
        """claim_role deal executes and records decision."""
        state = _setup(PLAYERS_8)
        state = _advance_to(state, "day")

        actor_id = PLAYERS_8[0]
        result = rt.start_deal(
            state,
            "claim_role",
            actor_id=actor_id,
            params={"claimed_role": "seer"},
        )
        assert result["ok"], f"claim_role failed: {result}"


# =========================================================================
# Deterministic Replay
# =========================================================================


class TestReplay:
    def test_full_round_replay(self):
        """A full round of actions replays deterministically."""
        from engine.archive import create_archive, verify

        state = _setup(PLAYERS_8, seed=42)
        seer = _find_by_role(state, "seer")
        wolf = _find_by_team(state, "wolves")[0]

        # Seer uses vision in first_night
        result = rt.start_deal(state, "seer_vision", actor_id=seer, target_id=wolf)
        assert result["ok"]
        state = result["state"]

        # Advance to night
        state = _advance_to(state, "night")

        # Wolf marks someone
        victim = [p for p in PLAYERS_8 if state.get_attr(p, "team") == "village"][0]
        result = rt.start_deal(state, "wolf_mark", actor_id=wolf, target_id=victim)
        assert result["ok"]
        state = result["state"]

        # Advance through dawn to day
        state = _advance_to(state, "day")

        # Verify replay
        archive = create_archive(werewolf, state)
        result = verify(archive, werewolf)
        assert result["valid"], (
            f"Replay mismatch: expected {result['decisions_expected']}, "
            f"got {result['decisions_replayed']}"
        )
