"""Strategy statistics — enriched win/loss tracking from game archives.

Scans archives for strategy_id in metadata and computes per-strategy stats:
- Win/loss/draw with win rate
- Per-opponent matchup breakdown
- Arena vs tournament game tagging
- Resource efficiency (average final resources)
- Phase activity (decisions per game)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from engine.archive import Archive, load_archive, replay_with_result

log = logging.getLogger(__name__)

ARCHIVE_DIR = Path("data/archives")


# ---------------------------------------------------------------------------
# Core stats computation
# ---------------------------------------------------------------------------


def strategy_stats(
    archive_dir: Path = ARCHIVE_DIR,
) -> dict[str, dict[str, Any]]:
    """Compute enriched per-strategy statistics from archives.

    Returns dict keyed by strategy_id with:
        games: int — total games played
        wins: int
        losses: int
        draws: int
        win_rate: float (0.0–1.0)
        game_ids: list[str] — game types played
        matchups: {opponent_sid: {wins, losses, draws}} — per-opponent
        arena_games: int — games tagged as arena runs
        tournament_games: int — games tagged as tournament
        resource_totals: {resource: float} — sum of final resources
        total_decisions: int — sum of all decisions across games
    """
    stats: dict[str, dict[str, Any]] = {}

    if not archive_dir.exists():
        return stats

    for path in archive_dir.glob("*.json"):
        try:
            archive = load_archive(path)
        except Exception:
            continue
        _process_archive(archive, stats)

    _finalize(stats)
    return stats


def strategy_stats_from_archives(
    archives: list[Archive],
) -> dict[str, dict[str, Any]]:
    """Compute stats from in-memory archives (no disk I/O).

    Useful for Arena and Evaluation pipelines that have archives in memory.
    """
    stats: dict[str, dict[str, Any]] = {}
    for archive in archives:
        _process_archive(archive, stats)
    _finalize(stats)
    return stats


def get_strategy_stats(
    strategy_id: str, archive_dir: Path = ARCHIVE_DIR
) -> dict[str, Any]:
    """Get stats for a single strategy."""
    all_stats = strategy_stats(archive_dir)
    return all_stats.get(strategy_id, _empty_stats())


def _empty_stats() -> dict[str, Any]:
    return {
        "games": 0,
        "wins": 0,
        "losses": 0,
        "draws": 0,
        "win_rate": 0.0,
        "game_ids": [],
        "matchups": {},
        "arena_games": 0,
        "tournament_games": 0,
        "resource_totals": {},
        "total_decisions": 0,
    }


# ---------------------------------------------------------------------------
# Archive processing
# ---------------------------------------------------------------------------


def _process_archive(archive: Archive, stats: dict[str, dict[str, Any]]) -> None:
    """Process one archive into the stats accumulator."""
    meta = archive.metadata or {}

    # Extract strategy mapping from metadata
    strategies = meta.get("strategies", {})
    if not strategies:
        # Legacy: single strategy_id + player_id
        sid = meta.get("strategy_id")
        pid = meta.get("agent_player_id")
        if sid and pid:
            strategies = {pid: sid}

    if not strategies:
        return

    # Determine winner
    winner_pid = meta.get("winner")
    if not winner_pid:
        for d in reversed(archive.decisions):
            if d.get("type") == "victory":
                winner_pid = d.get("winner")
                break

    # Source tagging
    is_arena = bool(meta.get("arena"))
    is_tournament = bool(meta.get("tournament_id"))

    # Decision count for the game
    decisions_count = len(archive.decisions)

    # Final resources from metadata (set by arena)
    final_resources_all = meta.get("final_resources", {})

    # Invert: player_id -> strategy_id → strategy_id -> set of opponent sids
    sid_set = set(strategies.values())

    for player_id, strategy_id in strategies.items():
        entry = stats.setdefault(strategy_id, _fresh_accum())
        entry["games"] += 1
        entry["game_ids_set"].add(archive.game_id)
        entry["total_decisions"] += decisions_count

        # Win/loss/draw
        if winner_pid:
            if winner_pid == player_id:
                entry["wins"] += 1
            else:
                entry["losses"] += 1
        else:
            entry["draws"] += 1

        # Source tagging
        if is_arena:
            entry["arena_games"] += 1
        if is_tournament:
            entry["tournament_games"] += 1

        # Per-opponent matchup
        opponent_sids = sid_set - {strategy_id}
        winner_sid = strategies.get(winner_pid) if winner_pid else None
        for opp_sid in opponent_sids:
            matchup = entry["matchups"].setdefault(
                opp_sid, {"wins": 0, "losses": 0, "draws": 0}
            )
            if winner_pid is None:
                matchup["draws"] += 1
            elif winner_sid == strategy_id:
                matchup["wins"] += 1
            else:
                matchup["losses"] += 1

        # Resource efficiency: accumulate final resources for averaging later
        player_res = final_resources_all.get(player_id, {})
        for resource, amount in player_res.items():
            entry["resource_totals"][resource] = (
                entry["resource_totals"].get(resource, 0.0) + amount
            )


def _fresh_accum() -> dict[str, Any]:
    """Create a fresh stats accumulator for one strategy."""
    return {
        "games": 0,
        "wins": 0,
        "losses": 0,
        "draws": 0,
        "game_ids_set": set(),
        "matchups": {},
        "arena_games": 0,
        "tournament_games": 0,
        "resource_totals": {},
        "total_decisions": 0,
    }


def _finalize(stats: dict[str, dict[str, Any]]) -> None:
    """Compute derived fields and clean up accumulators."""
    for entry in stats.values():
        total = entry["games"]
        entry["win_rate"] = entry["wins"] / total if total > 0 else 0.0

        # Convert game_ids_set to sorted list
        entry["game_ids"] = sorted(entry.pop("game_ids_set"))

        # Average resource efficiency
        if total > 0:
            entry["resource_efficiency"] = {
                r: round(v / total, 1)
                for r, v in entry["resource_totals"].items()
            }
        else:
            entry["resource_efficiency"] = {}

        # Average decisions per game
        entry["avg_decisions"] = round(entry["total_decisions"] / total, 1) if total > 0 else 0.0
