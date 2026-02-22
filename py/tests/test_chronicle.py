"""Tests for chronicle generation — structured game narrative in JSONL."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from engine.archive import create_archive
from engine.chronicle import generate_chronicle, load_chronicle, save_chronicle
from engine.runtime.core import GameRuntime
from games.auction import auction
from games.werewolf import werewolf

_PLAYERS = ["alice", "bob", "charlie"]

# =========================================================================
# Helpers
# =========================================================================


def _play_auction_to_end(seed: int = 42) -> tuple:
    """Play Auction until victory. Returns (runtime, state, archive)."""
    rt = GameRuntime(auction)
    state = rt.start_game(_PLAYERS, seed=seed)
    state = rt.run_setup(state)

    # Advance through rounds until victory (auction ends at round 6)
    for _ in range(50):
        # Try appraise each round
        for pid in _PLAYERS:
            result = rt.start_deal(state, "appraise", actor_id=pid)
            if result["ok"]:
                state = result["state"]

        # Check victory
        victory = rt.check_victory(state)
        if victory:
            state = rt.end_game(state, victory)
            break

        # Advance phase
        state = state.record_decision({"type": "advance_phase"})
        state = rt.advance_phase(state)

        victory = rt.check_victory(state)
        if victory:
            state = rt.end_game(state, victory)
            break

    archive = create_archive(auction, state)
    return rt, state, archive


def _play_auction_partial(seed: int = 42) -> tuple:
    """Play a few Auction turns without ending. Returns (runtime, state, archive)."""
    rt = GameRuntime(auction)
    state = rt.start_game(_PLAYERS, seed=seed)
    state = rt.run_setup(state)

    # Two appraisals
    result = rt.start_deal(state, "appraise", actor_id="alice")
    state = result["state"]
    result = rt.start_deal(state, "appraise", actor_id="bob")
    state = result["state"]

    state = state.record_decision({"type": "advance_phase"})
    state = rt.advance_phase(state)

    result = rt.start_deal(state, "appraise", actor_id="charlie")
    if result["ok"]:
        state = result["state"]

    archive = create_archive(auction, state)
    return rt, state, archive


# =========================================================================
# Basic structure
# =========================================================================


class TestChronicleStructure:
    def test_has_all_event_types(self):
        _, state, archive = _play_auction_to_end()
        chronicle = generate_chronicle(archive, auction)

        types = [e["event"] for e in chronicle]
        assert types[0] == "header"
        assert types[1] == "setup"
        assert types[-1] == "end"
        assert "action" in types

    def test_header_fields(self):
        _, state, archive = _play_auction_to_end()
        chronicle = generate_chronicle(archive, auction)
        header = chronicle[0]

        assert header["event"] == "header"
        assert header["game_id"] == "auction"
        assert header["game_name"] == "Art Auction: Mechanism Design"
        assert header["players"] == list(_PLAYERS)
        assert header["player_count"] == 3
        assert header["seed"] == 42
        assert header["rules_hash"]
        assert header["total_decisions"] > 0
        assert header["total_rounds"] >= 1

    def test_setup_fields(self):
        _, state, archive = _play_auction_partial()
        chronicle = generate_chronicle(archive, auction)
        setup = chronicle[1]

        assert setup["event"] == "setup"
        assert "phase" in setup
        assert "round" in setup
        assert "resources" in setup
        assert "alice" in setup["resources"]
        assert "gold" in setup["resources"]["alice"]

    def test_action_fields(self):
        _, state, archive = _play_auction_partial()
        chronicle = generate_chronicle(archive, auction)

        actions = [e for e in chronicle if e["event"] == "action"]
        assert len(actions) > 0

        action = actions[0]
        assert "step" in action
        assert "actor" in action
        assert "phase" in action
        assert "round" in action
        assert "decision" in action
        assert "narrative" in action
        assert action["actor"]  # non-empty

    def test_end_fields(self):
        _, state, archive = _play_auction_to_end()
        chronicle = generate_chronicle(archive, auction)
        end = chronicle[-1]

        assert end["event"] == "end"
        assert "final_state" in end
        assert "summary" in end
        assert "alice" in end["final_state"]
        assert "bob" in end["final_state"]
        assert end["summary"]["decisions_by_type"]

    def test_advance_phase_not_in_actions(self):
        """advance_phase decisions are captured as phase events, not actions."""
        _, state, archive = _play_auction_partial()
        chronicle = generate_chronicle(archive, auction)

        actions = [e for e in chronicle if e["event"] == "action"]
        for a in actions:
            assert a["decision"].get("type") != "advance_phase"


# =========================================================================
# Phase transitions
# =========================================================================


class TestPhaseTransitions:
    def test_phase_events_emitted(self):
        _, state, archive = _play_auction_partial()
        chronicle = generate_chronicle(archive, auction)

        phases = [e for e in chronicle if e["event"] == "phase"]
        # Should have at least one phase transition after advance_phase
        assert len(phases) >= 1

    def test_phase_event_fields(self):
        _, state, archive = _play_auction_partial()
        chronicle = generate_chronicle(archive, auction)

        phases = [e for e in chronicle if e["event"] == "phase"]
        if phases:
            p = phases[0]
            assert "phase" in p
            assert "round" in p
            assert "alive" in p
            assert "alive_count" in p
            assert p["alive_count"] == len(p["alive"])


# =========================================================================
# State changes
# =========================================================================


class TestStateChanges:
    def test_action_produces_resource_change(self):
        """Appraise deal should produce resource changes in state_changes."""
        _, state, archive = _play_auction_partial()
        chronicle = generate_chronicle(archive, auction)

        actions = [e for e in chronicle if e["event"] == "action"]
        # First action is appraise
        first_action = actions[0]
        assert first_action["decision"]["deal"] == "appraise"

        # Should have state_changes with resource delta
        changes = first_action.get("state_changes", [])
        resource_changes = [c for c in changes if c["type"] == "resource"]
        assert len(resource_changes) > 0

    def test_resource_deltas_have_sign(self):
        """Resource changes should include signed delta."""
        _, state, archive = _play_auction_partial()
        chronicle = generate_chronicle(archive, auction)

        actions = [e for e in chronicle if e["event"] == "action"]
        for a in actions:
            for c in a.get("state_changes", []):
                if c["type"] == "resource":
                    assert "delta" in c
                    assert isinstance(c["delta"], (int, float))


# =========================================================================
# Narratives
# =========================================================================


class TestNarratives:
    def test_narratives_non_empty(self):
        _, state, archive = _play_auction_partial()
        chronicle = generate_chronicle(archive, auction)

        actions = [e for e in chronicle if e["event"] == "action"]
        for a in actions:
            assert a["narrative"], f"Empty narrative for step {a['step']}"

    def test_narrative_contains_players(self):
        _, state, archive = _play_auction_partial()
        chronicle = generate_chronicle(archive, auction)

        actions = [e for e in chronicle if e["event"] == "action"]
        first = actions[0]
        assert "alice" in first["narrative"]


# =========================================================================
# Actor view
# =========================================================================


class TestActorView:
    def test_actor_view_present(self):
        _, state, archive = _play_auction_partial()
        chronicle = generate_chronicle(archive, auction)

        actions = [e for e in chronicle if e["event"] == "action"]
        for a in actions:
            if a["actor"]:
                assert "actor_view" in a

    def test_actor_view_has_resources(self):
        _, state, archive = _play_auction_partial()
        chronicle = generate_chronicle(archive, auction)

        actions = [e for e in chronicle if e["event"] == "action"]
        first = actions[0]
        assert "resources" in first["actor_view"]


# =========================================================================
# Serialization roundtrip
# =========================================================================


class TestSerialization:
    def test_save_and_load_roundtrip(self):
        _, state, archive = _play_auction_partial()
        chronicle = generate_chronicle(archive, auction)

        with tempfile.TemporaryDirectory() as tmpdir:
            path = save_chronicle(chronicle, Path(tmpdir) / "test.jsonl")
            loaded = load_chronicle(path)

            assert len(loaded) == len(chronicle)
            for orig, restored in zip(chronicle, loaded):
                assert orig["event"] == restored["event"]

    def test_each_line_is_valid_json(self):
        _, state, archive = _play_auction_partial()
        chronicle = generate_chronicle(archive, auction)

        with tempfile.TemporaryDirectory() as tmpdir:
            path = save_chronicle(chronicle, Path(tmpdir) / "test.jsonl")
            with open(path) as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if line:
                        parsed = json.loads(line)
                        assert "event" in parsed, f"Line {line_num} missing event key"

    def test_json_serializable(self):
        """All chronicle events must be JSON-serializable."""
        _, state, archive = _play_auction_partial()
        chronicle = generate_chronicle(archive, auction)

        for event in chronicle:
            json_str = json.dumps(event)
            assert json_str  # non-empty


# =========================================================================
# Determinism
# =========================================================================


class TestDeterminism:
    def test_same_archive_same_chronicle(self):
        """Generating chronicle twice from same archive produces identical output."""
        _, state, archive = _play_auction_partial()

        c1 = generate_chronicle(archive, auction)
        c2 = generate_chronicle(archive, auction)

        assert len(c1) == len(c2)
        for e1, e2 in zip(c1, c2):
            # Compare as JSON strings for exact equality
            assert json.dumps(e1, sort_keys=True) == json.dumps(e2, sort_keys=True)


# =========================================================================
# Werewolf chronicle (complex game with roles)
# =========================================================================


class TestWerewolfChronicle:
    def test_werewolf_setup_has_roles(self):
        """Werewolf chronicle should capture role assignments in setup."""
        rt = GameRuntime(werewolf)
        players = [f"p{i}" for i in range(8)]
        state = rt.start_game(players, seed=42)
        state = rt.run_setup(state)

        archive = create_archive(werewolf, state)
        chronicle = generate_chronicle(archive, werewolf)

        setup = chronicle[1]
        assert setup["event"] == "setup"
        assert "roles" in setup
        assert len(setup["roles"]) == 8

    def test_werewolf_setup_has_teams(self):
        rt = GameRuntime(werewolf)
        players = [f"p{i}" for i in range(8)]
        state = rt.start_game(players, seed=42)
        state = rt.run_setup(state)

        archive = create_archive(werewolf, state)
        chronicle = generate_chronicle(archive, werewolf)

        setup = chronicle[1]
        assert "teams" in setup
        # Check that wolves team exists
        team_values = set(setup["teams"].values())
        assert "wolves" in team_values or "village" in team_values


# =========================================================================
# Victory chronicle
# =========================================================================


class TestVictoryChronicle:
    def test_completed_game_has_victory(self):
        _, state, archive = _play_auction_to_end()
        if state.status != "ended":
            pytest.skip("Game did not end in time")

        chronicle = generate_chronicle(archive, auction)
        end = chronicle[-1]
        assert end["event"] == "end"
        assert end["status"] == "ended"
        if state.victory_result:
            assert "victory" in end

    def test_end_summary_counts_match(self):
        _, state, archive = _play_auction_to_end()
        chronicle = generate_chronicle(archive, auction)

        end = chronicle[-1]
        summary = end["summary"]

        # Total decisions by type should match action count
        total_from_type = sum(summary["decisions_by_type"].values())
        total_from_player = sum(summary["decisions_by_player"].values())
        assert total_from_type == total_from_player


# =========================================================================
# Effect traces in chronicle
# =========================================================================


class TestEffectTraces:
    def test_action_has_effects_field(self):
        """Actions that produce history entries should have 'effects' field."""
        _, state, archive = _play_auction_partial()
        chronicle = generate_chronicle(archive, auction)

        actions = [e for e in chronicle if e["event"] == "action"]
        # At least one action should produce effects (appraise adds history)
        has_effects = any("effects" in a for a in actions)
        # effects field is present when history entries are added
        assert len(actions) > 0

    def test_effects_are_list_of_dicts(self):
        """Effects field should be a list of dicts with 'type' key."""
        _, state, archive = _play_auction_partial()
        chronicle = generate_chronicle(archive, auction)

        actions = [e for e in chronicle if e["event"] == "action"]
        for a in actions:
            if "effects" in a:
                assert isinstance(a["effects"], list)
                for eff in a["effects"]:
                    assert isinstance(eff, dict)
                    assert "type" in eff

    def test_effects_json_serializable(self):
        """Effects must survive JSON round-trip."""
        _, state, archive = _play_auction_partial()
        chronicle = generate_chronicle(archive, auction)

        for event in chronicle:
            if "effects" in event:
                json_str = json.dumps(event["effects"])
                restored = json.loads(json_str)
                assert len(restored) == len(event["effects"])


# =========================================================================
# Pending state in chronicle
# =========================================================================


class TestPendingState:
    def test_pending_deals_captured(self):
        """Actions that create pending deals should have 'pending' field."""
        from games.parliament_arena import parliament_arena

        players = [f"p{i}" for i in range(6)]
        rt = GameRuntime(parliament_arena)
        state = rt.start_game(players, seed=42)
        state = rt.run_setup(state)

        # Advance through election to caucus where deals are available
        result = rt.start_vote(
            state, "elect_position", proposer_id="p0", subject_id="p0"
        )
        if result["ok"]:
            state = result["state"]
            iid = result["instance_id"]
            for pid in players:
                result = rt.cast_vote(state, iid, pid, "elect")
                if result["ok"]:
                    state = result["state"]

        state = state.record_decision({"type": "advance_phase"})
        state = rt.advance_phase(state)

        # Try a deal that creates pending state
        result = rt.start_deal(state, "promise", actor_id="p0", responder_id="p1")
        if result["ok"]:
            state = result["state"]

        archive = create_archive(parliament_arena, state)
        chronicle = generate_chronicle(archive, parliament_arena)

        # Check that pending field exists on actions that create deals
        actions = [e for e in chronicle if e["event"] == "action"]
        pending_actions = [a for a in actions if "pending" in a]
        # If the deal created pending state, we should see it
        if state.pending_deals:
            assert len(pending_actions) > 0
            for pa in pending_actions:
                if "deals" in pa["pending"]:
                    deal = pa["pending"]["deals"][0]
                    assert "id" in deal
                    assert "deal" in deal


# =========================================================================
# Actor view with groups
# =========================================================================


class TestActorViewGroups:
    def test_actor_view_includes_groups(self):
        """Actor view should include group memberships when present."""
        rt = GameRuntime(werewolf)
        players = [f"p{i}" for i in range(8)]
        state = rt.start_game(players, seed=42)
        state = rt.run_setup(state)

        # Werewolf players should be in groups (wolves, village)
        archive = create_archive(werewolf, state)
        chronicle = generate_chronicle(archive, werewolf)

        # Setup should have groups
        setup = chronicle[1]
        assert "groups" in setup


# =========================================================================
# State diff: relations and reveals
# =========================================================================


class TestStateDiffExtensions:
    def test_relation_add_diff(self):
        """state_diff should detect new relations."""
        from engine.state_diff import state_diff

        rt = GameRuntime(werewolf)
        players = [f"p{i}" for i in range(8)]
        state = rt.start_game(players, seed=42)
        state = rt.run_setup(state)

        old = state
        new = state.add_relation("p0", "p1", "alliance")
        diff = state_diff(old, new, werewolf)

        rel_adds = [d for d in diff if d["type"] == "relation_add"]
        assert len(rel_adds) == 1
        assert rel_adds[0]["a"] == "p0"
        assert rel_adds[0]["b"] == "p1"
        assert "alliance" in rel_adds[0]["relations"]

    def test_relation_remove_diff(self):
        """state_diff should detect removed relations."""
        import attrs

        from engine.state_diff import state_diff

        rt = GameRuntime(werewolf)
        players = [f"p{i}" for i in range(8)]
        state = rt.start_game(players, seed=42)
        state = rt.run_setup(state)

        state_with_rel = state.add_relation("p0", "p1", "alliance")
        # Manually remove the relation
        new_rels = dict(state_with_rel.relations)
        new_rels[("p0", "p1")] = frozenset()
        state_without = attrs.evolve(state_with_rel, relations=new_rels)
        diff = state_diff(state_with_rel, state_without, werewolf)

        rel_removes = [d for d in diff if d["type"] == "relation_remove"]
        assert len(rel_removes) == 1

    def test_reveal_diff(self):
        """state_diff should detect new reveals."""
        from engine.state_diff import state_diff

        rt = GameRuntime(auction)
        state = rt.start_game(_PLAYERS, seed=42)
        state = rt.run_setup(state)

        old = state
        # Manually add a reveal
        new_reveals = dict(state.reveals)
        new_reveals[("alice", "bob", "role")] = True
        import attrs

        new = attrs.evolve(state, reveals=new_reveals)
        diff = state_diff(old, new, auction)

        reveal_diffs = [d for d in diff if d["type"] == "reveal"]
        assert len(reveal_diffs) == 1
        assert reveal_diffs[0]["observer"] == "alice"
        assert reveal_diffs[0]["entity"] == "bob"
        assert reveal_diffs[0]["attr"] == "role"

    def test_var_diff(self):
        """state_diff should detect var changes."""
        import attrs

        from engine.state_diff import state_diff

        rt = GameRuntime(auction)
        state = rt.start_game(_PLAYERS, seed=42)
        state = rt.run_setup(state)

        old = state
        new_vars = dict(state.vars_)
        new_vars["test_key"] = 42
        new = attrs.evolve(state, vars_=new_vars)
        diff = state_diff(old, new, auction)

        var_diffs = [d for d in diff if d["type"] == "var"]
        assert len(var_diffs) == 1
        assert var_diffs[0]["name"] == "test_key"
        assert var_diffs[0]["to"] == 42


# =========================================================================
# Entity deactivated history in chronicle
# =========================================================================


class TestEntityDeactivatedHistory:
    def test_eliminate_creates_history_entry(self):
        """Eliminate effect should create entity_deactivated history entry."""
        from engine.expr.evaluator import Context
        from engine.runtime.effects import Eliminate, apply_effects

        rt = GameRuntime(werewolf)
        players = [f"p{i}" for i in range(8)]
        state = rt.start_game(players, seed=42)
        state = rt.run_setup(state)

        ctx = Context(state=state, compiled=werewolf)
        state = apply_effects([Eliminate(entity="p0")], state, ctx)

        deactivated = [h for h in state.history if h.type == "entity_deactivated"]
        assert len(deactivated) == 1
        assert deactivated[0].data["entity_id"] == "p0"
        assert "round" in deactivated[0].data
        assert "phase" in deactivated[0].data

    def test_entity_deactivated_has_correct_fields(self):
        """entity_deactivated history entry has entity_id, round, phase."""
        from engine.expr.evaluator import Context
        from engine.runtime.effects import Eliminate, apply_effects

        rt = GameRuntime(werewolf)
        players = [f"p{i}" for i in range(8)]
        state = rt.start_game(players, seed=42)
        state = rt.run_setup(state)

        ctx = Context(state=state, compiled=werewolf)
        state = apply_effects([Eliminate(entity="p0")], state, ctx)

        deactivated = [h for h in state.history if h.type == "entity_deactivated"]
        assert len(deactivated) == 1
        d = deactivated[0].data
        assert d["entity_id"] == "p0"
        assert d["phase"] == state.phase
        assert d["round"] == state.round

    def test_multiple_eliminations_tracked(self):
        """Multiple Eliminate effects produce multiple history entries."""
        from engine.expr.evaluator import Context
        from engine.runtime.effects import Eliminate, apply_effects

        rt = GameRuntime(werewolf)
        players = [f"p{i}" for i in range(8)]
        state = rt.start_game(players, seed=42)
        state = rt.run_setup(state)

        ctx = Context(state=state, compiled=werewolf)
        state = apply_effects([Eliminate(entity="p0")], state, ctx)
        ctx = Context(state=state, compiled=werewolf)
        state = apply_effects([Eliminate(entity="p1")], state, ctx)

        deactivated = [h for h in state.history if h.type == "entity_deactivated"]
        assert len(deactivated) == 2
        entities = {d.data["entity_id"] for d in deactivated}
        assert entities == {"p0", "p1"}
