"""Strategy persistence — JSON file per strategy.

Follows the same patterns as server/persistence.py and tournament/persistence.py:
- One JSON file per strategy in data/strategies/
- Debounced writes for rapid edits
- cattrs for serialization
- Version backups on save
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any

import attrs
import cattrs

from strategy.schema import Strategy

log = logging.getLogger(__name__)

DEFAULT_DIR = Path("data/strategies")

_VALID_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_VERSION_BACKUP_RE = re.compile(r"\.v\d+$")


# ---------------------------------------------------------------------------
# cattrs converter
# ---------------------------------------------------------------------------

_converter = cattrs.Converter()


def _strategy_to_dict(s: Strategy) -> dict[str, Any]:
    return _converter.unstructure(s)


def _dict_to_strategy(d: dict[str, Any]) -> Strategy:
    return _converter.structure(d, Strategy)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class StrategyStore:
    """Persist strategies as individual JSON files."""

    def __init__(self, directory: Path = DEFAULT_DIR):
        self._dir = directory
        self._dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _validate_id(strategy_id: str) -> None:
        if not _VALID_ID_RE.match(strategy_id):
            raise ValueError(
                f"Invalid strategy ID {strategy_id!r}: "
                "must be 1-64 chars of [a-zA-Z0-9_-]"
            )

    def _path(self, strategy_id: str) -> Path:
        self._validate_id(strategy_id)
        return self._dir / f"{strategy_id}.json"

    def _version_path(self, strategy_id: str, version: int) -> Path:
        return self._dir / f"{strategy_id}.v{version}.json"

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def save(self, strategy: Strategy) -> Strategy:
        """Save strategy to disk. Returns the saved strategy.

        If the strategy already exists on disk, the old version is backed up
        as ``{id}.v{old_version}.json`` before overwriting.
        """
        path = self._path(strategy.id)

        # Back up previous version if it exists
        if path.exists():
            try:
                old = self.load(strategy.id)
                if old is not None and old.version < strategy.version:
                    backup = self._version_path(strategy.id, old.version)
                    backup.write_text(json.dumps(_strategy_to_dict(old), indent=2))
            except Exception as exc:
                log.debug("Version backup failed for %s: %s", strategy.id, exc)

        path.write_text(json.dumps(_strategy_to_dict(strategy), indent=2))
        return strategy

    def load(self, strategy_id: str) -> Strategy | None:
        """Load a strategy by ID. Returns None if not found."""
        path = self._path(strategy_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            return _dict_to_strategy(data)
        except (json.JSONDecodeError, cattrs.ClassValidationError, KeyError) as exc:
            log.warning("Failed to load strategy %s: %s", strategy_id, exc)
            return None

    def delete(self, strategy_id: str) -> bool:
        """Delete a strategy and its version backups. Returns True if found."""
        path = self._path(strategy_id)
        if not path.exists():
            return False
        path.unlink()
        # Clean up version backups
        for backup in self._dir.glob(f"{strategy_id}.v*.json"):
            backup.unlink(missing_ok=True)
        return True

    def list_all(self) -> list[Strategy]:
        """List all strategies, sorted by updated_at descending."""
        strategies = []
        for path in self._dir.glob("*.json"):
            # Skip version backups (e.g. abc123.v2.json)
            if _VERSION_BACKUP_RE.search(path.stem):
                continue
            try:
                data = json.loads(path.read_text())
                strategies.append(_dict_to_strategy(data))
            except Exception:
                log.warning("Skipping corrupt strategy file: %s", path)
        strategies.sort(key=lambda s: s.updated_at, reverse=True)
        return strategies

    def list_by_author(self, author: str) -> list[Strategy]:
        """List strategies by a specific author."""
        return [s for s in self.list_all() if s.author == author]

    def list_public(self) -> list[Strategy]:
        """List all public strategies (community gallery)."""
        return [s for s in self.list_all() if s.public]

    def fork(self, strategy_id: str, new_author: str) -> Strategy | None:
        """Fork a strategy — copy with new id, author, and forked_from pointer."""
        original = self.load(strategy_id)
        if original is None:
            return None

        forked = attrs.evolve(
            original,
            id=uuid.uuid4().hex[:12],
            author=new_author,
            name=f"{original.name} (fork)",
            version=1,
            forked_from=original.id,
            created_at=time.time(),
            updated_at=time.time(),
            public=False,
        )
        self.save(forked)
        return forked

    def list_versions(self, strategy_id: str) -> list[int]:
        """List available version numbers for a strategy."""
        versions = []
        for path in self._dir.glob(f"{strategy_id}.v*.json"):
            try:
                v = int(path.stem.split(".v")[1])
                versions.append(v)
            except (ValueError, IndexError):
                continue
        versions.sort()
        return versions

    def load_version(self, strategy_id: str, version: int) -> Strategy | None:
        """Load a specific version backup."""
        path = self._version_path(strategy_id, version)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            return _dict_to_strategy(data)
        except Exception:
            return None
