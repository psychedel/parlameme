"""Tournament archive — capture tournament progression and results.

Parallels the game-level Archive: minimal data to reconstruct a tournament.
Also provides a chronicle generator for structured tournament narrative.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import attrs

from .state import TournamentState

log = logging.getLogger(__name__)

TOURNAMENT_ARCHIVE_DIR = Path("data/archives/tournaments")


@attrs.frozen
class TournamentArchive:
    """Minimal tournament representation."""

    version: int = 1
    tournament_id: str = ""
    tournament_type: str = ""
    game_type: str = ""
    host: str = ""
    name: str = ""
    participants: tuple[str, ...] = ()
    matches: tuple[dict[str, Any], ...] = ()
    standings: dict[str, dict[str, Any]] = attrs.Factory(dict)
    winner: str | None = None
    seed: int = 0
    timestamp: float = attrs.Factory(time.time)
    match_archives: tuple[str, ...] = ()  # session_ids of individual match archives
    config: dict[str, Any] = attrs.Factory(dict)  # TournamentConfig snapshot


def create_tournament_archive(state: TournamentState) -> TournamentArchive:
    """Create archive from completed tournament state."""
    matches = tuple(
        {
            "id": m.id,
            "participants": list(m.participants),
            "round": m.round,
            "stage": m.stage,
            "winner": m.winner,
            "scores": m.scores,
            "session_id": m.session_id,
        }
        for m in sorted(state.matches.values(), key=lambda m: (m.round, m.id))
    )
    standings = {
        pid: {
            "points": s.points,
            "wins": s.wins,
            "losses": s.losses,
            "draws": s.draws,
            "goal_diff": s.goal_diff,
        }
        for pid, s in state.standings.items()
    }
    cfg = state.config
    return TournamentArchive(
        tournament_id=state.tournament_id,
        tournament_type=state.tournament_type,
        game_type=state.game_type,
        host=state.host,
        name=state.name,
        participants=state.participants,
        matches=matches,
        standings=standings,
        winner=state.winner,
        seed=state.seed,
        match_archives=tuple(
            m.session_id for m in state.matches.values() if m.session_id
        ),
        config={
            "win_points": cfg.win_points,
            "draw_points": cfg.draw_points,
            "tiebreaker": list(cfg.tiebreaker),
            "match_timeout": cfg.match_timeout,
            "phase_timeout": cfg.phase_timeout,
            "winner_credit": cfg.winner_credit,
            "participation_credit": cfg.participation_credit,
            "draw_credit": cfg.draw_credit,
            "swiss_max_rounds": cfg.swiss_max_rounds,
        },
    )


def generate_tournament_chronicle(state: TournamentState) -> list[dict[str, Any]]:
    """Generate chronicle events for a tournament."""
    events: list[dict[str, Any]] = []

    events.append(
        {
            "event": "header",
            "tournament_id": state.tournament_id,
            "type": state.tournament_type,
            "game_type": state.game_type,
            "host": state.host,
            "name": state.name,
            "participants": list(state.participants),
            "participant_count": len(state.participants),
        }
    )

    for match in sorted(state.matches.values(), key=lambda m: (m.round, m.id)):
        events.append(
            {
                "event": "match",
                "match_id": match.id,
                "round": match.round,
                "stage": match.stage,
                "participants": list(match.participants),
                "winner": match.winner,
                "scores": match.scores,
                "session_id": match.session_id,
            }
        )

    from .runtime import TournamentRuntime

    ranked = TournamentRuntime().get_standings_sorted(state)
    events.append(
        {
            "event": "end",
            "winner": state.winner,
            "standings": [
                {
                    "participant": s.participant,
                    "points": s.points,
                    "wins": s.wins,
                    "losses": s.losses,
                    "draws": s.draws,
                }
                for s in ranked
            ],
        }
    )

    return events


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def archive_to_dict(archive: TournamentArchive) -> dict[str, Any]:
    return {
        "version": archive.version,
        "tournament_id": archive.tournament_id,
        "tournament_type": archive.tournament_type,
        "game_type": archive.game_type,
        "host": archive.host,
        "name": archive.name,
        "participants": list(archive.participants),
        "matches": list(archive.matches),
        "standings": archive.standings,
        "winner": archive.winner,
        "seed": archive.seed,
        "timestamp": archive.timestamp,
        "match_archives": list(archive.match_archives),
        "config": archive.config,
    }


def dict_to_archive(data: dict[str, Any]) -> TournamentArchive:
    return TournamentArchive(
        version=data.get("version", 1),
        tournament_id=data.get("tournament_id", ""),
        tournament_type=data.get("tournament_type", ""),
        game_type=data.get("game_type", ""),
        host=data.get("host", ""),
        name=data.get("name", ""),
        participants=tuple(data.get("participants", ())),
        matches=tuple(data.get("matches", ())),
        standings=data.get("standings", {}),
        winner=data.get("winner"),
        seed=data.get("seed", 0),
        timestamp=data.get("timestamp", 0),
        match_archives=tuple(data.get("match_archives", ())),
        config=data.get("config", {}),
    )


def save_tournament_archive(
    archive: TournamentArchive, path: Path | None = None
) -> Path:
    if path is None:
        path = TOURNAMENT_ARCHIVE_DIR / f"{archive.tournament_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(archive_to_dict(archive), indent=2))
    return path


def load_tournament_archive(path: Path) -> TournamentArchive:
    data = json.loads(path.read_text())
    return dict_to_archive(data)


def save_tournament_chronicle(
    chronicle: list[dict[str, Any]], path: Path | None = None, tournament_id: str = ""
) -> Path:
    if path is None:
        path = TOURNAMENT_ARCHIVE_DIR / f"{tournament_id}-chronicle.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for event in chronicle:
            f.write(json.dumps(event, default=str, ensure_ascii=False))
            f.write("\n")
    return path
