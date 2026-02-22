"""Tests for strategy stats — enriched archive analysis."""

from __future__ import annotations

import pytest

from engine.archive import Archive
from strategy.stats import (
    _empty_stats,
    _finalize,
    _fresh_accum,
    _process_archive,
    strategy_stats,
    strategy_stats_from_archives,
    get_strategy_stats,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _archive(
    *,
    game_id: str = "auction",
    players: tuple[str, ...] = ("p0", "p1", "p2"),
    strategies: dict[str, str] | None = None,
    winner: str | None = "p0",
    arena: bool = False,
    tournament_id: str = "",
    final_resources: dict[str, dict] | None = None,
    decisions: int = 10,
) -> Archive:
    meta: dict = {}
    if strategies:
        meta["strategies"] = strategies
    if winner:
        meta["winner"] = winner
    if arena:
        meta["arena"] = True
    if tournament_id:
        meta["tournament_id"] = tournament_id
    if final_resources:
        meta["final_resources"] = final_resources

    return Archive(
        game_id=game_id,
        players=players,
        decisions=tuple({"type": "deal"} for _ in range(decisions)),
        metadata=meta,
    )


# ---------------------------------------------------------------------------
# Tests: process_archive
# ---------------------------------------------------------------------------


class TestProcessArchive:
    def test_basic_win_loss(self):
        stats: dict = {}
        a = _archive(
            strategies={"p0": "strat-A", "p1": "strat-B"},
            winner="p0",
        )
        _process_archive(a, stats)
        _finalize(stats)

        assert stats["strat-A"]["wins"] == 1
        assert stats["strat-A"]["losses"] == 0
        assert stats["strat-B"]["wins"] == 0
        assert stats["strat-B"]["losses"] == 1

    def test_draw(self):
        stats: dict = {}
        a = _archive(
            strategies={"p0": "strat-A", "p1": "strat-B"},
            winner=None,
        )
        _process_archive(a, stats)
        _finalize(stats)

        assert stats["strat-A"]["draws"] == 1
        assert stats["strat-B"]["draws"] == 1

    def test_matchup_tracking(self):
        stats: dict = {}
        a = _archive(
            strategies={"p0": "strat-A", "p1": "strat-B"},
            winner="p0",
        )
        _process_archive(a, stats)
        _finalize(stats)

        assert stats["strat-A"]["matchups"]["strat-B"]["wins"] == 1
        assert stats["strat-B"]["matchups"]["strat-A"]["losses"] == 1

    def test_arena_tagging(self):
        stats: dict = {}
        a = _archive(
            strategies={"p0": "strat-A"},
            arena=True,
        )
        _process_archive(a, stats)
        _finalize(stats)

        assert stats["strat-A"]["arena_games"] == 1
        assert stats["strat-A"]["tournament_games"] == 0

    def test_tournament_tagging(self):
        stats: dict = {}
        a = _archive(
            strategies={"p0": "strat-A"},
            tournament_id="tourney-1",
        )
        _process_archive(a, stats)
        _finalize(stats)

        assert stats["strat-A"]["tournament_games"] == 1
        assert stats["strat-A"]["arena_games"] == 0

    def test_resource_efficiency(self):
        stats: dict = {}
        a = _archive(
            strategies={"p0": "strat-A"},
            final_resources={"p0": {"credits": 100, "reputation": 50}},
        )
        _process_archive(a, stats)
        _finalize(stats)

        assert stats["strat-A"]["resource_efficiency"]["credits"] == 100.0
        assert stats["strat-A"]["resource_efficiency"]["reputation"] == 50.0

    def test_resource_efficiency_averaged(self):
        stats: dict = {}
        a1 = _archive(
            strategies={"p0": "strat-A"},
            final_resources={"p0": {"credits": 100}},
            winner="p0",
        )
        a2 = _archive(
            strategies={"p0": "strat-A"},
            final_resources={"p0": {"credits": 200}},
            winner="p0",
        )
        _process_archive(a1, stats)
        _process_archive(a2, stats)
        _finalize(stats)

        assert stats["strat-A"]["resource_efficiency"]["credits"] == 150.0

    def test_decision_tracking(self):
        stats: dict = {}
        a = _archive(
            strategies={"p0": "strat-A"},
            decisions=25,
        )
        _process_archive(a, stats)
        _finalize(stats)

        assert stats["strat-A"]["total_decisions"] == 25
        assert stats["strat-A"]["avg_decisions"] == 25.0

    def test_game_ids_tracked(self):
        stats: dict = {}
        a1 = _archive(
            game_id="auction",
            strategies={"p0": "strat-A"},
        )
        a2 = _archive(
            game_id="werewolf",
            strategies={"p0": "strat-A"},
        )
        _process_archive(a1, stats)
        _process_archive(a2, stats)
        _finalize(stats)

        assert sorted(stats["strat-A"]["game_ids"]) == ["auction", "werewolf"]

    def test_ignores_no_strategy_archives(self):
        stats: dict = {}
        a = Archive(game_id="auction", players=("p0",), metadata={})
        _process_archive(a, stats)
        assert len(stats) == 0

    def test_legacy_single_strategy(self):
        stats: dict = {}
        a = Archive(
            game_id="auction",
            players=("p0", "p1"),
            metadata={"strategy_id": "strat-A", "agent_player_id": "p0", "winner": "p0"},
        )
        _process_archive(a, stats)
        _finalize(stats)

        assert stats["strat-A"]["wins"] == 1


# ---------------------------------------------------------------------------
# Tests: from_archives (in-memory)
# ---------------------------------------------------------------------------


class TestStrategyStatsFromArchives:
    def test_multiple_archives(self):
        archives = [
            _archive(strategies={"p0": "s1", "p1": "s2"}, winner="p0"),
            _archive(strategies={"p0": "s1", "p1": "s2"}, winner="p1"),
            _archive(strategies={"p0": "s1", "p1": "s2"}, winner="p0"),
        ]
        stats = strategy_stats_from_archives(archives)

        assert stats["s1"]["wins"] == 2
        assert stats["s1"]["losses"] == 1
        assert stats["s1"]["win_rate"] == pytest.approx(2 / 3, abs=0.01)
        assert stats["s2"]["wins"] == 1
        assert stats["s2"]["losses"] == 2

    def test_empty_archives(self):
        stats = strategy_stats_from_archives([])
        assert stats == {}

    def test_three_player_matchups(self):
        """With 3 strategies, each should have matchups vs the other 2."""
        archives = [
            _archive(
                strategies={"p0": "s1", "p1": "s2", "p2": "s3"},
                winner="p0",
            ),
        ]
        stats = strategy_stats_from_archives(archives)

        assert "s2" in stats["s1"]["matchups"]
        assert "s3" in stats["s1"]["matchups"]
        assert stats["s1"]["matchups"]["s2"]["wins"] == 1
        assert stats["s1"]["matchups"]["s3"]["wins"] == 1

    def test_mixed_arena_tournament(self):
        archives = [
            _archive(strategies={"p0": "s1"}, arena=True),
            _archive(strategies={"p0": "s1"}, arena=True),
            _archive(strategies={"p0": "s1"}, tournament_id="t1"),
        ]
        stats = strategy_stats_from_archives(archives)

        assert stats["s1"]["arena_games"] == 2
        assert stats["s1"]["tournament_games"] == 1
        assert stats["s1"]["games"] == 3


# ---------------------------------------------------------------------------
# Tests: disk-based (with tmp_path)
# ---------------------------------------------------------------------------


class TestStrategyStatsDisk:
    def test_nonexistent_dir(self, tmp_path):
        stats = strategy_stats(tmp_path / "nonexistent")
        assert stats == {}


# ---------------------------------------------------------------------------
# Tests: helper functions
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_empty_stats(self):
        e = _empty_stats()
        assert e["games"] == 0
        assert e["win_rate"] == 0.0
        assert e["matchups"] == {}
        assert e["resource_totals"] == {}

    def test_fresh_accum(self):
        a = _fresh_accum()
        assert "game_ids_set" in a
        assert isinstance(a["game_ids_set"], set)

    def test_finalize_computes_avg(self):
        stats = {
            "s1": {
                **_fresh_accum(),
                "games": 4,
                "wins": 2,
                "losses": 1,
                "draws": 1,
                "total_decisions": 100,
                "resource_totals": {"gold": 400.0},
            }
        }
        stats["s1"]["game_ids_set"] = {"auction"}
        _finalize(stats)

        assert stats["s1"]["win_rate"] == 0.5
        assert stats["s1"]["avg_decisions"] == 25.0
        assert stats["s1"]["resource_efficiency"]["gold"] == 100.0
        assert stats["s1"]["game_ids"] == ["auction"]
        assert "game_ids_set" not in stats["s1"]
