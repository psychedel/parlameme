#!/usr/bin/env python3
"""Backfill PostgreSQL from existing JSON archives and ledger.

Usage:
    PG_DSN=postgresql://parlameme:parlameme-dev@localhost/parlameme \
        uv run python scripts/backfill_pg.py

Idempotent: uses ON CONFLICT DO NOTHING — safe to re-run.
"""

import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.pg import pg_sync


def main():
    dsn = os.environ.get("PG_DSN")
    if not dsn:
        print("ERROR: PG_DSN environment variable not set")
        print(
            "Example: PG_DSN=postgresql://parlameme:parlameme-dev@localhost/parlameme"
        )
        sys.exit(1)

    pg_sync.connect(dsn)
    if not pg_sync.enabled:
        print("ERROR: Could not connect to PostgreSQL")
        sys.exit(1)

    # 1. Backfill archives
    from server.analytics import load_all_archives

    archives = load_all_archives()
    print(f"Found {len(archives)} archives to backfill")
    for a in archives:
        sid = a.get("metadata", {}).get("session_id", "?")
        pg_sync.sync_archive(a)
        print(f"  [archive] {sid} ({a.get('game_id', '?')})")

    # 2. Backfill ledger
    from pathlib import Path

    from engine.ledger import FileLedger

    ledger_path = Path("data/ledger.json")
    if ledger_path.exists():
        ledger = FileLedger(ledger_path)
        entries = ledger.entries()
        print(f"Found {len(entries)} ledger entries to backfill")
        for entry in entries:
            pg_sync.sync_ledger_entry(entry)
            print(
                f"  [ledger] seq={entry.seq} {entry.type} {entry.player} {entry.amount}"
            )
    else:
        print("No ledger file found — skipping")

    # 3. Backfill player stats + ratings
    from engine.rating import Rating, compute_all, tier
    from server.analytics import _compute_player_stats

    if archives:
        stats = _compute_player_stats(archives)
        ratings = compute_all(archives)
        enriched = {}
        for pid, s in stats.items():
            r = ratings.get(pid, Rating())
            enriched[pid] = {**s, "tier": tier(r.mu)}
        pg_sync.bulk_update_player_stats(enriched, ratings)
        print(f"Updated {len(enriched)} player profiles")

    print("\nBackfill complete!")


if __name__ == "__main__":
    main()
