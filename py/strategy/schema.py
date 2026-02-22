"""Strategy document schema — structured AI agent configuration.

A Strategy is a frozen, serializable document that describes how an AI agent
should play a specific game.  It maps cleanly onto the game engine's
ContextConfig / PhaseHint / RoleHint / ChannelHint system and compiles into
an XML-sectioned system prompt.

Three levels of complexity:
  Level 1 — pick an archetype (pre-built template, works out of the box)
  Level 2 — tune sliders + reorder priorities
  Level 3 — write per-phase/per-role/per-deal tactics in text
"""

from __future__ import annotations

import time
import uuid

import attrs
from attrs import Factory

# ---------------------------------------------------------------------------
# Personality axes (Level 2 sliders)
# ---------------------------------------------------------------------------

PERSONALITY_AXES = ("aggression", "honesty", "loyalty", "risk_tolerance")

DEFAULT_PERSONALITY: dict[str, float] = {
    "aggression": 0.5,
    "honesty": 0.5,
    "loyalty": 0.5,
    "risk_tolerance": 0.5,
}

# Common priority labels available across all games
PRIORITY_OPTIONS = (
    "survival",
    "wealth",
    "reputation",
    "alliances",
    "information",
    "dominance",
    "deception",
)


# ---------------------------------------------------------------------------
# Strategy document
# ---------------------------------------------------------------------------


@attrs.frozen
class Strategy:
    """Immutable strategy document for an AI agent.

    Fields are grouped by complexity level so the UI can progressively
    disclose them.  All text fields default to empty — the prompt compiler
    falls back to game ContextConfig hints when a field is blank.
    """

    id: str = attrs.field(factory=lambda: uuid.uuid4().hex[:12])
    name: str = "Untitled Strategy"
    game_id: str = ""  # "auction" | "werewolf" | "parliament_arena" | "exchange"
    author: str = ""  # user id (browser-local UUID)

    # --- Level 1: archetype -------------------------------------------------
    archetype: str = ""  # template id (e.g. "diplomat", "shark")

    # --- Level 2: sliders + priorities --------------------------------------
    personality: dict[str, float] = Factory(lambda: dict(DEFAULT_PERSONALITY))
    priorities: tuple[str, ...] = ("survival", "wealth", "reputation", "alliances")

    # --- Level 3: structured text -------------------------------------------
    persona: str = ""  # free-text personality / backstory
    phase_tactics: dict[str, str] = Factory(dict)  # phase_id → tactic text
    role_overrides: dict[str, str] = Factory(dict)  # role_id → override text
    deal_rules: dict[str, str] = Factory(dict)  # deal_id → rule text
    channel_rules: dict[str, str] = Factory(dict)  # channel_id → usage text

    # --- Metadata -----------------------------------------------------------
    version: int = 1
    forked_from: str | None = None
    created_at: float = Factory(time.time)
    updated_at: float = Factory(time.time)
    tags: tuple[str, ...] = ()
    public: bool = False

    # --- Helpers ------------------------------------------------------------

    def evolve(self, **changes: object) -> Strategy:
        """Return a new Strategy with updated fields + bumped updated_at."""
        changes.setdefault("updated_at", time.time())
        return attrs.evolve(self, **changes)

    def bump_version(self) -> Strategy:
        """Return a copy with incremented version number."""
        return self.evolve(version=self.version + 1)
