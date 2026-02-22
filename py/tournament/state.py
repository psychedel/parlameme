"""Tournament state — immutable via attrs.frozen."""

from __future__ import annotations

import attrs

from .config import TournamentConfig


@attrs.frozen
class Match:
    """A single match in a tournament."""

    id: str
    participants: tuple[str, ...] = ()
    round: int = 1
    stage: str = "main"  # main | winners | losers | grand_final
    status: str = "pending"  # pending | active | completed
    winner: str | None = None
    scores: dict[str, int] = attrs.Factory(dict)
    session_id: str | None = None


@attrs.frozen
class Standing:
    """Per-participant standings."""

    participant: str
    points: int = 0
    wins: int = 0
    losses: int = 0
    draws: int = 0
    goal_diff: int = 0
    buchholz: float = 0.0  # strength of schedule (swiss)


@attrs.frozen
class TournamentState:
    """Complete tournament state — immutable."""

    tournament_id: str
    tournament_type: str  # round_robin | single_elimination | swiss
    status: str = "registration"  # registration | in_progress | completed | cancelled
    host: str = ""
    name: str = ""

    # Configuration
    game_type: str = ""
    min_participants: int = 2
    max_participants: int = 16
    match_size: int = 2  # players per match (from game's min_players)
    rounds: int | None = None  # for swiss (None = auto)
    config: TournamentConfig = attrs.Factory(TournamentConfig)

    # Participants
    participants: tuple[str, ...] = ()

    # Matches
    matches: dict[str, Match] = attrs.Factory(dict)

    # Standings
    standings: dict[str, Standing] = attrs.Factory(dict)

    # Result
    winner: str | None = None
    seed: int = 42
