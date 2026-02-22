"""Chronicle — structured game narrative in JSONL format.

Transforms an Archive (seed + decisions) into a rich, human-readable
event stream suitable for AI training and analysis.

Format: JSONL (one JSON object per line). Event types:
  header  — game metadata (first line)
  setup   — initial state after setup (roles, resources, groups)
  phase   — emitted on phase/round transitions
  action  — player decision with outcome, state changes, narrative
  end     — victory, final scores, summary (last line)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from engine.archive import Archive, _apply_decision
from engine.runtime.core import GameRuntime
from engine.runtime.state import CompiledGame, GameState, view_for
from engine.state_diff import state_diff

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_chronicle(archive: Archive, compiled: CompiledGame) -> list[dict]:
    """Generate chronicle events from an archive.

    Replays the archive step-by-step, emitting structured events
    that capture what happened, what changed, and what players knew.
    """
    states = _compute_all_states(archive, compiled)

    events: list[dict] = []

    # 1. Header
    events.append(_make_header(archive, compiled, states))

    # 2. Setup (state after run_setup, before first player decision)
    events.append(_make_setup(states[0], compiled))

    # 3. Walk decisions, emitting phase transitions and actions
    prev_phase = states[0].phase
    prev_round = states[0].round

    for i, decision in enumerate(archive.decisions):
        before = states[i]
        after = states[i + 1]

        # Phase/round transition
        if after.phase != prev_phase or after.round != prev_round:
            events.append(_make_phase(after, compiled))
            prev_phase, prev_round = after.phase, after.round

        # Skip advance_phase — already captured as phase event
        if decision.get("type") == "advance_phase":
            continue

        events.append(_make_action(i + 1, decision, before, after, compiled))

    # 4. End
    events.append(_make_end(states[-1], archive, compiled))

    return events


def save_chronicle(chronicle: list[dict], path: str | Path) -> Path:
    """Write chronicle as JSONL file (one JSON object per line)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for event in chronicle:
            f.write(json.dumps(event, default=_json_default, ensure_ascii=False))
            f.write("\n")
    return path


def load_chronicle(path: str | Path) -> list[dict]:
    """Read chronicle from JSONL file."""
    events = []
    with Path(path).open() as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


# ---------------------------------------------------------------------------
# Event builders
# ---------------------------------------------------------------------------


def _make_header(
    archive: Archive, compiled: CompiledGame, states: list[GameState]
) -> dict:
    """First line: game metadata."""
    final = states[-1]
    # Count non-advance_phase decisions
    player_decisions = sum(
        1 for d in archive.decisions if d.get("type") != "advance_phase"
    )
    # Count rounds by looking at final state
    total_rounds = final.round

    return {
        "event": "header",
        "game_id": compiled.id,
        "game_name": compiled.name,
        "players": list(archive.players),
        "player_count": len(archive.players),
        "seed": archive.seed,
        "rules_hash": archive.rules_hash,
        "timestamp": archive.timestamp,
        "total_decisions": player_decisions,
        "total_rounds": total_rounds,
        "status": final.status,
    }


def _make_setup(state: GameState, compiled: CompiledGame) -> dict:
    """Second line: initial state after setup phase."""
    roles = {}
    teams = {}
    resources = {}

    for eid, entity in state.entities.items():
        # Roles and teams from attrs
        role = entity.attrs_.get("role")
        if role:
            roles[eid] = role
        team = entity.attrs_.get("team")
        if team:
            teams[eid] = team

        # Resources
        if entity.resources:
            resources[eid] = dict(entity.resources)

    # Groups
    groups = []
    for gid, group in state.groups.items():
        groups.append(
            {
                "id": gid,
                "type": group.type,
                "members": sorted(group.members),
            }
        )

    result: dict[str, Any] = {
        "event": "setup",
        "phase": state.phase,
        "round": state.round,
    }
    if roles:
        result["roles"] = roles
    if teams:
        result["teams"] = teams
    if resources:
        result["resources"] = resources
    if groups:
        result["groups"] = groups

    return result


def _make_phase(state: GameState, compiled: CompiledGame) -> dict:
    """Emitted when phase or round changes."""
    alive = [eid for eid, e in state.entities.items() if e.active]
    return {
        "event": "phase",
        "phase": state.phase,
        "round": state.round,
        "alive": alive,
        "alive_count": len(alive),
    }


def _make_action(
    step: int,
    decision: dict,
    before: GameState,
    after: GameState,
    compiled: CompiledGame,
) -> dict:
    """Core event: player decision + outcome + state changes."""
    changes = state_diff(before, after, compiled)
    actor = (
        decision.get("proposer")
        or decision.get("voter")
        or decision.get("sender")
        or decision.get("responder")
        or ""
    )

    event: dict[str, Any] = {
        "event": "action",
        "step": step,
        "actor": actor,
        "phase": before.phase,
        "round": before.round,
        "decision": _clean_decision(decision),
    }

    if changes:
        event["state_changes"] = changes

    # All new history entries from this decision
    new_history = after.history[len(before.history) :]

    # Deal outcome from history
    dtype = decision.get("type")
    if dtype == "deal":
        for h in new_history:
            if h.type == "deal_executed" and h.data.get("outcome"):
                event["outcome"] = h.data["outcome"]
                break

    # Vote completion
    if dtype == "vote":
        for h in new_history:
            if h.type == "vote_completed":
                event["vote_result"] = {
                    "outcome": h.data.get("outcome"),
                    "tally": h.data.get("tally"),
                    "subject": h.data.get("subject"),
                }
                break

    # Effect trace: all history entries added by this decision
    if new_history:
        event["effects"] = [{"type": h.type, **h.data} for h in new_history]

    # Pending state after this decision
    pending: dict[str, Any] = {}
    if after.pending_deals:
        pending["deals"] = [
            {
                "id": iid,
                "deal": pd.deal_id,
                "proposer": pd.proposer,
                "awaiting": [r for r, v in pd.responders.items() if v is None],
            }
            for iid, pd in after.pending_deals.items()
        ]
    if after.pending_votes:
        pending["votes"] = [
            {
                "id": iid,
                "vote": pv.vote_id,
                "cast": len(pv.votes),
                "total": len(pv.eligible),
                "options": list(pv.options),
            }
            for iid, pv in after.pending_votes.items()
        ]
    if pending:
        event["pending"] = pending

    # Narrative
    event["narrative"] = _narrate(decision, changes)

    # Actor's view at decision time (what they knew)
    if actor:
        event["actor_view"] = _actor_snapshot(before, actor, compiled)

    return event


def _make_end(state: GameState, archive: Archive, compiled: CompiledGame) -> dict:
    """Last line: victory result and summary."""
    # Final entity states
    final_state = {}
    for eid, entity in state.entities.items():
        entry: dict[str, Any] = {"active": entity.active}
        if entity.resources:
            entry["resources"] = dict(entity.resources)
        # Include role/team if present
        for key in ("role", "team"):
            val = entity.attrs_.get(key)
            if val:
                entry[key] = val
        final_state[eid] = entry

    # Eliminations
    eliminations = []
    for h in state.history:
        if h.type == "entity_deactivated":
            eliminations.append(
                {
                    "entity": h.data.get("entity_id", ""),
                    "round": h.data.get("round", 0),
                    "phase": h.data.get("phase", ""),
                }
            )

    # Count decisions by player
    decisions_by_player: dict[str, int] = {}
    decisions_by_type: dict[str, int] = {}
    for d in archive.decisions:
        dtype = d.get("type", "")
        if dtype == "advance_phase":
            continue
        decisions_by_type[dtype] = decisions_by_type.get(dtype, 0) + 1
        actor = (
            d.get("proposer")
            or d.get("voter")
            or d.get("sender")
            or d.get("responder")
            or ""
        )
        if actor:
            decisions_by_player[actor] = decisions_by_player.get(actor, 0) + 1

    result: dict[str, Any] = {
        "event": "end",
        "status": state.status,
        "final_state": final_state,
        "summary": {
            "rounds": state.round,
            "eliminations": eliminations,
            "decisions_by_player": decisions_by_player,
            "decisions_by_type": decisions_by_type,
        },
    }

    if state.victory_result:
        result["victory"] = state.victory_result

    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _compute_all_states(archive: Archive, compiled: CompiledGame) -> list[GameState]:
    """Replay archive capturing state after each decision.

    After replay, checks for victory on final state (mirrors session behavior
    where advance_phase + check_victory are separate calls).
    """
    runtime = GameRuntime(compiled)
    state = runtime.start_game(list(archive.players), archive.seed)
    state = runtime.run_setup(state)

    states = [state]
    for decision in archive.decisions:
        state = _apply_decision(runtime, state, decision)
        states.append(state)

    # Check victory on final state (session layer does this, replay doesn't)
    if state.status == "active":
        victory = runtime.check_victory(state)
        if victory:
            state = runtime.end_game(state, victory)
            states[-1] = state

    return states


def _narrate(decision: dict, changes: list[dict]) -> str:
    """One-line human-readable description of what happened."""
    dtype = decision.get("type", "")

    if dtype == "deal":
        parts = [decision.get("proposer", "?"), decision.get("deal", "?")]
        if "target" in decision:
            parts.append(f"-> {decision['target']}")
        if "params" in decision:
            params_str = ", ".join(f"{k}={v}" for k, v in decision["params"].items())
            parts.append(f"({params_str})")
        # Key effects
        for c in changes:
            if c["type"] == "resource":
                parts.append(f"[{c['entity']} {c['resource']} {c['delta']:+g}]")
            elif c["type"] == "active" and not c["to"]:
                parts.append(f"[{c['entity']} eliminated]")
        return " ".join(parts)

    if dtype == "vote":
        line = f"{decision.get('voter', '?')} votes {decision.get('option', '?')}"
        # Check for vote completion in changes
        for c in changes:
            if c["type"] == "active" and not c["to"]:
                line += f" [{c['entity']} eliminated]"
        return line

    if dtype == "respond":
        return (
            f"{decision.get('responder', '?')} responds "
            f"{decision.get('response', '?')} to deal {decision.get('instance_id', '?')}"
        )

    if dtype == "message":
        content = decision.get("content", "")
        preview = content[:50] + ("..." if len(content) > 50 else "")
        return f"{decision.get('sender', '?')} -> #{decision.get('channel', '?')}: {preview}"

    if dtype == "speech_act":
        act = decision.get("act_type", "?")
        actor = decision.get("actor", "?")
        parts = [f"{actor} {act}s"]
        if decision.get("target"):
            parts.append(f"-> {decision['target']}")
        if decision.get("params"):
            params_str = ", ".join(f"{k}={v}" for k, v in decision["params"].items())
            parts.append(f"({params_str})")
        return " ".join(parts)

    if dtype == "endorse":
        return f"{decision.get('endorser', '?')} endorses {decision.get('target_instance_id', '?')}"

    if dtype == "inquire_response":
        return (
            f"{decision.get('responder', '?')} responds "
            f"'{decision.get('response', '?')}' to inquire {decision.get('instance_id', '?')}"
        )

    return str(decision)


def _actor_snapshot(
    state: GameState, actor_id: str, compiled: CompiledGame
) -> dict[str, Any]:
    """What the actor can see at decision time."""
    view = view_for(state, actor_id, compiled)
    me = view["entities"].get(actor_id, {})
    snapshot: dict[str, Any] = {}
    if me.get("resources"):
        snapshot["resources"] = me["resources"]
    if me.get("attrs"):
        snapshot["attrs"] = me["attrs"]
    if me.get("groups"):
        snapshot["groups"] = sorted(me["groups"])
    return snapshot


def _clean_decision(decision: dict) -> dict:
    """Return a clean copy of decision for serialization."""
    return {k: v for k, v in decision.items() if v is not None}


def _json_default(obj: Any) -> Any:
    """JSON serializer fallback for non-standard types."""
    if isinstance(obj, frozenset):
        return sorted(obj)
    if isinstance(obj, set):
        return sorted(obj)
    if hasattr(obj, "__dict__"):
        return str(obj)
    return str(obj)
