"""Tests for analytics engine."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from server.analytics import (
    _extract_winner,
    game_type_stats,
    head_to_head,
    invalidate_cache,
    platform_stats,
    player_stats,
)


def _make_archive(
    game_id: str = "duel",
    players: list[str] | None = None,
    decisions: list[dict] | None = None,
    metadata: dict | None = None,
) -> dict:
    return {
        "version": 1,
        "game_id": game_id,
        "rules_hash": "test",
        "seed": 42,
        "players": players or ["alice", "bob"],
        "decisions": decisions or [],
        "timestamp": 1000000,
        "metadata": metadata or {},
    }


def _patch_archives(archives: list[dict]):
    """Patch load_all_archives and invalidate cache so it re-reads."""
    invalidate_cache()
    return mock.patch("server.analytics.load_all_archives", return_value=archives)


class TestPlatformStats:
    def test_empty(self):
        with _patch_archives([]):
            s = platform_stats()
        assert s["total_games"] == 0
        assert s["unique_players"] == 0
        assert s["avg_decisions_per_game"] == 0

    def test_basic(self):
        archives = [
            _make_archive(
                "duel", ["alice", "bob"], [{"type": "deal"}, {"type": "deal"}]
            ),
            _make_archive("mafia", ["alice", "carol", "dave"], [{"type": "vote"}]),
        ]
        with _patch_archives(archives):
            s = platform_stats()
        assert s["total_games"] == 2
        assert s["unique_players"] == 4
        assert s["total_decisions"] == 3
        assert s["avg_decisions_per_game"] == 1.5
        assert s["games_by_type"]["duel"] == 1
        assert s["games_by_type"]["mafia"] == 1


class TestPlayerStats:
    def test_empty(self):
        with _patch_archives([]):
            s = player_stats()
        assert s == []

    def test_basic_stats(self):
        archives = [
            _make_archive(
                "duel",
                ["alice", "bob"],
                [
                    {"type": "deal", "proposer": "alice"},
                    {"type": "deal", "proposer": "bob"},
                    {"type": "deal", "proposer": "alice"},
                ],
                metadata={"winner": "alice"},
            ),
        ]
        with _patch_archives(archives):
            s = player_stats()

        alice = next(p for p in s if p["player_id"] == "alice")
        bob = next(p for p in s if p["player_id"] == "bob")

        assert alice["games"] == 1
        assert alice["wins"] == 1
        assert alice["losses"] == 0
        assert alice["decisions"] == 2
        assert alice["win_rate"] == 100.0

        assert bob["games"] == 1
        assert bob["wins"] == 0
        assert bob["losses"] == 1
        assert bob["decisions"] == 1

    def test_filter_by_player(self):
        archives = [
            _make_archive("duel", ["alice", "bob"], metadata={"winner": "alice"}),
        ]
        with _patch_archives(archives):
            s = player_stats("bob")
        assert len(s) == 1
        assert s[0]["player_id"] == "bob"


class TestGameTypeStats:
    def test_basic(self):
        archives = [
            _make_archive(
                "duel", decisions=[{"type": "deal"}, {"type": "advance_phase"}]
            ),
            _make_archive(
                "duel",
                decisions=[
                    {"type": "deal"},
                    {"type": "deal"},
                    {"type": "advance_phase"},
                ],
            ),
            _make_archive("mafia", decisions=[{"type": "vote"}]),
        ]
        with _patch_archives(archives):
            s = game_type_stats()

        duel = next(g for g in s if g["game_id"] == "duel")
        assert duel["games_played"] == 2
        assert duel["avg_decisions"] == 2.5
        assert duel["avg_rounds"] == 1.0

        mafia = next(g for g in s if g["game_id"] == "mafia")
        assert mafia["games_played"] == 1


class TestHeadToHead:
    def test_no_shared_games(self):
        archives = [
            _make_archive("duel", ["alice", "bob"]),
            _make_archive("duel", ["carol", "dave"]),
        ]
        with _patch_archives(archives):
            h = head_to_head("alice", "carol")
        assert h["total_games"] == 0

    def test_shared_games(self):
        archives = [
            _make_archive("duel", ["alice", "bob"], metadata={"winner": "alice"}),
            _make_archive("duel", ["alice", "bob"], metadata={"winner": "bob"}),
            _make_archive("duel", ["alice", "bob"], metadata={"winner": "alice"}),
        ]
        with _patch_archives(archives):
            h = head_to_head("alice", "bob")
        assert h["total_games"] == 3
        assert h["a_wins"] == 2
        assert h["b_wins"] == 1
        assert h["draws"] == 0


class TestExtractWinner:
    def test_from_metadata(self):
        assert _extract_winner({"metadata": {"winner": "alice"}}) == "alice"

    def test_from_victory_decision(self):
        a = {
            "metadata": {},
            "decisions": [
                {"type": "deal"},
                {"type": "victory", "winner": "bob"},
            ],
        }
        assert _extract_winner(a) == "bob"

    def test_no_winner(self):
        assert _extract_winner({"metadata": {}, "decisions": []}) is None


class TestTournamentContext:
    def test_player_stats_tournament_games(self):
        """Archives with tournament_id metadata are counted as tournament games."""
        archives = [
            _make_archive(
                "duel",
                ["alice", "bob"],
                metadata={"winner": "alice", "tournament_id": "t1"},
            ),
            _make_archive(
                "duel",
                ["alice", "bob"],
                metadata={"winner": "bob"},
            ),
        ]
        with _patch_archives(archives):
            stats = player_stats()
        alice = next(s for s in stats if s["player_id"] == "alice")
        bob = next(s for s in stats if s["player_id"] == "bob")
        assert alice["tournament_games"] == 1
        assert alice["games"] == 2
        assert bob["tournament_games"] == 1
        assert bob["games"] == 2

    def test_platform_stats_tournament_games(self):
        """Platform stats include tournament_games count."""
        archives = [
            _make_archive(metadata={"winner": "alice", "tournament_id": "t1"}),
            _make_archive(metadata={"winner": "bob"}),
            _make_archive(metadata={"winner": "alice", "tournament_id": "t2"}),
        ]
        with _patch_archives(archives):
            s = platform_stats()
        assert s["tournament_games"] == 2
        assert s["total_games"] == 3
