"""Batch self-play runner for multi-agent games.

Runs N games sequentially or in parallel using ProcessPoolExecutor.
Each worker creates a GameRuntime, plays through with agent policies,
and collects per-player trajectories with rewards.
"""

from __future__ import annotations

import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Any

import attrs
import numpy as np

from engine.archive import Archive, create_archive
from engine.runtime.core import GameRuntime
from engine.runtime.state import CompiledGame, GameState
from games import REGISTRY
from training.policies import AgentPolicy
from training.rewards import RewardCalculator
from training.spaces import ActionSpaceBuilder, ObservationEncoder


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@attrs.frozen
class Transition:
    """Single step in a trajectory."""

    obs: np.ndarray
    action: int
    action_mask: np.ndarray
    reward: float
    next_obs: np.ndarray
    done: bool
    truncated: bool
    info: dict[str, Any]


@attrs.frozen
class GameTrajectory:
    """Per-player trajectory for one game."""

    player_id: str
    transitions: tuple[Transition, ...]
    total_reward: float
    won: bool


@attrs.frozen
class GameResult:
    """Result of a single game."""

    seed: int
    trajectories: dict[str, GameTrajectory]
    victory: dict[str, Any] | None
    archive: Archive
    steps: int
    duration_ms: int


@attrs.frozen
class BatchResult:
    """Aggregate result of a batch of games."""

    game_id: str
    games: tuple[GameResult, ...]
    total_games: int
    completed_games: int
    avg_steps: float
    avg_duration_ms: float
    win_rates: dict[str, float]
    avg_rewards: dict[str, float]


# ---------------------------------------------------------------------------
# BatchRunner
# ---------------------------------------------------------------------------


class BatchRunner:
    """Run batch self-play games with configurable policies.

    Uses ProcessPoolExecutor for CPU-bound game execution.
    Each worker is independent — no shared state.
    """

    def __init__(
        self,
        game_id: str,
        num_bins: int = 10,
        max_steps_per_game: int = 500,
    ):
        if game_id not in REGISTRY:
            raise ValueError(f"Unknown game: {game_id}")
        self.game_id = game_id
        self.compiled = REGISTRY[game_id]
        self.num_bins = num_bins
        self.max_steps = max_steps_per_game

    def run_batch(
        self,
        agents: list[AgentPolicy] | AgentPolicy,
        n_games: int,
        seeds: list[int] | range | None = None,
        player_ids: list[str] | None = None,
        parallel: int = 1,
        reward_calculator: RewardCalculator | None = None,
        param_sampler: Any | None = None,
    ) -> BatchResult:
        """Run a batch of games.

        Args:
            agents: Single policy (used for all players) or list per slot.
            n_games: Number of games to play.
            seeds: Seeds per game. Defaults to range(n_games).
            player_ids: Player identifiers. Defaults to p0, p1, ...
            parallel: Number of parallel workers (1 = sequential).
            reward_calculator: Custom reward shaping.
            param_sampler: Callable(rng) -> dict of game params per game.
                If None, uses game defaults.
        """
        if seeds is None:
            seed_list = list(range(n_games))
        else:
            seed_list = list(seeds)

        n_players = self.compiled.min_players
        if player_ids is None:
            player_ids = [f"p{i}" for i in range(n_players)]

        if not isinstance(agents, list):
            agents_list = [agents] * len(player_ids)
        else:
            agents_list = agents

        reward_calc = reward_calculator or RewardCalculator(self.compiled)

        results: list[GameResult] = []

        if parallel <= 1:
            for seed in seed_list[:n_games]:
                game_params = None
                if param_sampler is not None:
                    rng = np.random.default_rng(seed)
                    game_params = param_sampler(rng)
                result = _run_single_game(
                    self.game_id, player_ids, agents_list,
                    seed, self.max_steps, self.num_bins, reward_calc,
                    game_params=game_params,
                )
                results.append(result)
        else:
            with ProcessPoolExecutor(max_workers=parallel) as executor:
                futures = {}
                for seed in seed_list[:n_games]:
                    game_params = None
                    if param_sampler is not None:
                        rng = np.random.default_rng(seed)
                        game_params = param_sampler(rng)
                    f = executor.submit(
                        _run_single_game,
                        self.game_id, player_ids, agents_list,
                        seed, self.max_steps, self.num_bins, reward_calc,
                        game_params=game_params,
                    )
                    futures[f] = seed

                for future in as_completed(futures):
                    results.append(future.result())

        return _aggregate(self.game_id, results, player_ids)


# ---------------------------------------------------------------------------
# Single game worker
# ---------------------------------------------------------------------------


def _run_single_game(
    game_id: str,
    player_ids: list[str],
    agents: list[AgentPolicy],
    seed: int,
    max_steps: int,
    num_bins: int,
    reward_calc: RewardCalculator,
    game_params: dict[str, Any] | None = None,
) -> GameResult:
    """Run a single game to completion."""
    compiled = REGISTRY[game_id]
    runtime = GameRuntime(compiled)
    action_builder = ActionSpaceBuilder(compiled, num_bins, player_ids)
    obs_encoder = ObservationEncoder(compiled, len(player_ids))

    t0 = time.monotonic_ns()

    state = runtime.start_game(player_ids, seed=seed, params=game_params)
    state = runtime.run_setup(state)
    state = _auto_advance(runtime, state)

    for agent in agents:
        if hasattr(agent, "reset"):
            agent.reset()

    trajectories: dict[str, list[Transition]] = {pid: [] for pid in player_ids}
    step_count = 0

    while state.status == "active" and step_count < max_steps:
        acting_order = _get_acting_order(state, player_ids)

        if not acting_order:
            state = state.record_decision({"type": "advance_phase"})
            state = runtime.advance_phase(state)
            state = _auto_advance(runtime, state)
            if state.status == "active":
                v = runtime.check_victory(state)
                if v:
                    state = runtime.end_game(state, v)
            continue

        for agent_id in acting_order:
            if state.status != "active":
                break

            agent_idx = player_ids.index(agent_id)
            agent = agents[agent_idx % len(agents)]

            prev_state = state
            obs = obs_encoder.encode(state, agent_id, compiled)
            mask = action_builder.action_mask(agent_id, state, compiled)

            info = {
                "player_id": agent_id,
                "phase": state.phase,
                "round": state.round,
                "step": step_count,
            }

            action = agent.act(obs, mask, info)

            # Ensure action is legal
            if mask[action] == 0:
                legal = np.where(mask > 0)[0]
                action = int(legal[0]) if len(legal) > 0 else 0

            action_spec = action_builder.decode_action(action, agent_id, state)
            state = _execute_action(runtime, state, action_spec, agent_id)
            state = _auto_advance(runtime, state)

            done = False
            if state.status == "active":
                v = runtime.check_victory(state)
                if v:
                    state = runtime.end_game(state, v)
                    done = True
            else:
                v = None
                done = True

            next_obs = obs_encoder.encode(state, agent_id, compiled)
            reward = reward_calc.step_reward(agent_id, prev_state, state, v)
            truncated = not done and step_count >= max_steps - 1

            trajectories[agent_id].append(Transition(
                obs=obs, action=action, action_mask=mask,
                reward=reward, next_obs=next_obs,
                done=done, truncated=truncated, info=info,
            ))

            step_count += 1
            if done or step_count >= max_steps:
                break

    # Add terminal rewards and mark done=True for all players
    victory = state.victory_result
    game_ended = state.status == "ended"
    for pid in player_ids:
        terminal_r = reward_calc.terminal_reward(pid, state, victory)
        if trajectories[pid] and (terminal_r != 0 or (game_ended and not trajectories[pid][-1].done)):
            last = trajectories[pid][-1]
            trajectories[pid][-1] = Transition(
                obs=last.obs, action=last.action, action_mask=last.action_mask,
                reward=last.reward + terminal_r,
                next_obs=last.next_obs, done=True,
                truncated=False if game_ended else last.truncated,
                info=last.info,
            )

    duration_ms = (time.monotonic_ns() - t0) // 1_000_000
    archive = create_archive(compiled, state, game_params=game_params)

    game_trajs = {}
    for pid in player_ids:
        transitions = tuple(trajectories[pid])
        total_reward = sum(t.reward for t in transitions)
        won = _check_won(victory, pid, state)
        game_trajs[pid] = GameTrajectory(
            player_id=pid, transitions=transitions,
            total_reward=total_reward, won=won,
        )

    return GameResult(
        seed=seed, trajectories=game_trajs,
        victory=victory, archive=archive,
        steps=step_count, duration_ms=duration_ms,
    )


# ---------------------------------------------------------------------------
# Shared helpers (also used by env.py)
# ---------------------------------------------------------------------------


def _check_won(victory: dict | None, pid: str, state: GameState) -> bool:
    """Check if player won — handles single winner, team, and distribution."""
    if victory is None:
        return False
    if victory.get("winner") == pid:
        return True
    team = victory.get("team")
    if team and state.get_attr(pid, "team") == team:
        return True
    return False


def _auto_advance(runtime: GameRuntime, state: GameState) -> GameState:
    """Cascade through automatic/resolution phases."""
    for _ in range(30):
        idx = state.phase_index
        if idx < 0 or idx >= len(runtime.compiled.phases):
            break
        phase = runtime.compiled.phases[idx]
        if not phase.automatic:
            break
        state = state.record_decision({"type": "advance_phase"})
        state = runtime.advance_phase(state)
        v = runtime.check_victory(state)
        if v:
            state = runtime.end_game(state, v)
            break
    return state


def get_acting_order(state: GameState, player_ids: list[str]) -> list[str]:
    """Determine which agents need to act, in priority order.

    Priority:
    1. Agents with pending deal responses
    2. Agents with pending vote casts
    3. All active agents
    """
    return _get_acting_order(state, player_ids)


def _get_acting_order(state: GameState, player_ids: list[str]) -> list[str]:
    order: list[str] = []
    active = set(state.get_active_entity_ids())

    for pd in state.pending_deals.values():
        for rid, resp in pd.responders.items():
            if resp is None and rid in active and rid not in order:
                order.append(rid)

    for pv in state.pending_votes.values():
        for eid in pv.eligible:
            if eid not in pv.votes and eid in active and eid not in order:
                order.append(eid)

    if not order:
        order = [a for a in player_ids if a in active]

    return order


def _execute_action(
    runtime: GameRuntime,
    state: GameState,
    action_spec: dict[str, Any],
    agent: str,
) -> GameState:
    """Execute a decoded action specification."""
    atype = action_spec["type"]

    if atype == "deal":
        result = runtime.start_deal(
            state, action_spec["deal_id"],
            actor_id=agent,
            target_id=action_spec.get("target"),
            responder_id=action_spec.get("responder"),
            params=action_spec.get("params"),
        )
        return result["state"] if result["ok"] else state

    if atype == "respond":
        result = runtime.respond_to_deal(
            state, action_spec["instance_id"], agent, action_spec["response"],
        )
        return result["state"] if result["ok"] else state

    if atype == "vote_start":
        result = runtime.start_vote(
            state, action_spec["vote_id"],
            proposer_id=agent,
            subject_id=action_spec.get("subject"),
        )
        return result["state"] if result["ok"] else state

    if atype == "vote_cast":
        result = runtime.cast_vote(
            state, action_spec["instance_id"], agent, action_spec["option"],
        )
        return result["state"] if result["ok"] else state

    if atype == "advance_phase":
        state = state.record_decision({"type": "advance_phase"})
        return runtime.advance_phase(state)

    return state


def _aggregate(
    game_id: str,
    results: list[GameResult],
    player_ids: list[str],
) -> BatchResult:
    if not results:
        return BatchResult(
            game_id=game_id, games=(), total_games=0,
            completed_games=0, avg_steps=0, avg_duration_ms=0,
            win_rates={}, avg_rewards={},
        )

    wins = {pid: 0 for pid in player_ids}
    rewards = {pid: 0.0 for pid in player_ids}
    for r in results:
        for pid, traj in r.trajectories.items():
            rewards[pid] += traj.total_reward
            if traj.won:
                wins[pid] += 1

    n = len(results)
    return BatchResult(
        game_id=game_id,
        games=tuple(results),
        total_games=n,
        completed_games=sum(1 for r in results if r.victory is not None),
        avg_steps=sum(r.steps for r in results) / n,
        avg_duration_ms=sum(r.duration_ms for r in results) / n,
        win_rates={pid: wins[pid] / n for pid in player_ids},
        avg_rewards={pid: rewards[pid] / n for pid in player_ids},
    )
