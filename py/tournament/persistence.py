"""Tournament persistence — survive server restarts.

Serializes TournamentState to JSON for recovery.
Debounced writes like SessionStore.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import attrs

from .config import TournamentConfig
from .state import Match, Standing, TournamentState

log = logging.getLogger(__name__)

DEFAULT_PATH = Path("data/tournaments.json")
SAVE_DELAY = 2.0  # seconds — coalesce rapid updates


def _state_to_dict(state: TournamentState) -> dict[str, Any]:
    """Serialize TournamentState to JSON-safe dict."""
    return {
        "tournament_id": state.tournament_id,
        "tournament_type": state.tournament_type,
        "status": state.status,
        "host": state.host,
        "name": state.name,
        "game_type": state.game_type,
        "min_participants": state.min_participants,
        "max_participants": state.max_participants,
        "match_size": state.match_size,
        "rounds": state.rounds,
        "participants": list(state.participants),
        "matches": {
            mid: {
                "id": m.id,
                "participants": list(m.participants),
                "round": m.round,
                "stage": m.stage,
                "status": m.status,
                "winner": m.winner,
                "scores": m.scores,
                "session_id": m.session_id,
            }
            for mid, m in state.matches.items()
        },
        "standings": {
            pid: {
                "participant": s.participant,
                "points": s.points,
                "wins": s.wins,
                "losses": s.losses,
                "draws": s.draws,
                "goal_diff": s.goal_diff,
                "buchholz": s.buchholz,
            }
            for pid, s in state.standings.items()
        },
        "winner": state.winner,
        "seed": state.seed,
        "config": {
            "win_points": state.config.win_points,
            "draw_points": state.config.draw_points,
            "loss_points": state.config.loss_points,
            "tiebreaker": list(state.config.tiebreaker),
            "match_timeout": state.config.match_timeout,
            "phase_timeout": state.config.phase_timeout,
            "winner_credit": state.config.winner_credit,
            "participation_credit": state.config.participation_credit,
            "draw_credit": state.config.draw_credit,
            "swiss_max_rounds": state.config.swiss_max_rounds,
        },
    }


def _dict_to_state(d: dict[str, Any]) -> TournamentState:
    """Deserialize dict back to TournamentState."""
    matches = {
        mid: Match(
            id=m["id"],
            participants=tuple(m["participants"]),
            round=m["round"],
            stage=m.get("stage", "main"),
            status=m["status"],
            winner=m.get("winner"),
            scores=m.get("scores", {}),
            session_id=m.get("session_id"),
        )
        for mid, m in d.get("matches", {}).items()
    }
    standings = {
        pid: Standing(
            participant=s["participant"],
            points=s.get("points", 0),
            wins=s.get("wins", 0),
            losses=s.get("losses", 0),
            draws=s.get("draws", 0),
            goal_diff=s.get("goal_diff", 0),
            buchholz=s.get("buchholz", 0.0),
        )
        for pid, s in d.get("standings", {}).items()
    }
    raw_cfg = d.get("config", {})
    config = TournamentConfig(
        win_points=raw_cfg.get("win_points", 3),
        draw_points=raw_cfg.get("draw_points", 1),
        loss_points=raw_cfg.get("loss_points", 0),
        tiebreaker=tuple(raw_cfg.get("tiebreaker", ("points", "goal_diff", "wins"))),
        match_timeout=raw_cfg.get("match_timeout", 1800),
        phase_timeout=raw_cfg.get("phase_timeout", 300),
        winner_credit=raw_cfg.get("winner_credit", 100),
        participation_credit=raw_cfg.get("participation_credit", 10),
        draw_credit=raw_cfg.get("draw_credit", 30),
        swiss_max_rounds=raw_cfg.get("swiss_max_rounds"),
    )
    return TournamentState(
        tournament_id=d["tournament_id"],
        tournament_type=d["tournament_type"],
        status=d.get("status", "registration"),
        host=d.get("host", ""),
        name=d.get("name", ""),
        game_type=d.get("game_type", ""),
        min_participants=d.get("min_participants", 2),
        max_participants=d.get("max_participants", 16),
        match_size=d.get("match_size", 2),
        rounds=d.get("rounds"),
        config=config,
        participants=tuple(d.get("participants", [])),
        matches=matches,
        standings=standings,
        winner=d.get("winner"),
        seed=d.get("seed", 42),
    )


class TournamentStore:
    """Persists tournament state for restart recovery."""

    def __init__(self, path: Path = DEFAULT_PATH):
        self._path = path
        self._data: dict[str, dict[str, Any]] = {}
        self._dirty = False
        self._save_task: asyncio.Task[None] | None = None

    def load(self) -> dict[str, TournamentState]:
        """Load on startup. Returns {tournament_id: TournamentState}."""
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text())
                log.info(
                    "Loaded %d persisted tournaments from %s",
                    len(self._data),
                    self._path,
                )
            except (json.JSONDecodeError, OSError) as exc:
                log.warning(
                    "Failed to load tournaments from %s (%s) — starting fresh",
                    self._path,
                    exc,
                )
                self._data = {}

        return {tid: _dict_to_state(d) for tid, d in self._data.items()}

    def save(self, state: TournamentState) -> None:
        """Persist a tournament (upsert)."""
        self._data[state.tournament_id] = _state_to_dict(state)
        self._schedule_save()

    def remove(self, tournament_id: str) -> None:
        """Remove a completed/cancelled tournament from persistence."""
        if self._data.pop(tournament_id, None) is not None:
            self._schedule_save()

    # ------------------------------------------------------------------
    # Save management
    # ------------------------------------------------------------------

    def _schedule_save(self) -> None:
        self._dirty = True
        if self._save_task is None or self._save_task.done():
            try:
                loop = asyncio.get_running_loop()
                self._save_task = loop.create_task(self._delayed_save())
            except RuntimeError:
                self.flush()

    async def _delayed_save(self) -> None:
        await asyncio.sleep(SAVE_DELAY)
        if self._dirty:
            self._write()

    def flush(self) -> None:
        """Synchronous save — call on shutdown."""
        if self._dirty:
            self._write()

    def _write(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._data, indent=2))
            self._dirty = False
        except OSError:
            log.exception("Failed to persist tournaments to %s", self._path)
