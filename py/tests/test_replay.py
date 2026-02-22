"""Tests for ReplayController — step-through game replay."""

from __future__ import annotations

import pytest

from engine.archive import create_archive, replay
from engine.runtime.core import GameRuntime
from games.auction import auction
from server.replay import ReplayController

_PLAYERS = ["alice", "bob", "charlie"]


def _play_auction_to_archive():
    """Play a short auction and return the archive."""
    rt = GameRuntime(auction)
    state = rt.start_game(_PLAYERS, seed=42)
    state = rt.run_setup(state)

    # Several rounds of appraise actions + phase advances
    for _ in range(3):
        result = rt.start_deal(state, "appraise", actor_id="alice")
        if result["ok"]:
            state = result["state"]
        result = rt.start_deal(state, "appraise", actor_id="bob")
        if result["ok"]:
            state = result["state"]
        state = state.record_decision({"type": "advance_phase"})
        state = rt.advance_phase(state)

    return create_archive(auction, state)


class TestReplayController:
    def test_initial_state(self):
        archive = _play_auction_to_archive()
        ctrl = ReplayController(archive, auction)
        assert ctrl.step == 0
        assert ctrl.total_steps > 0
        assert ctrl.decision is None  # step 0 = initial
        assert ctrl.current.status == "active"

    def test_forward_back(self):
        archive = _play_auction_to_archive()
        ctrl = ReplayController(archive, auction)

        ctrl.forward()
        assert ctrl.step == 1
        assert ctrl.decision is not None

        ctrl.back()
        assert ctrl.step == 0
        assert ctrl.decision is None

    def test_go_to(self):
        archive = _play_auction_to_archive()
        ctrl = ReplayController(archive, auction)

        ctrl.go_to(3)
        assert ctrl.step == 3

        ctrl.go_to(999)  # clamp to max
        assert ctrl.step == ctrl.total_steps

        ctrl.go_to(-5)  # clamp to 0
        assert ctrl.step == 0

    def test_to_start_end(self):
        archive = _play_auction_to_archive()
        ctrl = ReplayController(archive, auction)

        ctrl.to_end()
        assert ctrl.step == ctrl.total_steps

        ctrl.to_start()
        assert ctrl.step == 0

    def test_diff_at_step_0(self):
        archive = _play_auction_to_archive()
        ctrl = ReplayController(archive, auction)
        assert ctrl.diff() == []  # no prev state

    def test_diff_after_action(self):
        archive = _play_auction_to_archive()
        ctrl = ReplayController(archive, auction)

        ctrl.forward()  # first decision
        changes = ctrl.diff()
        assert len(changes) > 0

    def test_total_steps_equals_decisions(self):
        archive = _play_auction_to_archive()
        ctrl = ReplayController(archive, auction)
        assert ctrl.total_steps == len(archive.decisions)

    def test_final_state_matches_replay(self):
        """Final step state should match direct replay."""
        archive = _play_auction_to_archive()
        ctrl = ReplayController(archive, auction)

        ctrl.to_end()
        direct = replay(archive, auction)

        # Same resources
        for pid in _PLAYERS:
            for res in ["gold", "reputation"]:
                assert ctrl.current.get_resource(pid, res) == direct.get_resource(
                    pid, res
                ), f"{pid}.{res} mismatch"

    def test_prev_at_step_0(self):
        archive = _play_auction_to_archive()
        ctrl = ReplayController(archive, auction)
        assert ctrl.prev is None

    def test_prev_at_step_1(self):
        archive = _play_auction_to_archive()
        ctrl = ReplayController(archive, auction)
        ctrl.forward()
        assert ctrl.prev is not None
        assert ctrl.prev is ctrl._states[0]
