"""Tournament configuration — all tuneable constants in one frozen object.

All fields have defaults matching the previous hardcoded values, so
existing tournaments persisted without a config field will transparently
use these defaults when deserialized.
"""

from __future__ import annotations

import attrs


@attrs.frozen
class TournamentConfig:
    """Immutable configuration for a tournament instance."""

    # --- Scoring ---
    win_points: int = 3
    draw_points: int = 1
    loss_points: int = 0

    # --- Tiebreaker ---
    # Ordered sequence of Standing field names to sort by (descending).
    tiebreaker: tuple[str, ...] = ("points", "goal_diff", "wins")

    # --- Timeouts (seconds) ---
    match_timeout: int = 1800  # 30 minutes — forces draw on expiry
    phase_timeout: int = 300  # 5 minutes — default when PhaseDef.duration unset

    # --- Ledger credits ---
    winner_credit: int = 100
    participation_credit: int = 10
    draw_credit: int = 30

    # --- Swiss format ---
    # None means auto: max(3, int(sqrt(n) * 2)). Set an int to override.
    swiss_max_rounds: int | None = None
