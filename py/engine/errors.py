"""Centralized error registry — all error codes in one place.

Benefits over ad-hoc strings:
- IDE-discoverable, autocomplete-friendly
- Consistent formatting via templates
- Enumerable for docs/MCP schema generation
- Type-safe — typos caught at import time, not runtime
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, TypedDict, Union

# ---------------------------------------------------------------------------
# Error codes
# ---------------------------------------------------------------------------


class E(str, Enum):
    """All engine error codes. str mixin for direct JSON serialization."""

    # Deals
    UNKNOWN_DEAL = "unknown_deal"
    DEAL_NOT_FOUND = "deal_not_found"
    DEAL_NOT_ALLOWED = "deal_not_allowed"
    FILTER_FAILED = "filter_failed"
    GUARD_FAILED = "guard_failed"
    NO_OUTCOME = "no_outcome"
    INVALID_RESPONSE = "invalid_response"
    USAGE_LIMIT = "usage_limit"
    INVALID_PARAM = "invalid_param"
    MISSING_PARAM = "missing_param"

    # Votes
    UNKNOWN_VOTE = "unknown_vote"
    VOTE_NOT_FOUND = "vote_not_found"
    NOT_ELIGIBLE = "not_eligible"
    ALREADY_VOTED = "already_voted"
    INVALID_OPTION = "invalid_option"
    NO_VOTERS = "no_voters"

    # Messaging
    UNKNOWN_CHANNEL = "unknown_channel"
    SENDER_INACTIVE = "sender_inactive"
    CHANNEL_NOT_AVAILABLE = "channel_not_available"
    WRITE_DENIED = "write_denied"
    NOT_IN_GROUP = "not_in_group"

    # Sessions / MCP
    SESSION_NOT_FOUND = "session_not_found"
    PLAYER_NOT_FOUND = "player_not_found"
    ALREADY_IN_GAME = "already_in_game"
    GAME_NOT_STARTED = "game_not_started"
    GAME_ENDED = "game_ended"
    IMPERSONATION = "impersonation"
    INVALID_TOKEN = "invalid_token"

    # Speech Acts
    UNKNOWN_SPEECH_ACT = "unknown_speech_act"
    SPEECH_ACT_NOT_ALLOWED = "speech_act_not_allowed"
    SPEECH_ACT_NOT_FOUND = "speech_act_not_found"
    INQUIRE_ALREADY_RESPONDED = "inquire_already_responded"
    CANNOT_ENDORSE_OWN = "cannot_endorse_own"
    ALREADY_ENDORSED = "already_endorsed"
    NOTHING_TO_ENDORSE = "nothing_to_endorse"
    INSUFFICIENT_COST = "insufficient_cost"
    INQUIRE_SILENCE = "inquire_silence"

    # Ledger
    INSUFFICIENT_BALANCE = "insufficient_balance"
    CHAIN_CORRUPTED = "chain_corrupted"

    # Tournaments
    REGISTRATION_CLOSED = "registration_closed"
    TOURNAMENT_FULL = "tournament_full"
    ALREADY_REGISTERED = "already_registered"
    NOT_REGISTERED = "not_registered"
    TOURNAMENT_STARTED = "tournament_started"
    MATCH_NOT_FOUND = "match_not_found"
    MATCH_COMPLETED = "match_completed"
    WINNER_NOT_IN_MATCH = "winner_not_in_match"
    NOT_ENOUGH_PARTICIPANTS = "not_enough_participants"
    TOURNAMENT_NOT_FOUND = "tournament_not_found"
    NOT_HOST = "not_host"
    TOURNAMENT_CANCELLED = "tournament_cancelled"


# ---------------------------------------------------------------------------
# Message templates — {0}, {1} etc. for positional args
# ---------------------------------------------------------------------------

_TEMPLATES: dict[E, str] = {
    E.UNKNOWN_DEAL: "Deal '{}' not found",
    E.DEAL_NOT_FOUND: "No pending deal '{}'",
    E.DEAL_NOT_ALLOWED: "Deal '{}' not allowed in phase '{}'",
    E.FILTER_FAILED: "{} '{}' does not match filter for deal '{}'",
    E.GUARD_FAILED: "Guard for deal '{}' is not met",
    E.NO_OUTCOME: "No outcome for response '{}'",
    E.INVALID_RESPONSE: "'{}' not in {}",
    E.USAGE_LIMIT: "'{}' limit reached {}",
    E.INVALID_PARAM: "Invalid '{}': {}",
    E.MISSING_PARAM: "Missing required parameter '{}'",
    E.UNKNOWN_VOTE: "Vote '{}' not found",
    E.VOTE_NOT_FOUND: "No pending vote '{}'",
    E.NOT_ELIGIBLE: "'{}' is not eligible to vote",
    E.ALREADY_VOTED: "'{}' has already voted",
    E.INVALID_OPTION: "'{}' not in {}",
    E.NO_VOTERS: "No eligible voters",
    E.UNKNOWN_CHANNEL: "Channel '{}' not found",
    E.SENDER_INACTIVE: "'{}' is not active",
    E.CHANNEL_NOT_AVAILABLE: "Channel '{}' not available in phase '{}'",
    E.WRITE_DENIED: "'{}' cannot write to channel '{}'",
    E.NOT_IN_GROUP: "'{}' is not a member of group '{}'",
    E.SESSION_NOT_FOUND: "Session '{}' not found",
    E.PLAYER_NOT_FOUND: "Player '{}' is not in session",
    E.ALREADY_IN_GAME: "'{}' is already in a game",
    E.GAME_NOT_STARTED: "Game has not started yet",
    E.GAME_ENDED: "Game has already ended",
    E.IMPERSONATION: "Player '{}' is already claimed by another agent",
    E.INVALID_TOKEN: "Invalid or expired token",
    E.UNKNOWN_SPEECH_ACT: "Speech act '{}' not found",
    E.SPEECH_ACT_NOT_ALLOWED: "Speech act '{}' not allowed in phase '{}'",
    E.SPEECH_ACT_NOT_FOUND: "No pending speech act '{}'",
    E.INQUIRE_ALREADY_RESPONDED: "Inquire '{}' already responded",
    E.CANNOT_ENDORSE_OWN: "Cannot endorse own speech act",
    E.ALREADY_ENDORSED: "'{}' already endorsed '{}'",
    E.NOTHING_TO_ENDORSE: "Speech act '{}' not found or not endorsable",
    E.INSUFFICIENT_COST: "'{}' cannot afford {} (need {}, have {})",
    E.INQUIRE_SILENCE: "'{}' did not respond to inquire '{}' — silence penalty",
    E.INSUFFICIENT_BALANCE: "'{}' has insufficient balance (need {}, have {})",
    E.CHAIN_CORRUPTED: "Ledger chain integrity check failed at entry {}",
    # Tournaments
    E.REGISTRATION_CLOSED: "Registration is closed",
    E.TOURNAMENT_FULL: "Tournament is full",
    E.ALREADY_REGISTERED: "'{}' is already registered",
    E.NOT_REGISTERED: "'{}' is not registered",
    E.TOURNAMENT_STARTED: "Tournament already started",
    E.MATCH_NOT_FOUND: "Match not found: {}",
    E.MATCH_COMPLETED: "Match already completed: {}",
    E.WINNER_NOT_IN_MATCH: "'{}' is not in match '{}'",
    E.NOT_ENOUGH_PARTICIPANTS: "Need at least {} participants (have {})",
    E.TOURNAMENT_NOT_FOUND: "Tournament not found: {}",
    E.NOT_HOST: "Only the host can perform this action",
    E.TOURNAMENT_CANCELLED: "Tournament is cancelled",
}


# ---------------------------------------------------------------------------
# Result types (re-exported from here as canonical location)
# ---------------------------------------------------------------------------


class ErrorInfo(TypedDict, total=False):
    code: str
    message: str
    detail: str


class Ok(TypedDict, total=False):
    ok: Literal[True]
    state: Any  # GameState — avoid circular import
    outcome: str
    instance_id: str
    victory: dict[str, Any]
    auto_completed: bool
    tally: dict[str, int]
    message_id: str


class Err(TypedDict):
    ok: Literal[False]
    error: ErrorInfo


Result = Union[Ok, Err]


# ---------------------------------------------------------------------------
# Constructors
# ---------------------------------------------------------------------------


def ok(
    state: Any,
    *,
    outcome: str | None = None,
    instance_id: str | None = None,
    victory: dict[str, Any] | None = None,
    auto_completed: bool | None = None,
    tally: dict[str, int] | None = None,
    message_id: str | None = None,
    waiting: list[str] | None = None,
) -> Ok:
    """Create a success result."""
    r: Ok = {"ok": True, "state": state}
    if outcome is not None:
        r["outcome"] = outcome
    if instance_id is not None:
        r["instance_id"] = instance_id
    if victory is not None:
        r["victory"] = victory
    if auto_completed is not None:
        r["auto_completed"] = auto_completed
    if tally is not None:
        r["tally"] = tally
    if message_id is not None:
        r["message_id"] = message_id
    if waiting is not None:
        r["waiting"] = waiting
    return r


def fail(code: E, *args: Any, detail: str = "") -> Err:
    """Create a typed error with formatted message.

    Usage:
        fail(E.UNKNOWN_DEAL, deal_id)
        fail(E.DEAL_NOT_ALLOWED, deal_id, phase_id)
        fail(E.GUARD_FAILED, deal_id, detail="responder's asset >= qty")
    """
    template = _TEMPLATES.get(code)
    if template and args:
        message = template.format(*args)
    elif template:
        message = template
    else:
        message = str(code.value)
    err: ErrorInfo = {"code": code.value, "message": message}
    if detail:
        err["detail"] = detail
    return {"ok": False, "error": err}
