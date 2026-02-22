"""Hash-chain ledger — append-only balance tracking with integrity proof.

Each entry links to the previous via SHA-256 hash. Tampering with
any entry invalidates the chain from that point forward.

Design choices:
- Protocol class for abstraction (FileLedger, MemoryLedger)
- Entries are frozen dataclasses — immutable once created
- Balance maintained as running cache (no need to recompute from chain)
- JSON file persistence — simple, human-readable, git-friendly

Not SQLite because the hash-chain IS the value proposition.
A database can be silently modified; a hash chain cannot.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

log = logging.getLogger(__name__)

# Entry types where amount is added to balance
_CREDIT_TYPES = frozenset({"deposit", "game_credit", "refund", "bonus"})


@dataclass(frozen=True)
class LedgerEntry:
    """Single immutable ledger entry."""

    seq: int
    type: str  # deposit | withdraw | game_credit | game_debit | refund | bonus
    player: str
    amount: int  # smallest unit (e.g., cents)
    ref: str = ""  # session-id, tx-hash, or description
    timestamp: float = field(default_factory=time.time)
    prev_hash: str = ""
    content_hash: str = ""

    def is_credit(self) -> bool:
        return self.type in _CREDIT_TYPES


def _compute_hash(
    seq: int, type: str, player: str, amount: int, ref: str, prev_hash: str
) -> str:
    """Deterministic hash of entry content."""
    content = f"{seq}|{type}|{player}|{amount}|{ref}|{prev_hash}"
    return hashlib.sha256(content.encode()).hexdigest()


@runtime_checkable
class Ledger(Protocol):
    """Abstract ledger interface."""

    def append(
        self, type: str, player: str, amount: int, ref: str = ""
    ) -> LedgerEntry: ...
    def balance(self, player: str) -> int: ...
    def all_balances(self) -> dict[str, int]: ...
    def entries(self, player: str | None = None) -> list[LedgerEntry]: ...
    def verify(self) -> bool: ...


class MemoryLedger:
    """In-memory ledger — for tests and short-lived processes."""

    def __init__(self) -> None:
        self._entries: list[LedgerEntry] = []
        self._balances: dict[str, int] = {}
        self._seq = 0

    def append(self, type: str, player: str, amount: int, ref: str = "") -> LedgerEntry:
        prev_hash = self._entries[-1].content_hash if self._entries else "genesis"
        self._seq += 1
        content_hash = _compute_hash(self._seq, type, player, amount, ref, prev_hash)

        entry = LedgerEntry(
            seq=self._seq,
            type=type,
            player=player,
            amount=amount,
            ref=ref,
            prev_hash=prev_hash,
            content_hash=content_hash,
        )
        self._entries.append(entry)

        delta = amount if entry.is_credit() else -amount
        self._balances[player] = self._balances.get(player, 0) + delta
        return entry

    def balance(self, player: str) -> int:
        return self._balances.get(player, 0)

    def all_balances(self) -> dict[str, int]:
        return dict(self._balances)

    def entries(self, player: str | None = None) -> list[LedgerEntry]:
        if player is None:
            return list(self._entries)
        return [e for e in self._entries if e.player == player]

    def verify(self) -> bool:
        return _verify_chain(self._entries)

    def __len__(self) -> int:
        return len(self._entries)


class FileLedger:
    """Persistent ledger backed by JSON file with hash-chain integrity."""

    def __init__(self, path: Path = Path("data/ledger.json")) -> None:
        self._path = path
        self._inner = MemoryLedger()
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text())
            for raw in data.get("entries", []):
                entry = LedgerEntry(**raw)
                self._inner._entries.append(entry)
                self._inner._seq = entry.seq
                delta = entry.amount if entry.is_credit() else -entry.amount
                self._inner._balances[entry.player] = (
                    self._inner._balances.get(entry.player, 0) + delta
                )
            if not _verify_chain(self._inner._entries):
                log.error("Ledger chain integrity check failed on load!")
        except (json.JSONDecodeError, OSError, TypeError, KeyError) as exc:
            log.exception("Failed to load ledger from %s: %s", self._path, exc)

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = {"entries": [asdict(e) for e in self._inner._entries]}
            self._path.write_text(json.dumps(data, indent=2))
        except OSError:
            log.exception("Failed to save ledger to %s", self._path)

    def append(self, type: str, player: str, amount: int, ref: str = "") -> LedgerEntry:
        entry = self._inner.append(type, player, amount, ref)
        self._save()
        # Mirror to PostgreSQL (fail-open)
        from engine.pg import pg_sync

        pg_sync.sync_ledger_entry(entry)
        return entry

    def balance(self, player: str) -> int:
        return self._inner.balance(player)

    def all_balances(self) -> dict[str, int]:
        return self._inner.all_balances()

    def entries(self, player: str | None = None) -> list[LedgerEntry]:
        return self._inner.entries(player)

    def verify(self) -> bool:
        return self._inner.verify()

    def __len__(self) -> int:
        return len(self._inner)


# ---------------------------------------------------------------------------
# Chain verification
# ---------------------------------------------------------------------------


def _verify_chain(entries: list[LedgerEntry]) -> bool:
    """Verify hash-chain integrity of a list of entries."""
    prev_hash = "genesis"
    for entry in entries:
        if entry.prev_hash != prev_hash:
            return False
        expected = _compute_hash(
            entry.seq,
            entry.type,
            entry.player,
            entry.amount,
            entry.ref,
            entry.prev_hash,
        )
        if entry.content_hash != expected:
            return False
        prev_hash = entry.content_hash
    return True
