"""Utility functions for training infrastructure.

Helpers for action space analysis and trajectory inspection.
"""

from __future__ import annotations

from typing import Any

from engine.archive import Archive, create_archive
from engine.runtime.state import CompiledGame, GameState
from training.spaces import ActionSpaceBuilder


def trajectory_to_archive(
    compiled: CompiledGame,
    state: GameState,
    metadata: dict[str, Any] | None = None,
) -> Archive:
    """Convert final game state to archive.

    The state already contains all decisions recorded during play.
    Convenience wrapper around create_archive.
    """
    return create_archive(compiled, state, metadata)


def analyze_action_space(
    compiled: CompiledGame,
    num_bins: int = 10,
    player_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Analyze action space size and composition for a game."""
    if player_ids is None:
        player_ids = [f"p{i}" for i in range(compiled.min_players)]
    builder = ActionSpaceBuilder(compiled, num_bins, player_ids)

    type_counts: dict[str, int] = {}
    deal_counts: dict[str, int] = {}

    for slot in builder._action_table:
        type_counts[slot.type] = type_counts.get(slot.type, 0) + 1
        if slot.type == "deal":
            did = slot.meta["deal_id"]
            deal_counts[did] = deal_counts.get(did, 0) + 1

    return {
        "total_actions": builder.num_actions,
        "by_type": type_counts,
        "by_deal": deal_counts,
        "num_bins": num_bins,
        "num_players": len(player_ids),
    }
