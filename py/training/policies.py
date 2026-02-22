"""Built-in agent policies for batch self-play.

RandomPolicy: uniform random from legal actions.
GreedyPolicy: always picks first legal action (lowest index).
FirstDealPolicy: prefers deal actions over noop/advance.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import numpy as np


@runtime_checkable
class AgentPolicy(Protocol):
    """Protocol for agent policies used in batch self-play."""

    def act(
        self,
        obs: np.ndarray,
        action_mask: np.ndarray,
        info: dict[str, Any],
    ) -> int:
        """Select an action given observation, mask, and info."""
        ...

    def reset(self) -> None:
        """Reset agent state for a new game."""
        ...


class RandomPolicy:
    """Uniformly random action from legal actions."""

    def __init__(self, seed: int = 0):
        self._rng = np.random.default_rng(seed)

    def act(
        self,
        obs: np.ndarray,
        action_mask: np.ndarray,
        info: dict[str, Any],
    ) -> int:
        legal = np.where(action_mask > 0)[0]
        if len(legal) == 0:
            return 0
        return int(self._rng.choice(legal))

    def reset(self) -> None:
        pass


class GreedyPolicy:
    """Always picks the first legal action (lowest index)."""

    def act(
        self,
        obs: np.ndarray,
        action_mask: np.ndarray,
        info: dict[str, Any],
    ) -> int:
        legal = np.where(action_mask > 0)[0]
        if len(legal) == 0:
            return 0
        return int(legal[0])

    def reset(self) -> None:
        pass


class FirstDealPolicy:
    """Prefers deal actions (idx >= 2), falls back to advance_phase."""

    def act(
        self,
        obs: np.ndarray,
        action_mask: np.ndarray,
        info: dict[str, Any],
    ) -> int:
        legal = np.where(action_mask > 0)[0]
        # Skip noop (0), prefer deal actions (idx >= 2)
        deals = [a for a in legal if a >= 2]
        if deals:
            return int(deals[0])
        # Fall back to advance_phase (1) if legal
        if 1 in legal:
            return 1
        return int(legal[0]) if len(legal) > 0 else 0

    def reset(self) -> None:
        pass
