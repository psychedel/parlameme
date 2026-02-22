"""PettingZoo AEC adapter for Parlameme games.

Wraps GameRuntime in a PettingZoo AEC (Agent Environment Cycle) interface
for multi-agent reinforcement learning.

Requires optional dependencies: gymnasium, pettingzoo.
"""

from __future__ import annotations

from typing import Any

import numpy as np

try:
    from gymnasium.spaces import Box, Dict, Discrete, MultiBinary
    from pettingzoo import AECEnv
    from pettingzoo.utils.agent_selector import AgentSelector

    HAS_PETTINGZOO = True
except ImportError:
    HAS_PETTINGZOO = False
    AECEnv = object  # fallback for class definition

from engine.runtime.core import GameRuntime
from engine.runtime.state import CompiledGame, GameState
from training.rewards import RewardCalculator
from training.runner import _auto_advance, _execute_action, get_acting_order
from training.spaces import ActionSpaceBuilder, ObservationEncoder


def _require_pettingzoo() -> None:
    if not HAS_PETTINGZOO:
        raise ImportError(
            "PettingZoo adapter requires gymnasium and pettingzoo. "
            "Install with: pip install gymnasium pettingzoo"
        )


class ParlamemeEnv(AECEnv):
    """PettingZoo AEC environment wrapping a Parlameme game.

    Key design decisions:
    - Flat discrete action space with masking (MaskablePPO compatible)
    - Observation is Dict with "observation" and "action_mask"
    - Turn order: pending responses > pending votes > all active players
    - Games end via check_victory() or max_steps truncation
    """

    metadata = {"name": "parlameme_v0", "is_parallelizable": False}

    def __init__(
        self,
        compiled: CompiledGame,
        player_ids: list[str] | None = None,
        seed: int = 42,
        max_steps: int = 500,
        num_bins: int = 10,
        reward_calculator: RewardCalculator | None = None,
        param_sampler: Any | None = None,
    ):
        _require_pettingzoo()

        self.compiled = compiled
        self.runtime = GameRuntime(compiled)
        self._seed = seed
        self._max_steps = max_steps
        self._step_count = 0
        self._param_sampler = param_sampler

        n = compiled.min_players if player_ids is None else len(player_ids)
        self._player_ids = player_ids or [f"p{i}" for i in range(n)]
        self.possible_agents = list(self._player_ids)
        self.agents: list[str] = []

        self._action_builder = ActionSpaceBuilder(compiled, num_bins, self._player_ids)
        self._obs_encoder = ObservationEncoder(compiled, len(self._player_ids))
        self._reward_calc = reward_calculator or RewardCalculator(compiled)

        num_actions = self._action_builder.num_actions
        obs_dim = self._obs_encoder.obs_dim

        self.action_spaces = {
            agent: Discrete(num_actions)
            for agent in self.possible_agents
        }
        self.observation_spaces = {
            agent: Dict({
                "observation": Box(low=-1.0, high=1.0, shape=(obs_dim,), dtype=np.float32),
                "action_mask": MultiBinary(num_actions),
            })
            for agent in self.possible_agents
        }

        self._state: GameState | None = None
        self._prev_state: GameState | None = None
        self._agent_selector: Any = None
        self.agent_selection: str = ""

        self.rewards: dict[str, float] = {}
        self.terminations: dict[str, bool] = {}
        self.truncations: dict[str, bool] = {}
        self.infos: dict[str, dict] = {}
        self._cumulative_rewards: dict[str, float] = {}

    def reset(
        self,
        seed: int | None = None,
        options: dict | None = None,
    ) -> None:
        """Reset the environment to initial state."""
        s = seed if seed is not None else self._seed
        game_params = None
        if self._param_sampler is not None:
            rng = np.random.default_rng(s)
            game_params = self._param_sampler(rng)
        self._state = self.runtime.start_game(self._player_ids, seed=s, params=game_params)
        self._state = self.runtime.run_setup(self._state)
        self._prev_state = None
        self._step_count = 0

        self.agents = list(self._player_ids)
        self.rewards = {a: 0.0 for a in self.possible_agents}
        self.terminations = {a: False for a in self.possible_agents}
        self.truncations = {a: False for a in self.possible_agents}
        self.infos = {a: {} for a in self.possible_agents}
        self._cumulative_rewards = {a: 0.0 for a in self.possible_agents}

        self._state = _auto_advance(self.runtime, self._state)

        acting = get_acting_order(self._state, self._player_ids)
        if not acting:
            acting = list(self._player_ids)
        self._agent_selector = AgentSelector(acting)
        self.agent_selection = self._agent_selector.next()

    def step(self, action: int) -> None:
        """Execute one action for the current agent."""
        agent = self.agent_selection

        if self.terminations.get(agent) or self.truncations.get(agent):
            # Dead step — accumulate rewards and advance
            self._cumulative_rewards[agent] = 0.0
            self.agent_selection = self._next_agent()
            return

        self._prev_state = self._state
        self._step_count += 1

        action_spec = self._action_builder.decode_action(action, agent, self._state)
        self._state = _execute_action(self.runtime, self._state, action_spec, agent)
        self._state = _auto_advance(self.runtime, self._state)

        victory = None
        if self._state.status == "active":
            victory = self.runtime.check_victory(self._state)
            if victory:
                self._state = self.runtime.end_game(self._state, victory)

        # Only the acting agent gets step reward
        for a in self.possible_agents:
            if a == agent:
                self.rewards[a] = self._reward_calc.step_reward(
                    a, self._prev_state, self._state, victory
                )
            else:
                self.rewards[a] = 0.0
        self._cumulative_rewards[agent] += self.rewards[agent]

        game_over = self._state.status == "ended"
        truncated = self._step_count >= self._max_steps

        if game_over or truncated:
            for a in self.possible_agents:
                self.terminations[a] = game_over
                self.truncations[a] = truncated and not game_over
                terminal_r = self._reward_calc.terminal_reward(a, self._state, victory)
                self.rewards[a] += terminal_r
                self._cumulative_rewards[a] += terminal_r
                self.infos[a] = {
                    "player_id": a,
                    "phase": self._state.phase,
                    "round": self._state.round,
                    "step": self._step_count,
                }
                if victory:
                    self.infos[a]["victory"] = victory
            self.agents = []
        else:
            for a in self.possible_agents:
                self.infos[a] = {
                    "player_id": a,
                    "phase": self._state.phase,
                    "round": self._state.round,
                    "step": self._step_count,
                }

            acting = get_acting_order(self._state, self._player_ids)
            if not acting:
                # No one can act — try auto-advance (bounded retries)
                for _ in range(10):
                    self._state = self._state.record_decision({"type": "advance_phase"})
                    self._state = self.runtime.advance_phase(self._state)
                    self._state = _auto_advance(self.runtime, self._state)
                    if self._state.status != "active":
                        break
                    acting = get_acting_order(self._state, self._player_ids)
                    if acting:
                        break

            if self._state.status != "active":
                # Game ended during auto-advance
                for a in self.possible_agents:
                    self.terminations[a] = True
                    self.truncations[a] = False
                self.agents = []
            elif acting:
                self._agent_selector = AgentSelector(acting)
                self.agent_selection = self._agent_selector.next()
            else:
                # Truly stuck — truncate
                for a in self.possible_agents:
                    self.terminations[a] = False
                    self.truncations[a] = True
                self.agents = []

    def observe(self, agent: str) -> dict[str, Any]:
        """Return observation for agent."""
        if self._state is None:
            obs_dim = self._obs_encoder.obs_dim
            num_actions = self._action_builder.num_actions
            return {
                "observation": np.zeros(obs_dim, dtype=np.float32),
                "action_mask": np.zeros(num_actions, dtype=np.int8),
            }

        obs = self._obs_encoder.encode(self._state, agent, self.compiled)
        mask = self._action_builder.action_mask(agent, self._state, self.compiled)
        return {"observation": obs, "action_mask": mask}

    def last(self) -> tuple[dict[str, Any], float, bool, bool, dict]:
        """Return (observation, reward, terminated, truncated, info) for current agent."""
        agent = self.agent_selection
        obs = self.observe(agent)
        return (
            obs,
            self._cumulative_rewards.get(agent, 0.0),
            self.terminations.get(agent, False),
            self.truncations.get(agent, False),
            self.infos.get(agent, {}),
        )

    def observation_space(self, agent: str):
        return self.observation_spaces[agent]

    def action_space(self, agent: str):
        return self.action_spaces[agent]

    def agent_iter(self, max_iter: int = 2**31):
        """Iterate over agents in turn order (PettingZoo-compatible)."""
        i = 0
        while self.agents and i < max_iter:
            yield self.agent_selection
            i += 1

    def _next_agent(self) -> str:
        """Advance to next agent in selector."""
        try:
            return self._agent_selector.next()
        except StopIteration:
            acting = get_acting_order(self._state, self._player_ids)
            if acting:
                self._agent_selector = AgentSelector(acting)
                return self._agent_selector.next()
            return self.agent_selection
