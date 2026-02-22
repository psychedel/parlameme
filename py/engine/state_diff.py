"""Shared state diff — compute readable diffs between two GameState snapshots.

Used by both chronicle.py and replay.py. Single source of truth for
what constitutes a "change" between game states.
"""

from __future__ import annotations

from typing import Any

from engine.runtime.state import CompiledGame, GameState


def state_diff(
    old: GameState, new: GameState, compiled: CompiledGame
) -> list[dict[str, Any]]:
    """Compute readable diffs between two game states."""
    changes: list[dict[str, Any]] = []

    if old.phase != new.phase:
        changes.append({"type": "phase", "from": old.phase, "to": new.phase})
    if old.round != new.round:
        changes.append({"type": "round", "from": old.round, "to": new.round})
    if old.status != new.status:
        changes.append({"type": "status", "from": old.status, "to": new.status})

    for eid in set(old.entities) | set(new.entities):
        old_e = old.entities.get(eid)
        new_e = new.entities.get(eid)
        if old_e is None or new_e is None:
            continue

        if old_e.active != new_e.active:
            changes.append(
                {
                    "type": "active",
                    "entity": eid,
                    "from": old_e.active,
                    "to": new_e.active,
                }
            )

        for rid in set(old_e.resources) | set(new_e.resources):
            old_val = old_e.resources.get(rid, 0)
            new_val = new_e.resources.get(rid, 0)
            if old_val != new_val:
                changes.append(
                    {
                        "type": "resource",
                        "entity": eid,
                        "resource": rid,
                        "from": old_val,
                        "to": new_val,
                        "delta": new_val - old_val,
                    }
                )

        for aid in set(old_e.attrs_) | set(new_e.attrs_):
            old_val = old_e.attrs_.get(aid)
            new_val = new_e.attrs_.get(aid)
            if old_val != new_val:
                changes.append(
                    {
                        "type": "attr",
                        "entity": eid,
                        "attr": aid,
                        "from": old_val,
                        "to": new_val,
                    }
                )

        if old_e.groups != new_e.groups:
            added = new_e.groups - old_e.groups
            removed = old_e.groups - new_e.groups
            if added:
                changes.append(
                    {"type": "group_join", "entity": eid, "groups": sorted(added)}
                )
            if removed:
                changes.append(
                    {"type": "group_leave", "entity": eid, "groups": sorted(removed)}
                )

    # Var changes
    for key in set(old.vars_) | set(new.vars_):
        old_val = old.vars_.get(key)
        new_val = new.vars_.get(key)
        if old_val != new_val:
            changes.append({"type": "var", "name": key, "from": old_val, "to": new_val})

    # Relation changes
    for key in set(old.relations) | set(new.relations):
        old_rels = old.relations.get(key, frozenset())
        new_rels = new.relations.get(key, frozenset())
        if old_rels != new_rels:
            a, b = key
            added = new_rels - old_rels
            removed = old_rels - new_rels
            if added:
                changes.append(
                    {
                        "type": "relation_add",
                        "a": a,
                        "b": b,
                        "relations": sorted(added),
                    }
                )
            if removed:
                changes.append(
                    {
                        "type": "relation_remove",
                        "a": a,
                        "b": b,
                        "relations": sorted(removed),
                    }
                )

    # Reveal changes (only new reveals — reveals are never removed)
    for rkey in set(new.reveals) - set(old.reveals):
        if len(rkey) >= 3:
            observer, eid, attr = rkey[0], rkey[1], rkey[2]
        else:
            continue
        changes.append(
            {"type": "reveal", "observer": observer, "entity": eid, "attr": attr}
        )

    return changes
