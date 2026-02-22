"""Session persistence — survive server restarts.

Tracks session metadata (NOT full game state — that's archives).
Enables: restart recovery, stale detection, activity monitoring.

Design:
- In-memory sessions remain the source of truth while running.
- This store persists metadata for recovery decisions on restart.
- Debounced writes coalesce rapid updates into fewer disk ops.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_PATH = Path("data/sessions.json")
SAVE_DELAY = 2.0  # seconds — coalesce rapid updates
STALE_THRESHOLD = 3600  # 1 hour — sessions older than this are "stale"


class SessionStore:
    """Persists session metadata for restart recovery."""

    def __init__(self, path: Path = DEFAULT_PATH):
        self._path = path
        self._data: dict[str, dict[str, Any]] = {}
        self._dirty = False
        self._save_task: asyncio.Task[None] | None = None

    def load(self) -> dict[str, dict[str, dict[str, Any]]]:
        """Load on startup. Returns {"stale": {...}, "active": {...}}."""
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text())
                log.info(
                    "Loaded %d persisted sessions from %s", len(self._data), self._path
                )
            except (json.JSONDecodeError, OSError) as exc:
                log.warning(
                    "Failed to load sessions from %s (%s) — starting fresh",
                    self._path,
                    exc,
                )
                self._data = {}

        now = time.time()
        cutoff = now - STALE_THRESHOLD
        stale = {
            k: v for k, v in self._data.items() if v.get("last_activity", 0) < cutoff
        }
        active = {
            k: v for k, v in self._data.items() if v.get("last_activity", 0) >= cutoff
        }
        return {"stale": stale, "active": active}

    def track(
        self, session_id: str, game_type: str, players: list[str], seed: int = 42
    ) -> None:
        """Track a new session."""
        now = time.time()
        self._data[session_id] = {
            "game_type": game_type,
            "players": players,
            "seed": seed,
            "created_at": now,
            "last_activity": now,
        }
        self._schedule_save()

    def touch(self, session_id: str, decisions: tuple | None = None) -> None:
        """Update activity timestamp and optionally persist decisions."""
        if session_id in self._data:
            self._data[session_id]["last_activity"] = time.time()
            if decisions is not None:
                self._data[session_id]["decisions"] = [dict(d) for d in decisions]
            self._schedule_save()

    def remove(self, session_id: str) -> None:
        """Session completed or cancelled."""
        if self._data.pop(session_id, None) is not None:
            self._schedule_save()

    @property
    def sessions(self) -> dict[str, dict[str, Any]]:
        """Current tracked sessions (read-only view)."""
        return dict(self._data)

    # ------------------------------------------------------------------
    # Save management
    # ------------------------------------------------------------------

    def _schedule_save(self) -> None:
        """Debounced save — coalesce rapid updates."""
        self._dirty = True
        if self._save_task is None or self._save_task.done():
            try:
                loop = asyncio.get_running_loop()
                self._save_task = loop.create_task(self._delayed_save())
            except RuntimeError:
                # No event loop — save synchronously (test/CLI context)
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
            log.exception("Failed to persist sessions to %s", self._path)
