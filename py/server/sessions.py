"""Game session management — async, locked, shared state."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Callable

from engine.archive import (
    Archive,
    _apply_decision,
    create_archive,
    save_archive,
)
from engine.runtime.core import GameRuntime
from engine.runtime.state import CompiledGame, GameState
from server.analytics import invalidate_cache
from server.persistence import SessionStore

log = logging.getLogger(__name__)

ARCHIVE_DIR = Path("data/archives")
CHRONICLE_DIR = Path("data/chronicles")

# Optional ledger for winner crediting (set via set_ledger())
_ledger = None

WINNER_CREDIT = 100  # Credits awarded to game winner
PARTICIPATION_CREDIT = 10  # Credits awarded to all other participants
DRAW_CREDIT = 30  # Credits awarded to each player on draw


def set_ledger(ledger) -> None:
    """Inject ledger instance (called from app startup)."""
    global _ledger
    _ledger = ledger


# Type alias for session listener callbacks
Listener = Callable[[GameState], Any]


class GameSession:
    """Manages one game instance. Thread-safe via asyncio.Lock.

    Pattern: mutate state under lock, then notify OUTSIDE the lock
    to avoid deadlock if a listener calls back into the session.
    """

    # Default phase timeout (seconds) when PhaseDef.duration is not set
    DEFAULT_PHASE_TIMEOUT: int = 300  # 5 minutes

    def __init__(
        self,
        session_id: str,
        compiled: CompiledGame,
        player_ids: list[str],
        seed: int = 42,
        extra_metadata: dict[str, Any] | None = None,
        phase_timeout: int | None = None,
        winner_credit: int | None = None,
        participation_credit: int | None = None,
        draw_credit: int | None = None,
    ):
        self.session_id = session_id
        self.runtime = GameRuntime(compiled)
        self.compiled = compiled
        self.player_ids = player_ids
        self._state = self.runtime.start_game(player_ids, seed)
        self._lock = asyncio.Lock()
        self._listeners: list[Listener] = []
        self._archive: Archive | None = None
        self._timeout_task: asyncio.Task | None = None
        self._timer_start: float | None = None
        self._timer_duration: int | None = None
        self._extra_metadata = extra_metadata or {}
        self._phase_timeout = phase_timeout if phase_timeout is not None else self.DEFAULT_PHASE_TIMEOUT
        self._winner_credit = winner_credit if winner_credit is not None else WINNER_CREDIT
        self._participation_credit = participation_credit if participation_credit is not None else PARTICIPATION_CREDIT
        self._draw_credit = draw_credit if draw_credit is not None else DRAW_CREDIT

    @property
    def state(self) -> GameState:
        return self._state

    @property
    def phase_timeout_remaining(self) -> int | None:
        """Seconds remaining until phase timeout, or None if no timer."""
        if self._timer_start is None or self._timer_duration is None:
            return None
        elapsed = time.monotonic() - self._timer_start
        remaining = self._timer_duration - elapsed
        return max(0, int(remaining))

    async def start(self) -> GameState:
        """Run setup and advance to first interactive phase."""
        async with self._lock:
            self._state = self.runtime.run_setup(self._state)
            self._start_phase_timer()
        await self._notify()
        return self._state

    async def execute_deal(self, deal_id: str, **kwargs) -> dict[str, Any]:
        async with self._lock:
            result = self.runtime.start_deal(self._state, deal_id, **kwargs)
            if result["ok"]:
                self._state = result["state"]
                self._check_victory_and_archive(result)
                if self._state.status != "ended":
                    self._maybe_auto_advance(result)
        await self._notify()
        return result

    async def respond_deal(
        self, instance_id: str, responder_id: str, response: str
    ) -> dict[str, Any]:
        async with self._lock:
            result = self.runtime.respond_to_deal(
                self._state, instance_id, responder_id, response
            )
            if result["ok"]:
                self._state = result["state"]
                self._check_victory_and_archive(result)
                if self._state.status != "ended":
                    self._maybe_auto_advance(result)
        await self._notify()
        return result

    async def start_vote(self, vote_id: str, **kwargs) -> dict[str, Any]:
        async with self._lock:
            result = self.runtime.start_vote(self._state, vote_id, **kwargs)
            if result["ok"]:
                self._state = result["state"]
        await self._notify()
        return result

    async def cast_vote(
        self, instance_id: str, voter_id: str, option: str
    ) -> dict[str, Any]:
        async with self._lock:
            result = self.runtime.cast_vote(self._state, instance_id, voter_id, option)
            if result["ok"]:
                self._state = result["state"]
                self._check_victory_and_archive(result)
                # FIX-16: Auto-advance when vote completes and nothing pending
                if (
                    result.get("auto_completed")
                    and not self._state.pending_votes
                    and not self._state.pending_deals
                    and self._state.status != "ended"
                ):
                    self._state = self._state.record_decision({"type": "advance_phase"})
                    self._state = self.runtime.advance_phase(self._state)
                    result["auto_advanced"] = True
                    self._check_victory_and_archive(result)
        await self._notify()
        return result

    async def send_message(
        self, channel_id: str, sender_id: str, content: str
    ) -> dict[str, Any]:
        async with self._lock:
            result = self.runtime.send_message(
                self._state, channel_id, sender_id, content
            )
            if result["ok"]:
                self._state = result["state"]
        await self._notify()
        return result

    async def execute_speech_act(self, speech_act_id: str, **kwargs) -> dict[str, Any]:
        async with self._lock:
            result = self.runtime.execute_speech_act(
                self._state, speech_act_id, **kwargs
            )
            if result["ok"]:
                self._state = result["state"]
        await self._notify()
        return result

    async def endorse_speech_act(
        self, target_instance_id: str, endorser_id: str
    ) -> dict[str, Any]:
        async with self._lock:
            result = self.runtime.endorse_speech_act(
                self._state, target_instance_id, endorser_id
            )
            if result["ok"]:
                self._state = result["state"]
        await self._notify()
        return result

    async def respond_to_inquire(
        self, instance_id: str, responder_id: str, response: str
    ) -> dict[str, Any]:
        async with self._lock:
            result = self.runtime.respond_to_inquire(
                self._state, instance_id, responder_id, response
            )
            if result["ok"]:
                self._state = result["state"]
        await self._notify()
        return result

    async def advance_phase(self) -> GameState:
        async with self._lock:
            if self._state.status == "ended":
                return self._state
            # Expire pending deals before advancing (return stakes via reject)
            self._expire_pending_deals()
            self._state = self._state.record_decision({"type": "advance_phase"})
            try:
                self._state = self.runtime.advance_phase(self._state)
            except RuntimeError as exc:
                log.error("Phase cascade error in session %s: %s", self.session_id, exc)
                return self._state
            victory = self.runtime.check_victory(self._state)
            if victory:
                self._state = self.runtime.end_game(self._state, victory)
                self._maybe_archive()
            self._start_phase_timer()
        await self._notify()
        return self._state

    @property
    def archive(self) -> Archive | None:
        """Return the archive if the game has ended."""
        return self._archive

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _expire_pending_deals(self) -> None:
        """Expire all pending deals by auto-rejecting. Must be called under lock."""
        for iid, pd in list(self._state.pending_deals.items()):
            for resp_id, resp_val in pd.responders.items():
                if resp_val is None:
                    self._state = self._state.record_decision(
                        {
                            "type": "timeout_expire_deal",
                            "instance_id": iid,
                            "responder": resp_id,
                        }
                    )
                    result = self.runtime.respond_to_deal(
                        self._state, iid, resp_id, "reject"
                    )
                    if result["ok"]:
                        self._state = result["state"]
                    break  # one reject resolves the deal

    def _check_victory_and_archive(self, result: dict[str, Any]) -> None:
        """Check for victory, end game if found. Must be called under lock."""
        victory = self.runtime.check_victory(self._state)
        if victory:
            self._state = self.runtime.end_game(self._state, victory)
            result["victory"] = victory
            self._maybe_archive()

    def _maybe_auto_advance(self, result: dict[str, Any]) -> None:
        """Auto-advance phase when no actions remain for any player.

        Conditions: no pending deals, no pending votes, and no usable
        deals for any active entity in the current phase.
        Must be called under lock.
        """
        if self._state.pending_deals or self._state.pending_votes:
            return

        phase_def = None
        for p in self.compiled.phases:
            if p.id == self._state.phase:
                phase_def = p
                break
        if not phase_def or phase_def.automatic:
            return

        # Check if any active entity can still use any allowed action
        for eid in self._state.get_active_entity_ids():
            for action_id in phase_def.allows:
                deal = self.compiled.deals.get(action_id)
                if deal:
                    # Only check usage limits (fast); skip expr filter evaluation
                    # to avoid false positives from guards that depend on context
                    if not self._is_usage_exhausted(action_id, eid, deal):
                        return  # at least one deal still available
                    continue
                vote = self.compiled.votes.get(action_id)
                if vote:
                    return  # votes are always available (anyone can initiate)
                sa = self.compiled.speech_acts.get(action_id)
                if sa:
                    if not self._is_usage_exhausted(action_id, eid, sa):
                        return  # at least one speech act still available

        # All actions exhausted — auto-advance
        self._state = self._state.record_decision({"type": "advance_phase"})
        self._state = self.runtime.advance_phase(self._state)
        result["auto_advanced"] = True
        self._check_victory_and_archive(result)
        self._start_phase_timer()

    def _is_usage_exhausted(self, action_id: str, entity_id: str, deal_def) -> bool:
        """Check if all usage limits are exhausted for this action+entity."""
        key = f"{entity_id}:{action_id}"
        usage = self._state.usage.get(key, {})
        state = self._state

        if (
            deal_def.per_round is not None
            and usage.get(f"round:{state.round}", 0) >= deal_def.per_round
        ):
            return True
        if (
            deal_def.per_phase is not None
            and usage.get(f"phase:{state.phase}", 0) >= deal_def.per_phase
        ):
            return True
        if deal_def.per_game is not None and usage.get("game", 0) >= deal_def.per_game:
            return True

        # No limit defined = never exhausted via usage
        if (
            deal_def.per_round is None
            and deal_def.per_phase is None
            and deal_def.per_game is None
        ):
            return False

        return False

    # ------------------------------------------------------------------
    # Phase timeout watchdog
    # ------------------------------------------------------------------

    def _start_phase_timer(self) -> None:
        """Start (or restart) the phase timeout timer. Must be called under lock."""
        self._cancel_phase_timer()
        if self._state.status == "ended":
            return

        # Find current phase duration
        phase_def = None
        for p in self.compiled.phases:
            if p.id == self._state.phase:
                phase_def = p
                break

        if phase_def and phase_def.automatic:
            return  # automatic phases don't need timeouts

        duration = (
            phase_def.duration if phase_def else None
        ) or self._phase_timeout
        phase_id = self._state.phase
        round_num = self._state.round

        self._timer_start = time.monotonic()
        self._timer_duration = duration
        self._timeout_task = asyncio.ensure_future(
            self._phase_timeout_handler(duration, phase_id, round_num)
        )

    def _cancel_phase_timer(self) -> None:
        if self._timeout_task and not self._timeout_task.done():
            self._timeout_task.cancel()
        self._timeout_task = None
        self._timer_start = None
        self._timer_duration = None

    async def _phase_timeout_handler(
        self, duration: int, phase_id: str, round_num: int
    ) -> None:
        """Called when phase timeout fires. Expire deals, abstain votes, advance."""
        try:
            await asyncio.sleep(duration)
        except asyncio.CancelledError:
            return

        async with self._lock:
            # Guard: phase may have changed while we slept
            if (
                self._state.phase != phase_id
                or self._state.round != round_num
                or self._state.status == "ended"
            ):
                return

            log.info(
                "Phase timeout: %s round %d in session %s",
                phase_id,
                round_num,
                self.session_id,
            )

            # Expire pending deals (return stakes via reject)
            self._expire_pending_deals()

            # Auto-vote for non-voters (first option as default)
            for iid, pv in list(self._state.pending_votes.items()):
                for voter in pv.eligible:
                    if voter not in pv.votes and pv.options:
                        default_option = pv.options[0]
                        self._state = self._state.record_decision(
                            {
                                "type": "timeout_auto_vote",
                                "instance_id": iid,
                                "voter": voter,
                                "option": default_option,
                            }
                        )
                        result = self.runtime.cast_vote(
                            self._state, iid, voter, default_option
                        )
                        if result["ok"]:
                            self._state = result["state"]

            # Advance phase
            if self._state.status != "ended":
                self._state = self._state.record_decision({"type": "timeout_advance"})
                try:
                    self._state = self.runtime.advance_phase(self._state)
                except RuntimeError as exc:
                    log.error(
                        "Phase cascade error on timeout in %s: %s", self.session_id, exc
                    )
                    return
                victory = self.runtime.check_victory(self._state)
                if victory:
                    self._state = self.runtime.end_game(self._state, victory)
                    self._maybe_archive()
                self._start_phase_timer()

        await self._notify()

    def _maybe_archive(self):
        """Save archive if game just ended. Credit winner via ledger."""
        if self._state.status != "ended" or self._archive is not None:
            return
        try:
            # FIX-28: Include winner/scores in archive metadata
            meta: dict[str, Any] = {
                "session_id": self.session_id,
                **self._extra_metadata,
            }
            victory = self._state.victory_result
            if victory:
                if victory.get("winner"):
                    meta["winner"] = victory["winner"]
                if victory.get("scores"):
                    meta["scores"] = victory["scores"]
                if victory.get("condition"):
                    meta["condition"] = victory["condition"]
            self._archive = create_archive(
                self.compiled,
                self._state,
                metadata=meta,
            )
            path = ARCHIVE_DIR / f"{self.session_id}.json"
            save_archive(self._archive, path)
            invalidate_cache()
            self._credit_participants()
            self._save_chronicle()
            # Sync to PostgreSQL (fail-open)
            from engine.archive import archive_to_dict
            from engine.pg import pg_sync

            pg_sync.sync_archive(archive_to_dict(self._archive))
            log.info(
                "Archive saved: %s (%d decisions)", path, len(self._archive.decisions)
            )
        except Exception:
            log.exception("Failed to save archive for %s", self.session_id)

    def _credit_participants(self):
        """Credit participants via ledger: winner, losers, or draw."""
        if _ledger is None:
            return
        victory = self._state.victory_result
        winner = victory.get("winner") if victory else None
        if winner:
            _ledger.append(
                "game_credit", winner, self._winner_credit, ref=self.session_id
            )
            for pid in self.player_ids:
                if pid != winner:
                    _ledger.append(
                        "game_credit", pid, self._participation_credit, ref=self.session_id
                    )
            log.info(
                "Credited %d to winner %s, %d to %d others (session=%s)",
                self._winner_credit,
                winner,
                self._participation_credit,
                len(self.player_ids) - 1,
                self.session_id,
            )
        else:
            # Draw: everyone gets draw credit
            for pid in self.player_ids:
                _ledger.append(
                    "game_credit", pid, self._draw_credit, ref=self.session_id
                )
            log.info(
                "Credited %d (draw) to %d players (session=%s)",
                self._draw_credit,
                len(self.player_ids),
                self.session_id,
            )

    def _save_chronicle(self):
        """Generate and save game chronicle alongside archive."""
        if self._archive is None:
            return
        try:
            from engine.chronicle import generate_chronicle, save_chronicle

            chronicle = generate_chronicle(self._archive, self.compiled)
            chronicle_path = CHRONICLE_DIR / f"{self.session_id}.jsonl"
            save_chronicle(chronicle, chronicle_path)
            log.info("Chronicle saved: %s (%d events)", chronicle_path, len(chronicle))
        except Exception:
            log.exception("Failed to save chronicle for %s", self.session_id)

    def subscribe(self, callback: Listener) -> None:
        self._listeners.append(callback)

    def unsubscribe(self, callback: Listener) -> None:
        self._listeners = [cb for cb in self._listeners if cb is not callback]

    async def _notify(self):
        """Notify all listeners. Called OUTSIDE the lock."""
        _persistence.touch(self.session_id, decisions=self._state.decisions)
        for callback in self._listeners:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(self._state)
                else:
                    callback(self._state)
            except Exception:
                log.exception("Session listener callback failed")


# ---------------------------------------------------------------------------
# Global session store
# ---------------------------------------------------------------------------

_sessions: dict[str, GameSession] = {}
_persistence = SessionStore()


def get_persistence() -> SessionStore:
    """Access the persistence store (for startup/shutdown hooks)."""
    return _persistence


def create_session(
    session_id: str,
    compiled: CompiledGame,
    player_ids: list[str],
    seed: int = 42,
    extra_metadata: dict[str, Any] | None = None,
    phase_timeout: int | None = None,
    winner_credit: int | None = None,
    participation_credit: int | None = None,
    draw_credit: int | None = None,
) -> GameSession:
    session = GameSession(
        session_id,
        compiled,
        player_ids,
        seed,
        extra_metadata,
        phase_timeout=phase_timeout,
        winner_credit=winner_credit,
        participation_credit=participation_credit,
        draw_credit=draw_credit,
    )
    _sessions[session_id] = session
    _persistence.track(session_id, compiled.id, player_ids, seed)
    return session


def get_session(session_id: str) -> GameSession | None:
    return _sessions.get(session_id)


def list_sessions() -> dict[str, GameSession]:
    return dict(_sessions)


def remove_session(session_id: str):
    session = _sessions.pop(session_id, None)
    if session is not None:
        session._cancel_phase_timer()
    _persistence.remove(session_id)


# ---------------------------------------------------------------------------
# Session recovery from persisted decisions
# ---------------------------------------------------------------------------


def recover_sessions(
    active_data: dict[str, dict], game_registry: dict[str, CompiledGame]
) -> int:
    """Recover active sessions from persisted metadata + decisions.

    Returns number of sessions successfully recovered.
    """
    recovered = 0
    for sid, meta in active_data.items():
        game_type = meta.get("game_type", "")
        compiled = game_registry.get(game_type)
        if not compiled:
            log.warning("Cannot recover %s: unknown game type %s", sid, game_type)
            continue

        players = meta.get("players", [])
        seed = meta.get("seed", 42)
        decisions = meta.get("decisions", [])

        if not players:
            log.warning("Cannot recover %s: no player list", sid)
            continue

        try:
            runtime = GameRuntime(compiled)
            state = runtime.start_game(players, seed)
            state = runtime.run_setup(state)

            for decision in decisions:
                state = _apply_decision(runtime, state, decision)

            # Create session with recovered state
            session = GameSession(sid, compiled, players, seed)
            session._state = state
            _sessions[sid] = session
            # Start phase timer for active recovered sessions
            if state.status != "ended":
                session._start_phase_timer()
            recovered += 1
            log.info(
                "Recovered session %s: phase=%s round=%d (%d decisions)",
                sid,
                state.phase,
                state.round,
                len(decisions),
            )
        except Exception:
            log.exception("Failed to recover session %s", sid)

    return recovered


# ---------------------------------------------------------------------------
# Archive listing
# ---------------------------------------------------------------------------


def list_archives() -> list[dict[str, Any]]:
    """List saved archives with summary info."""
    import json

    archives = []
    if not ARCHIVE_DIR.exists():
        return archives
    for path in sorted(ARCHIVE_DIR.glob("*.json"), reverse=True):
        try:
            data = json.loads(path.read_text())
            archives.append(
                {
                    "session_id": path.stem,
                    "game_id": data.get("game_id", ""),
                    "players": data.get("players", []),
                    "decisions": len(data.get("decisions", [])),
                    "timestamp": data.get("timestamp", 0),
                    "metadata": data.get("metadata", {}),
                    "path": str(path),
                }
            )
        except Exception:
            log.warning("Skipping corrupt archive: %s", path)
    return archives
