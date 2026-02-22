"""Reward calculation for RL training.

Three reward sources:
1. Terminal: +1 for winner, -1 for losers (or score-proportional for distribution)
2. Per-step resource delta: normalized resource changes as reward signal
3. Potential-based shaping (optional): gamma * Phi(s') - Phi(s) — Ng 1999

For MVP: terminal + resource delta.  Potential-based is an optional add-on.
"""

from __future__ import annotations

from typing import Any, Callable

from engine.runtime.state import CompiledGame, GameState, Visibility


class RewardCalculator:
    """Compute per-agent rewards from state transitions.

    Args:
        compiled: The compiled game definition.
        terminal_win: Reward for winning (default +1).
        terminal_lose: Reward for losing (default -1).
        resource_weights: {resource_id: weight} for per-step delta.
            Defaults to all public resources weighted by 1/initial.
        step_scale: Multiplier for per-step resource delta rewards.
        score_proportional: Use score-proportional terminal for distribution victories.
        gamma: Discount factor for potential-based shaping.
        shaping_potential: Optional potential function Phi(state, player_id, compiled) -> float.
    """

    def __init__(
        self,
        compiled: CompiledGame,
        terminal_win: float = 1.0,
        terminal_lose: float = -1.0,
        resource_weights: dict[str, float] | None = None,
        step_scale: float = 0.01,
        score_proportional: bool = True,
        gamma: float = 0.99,
        shaping_potential: Callable[[GameState, str, CompiledGame], float] | None = None,
        phase_reward_scale: float = 0.01,
    ):
        self.compiled = compiled
        self.terminal_win = terminal_win
        self.terminal_lose = terminal_lose
        self.step_scale = step_scale
        self.score_proportional = score_proportional
        self.gamma = gamma
        self._shaping_potential = shaping_potential
        self._phase_reward_scale = phase_reward_scale

        if resource_weights is None:
            self.resource_weights = _auto_resource_weights(compiled)
        else:
            self.resource_weights = resource_weights

    def step_reward(
        self,
        player_id: str,
        prev_state: GameState | None,
        state: GameState,
        victory: dict[str, Any] | None = None,
    ) -> float:
        """Compute per-step reward for a player."""
        reward = 0.0

        if prev_state is not None:
            # Resource delta reward
            for rid, weight in self.resource_weights.items():
                prev_val = prev_state.get_resource(player_id, rid)
                curr_val = state.get_resource(player_id, rid)
                delta = curr_val - prev_val
                reward += delta * weight * self.step_scale

            # Potential-based shaping: F = gamma * Phi(s') - Phi(s)
            if self._shaping_potential is not None:
                phi_prev = self._shaping_potential(prev_state, player_id, self.compiled)
                phi_curr = self._shaping_potential(state, player_id, self.compiled)
                reward += self.gamma * phi_curr - phi_prev

            # Per-phase reward signal (delta style, PBRS-compatible)
            if self._phase_reward_scale != 0.0:
                reward += self._phase_reward(player_id, prev_state, state)

        return reward

    def _phase_reward(
        self,
        player_id: str,
        prev_state: GameState,
        state: GameState,
    ) -> float:
        """Evaluate PhaseDef.reward_expr as a delta signal.

        Uses current phase's reward_expr. Delta style (val_curr - val_prev)
        is PBRS-compatible — doesn't distort optimal policy.
        """
        from engine.expr.evaluator import Context, evaluate

        phase_def = None
        for p in self.compiled.phases:
            if p.id == state.phase:
                phase_def = p
                break

        if phase_def is None or phase_def.reward_expr is None:
            return 0.0

        expr = phase_def.reward_expr
        ctx_curr = Context(
            state=state, compiled=self.compiled,
            bindings={"actor": player_id},
        )
        ctx_prev = Context(
            state=prev_state, compiled=self.compiled,
            bindings={"actor": player_id},
        )
        val_curr = evaluate(expr, ctx_curr)
        val_prev = evaluate(expr, ctx_prev)
        curr = float(val_curr) if val_curr is not None else 0.0
        prev = float(val_prev) if val_prev is not None else 0.0
        return (curr - prev) * self._phase_reward_scale

    def terminal_reward(
        self,
        player_id: str,
        state: GameState,
        victory: dict[str, Any] | None,
    ) -> float:
        """Compute terminal reward when game ends."""
        if victory is None:
            return 0.0

        vtype = victory.get("type", "single")

        if vtype == "distribution" and self.score_proportional:
            scores = victory.get("scores", {})
            if not scores:
                return 0.0
            player_score = scores.get(player_id, 0.0)
            max_score = max(scores.values())
            min_score = min(scores.values())
            score_range = max_score - min_score
            if score_range == 0:
                return 0.0
            normalized = 2.0 * (player_score - min_score) / score_range - 1.0
            return normalized * self.terminal_win

        # Single winner
        winner = victory.get("winner")

        # Team victory — all team members get equal reward
        team = victory.get("team")
        if team:
            player_team = state.get_attr(player_id, "team")
            if player_team == team:
                return self.terminal_win
            return self.terminal_lose

        # Individual winner
        if winner == player_id:
            return self.terminal_win

        return self.terminal_lose


def _auto_resource_weights(compiled: CompiledGame) -> dict[str, float]:
    """Compute resource weights from CompiledGame — no game-specific hardcoding.

    Heuristic: public resources with positive initial get positive weight,
    resources named with negative connotations (detected by resource definition
    having initial=0 and being public) get positive weight too (delta can be
    negative, which naturally produces negative reward).
    """
    weights: dict[str, float] = {}
    for rid, rdef in compiled.resources.items():
        if rdef.visibility == Visibility.PUBLIC:
            norm = max(1.0, abs(rdef.initial) if rdef.initial else 100.0)
            weights[rid] = 1.0 / norm
    return weights


class RewardConfig:
    """Pre-built reward configurations.

    The universal `from_compiled()` reads resource definitions data-driven.
    Named factories exist for convenience with tuned step_scale values.
    """

    @staticmethod
    def from_compiled(
        compiled: CompiledGame,
        step_scale: float = 0.005,
        include_private: bool = False,
        **kwargs: Any,
    ) -> RewardCalculator:
        """Build RewardCalculator from any CompiledGame — fully data-driven.

        Args:
            compiled: Any compiled game.
            step_scale: Per-step reward scaling.
            include_private: Also include private resources in weights.
            **kwargs: Passed to RewardCalculator (terminal_win, gamma, etc).
        """
        weights: dict[str, float] = {}
        for rid, rdef in compiled.resources.items():
            if rdef.visibility == Visibility.PUBLIC or include_private:
                norm = max(1.0, abs(rdef.initial) if rdef.initial else 100.0)
                weights[rid] = 1.0 / norm
        return RewardCalculator(
            compiled,
            resource_weights=weights,
            step_scale=step_scale,
            **kwargs,
        )

    @staticmethod
    def auction() -> RewardCalculator:
        from games.auction import auction
        return RewardConfig.from_compiled(auction, step_scale=0.005)

    @staticmethod
    def exchange() -> RewardCalculator:
        from games.exchange import exchange
        return RewardConfig.from_compiled(exchange, step_scale=0.005)

    @staticmethod
    def werewolf() -> RewardCalculator:
        from games.werewolf import werewolf
        return RewardConfig.from_compiled(
            werewolf, step_scale=0.001, score_proportional=False,
        )

    @staticmethod
    def parliament_arena() -> RewardCalculator:
        from games.parliament_arena import parliament_arena
        return RewardConfig.from_compiled(parliament_arena, step_scale=0.005)
