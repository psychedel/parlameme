"""Tests for PostgreSQL sync module (engine/pg.py).

Tests the PgSync class in disabled mode and with mocked connections.
No real PostgreSQL needed.
"""

from unittest.mock import MagicMock, patch

from engine.pg import PgSync


class TestPgSyncDisabled:
    """When PG_DSN is not set, everything should be a no-op."""

    def test_not_enabled_by_default(self):
        sync = PgSync()
        assert not sync.enabled

    def test_connect_none_stays_disabled(self):
        sync = PgSync()
        sync.connect(None)
        assert not sync.enabled

    def test_connect_empty_stays_disabled(self):
        sync = PgSync()
        sync.connect("")
        assert not sync.enabled

    def test_sync_archive_noop_when_disabled(self):
        sync = PgSync()
        # Should not raise
        sync.sync_archive({"game_id": "duel", "players": ["a", "b"], "decisions": []})

    def test_sync_ledger_entry_noop_when_disabled(self):
        sync = PgSync()
        entry = MagicMock(
            seq=1,
            type="deposit",
            player="alice",
            amount=100,
            ref="",
            prev_hash="genesis",
            content_hash="abc",
        )
        sync.sync_ledger_entry(entry)

    def test_update_player_stats_noop_when_disabled(self):
        sync = PgSync()
        rating = MagicMock(mu=1500.0, rd=350.0, vol=0.06)
        sync.update_player_stats("alice", {"games": 5, "wins": 3}, rating)

    def test_bulk_update_noop_when_disabled(self):
        sync = PgSync()
        sync.bulk_update_player_stats({}, {})

    def test_upsert_session_noop_when_disabled(self):
        sync = PgSync()
        sync.upsert_session("s1", {"game_type": "duel"})

    def test_remove_session_noop_when_disabled(self):
        sync = PgSync()
        sync.remove_session("s1")


class TestPgSyncConnectionFailure:
    """When psycopg is available but PG is down, fail-open."""

    def test_connect_failure_stays_disabled(self):
        sync = PgSync()
        with patch("engine.pg.psycopg") as mock_psycopg:
            mock_psycopg.connect.side_effect = Exception("Connection refused")
            sync.connect("postgresql://bad:bad@localhost/bad")
        assert not sync.enabled

    def test_sync_archive_handles_connection_error(self):
        """If connected but PG goes down mid-operation, log and continue."""
        sync = PgSync()
        sync._dsn = "postgresql://test@localhost/test"
        with patch("engine.pg.psycopg") as mock_psycopg:
            mock_psycopg.connect.side_effect = Exception("PG went away")
            # Should NOT raise
            sync.sync_archive(
                {
                    "game_id": "duel",
                    "metadata": {"session_id": "test"},
                    "players": [],
                    "decisions": [],
                }
            )


class TestPgSyncWithMock:
    """Verify SQL calls with mocked connection."""

    def _make_sync(self):
        sync = PgSync()
        sync._dsn = "postgresql://test@localhost/test"
        return sync

    def test_sync_archive_calls_execute(self):
        sync = self._make_sync()
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)

        with patch("engine.pg.psycopg") as mock_psycopg:
            mock_psycopg.connect.return_value = mock_conn
            sync.sync_archive(
                {
                    "game_id": "duel",
                    "rules_hash": "abc123",
                    "seed": 42,
                    "players": ["alice", "bob"],
                    "decisions": [
                        {"type": "advance_phase"},
                        {"type": "deal", "deal": "attack"},
                    ],
                    "metadata": {
                        "session_id": "test-1",
                        "winner": "alice",
                        "condition": "health",
                    },
                }
            )
        mock_conn.execute.assert_called_once()
        mock_conn.commit.assert_called_once()

        # Verify the SQL contains INSERT INTO game_archives
        sql = mock_conn.execute.call_args[0][0]
        assert "INSERT INTO game_archives" in sql
        assert "ON CONFLICT" in sql

        # Verify params
        params = mock_conn.execute.call_args[0][1]
        assert params[0] == "test-1"  # session_id
        assert params[1] == "duel"  # game_type
        assert params[5] == 2  # player_count
        assert params[6] == "alice"  # winner
        assert params[9] == 2  # decision_count
        assert params[10] == 1  # round_count (1 advance_phase)

    def test_sync_ledger_entry_calls_execute(self):
        sync = self._make_sync()
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)

        entry = MagicMock(
            seq=1,
            type="deposit",
            player="alice",
            amount=100,
            ref="test",
            prev_hash="genesis",
            content_hash="abc123",
        )

        with patch("engine.pg.psycopg") as mock_psycopg:
            mock_psycopg.connect.return_value = mock_conn
            sync.sync_ledger_entry(entry)

        sql = mock_conn.execute.call_args[0][0]
        assert "INSERT INTO ledger_entries" in sql
        params = mock_conn.execute.call_args[0][1]
        assert params == (1, "deposit", "alice", 100, "test", "genesis", "abc123")

    def test_update_player_stats_upsert(self):
        sync = self._make_sync()
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)

        from engine.rating import Rating

        rating = Rating(mu=1650.0, rd=200.0, vol=0.05)
        stats = {
            "games": 10,
            "wins": 7,
            "losses": 3,
            "win_rate": 70.0,
            "best_streak": 4,
            "tier": "Advanced",
        }

        with patch("engine.pg.psycopg") as mock_psycopg:
            mock_psycopg.connect.return_value = mock_conn
            sync.update_player_stats("alice", stats, rating)

        sql = mock_conn.execute.call_args[0][0]
        assert "INSERT INTO player_profiles" in sql
        assert "ON CONFLICT (display_name) DO UPDATE" in sql
        params = mock_conn.execute.call_args[0][1]
        assert params[0] == "alice"
        assert params[1] == 1650.0  # rating_mu
        assert params[5] == 10  # games_played

    def test_upsert_session(self):
        sync = self._make_sync()
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)

        with patch("engine.pg.psycopg") as mock_psycopg:
            mock_psycopg.connect.return_value = mock_conn
            sync.upsert_session(
                "s1",
                {
                    "game_type": "werewolf",
                    "players": ["a", "b", "c"],
                    "seed": 99,
                    "decisions": [{"type": "advance_phase"}],
                    "current_phase": "night",
                    "current_round": 2,
                    "status": "active",
                },
            )

        sql = mock_conn.execute.call_args[0][0]
        assert "INSERT INTO active_sessions" in sql

    def test_remove_session(self):
        sync = self._make_sync()
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)

        with patch("engine.pg.psycopg") as mock_psycopg:
            mock_psycopg.connect.return_value = mock_conn
            sync.remove_session("s1")

        sql = mock_conn.execute.call_args[0][0]
        assert "DELETE FROM active_sessions" in sql


class TestModuleSingleton:
    """Module-level pg_sync singleton."""

    def test_singleton_exists(self):
        from engine.pg import pg_sync

        assert isinstance(pg_sync, PgSync)
        assert not pg_sync.enabled  # Disabled by default in tests
