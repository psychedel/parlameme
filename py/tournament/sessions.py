"""Tournament session management — async, locked, shared state."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

import attrs

from engine.runtime.state import CompiledGame, GameState
from server.sessions import create_session

log = logging.getLogger(__name__)

from .persistence import TournamentStore
from .runtime import TournamentRuntime
from .state import TournamentState

_runtime = TournamentRuntime()
_store = TournamentStore()


class TournamentSession:
    """Manages one tournament instance. Thread-safe via asyncio.Lock."""

    def __init__(self, state: TournamentState) -> None:
        self._state = state
        self._lock = asyncio.Lock()
        self._match_timeouts: dict[str, asyncio.Task] = {}  # match_id → timer
        self._listeners: list[Any] = []
        self._completion_callbacks: list[Callable] = []

    @property
    def state(self) -> TournamentState:
        return self._state

    async def register(self, participant: str) -> TournamentState:
        async with self._lock:
            result = _runtime.register(self._state, participant)
            if not result["ok"]:
                raise ValueError(result["error"]["message"])
            self._state = result["state"]
            _store.save(self._state)
        await self._notify()
        return self._state

    async def unregister(self, participant: str) -> TournamentState:
        async with self._lock:
            result = _runtime.unregister(self._state, participant)
            if not result["ok"]:
                raise ValueError(result["error"]["message"])
            self._state = result["state"]
            _store.save(self._state)
        await self._notify()
        return self._state

    async def start(self, compiled: CompiledGame | None = None) -> TournamentState:
        async with self._lock:
            result = _runtime.start(self._state)
            if not result["ok"]:
                raise ValueError(result["error"]["message"])
            self._state = result["state"]
            if compiled:
                await self._spawn_matches(compiled)
            _store.save(self._state)
        await self._notify()
        return self._state

    async def cancel(self, requester: str) -> TournamentState:
        async with self._lock:
            result = _runtime.cancel(self._state, requester)
            if not result["ok"]:
                raise ValueError(result["error"]["message"])
            self._state = result["state"]
            # Cancel all running match timeouts
            for mid, task in list(self._match_timeouts.items()):
                if task and not task.done():
                    task.cancel()
            self._match_timeouts.clear()
            _store.save(self._state)
        await self._notify()
        return self._state

    async def report_result(
        self,
        match_id: str,
        winner: str,
        scores: dict[str, int] | None = None,
        compiled: CompiledGame | None = None,
    ) -> TournamentState:
        completed = False
        async with self._lock:
            result = _runtime.report_result(
                self._state, match_id, winner, scores
            )
            if not result["ok"]:
                raise ValueError(result["error"]["message"])
            self._state = result["state"]

            # Cancel and clean up match timeout
            self._cancel_match_timeout(match_id)

            if compiled:
                await self._spawn_matches(compiled)

            completed = self._state.status == "completed"
            _store.save(self._state)
        if completed:
            await self._on_complete()
        await self._notify()
        return self._state

    async def report_draw(
        self,
        match_id: str,
        scores: dict[str, int] | None = None,
        compiled: CompiledGame | None = None,
    ) -> TournamentState:
        completed = False
        async with self._lock:
            result = _runtime.report_draw(self._state, match_id, scores)
            if not result["ok"]:
                raise ValueError(result["error"]["message"])
            self._state = result["state"]

            self._cancel_match_timeout(match_id)

            if compiled:
                await self._spawn_matches(compiled)

            completed = self._state.status == "completed"
            _store.save(self._state)
        if completed:
            await self._on_complete()
        await self._notify()
        return self._state

    def _cancel_match_timeout(self, match_id: str) -> None:
        """Cancel and remove a match timeout task."""
        task = self._match_timeouts.pop(match_id, None)
        if task and not task.done():
            task.cancel()

    def get_match_session_id(self, match_id: str) -> str | None:
        match = self._state.matches.get(match_id)
        return match.session_id if match else None

    def on_completion(self, callback: Callable) -> None:
        self._completion_callbacks.append(callback)

    def subscribe(self, callback: Any) -> None:
        self._listeners.append(callback)

    def unsubscribe(self, callback: Any) -> None:
        self._listeners = [l for l in self._listeners if l is not callback]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _spawn_matches(self, compiled: CompiledGame) -> None:
        """Create game sessions for pending matches."""
        pending = _runtime.get_pending_matches(self._state)
        for match in pending:
            if match.session_id:
                continue  # already spawned

            session_id = f"t-{self._state.tournament_id}-{match.id}"
            cfg = self._state.config
            session = create_session(
                session_id,
                compiled,
                list(match.participants),
                seed=abs(hash(f"{self._state.seed}-{match.id}")) % (2**31),
                extra_metadata={
                    "tournament_id": self._state.tournament_id,
                    "tournament_type": self._state.tournament_type,
                    "match_id": match.id,
                    "match_round": match.round,
                },
                phase_timeout=cfg.phase_timeout,
                winner_credit=cfg.winner_credit,
                participation_credit=cfg.participation_credit,
                draw_credit=cfg.draw_credit,
            )
            await session.start()

            # Subscribe auto-report listener: when game ends, report result
            self._subscribe_auto_report(session, match.id, compiled)

            # Start match-level timeout
            self._start_match_timeout(match.id, compiled)

            # Mark match as active with session_id
            updated = attrs.evolve(match, status="active", session_id=session_id)
            self._state = attrs.evolve(
                self._state,
                matches={**self._state.matches, match.id: updated},
            )

    def _subscribe_auto_report(
        self,
        game_session: Any,
        match_id: str,
        compiled: CompiledGame,
    ) -> None:
        """Subscribe to a game session; auto-report result when game ends."""
        reported = False  # guard against double-report

        async def _on_game_state_change(game_state: GameState) -> None:
            nonlocal reported
            if reported or game_state.status != "ended":
                return
            reported = True

            victory = game_state.victory_result
            winner = victory.get("winner") if victory else None
            scores = victory.get("scores") if victory else None

            try:
                if winner:
                    await self.report_result(
                        match_id, winner, scores=scores, compiled=compiled
                    )
                    log.info("Auto-reported match %s: winner=%s", match_id, winner)
                else:
                    await self.report_draw(
                        match_id, scores=scores, compiled=compiled
                    )
                    log.info("Auto-reported match %s: draw", match_id)
            except ValueError as exc:
                # Already reported or invalid — not fatal
                log.warning("Auto-report for match %s failed: %s", match_id, exc)

        game_session.subscribe(_on_game_state_change)

    def _start_match_timeout(self, match_id: str, compiled: CompiledGame) -> None:
        """Start a timer that forces a draw if match doesn't complete."""

        async def _timeout() -> None:
            try:
                await asyncio.sleep(self._state.config.match_timeout)
            except asyncio.CancelledError:
                return

            match = self._state.matches.get(match_id)
            if not match or match.status == "completed":
                return

            log.warning(
                "Match timeout: %s in tournament %s — forcing draw",
                match_id,
                self._state.tournament_id,
            )

            try:
                await self.report_draw(match_id, compiled=compiled)
            except ValueError:
                pass  # already completed

        task = asyncio.ensure_future(_timeout())
        self._match_timeouts[match_id] = task

    async def _on_complete(self) -> None:
        self._save_tournament_archive()
        for cb in self._completion_callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(self._state)
                else:
                    cb(self._state)
            except Exception:
                log.exception("Tournament completion callback failed")

    def _save_tournament_archive(self) -> None:
        """Save tournament archive and chronicle on completion."""
        try:
            from .archive import (
                create_tournament_archive,
                generate_tournament_chronicle,
                save_tournament_archive,
                save_tournament_chronicle,
            )

            archive = create_tournament_archive(self._state)
            save_tournament_archive(archive)
            chronicle = generate_tournament_chronicle(self._state)
            save_tournament_chronicle(
                chronicle, tournament_id=self._state.tournament_id
            )
            log.info(
                "Tournament archive saved: %s (%d matches)",
                self._state.tournament_id,
                len(self._state.matches),
            )
        except Exception:
            log.exception(
                "Failed to save tournament archive for %s",
                self._state.tournament_id,
            )

    async def _notify(self) -> None:
        for cb in self._listeners:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(self._state)
                else:
                    cb(self._state)
            except Exception:
                log.exception("Tournament listener callback failed")


# ---------------------------------------------------------------------------
# Global tournament store
# ---------------------------------------------------------------------------

_tournaments: dict[str, TournamentSession] = {}


def create_tournament(
    tournament_id: str,
    tournament_type: str,
    host: str,
    game_type: str,
    **kwargs: Any,
) -> TournamentSession:
    state = _runtime.create(
        tournament_id=tournament_id,
        tournament_type=tournament_type,
        host=host,
        game_type=game_type,
        **kwargs,
    )
    session = TournamentSession(state)
    _tournaments[tournament_id] = session
    _store.save(state)
    return session


def get_tournament(tournament_id: str) -> TournamentSession | None:
    return _tournaments.get(tournament_id)


def list_tournaments() -> dict[str, TournamentSession]:
    return dict(_tournaments)


def remove_tournament(tournament_id: str) -> None:
    _tournaments.pop(tournament_id, None)
    _store.remove(tournament_id)


def load_tournaments() -> int:
    """Load persisted tournaments on startup. Returns count loaded."""
    states = _store.load()
    count = 0
    for tid, state in states.items():
        if tid not in _tournaments:
            _tournaments[tid] = TournamentSession(state)
            count += 1
    return count


def flush_tournaments() -> None:
    """Save all tournament state to disk (call on shutdown)."""
    for ts in _tournaments.values():
        _store.save(ts.state)
    _store.flush()


def reset_all() -> None:
    _tournaments.clear()
