"""Archive system — deterministic replay from seed + decisions.

An archive is the minimal representation of a complete game:
- Game type + version hash
- Players
- Seed
- Decisions (the only non-deterministic inputs)

From these, the entire game state can be reconstructed identically.
This is the foundation for:
- Blockchain verification (~400 bytes compressed per game)
- Audit trails
- Game history browsing
- Cross-platform replay (Python ↔ Clojure parity)
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

import attrs

from engine.runtime.core import GameRuntime
from engine.runtime.state import CompiledGame, GameState

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Archive data structure
# ---------------------------------------------------------------------------


@attrs.frozen
class Archive:
    """Minimal game representation for deterministic replay."""

    version: int = 1
    game_id: str = ""
    rules_hash: str = ""  # content hash of CompiledGame
    seed: int = 0
    players: tuple[str, ...] = ()
    decisions: tuple[dict[str, Any], ...] = ()
    timestamp: float = attrs.Factory(time.time)
    metadata: dict[str, Any] = attrs.Factory(dict)
    game_params: dict[str, Any] = attrs.Factory(dict)


# ---------------------------------------------------------------------------
# Create archive from finished game
# ---------------------------------------------------------------------------


def create_archive(
    compiled: CompiledGame,
    state: GameState,
    metadata: dict[str, Any] | None = None,
    game_params: dict[str, Any] | None = None,
) -> Archive:
    """Create an archive from a completed game state."""
    return Archive(
        game_id=compiled.id,
        rules_hash=compiled.source_hash or _hash_game(compiled),
        seed=state.seed,
        players=tuple(state.entities.keys()),
        decisions=state.decisions,
        metadata=metadata or {},
        game_params=game_params or {},
    )


# ---------------------------------------------------------------------------
# Deterministic replay
# ---------------------------------------------------------------------------


@attrs.frozen
class ReplayResult:
    """Result of archive replay with error tracking."""

    state: GameState
    failed: int = 0  # number of decisions that failed to apply


def replay(archive: Archive, compiled: CompiledGame) -> GameState:
    """Replay an archive to reconstruct the final game state.

    Given the same compiled game and archive, this MUST produce
    an identical state every time. This is the core guarantee.
    """
    result = replay_with_result(archive, compiled)
    if result.failed:
        log.warning(
            "Archive replay had %d failed decisions out of %d",
            result.failed,
            len(archive.decisions),
        )
    return result.state


def replay_with_result(archive: Archive, compiled: CompiledGame) -> ReplayResult:
    """Replay an archive and return detailed result including error count."""
    runtime = GameRuntime(compiled)
    state = runtime.start_game(
        list(archive.players), archive.seed,
        params=archive.game_params or None,
    )
    state = runtime.run_setup(state)

    failed = 0
    for decision in archive.decisions:
        before = state
        state = _apply_decision(runtime, state, decision)
        if state is before:
            # _apply_decision returned unchanged state — decision failed
            failed += 1

    return ReplayResult(state=state, failed=failed)


def _apply_decision(
    runtime: GameRuntime, state: GameState, decision: dict[str, Any]
) -> GameState:
    """Apply a single recorded decision to the game state."""
    dtype = decision["type"]

    if dtype == "deal":
        # Decision records "proposer"; start_deal takes "actor_id"
        kwargs: dict[str, Any] = {
            "actor_id": decision.get("proposer") or decision.get("actor", ""),
        }
        if "target" in decision:
            kwargs["target_id"] = decision["target"]
        if "responder" in decision:
            kwargs["responder_id"] = decision["responder"]
        if "responders" in decision:
            kwargs["responder_ids"] = decision["responders"]
        if "params" in decision:
            kwargs["params"] = decision["params"]
        result = runtime.start_deal(state, decision["deal"], **kwargs)
        if result["ok"]:
            state = result["state"]
        else:
            log.warning(
                "Replay: deal %s failed: %s", decision["deal"], result.get("error")
            )
        return state

    if dtype == "respond":
        result = runtime.respond_to_deal(
            state,
            decision["instance_id"],
            decision["responder"],
            decision["response"],
        )
        if result["ok"]:
            state = result["state"]
        else:
            log.warning(
                "Replay: respond %s failed: %s",
                decision["instance_id"],
                result.get("error"),
            )
        return state

    if dtype == "start_vote":
        result = runtime.start_vote(
            state,
            decision["vote_id"],
            proposer_id=decision.get("proposer"),
            subject_id=decision.get("subject"),
            params=decision.get("params"),
        )
        if result["ok"]:
            state = result["state"]
        else:
            log.warning(
                "Replay: start_vote %s failed: %s",
                decision["vote_id"],
                result.get("error"),
            )
        return state

    if dtype == "vote":
        result = runtime.cast_vote(
            state,
            decision["instance_id"],
            decision["voter"],
            decision["option"],
        )
        if result["ok"]:
            state = result["state"]
        else:
            log.warning(
                "Replay: vote %s failed: %s",
                decision["instance_id"],
                result.get("error"),
            )
        return state

    if dtype in ("advance_phase", "timeout_advance"):
        state = state.record_decision({"type": dtype})
        return runtime.advance_phase(state)

    if dtype == "timeout_expire_deal":
        # Deal expired by timeout — return stakes
        result = runtime.respond_to_deal(
            state,
            decision["instance_id"],
            decision.get("responder", ""),
            "reject",
        )
        if result["ok"]:
            state = result["state"]
        return state

    if dtype == "timeout_auto_vote":
        # Auto-cast abstain/default vote on timeout
        result = runtime.cast_vote(
            state,
            decision["instance_id"],
            decision["voter"],
            decision["option"],
        )
        if result["ok"]:
            state = result["state"]
        return state

    if dtype == "message":
        result = runtime.send_message(
            state,
            decision["channel"],
            decision["sender"],
            decision["content"],
        )
        if result["ok"]:
            state = result["state"]
        return state

    if dtype == "speech_act":
        result = runtime.execute_speech_act(
            state,
            decision["speech_act_id"],
            decision["actor"],
            decision.get("target"),
            decision.get("params"),
        )
        if result["ok"]:
            state = result["state"]
        else:
            log.warning(
                "Replay: speech_act %s failed: %s",
                decision["speech_act_id"],
                result.get("error"),
            )
        return state

    if dtype == "endorse":
        result = runtime.endorse_speech_act(
            state,
            decision["target_instance_id"],
            decision["endorser"],
        )
        if result["ok"]:
            state = result["state"]
        else:
            log.warning(
                "Replay: endorse %s failed: %s",
                decision["target_instance_id"],
                result.get("error"),
            )
        return state

    if dtype == "inquire_response":
        result = runtime.respond_to_inquire(
            state,
            decision["instance_id"],
            decision["responder"],
            decision["response"],
        )
        if result["ok"]:
            state = result["state"]
        else:
            log.warning(
                "Replay: inquire_response %s failed: %s",
                decision["instance_id"],
                result.get("error"),
            )
        return state

    # Unknown decision type — skip (forward compatibility)
    return state


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def archive_to_dict(archive: Archive) -> dict[str, Any]:
    """Serialize archive to a plain dict (JSON-ready)."""
    d = {
        "version": archive.version,
        "game_id": archive.game_id,
        "rules_hash": archive.rules_hash,
        "seed": archive.seed,
        "players": list(archive.players),
        "decisions": [dict(d) for d in archive.decisions],
        "timestamp": archive.timestamp,
        "metadata": archive.metadata,
    }
    if archive.game_params:
        d["game_params"] = archive.game_params
    return d


def dict_to_archive(data: dict[str, Any]) -> Archive:
    """Deserialize archive from a plain dict."""
    return Archive(
        version=data.get("version", 1),
        game_id=data.get("game_id", ""),
        rules_hash=data.get("rules_hash", ""),
        seed=data.get("seed", 0),
        players=tuple(data.get("players", ())),
        decisions=tuple(data.get("decisions", ())),
        timestamp=data.get("timestamp", 0),
        metadata=data.get("metadata", {}),
        game_params=data.get("game_params", {}),
    )


def save_archive(archive: Archive, path: str | Path) -> Path:
    """Save archive to JSON file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(archive_to_dict(archive), indent=2))
    return path


def load_archive(path: str | Path) -> Archive:
    """Load archive from JSON file."""
    data = json.loads(Path(path).read_text())
    return dict_to_archive(data)


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def verify(archive: Archive, compiled: CompiledGame) -> dict[str, Any]:
    """Verify an archive by replaying and comparing.

    Returns dict with:
        valid           — True if replay matches (decisions + no failures)
        state           — replayed final GameState
        decisions_expected / decisions_replayed — counts
        failed          — number of decisions that failed during replay
        fingerprint     — short hash of game-relevant final state
    """
    result = replay_with_result(archive, compiled)
    state = result.state
    decisions_match = len(state.decisions) == len(archive.decisions)
    fingerprint = _state_fingerprint(state)
    return {
        "valid": decisions_match and result.failed == 0,
        "state": state,
        "decisions_expected": len(archive.decisions),
        "decisions_replayed": len(state.decisions),
        "failed": result.failed,
        "fingerprint": fingerprint,
    }


def _state_fingerprint(state: GameState) -> str:
    """Quick hash of game-relevant state for verification."""
    data = {
        "phase": state.phase,
        "round": state.round,
        "status": state.status,
        "entities": {
            eid: {
                "active": e.active,
                "resources": dict(e.resources),
                "attrs": dict(e.attrs_),
            }
            for eid, e in sorted(state.entities.items())
        },
        "vars": dict(sorted(state.vars_.items())),
    }
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Winner extraction (canonical location — used by rating.py, analytics.py)
# ---------------------------------------------------------------------------


def extract_winner(archive: dict[str, Any]) -> str | None:
    """Extract winner from archive metadata or decisions."""
    meta = archive.get("metadata", {})
    if "winner" in meta:
        return meta["winner"]
    for d in reversed(archive.get("decisions", [])):
        if d.get("type") == "victory":
            return d.get("winner")
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hash_game(compiled: CompiledGame) -> str:
    """Compute content hash of a compiled game for version tracking."""
    # Hash the game's structural properties
    content = json.dumps(
        {
            "id": compiled.id,
            "resources": sorted(compiled.resources.keys()),
            "deals": sorted(compiled.deals.keys()),
            "votes": sorted(compiled.votes.keys()),
            "phases": [p.id for p in compiled.phases],
            "victories": [v.id for v in compiled.victories],
        },
        sort_keys=True,
    )
    return hashlib.sha256(content.encode()).hexdigest()[:16]
