"""PostgreSQL sync — dual-write mirror of JSON persistence.

Fail-open design: if PG is unavailable, log warning and continue.
JSON files remain the source of truth. PG is a read-optimized projection.

Usage:
    from engine.pg import pg_sync

    # On startup (disabled if PG_DSN not set)
    pg_sync.connect(os.environ.get("PG_DSN"))

    # After archiving a game
    pg_sync.sync_archive(archive_dict)

    # After ledger append
    pg_sync.sync_ledger_entry(entry)

    # After stats recompute
    pg_sync.update_player_stats(player_id, stats_dict, rating)
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from engine.ledger import LedgerEntry
    from engine.rating import Rating

log = logging.getLogger(__name__)

try:
    import psycopg
except ImportError:
    psycopg = None  # type: ignore[assignment]


class PgSync:
    """Dual-write mirror to PostgreSQL. Fail-open on every operation."""

    def __init__(self) -> None:
        self._dsn: str | None = None

    def connect(self, dsn: str | None) -> None:
        """Enable PG sync. Call once on startup."""
        if not dsn:
            log.info("PG_DSN not set — PostgreSQL sync disabled")
            return
        if psycopg is None:
            log.warning("psycopg not installed — PostgreSQL sync disabled")
            return
        # Verify connection works
        try:
            with psycopg.connect(dsn) as conn:
                conn.execute("SELECT 1")
            self._dsn = dsn
            host = dsn.split("@")[-1] if "@" in dsn else dsn
            log.info("PostgreSQL sync enabled: %s", host)
        except Exception:
            log.exception("PostgreSQL connection failed — sync disabled")

    @property
    def enabled(self) -> bool:
        return self._dsn is not None and psycopg is not None

    @contextmanager
    def _conn(self):
        """Yield a connection or None if unavailable. Never raises."""
        if not self.enabled:
            yield None
            return
        try:
            with psycopg.connect(self._dsn) as conn:  # type: ignore[arg-type]
                yield conn
        except Exception:
            log.exception("PostgreSQL connection failed — skipping sync")
            yield None

    # ------------------------------------------------------------------
    # Archive sync
    # ------------------------------------------------------------------

    def sync_archive(self, archive: dict[str, Any]) -> None:
        """Index a completed game archive in PostgreSQL."""
        with self._conn() as conn:
            if conn is None:
                return
            meta = archive.get("metadata", {})
            players = archive.get("players", [])
            decisions = archive.get("decisions", [])
            round_count = sum(1 for d in decisions if d.get("type") == "advance_phase")
            conn.execute(
                """
                INSERT INTO game_archives
                    (session_id, game_type, rules_hash, seed,
                     players, player_count, winner, scores,
                     victory_condition, decision_count, round_count,
                     archive_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (session_id) DO NOTHING
                """,
                (
                    meta.get("session_id", ""),
                    archive.get("game_id", ""),
                    archive.get("rules_hash", ""),
                    archive.get("seed", 0),
                    json.dumps(players),
                    len(players),
                    meta.get("winner"),
                    json.dumps(meta.get("scores")) if meta.get("scores") else None,
                    meta.get("condition"),
                    len(decisions),
                    round_count,
                    json.dumps(archive),
                ),
            )
            conn.commit()
            log.debug("PG: synced archive %s", meta.get("session_id"))

    # ------------------------------------------------------------------
    # Ledger sync
    # ------------------------------------------------------------------

    def sync_ledger_entry(self, entry: LedgerEntry) -> None:
        """Mirror a ledger entry to PostgreSQL."""
        with self._conn() as conn:
            if conn is None:
                return
            conn.execute(
                """
                INSERT INTO ledger_entries
                    (seq, type, player, amount, ref, prev_hash, content_hash)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (seq) DO NOTHING
                """,
                (
                    entry.seq,
                    entry.type,
                    entry.player,
                    entry.amount,
                    entry.ref,
                    entry.prev_hash,
                    entry.content_hash,
                ),
            )
            conn.commit()

    # ------------------------------------------------------------------
    # Player stats sync
    # ------------------------------------------------------------------

    def update_player_stats(
        self, player_id: str, stats: dict[str, Any], rating: Rating
    ) -> None:
        """Upsert player profile with latest stats and rating."""
        with self._conn() as conn:
            if conn is None:
                return
            conn.execute(
                """
                INSERT INTO player_profiles
                    (display_name, rating_mu, rating_rd, rating_vol, rating_tier,
                     games_played, wins, losses, win_rate, best_streak, last_game_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (display_name) DO UPDATE SET
                    rating_mu = EXCLUDED.rating_mu,
                    rating_rd = EXCLUDED.rating_rd,
                    rating_vol = EXCLUDED.rating_vol,
                    rating_tier = EXCLUDED.rating_tier,
                    games_played = EXCLUDED.games_played,
                    wins = EXCLUDED.wins,
                    losses = EXCLUDED.losses,
                    win_rate = EXCLUDED.win_rate,
                    best_streak = EXCLUDED.best_streak,
                    last_game_at = NOW()
                """,
                (
                    player_id,
                    round(rating.mu, 1),
                    round(rating.rd, 1),
                    rating.vol,
                    stats.get("tier", "Novice"),
                    stats.get("games", 0),
                    stats.get("wins", 0),
                    stats.get("losses", 0),
                    stats.get("win_rate", 0.0),
                    stats.get("best_streak", 0),
                ),
            )
            conn.commit()

    def bulk_update_player_stats(
        self, all_stats: dict[str, dict[str, Any]], all_ratings: dict[str, Rating]
    ) -> None:
        """Batch upsert all player profiles after stats recompute."""
        if not self.enabled:
            return
        from engine.rating import Rating as RatingClass

        with self._conn() as conn:
            if conn is None:
                return
            for player_id, stats in all_stats.items():
                rating = all_ratings.get(player_id, RatingClass())
                conn.execute(
                    """
                    INSERT INTO player_profiles
                        (display_name, rating_mu, rating_rd, rating_vol, rating_tier,
                         games_played, wins, losses, win_rate, best_streak)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (display_name) DO UPDATE SET
                        rating_mu = EXCLUDED.rating_mu,
                        rating_rd = EXCLUDED.rating_rd,
                        rating_vol = EXCLUDED.rating_vol,
                        rating_tier = EXCLUDED.rating_tier,
                        games_played = EXCLUDED.games_played,
                        wins = EXCLUDED.wins,
                        losses = EXCLUDED.losses,
                        win_rate = EXCLUDED.win_rate,
                        best_streak = EXCLUDED.best_streak
                    """,
                    (
                        player_id,
                        round(rating.mu, 1),
                        round(rating.rd, 1),
                        rating.vol,
                        stats.get("tier", "Novice"),
                        stats.get("games", 0),
                        stats.get("wins", 0),
                        stats.get("losses", 0),
                        stats.get("win_rate", 0.0),
                        stats.get("best_streak", 0),
                    ),
                )
            conn.commit()
            log.debug("PG: bulk-updated %d player profiles", len(all_stats))

    # ------------------------------------------------------------------
    # Session sync (replaces sessions.json)
    # ------------------------------------------------------------------

    def upsert_session(self, session_id: str, data: dict[str, Any]) -> None:
        """Track active session in PostgreSQL."""
        with self._conn() as conn:
            if conn is None:
                return
            conn.execute(
                """
                INSERT INTO active_sessions
                    (session_id, game_type, players, seed, decisions,
                     current_phase, current_round, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (session_id) DO UPDATE SET
                    decisions = EXCLUDED.decisions,
                    current_phase = EXCLUDED.current_phase,
                    current_round = EXCLUDED.current_round,
                    status = EXCLUDED.status,
                    last_activity = NOW()
                """,
                (
                    session_id,
                    data.get("game_type", ""),
                    json.dumps(data.get("players", [])),
                    data.get("seed", 42),
                    json.dumps(data.get("decisions", [])),
                    data.get("current_phase"),
                    data.get("current_round", 1),
                    data.get("status", "active"),
                ),
            )
            conn.commit()

    def remove_session(self, session_id: str) -> None:
        """Remove completed/cancelled session from active tracking."""
        with self._conn() as conn:
            if conn is None:
                return
            conn.execute(
                "DELETE FROM active_sessions WHERE session_id = %s",
                (session_id,),
            )
            conn.commit()

    # ------------------------------------------------------------------
    # Tournament sync
    # ------------------------------------------------------------------

    def sync_tournament(self, state: Any) -> None:
        """Upsert tournament state to PostgreSQL."""
        with self._conn() as conn:
            if conn is None:
                return
            import attrs

            conn.execute(
                """
                INSERT INTO tournaments
                    (id, tournament_type, status, host, name, game_type,
                     min_participants, max_participants, match_size, seed,
                     participants, winner, standings)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    status = EXCLUDED.status,
                    participants = EXCLUDED.participants,
                    winner = EXCLUDED.winner,
                    standings = EXCLUDED.standings,
                    started_at = CASE WHEN EXCLUDED.status = 'in_progress'
                                      THEN COALESCE(tournaments.started_at, NOW())
                                      ELSE tournaments.started_at END,
                    completed_at = CASE WHEN EXCLUDED.status = 'completed'
                                        THEN NOW()
                                        ELSE tournaments.completed_at END
                """,
                (
                    state.tournament_id,
                    state.tournament_type,
                    state.status,
                    state.host,
                    state.name,
                    state.game_type,
                    getattr(state, "min_participants", 2),
                    getattr(state, "max_participants", 16),
                    getattr(state, "match_size", 2),
                    state.seed,
                    json.dumps(list(state.participants)),
                    state.winner,
                    json.dumps({k: attrs.asdict(v) for k, v in state.standings.items()})
                    if state.standings
                    else None,
                ),
            )
            # Sync matches
            for match in state.matches.values():
                conn.execute(
                    """
                    INSERT INTO tournament_matches
                        (id, tournament_id, participants, round, stage, status,
                         winner, scores, session_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        status = EXCLUDED.status,
                        winner = EXCLUDED.winner,
                        scores = EXCLUDED.scores,
                        session_id = EXCLUDED.session_id,
                        completed_at = CASE WHEN EXCLUDED.status = 'completed'
                                            THEN NOW()
                                            ELSE tournament_matches.completed_at END
                    """,
                    (
                        match.id,
                        state.tournament_id,
                        json.dumps(list(match.participants)),
                        match.round,
                        match.stage,
                        match.status,
                        match.winner,
                        json.dumps(dict(match.scores)) if match.scores else None,
                        match.session_id,
                    ),
                )
            conn.commit()
            log.debug("PG: synced tournament %s", state.tournament_id)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

pg_sync = PgSync()
