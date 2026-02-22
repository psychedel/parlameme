"""Analytics engine — cached stats from archives with Glicko-2 ratings.

All computation is pure: reads archives, returns dicts.
No UI code here — pages import and render.

Key improvements over naive approach:
- StatsCache with TTL — avoids re-reading all archives on every call
- Streaks and recent form per player
- Glicko-2 rating integration with tier badges
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from engine.rating import Rating, tier
from engine.rating import compute_all as compute_ratings

log = logging.getLogger(__name__)

ARCHIVE_DIR = Path("data/archives")
CACHE_MAX_AGE = 300.0  # 5 minutes


# ---------------------------------------------------------------------------
# Archive loading
# ---------------------------------------------------------------------------


def load_all_archives() -> list[dict[str, Any]]:
    """Load all archive files as raw dicts, sorted by timestamp."""
    archives = []
    if not ARCHIVE_DIR.exists():
        return archives
    for path in sorted(ARCHIVE_DIR.glob("*.json")):
        try:
            archives.append(json.loads(path.read_text()))
        except Exception:
            log.warning("Skipping corrupt archive: %s", path)
    return archives


def _extract_winner(archive: dict[str, Any]) -> str | None:
    """Try to determine the winner from archive metadata or decisions."""
    from engine.archive import extract_winner

    return extract_winner(archive)


# ---------------------------------------------------------------------------
# Cached stats engine
# ---------------------------------------------------------------------------


class StatsCache:
    """Cached statistics with lazy refresh.

    Recomputes when stale (>max_age seconds) or on explicit invalidate().
    Thread-safe for async contexts: reads are lock-free, writes are atomic
    (dict/list assignment in CPython is atomic due to GIL).
    """

    def __init__(self, max_age: float = CACHE_MAX_AGE):
        self._max_age = max_age
        self._computed_at: float = 0.0
        self._archives: list[dict[str, Any]] = []
        self._player_stats: dict[str, dict[str, Any]] = {}
        self._ratings: dict[str, Rating] = {}
        self._game_type_stats: list[dict[str, Any]] = []
        self._platform: dict[str, Any] = {}

    def invalidate(self) -> None:
        """Force recomputation on next access."""
        self._computed_at = 0.0

    def _ensure_fresh(self) -> None:
        if time.time() - self._computed_at > self._max_age:
            self._recompute()
            # Double-check not needed: CPython GIL makes dict assignment
            # atomic, and recompute is idempotent. Worst case: two concurrent
            # calls both recompute (harmless).

    def _recompute(self) -> None:
        archives = load_all_archives()
        self._archives = archives
        self._player_stats = _compute_player_stats(archives)
        self._ratings = compute_ratings(archives)
        self._game_type_stats = _compute_game_type_stats(archives)
        self._platform = _compute_platform_stats(archives)
        self._computed_at = time.time()
        # Sync player stats to PostgreSQL (fail-open)
        from engine.pg import pg_sync

        if pg_sync.enabled:
            enriched = {}
            for pid, s in self._player_stats.items():
                r = self._ratings.get(pid, Rating())
                enriched[pid] = {**s, "tier": tier(r.mu)}
            pg_sync.bulk_update_player_stats(enriched, self._ratings)

    # -- Public API (all auto-refresh) --

    def platform_stats(self) -> dict[str, Any]:
        self._ensure_fresh()
        return dict(self._platform)

    def player_stats(self, player_id: str | None = None) -> list[dict[str, Any]]:
        """Player stats enriched with ratings, streaks, recent form."""
        self._ensure_fresh()
        result = []
        for pid, s in sorted(self._player_stats.items()):
            if player_id and pid != player_id:
                continue
            rating = self._ratings.get(pid, Rating())
            result.append(
                {
                    **s,
                    "rating": round(rating.mu, 1),
                    "rd": round(rating.rd, 1),
                    "tier": tier(rating.mu),
                }
            )
        # Sort by rating (primary), then win rate
        result.sort(key=lambda x: (-x["rating"], -x["win_rate"]))
        return result

    def game_type_stats(self) -> list[dict[str, Any]]:
        self._ensure_fresh()
        return list(self._game_type_stats)

    def head_to_head(self, player_a: str, player_b: str) -> dict[str, Any]:
        self._ensure_fresh()
        shared_games = []
        for a in self._archives:
            players = a.get("players", [])
            if player_a in players and player_b in players:
                winner = _extract_winner(a)
                shared_games.append(
                    {
                        "game_id": a.get("game_id", ""),
                        "session_id": a.get("metadata", {}).get("session_id", ""),
                        "winner": winner,
                        "decisions": len(a.get("decisions", [])),
                    }
                )

        a_wins = sum(1 for g in shared_games if g["winner"] == player_a)
        b_wins = sum(1 for g in shared_games if g["winner"] == player_b)

        rating_a = self._ratings.get(player_a, Rating())
        rating_b = self._ratings.get(player_b, Rating())

        return {
            "player_a": player_a,
            "player_b": player_b,
            "total_games": len(shared_games),
            "a_wins": a_wins,
            "b_wins": b_wins,
            "draws": len(shared_games) - a_wins - b_wins,
            "a_rating": round(rating_a.mu, 1),
            "b_rating": round(rating_b.mu, 1),
            "games": shared_games,
        }


# ---------------------------------------------------------------------------
# Computation functions (pure)
# ---------------------------------------------------------------------------


def _compute_player_stats(archives: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Compute per-player stats including streaks and recent form."""
    raw: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "games": 0,
            "wins": 0,
            "losses": 0,
            "decisions": 0,
            "game_types": Counter(),
            "tournament_games": 0,
            "history": [],  # list of "W"/"L"/"D" for streak/form computation
        }
    )

    for a in archives:
        players = a.get("players", [])
        decisions = a.get("decisions", [])
        game_id = a.get("game_id", "")
        winner = _extract_winner(a)
        is_tournament = bool(a.get("metadata", {}).get("tournament_id"))

        player_decisions: Counter = Counter()
        for d in decisions:
            actor = (
                d.get("proposer") or d.get("actor") or d.get("voter") or d.get("sender")
            )
            if actor:
                player_decisions[actor] += 1

        for p in players:
            s = raw[p]
            s["games"] += 1
            s["game_types"][game_id] += 1
            s["decisions"] += player_decisions.get(p, 0)
            if is_tournament:
                s["tournament_games"] += 1
            if winner:
                if p == winner:
                    s["wins"] += 1
                    s["history"].append("W")
                else:
                    s["losses"] += 1
                    s["history"].append("L")
            else:
                s["history"].append("D")

    result = {}
    for pid, s in raw.items():
        history = s["history"]
        result[pid] = {
            "player_id": pid,
            "games": s["games"],
            "wins": s["wins"],
            "losses": s["losses"],
            "win_rate": round(s["wins"] / s["games"] * 100, 1) if s["games"] else 0,
            "decisions": s["decisions"],
            "avg_decisions": round(s["decisions"] / s["games"], 1) if s["games"] else 0,
            "game_types": dict(s["game_types"].most_common()),
            "tournament_games": s["tournament_games"],
            "streak": _current_streak(history),
            "best_streak": _best_streak(history),
            "recent_form": history[-10:],  # last 10 games
        }
    return result


def _current_streak(history: list[str]) -> dict[str, Any]:
    """Current streak from most recent games."""
    if not history:
        return {"type": None, "count": 0}
    current = history[-1]
    count = 0
    for r in reversed(history):
        if r == current:
            count += 1
        else:
            break
    return {"type": current, "count": count}


def _best_streak(history: list[str]) -> int:
    """Longest win streak."""
    best = 0
    current = 0
    for r in history:
        if r == "W":
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def _compute_game_type_stats(archives: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-game-type statistics."""
    stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "games": 0,
            "total_decisions": 0,
            "total_rounds": 0,
            "players_seen": set(),
            "decision_types": Counter(),
        }
    )

    for a in archives:
        gid = a.get("game_id", "unknown")
        s = stats[gid]
        s["games"] += 1
        decisions = a.get("decisions", [])
        s["total_decisions"] += len(decisions)
        for p in a.get("players", []):
            s["players_seen"].add(p)
        for d in decisions:
            s["decision_types"][d.get("type", "?")] += 1
        s["total_rounds"] += sum(
            1 for d in decisions if d.get("type") == "advance_phase"
        )

    result = []
    for gid, s in sorted(stats.items()):
        n = s["games"]
        result.append(
            {
                "game_id": gid,
                "games_played": n,
                "unique_players": len(s["players_seen"]),
                "avg_decisions": round(s["total_decisions"] / n, 1) if n else 0,
                "avg_rounds": round(s["total_rounds"] / n, 1) if n else 0,
                "decision_breakdown": dict(s["decision_types"].most_common()),
            }
        )
    return result


def _compute_platform_stats(archives: list[dict[str, Any]]) -> dict[str, Any]:
    """High-level platform statistics."""
    total = len(archives)
    total_decisions = sum(len(a.get("decisions", [])) for a in archives)
    unique_players: set[str] = set()
    game_counts: Counter = Counter()
    tournament_count = 0

    for a in archives:
        for p in a.get("players", []):
            unique_players.add(p)
        game_counts[a.get("game_id", "unknown")] += 1
        if a.get("metadata", {}).get("tournament_id"):
            tournament_count += 1

    return {
        "total_games": total,
        "total_decisions": total_decisions,
        "unique_players": len(unique_players),
        "games_by_type": dict(game_counts.most_common()),
        "avg_decisions_per_game": round(total_decisions / total, 1) if total else 0,
        "tournament_games": tournament_count,
    }


# ---------------------------------------------------------------------------
# Module-level cache instance + convenience functions
# ---------------------------------------------------------------------------

_cache = StatsCache()


def platform_stats() -> dict[str, Any]:
    return _cache.platform_stats()


def player_stats(player_id: str | None = None) -> list[dict[str, Any]]:
    return _cache.player_stats(player_id)


def game_type_stats() -> list[dict[str, Any]]:
    return _cache.game_type_stats()


def head_to_head(player_a: str, player_b: str) -> dict[str, Any]:
    return _cache.head_to_head(player_a, player_b)


def invalidate_cache() -> None:
    """Call after archiving a game to force fresh stats."""
    _cache.invalidate()
