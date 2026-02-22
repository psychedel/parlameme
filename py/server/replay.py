"""Replay controller — step-through game replay from archive.

Pre-computes all intermediate states so forward/back navigation is instant.
Purely stateful (step index), no async, no session dependency.
"""

from __future__ import annotations

from typing import Any

from engine.archive import Archive, _apply_decision
from engine.runtime.core import GameRuntime
from engine.runtime.state import CompiledGame, GameState
from engine.state_diff import state_diff


class ReplayController:
    """Step-through replay of a game archive."""

    def __init__(self, archive: Archive, compiled: CompiledGame):
        self.archive = archive
        self.compiled = compiled
        self._states = _compute_all_states(archive, compiled)
        self._step = 0

    @property
    def step(self) -> int:
        return self._step

    @property
    def total_steps(self) -> int:
        return len(self._states) - 1

    @property
    def current(self) -> GameState:
        return self._states[self._step]

    @property
    def prev(self) -> GameState | None:
        return self._states[self._step - 1] if self._step > 0 else None

    @property
    def decision(self) -> dict[str, Any] | None:
        """The decision that produced the current state (None at step 0)."""
        if self._step == 0:
            return None
        return dict(self.archive.decisions[self._step - 1])

    def go_to(self, step: int) -> None:
        self._step = max(0, min(step, self.total_steps))

    def forward(self) -> None:
        self.go_to(self._step + 1)

    def back(self) -> None:
        self.go_to(self._step - 1)

    def to_start(self) -> None:
        self._step = 0

    def to_end(self) -> None:
        self._step = self.total_steps

    def diff(self) -> list[dict[str, Any]]:
        """Compute human-readable diff between prev and current state."""
        if self.prev is None:
            return []
        return state_diff(self.prev, self.current, self.compiled)


def _compute_all_states(archive: Archive, compiled: CompiledGame) -> list[GameState]:
    """Replay archive capturing state after each decision."""
    runtime = GameRuntime(compiled)
    state = runtime.start_game(list(archive.players), archive.seed)
    state = runtime.run_setup(state)

    states = [state]
    for decision in archive.decisions:
        state = _apply_decision(runtime, state, decision)
        states.append(state)
    return states
