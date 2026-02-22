"""Arena — run multiple LLM agents against each other in real games.

Each player gets their own Strategy (system prompt), AgentRunner, and
InProcessBridge. BotRunner fills remaining slots if fewer strategies
than min_players. Archives get full strategy metadata for all agents.

Usage:
    arena = Arena(provider_type="anthropic", api_key="sk-...")
    report = await arena.run(
        game_id="auction",
        strategies=["strat-abc", "strat-def"],  # strategy IDs
        num_games=5,
    )
    print(report.per_strategy)  # {sid: {wins, losses, win_rate}}
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

import attrs

from strategy.schema import Strategy
from strategy.store import StrategyStore

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@attrs.frozen
class ArenaConfig:
    """Configuration for an arena run."""

    game_id: str
    strategy_ids: tuple[str, ...]  # strategy IDs from store
    num_games: int = 1
    seeds: tuple[int, ...] = ()  # explicit seeds; auto-generated if empty
    provider_type: str = "anthropic"
    model: str = ""
    api_key: str = ""
    phase_timeout: int = 120
    game_timeout: int = 600  # max seconds per game before forced stop


@attrs.frozen
class GameResult:
    """Result of a single arena game."""

    game_index: int
    seed: int
    winner: str | None
    scores: dict[str, int]
    strategy_map: dict[str, str]  # player_id -> strategy_id
    archive_path: str
    decisions_count: int
    final_resources: dict[str, dict[str, int]]  # player -> {resource: amount}
    error: str = ""


@attrs.frozen
class ArenaReport:
    """Aggregated results across all arena games."""

    config: ArenaConfig
    results: tuple[GameResult, ...]
    per_strategy: dict[str, dict[str, Any]]  # sid -> {wins, losses, draws, win_rate, games}
    head_to_head: dict[str, dict[str, int]]  # "s1_vs_s2" -> {s1: N, s2: N, draws: N}
    total_games: int
    total_decisions: int
    elapsed_seconds: float
    timestamp: float = attrs.Factory(time.time)


# ---------------------------------------------------------------------------
# Arena
# ---------------------------------------------------------------------------


class Arena:
    """Orchestrate multi-strategy LLM agent games.

    Creates game sessions, wires each player to an LLM AgentRunner with
    their strategy as system prompt, and collects results from archives.
    """

    def __init__(
        self,
        provider_type: str = "anthropic",
        model: str = "",
        api_key: str = "",
        store: StrategyStore | None = None,
    ):
        self._provider_type = provider_type
        self._model = model
        self._api_key = api_key
        self._store = store or StrategyStore()

    async def run(
        self,
        game_id: str,
        strategy_ids: list[str],
        num_games: int = 1,
        seeds: list[int] | None = None,
        phase_timeout: int = 120,
        game_timeout: int = 600,
    ) -> ArenaReport:
        """Run a full arena evaluation. Main entry point.

        Args:
            game_id: which game to play (must be in REGISTRY)
            strategy_ids: list of strategy IDs (each becomes one LLM agent)
            num_games: how many games to run
            seeds: explicit seeds for reproducibility; auto if None
            phase_timeout: seconds per game phase
            game_timeout: max seconds per game total

        Returns:
            ArenaReport with per-strategy stats and head-to-head matrix.
        """
        from games import REGISTRY as GAME_REGISTRY

        compiled = GAME_REGISTRY.get(game_id)
        if not compiled:
            raise ValueError(f"Game '{game_id}' not found in registry")

        # Load strategies
        strategies: dict[str, Strategy] = {}
        for sid in strategy_ids:
            s = self._store.load(sid)
            if not s:
                raise ValueError(f"Strategy '{sid}' not found")
            strategies[sid] = s

        # Generate seeds
        if seeds:
            game_seeds = list(seeds)
        else:
            import random as _random

            rng = _random.Random(42)
            game_seeds = [rng.randint(0, 2**31) for _ in range(num_games)]

        config = ArenaConfig(
            game_id=game_id,
            strategy_ids=tuple(strategy_ids),
            num_games=num_games,
            seeds=tuple(game_seeds),
            provider_type=self._provider_type,
            model=self._model,
            api_key=self._api_key,
            phase_timeout=phase_timeout,
            game_timeout=game_timeout,
        )

        start_time = time.monotonic()
        results: list[GameResult] = []

        for i, seed in enumerate(game_seeds[:num_games]):
            log.info("Arena game %d/%d (seed=%d)", i + 1, num_games, seed)
            result = await self._run_single_game(
                compiled, strategies, i, seed, config
            )
            results.append(result)

        elapsed = time.monotonic() - start_time
        return _aggregate(config, tuple(results), elapsed)

    async def run_with_strategies(
        self,
        game_id: str,
        strategies: list[Strategy],
        num_games: int = 1,
        seeds: list[int] | None = None,
        phase_timeout: int = 120,
        game_timeout: int = 600,
    ) -> ArenaReport:
        """Run arena with Strategy objects directly (no store lookup).

        Useful for testing and programmatic usage where strategies
        aren't persisted.
        """
        from games import REGISTRY as GAME_REGISTRY

        compiled = GAME_REGISTRY.get(game_id)
        if not compiled:
            raise ValueError(f"Game '{game_id}' not found in registry")

        strategy_map = {s.id: s for s in strategies}

        if seeds:
            game_seeds = list(seeds)
        else:
            import random as _random

            rng = _random.Random(42)
            game_seeds = [rng.randint(0, 2**31) for _ in range(num_games)]

        config = ArenaConfig(
            game_id=game_id,
            strategy_ids=tuple(s.id for s in strategies),
            num_games=num_games,
            seeds=tuple(game_seeds),
            provider_type=self._provider_type,
            model=self._model,
            api_key=self._api_key,
            phase_timeout=phase_timeout,
            game_timeout=game_timeout,
        )

        start_time = time.monotonic()
        results: list[GameResult] = []

        for i, seed in enumerate(game_seeds[:num_games]):
            result = await self._run_single_game(
                compiled, strategy_map, i, seed, config
            )
            results.append(result)

        elapsed = time.monotonic() - start_time
        return _aggregate(config, tuple(results), elapsed)

    # ------------------------------------------------------------------
    # Single game execution
    # ------------------------------------------------------------------

    async def _run_single_game(
        self,
        compiled: Any,
        strategies: dict[str, Strategy],
        game_index: int,
        seed: int,
        config: ArenaConfig,
    ) -> GameResult:
        """Run one game with LLM agents for each strategy."""
        from agent.bots import BotRunner
        from agent.bridge import InProcessBridge
        from agent.runner import AgentRunner
        from mcp.server import MCPServer
        from server.sessions import create_session, remove_session

        session_id = f"arena-{uuid.uuid4().hex[:8]}"
        strategy_list = list(strategies.values())

        # Assign strategies to player slots (rotate across games for fairness)
        min_players = compiled.min_players
        num_agents = len(strategy_list)

        # Rotate strategy assignment by game_index
        rotated = strategy_list[game_index % num_agents :] + strategy_list[: game_index % num_agents]

        # Build player list: agent slots + bot filler slots if needed
        players: list[str] = []
        strategy_map: dict[str, str] = {}  # player_id -> strategy_id

        for i, strat in enumerate(rotated[:min_players]):
            pid = f"agent-{i}"
            players.append(pid)
            strategy_map[pid] = strat.id

        # Fill remaining slots with BotRunner players
        bot_ids: list[str] = []
        for i in range(len(players), min_players):
            pid = f"bot-{i}"
            players.append(pid)
            bot_ids.append(pid)

        # Create session with strategy metadata
        extra_metadata = {
            "strategies": strategy_map,
            "arena": True,
            "arena_game_index": game_index,
        }
        session = create_session(
            session_id,
            compiled,
            players,
            seed=seed,
            extra_metadata=extra_metadata,
            phase_timeout=config.phase_timeout,
        )

        # Get or create MCPServer for in-process bridge
        mcp = _get_mcp_server()

        error = ""
        try:
            await session.start()

            # Create agents for strategy-assigned players
            runners: list[AgentRunner] = []
            tasks: list[asyncio.Task] = []

            for pid, sid in strategy_map.items():
                strat = strategies[sid]
                bridge = InProcessBridge(mcp, pid)
                provider = self._create_provider()
                runner = AgentRunner(
                    strategy=strat,
                    bridge=bridge,
                    provider=provider,
                    compiled=compiled,
                )
                runners.append(runner)

                # Initialize and join game via MCP
                await bridge.initialize()
                await bridge.call_tool(
                    "join_game",
                    {"session_id": session_id, "player_name": strat.name},
                )

            # Start BotRunner for filler players
            bot_runner: BotRunner | None = None
            if bot_ids:
                bot_runner = BotRunner(session, compiled, bot_ids, seed=seed)
                bot_runner.start()

            # Run all agents concurrently with game timeout
            agent_tasks = [asyncio.create_task(r.run_game()) for r in runners]

            try:
                await asyncio.wait_for(
                    asyncio.gather(*agent_tasks, return_exceptions=True),
                    timeout=config.game_timeout,
                )
            except asyncio.TimeoutError:
                log.warning("Arena game %d timed out after %ds", game_index, config.game_timeout)
                for t in agent_tasks:
                    if not t.done():
                        t.cancel()
                for r in runners:
                    r.stop()
                # Let cancellations propagate
                await asyncio.sleep(0.1)

            # Stop bot runner
            if bot_runner:
                bot_runner.stop()

        except Exception as exc:
            error = str(exc)
            log.exception("Arena game %d failed", game_index)

        # Extract result from session state
        state = session.state
        victory = state.victory_result
        winner_pid = victory.get("winner") if victory else None
        scores = victory.get("scores") if victory else {}

        # Map winner player_id back to strategy_id
        winner_sid = strategy_map.get(winner_pid) if winner_pid else None

        # Extract final resources
        final_resources: dict[str, dict[str, int]] = {}
        for pid in players:
            entity = state.entities.get(pid)
            if entity:
                final_resources[pid] = dict(entity.resources)

        # Archive path
        archive_path = ""
        if session.archive:
            archive_path = f"data/archives/{session_id}.json"

        # Cleanup
        remove_session(session_id)

        return GameResult(
            game_index=game_index,
            seed=seed,
            winner=winner_sid,
            scores=scores or {},
            strategy_map=strategy_map,
            archive_path=archive_path,
            decisions_count=len(state.decisions),
            final_resources=final_resources,
            error=error,
        )

    def _create_provider(self) -> Any:
        """Create an LLM provider for one agent."""
        from agent.providers import create_provider

        kwargs: dict[str, Any] = {}
        if self._api_key:
            kwargs["api_key"] = self._api_key
        return create_provider(
            self._provider_type,
            model=self._model or None,
            **kwargs,
        )


# ---------------------------------------------------------------------------
# MCP server singleton for arena
# ---------------------------------------------------------------------------

_arena_mcp: MCPServer | None = None


def _get_mcp_server() -> Any:
    """Get or create an MCPServer for arena use.

    Tries to reuse the app's MCPServer first (if running inside the
    NiceGUI app). Falls back to creating a standalone instance.
    """
    global _arena_mcp

    # Try the app's MCP server first
    try:
        from server.app import mcp

        if mcp is not None:
            return mcp
    except (ImportError, AttributeError):
        pass

    # Create standalone MCP server for headless arena
    if _arena_mcp is None:
        from mcp.server import MCPServer

        _arena_mcp = MCPServer()
    return _arena_mcp


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _aggregate(
    config: ArenaConfig,
    results: tuple[GameResult, ...],
    elapsed: float,
) -> ArenaReport:
    """Aggregate individual game results into an ArenaReport."""
    per_strategy: dict[str, dict[str, Any]] = {}
    for sid in config.strategy_ids:
        per_strategy[sid] = {"wins": 0, "losses": 0, "draws": 0, "games": 0, "win_rate": 0.0}

    h2h: dict[str, dict[str, int]] = {}
    total_decisions = 0

    for r in results:
        total_decisions += r.decisions_count

        # Track per-strategy results
        participating_sids = set(r.strategy_map.values())
        for sid in participating_sids:
            per_strategy.setdefault(sid, {"wins": 0, "losses": 0, "draws": 0, "games": 0, "win_rate": 0.0})
            per_strategy[sid]["games"] += 1

            if r.winner is None:
                per_strategy[sid]["draws"] += 1
            elif r.winner == sid:
                per_strategy[sid]["wins"] += 1
            else:
                per_strategy[sid]["losses"] += 1

        # Head-to-head tracking
        sids_in_game = sorted(participating_sids)
        for i, s1 in enumerate(sids_in_game):
            for s2 in sids_in_game[i + 1 :]:
                key = f"{s1}_vs_{s2}"
                if key not in h2h:
                    h2h[key] = {s1: 0, s2: 0, "draws": 0}
                if r.winner == s1:
                    h2h[key][s1] += 1
                elif r.winner == s2:
                    h2h[key][s2] += 1
                else:
                    h2h[key]["draws"] += 1

    # Compute win rates
    for sid, stats in per_strategy.items():
        total = stats["games"]
        stats["win_rate"] = stats["wins"] / total if total > 0 else 0.0

    return ArenaReport(
        config=config,
        results=results,
        per_strategy=per_strategy,
        head_to_head=h2h,
        total_games=len(results),
        total_decisions=total_decisions,
        elapsed_seconds=elapsed,
    )
