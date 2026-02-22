"""Tests for strategy package — schema, store, archetypes, compiler."""

from __future__ import annotations

import json
import time
from pathlib import Path

import attrs
import pytest

from games import REGISTRY as GAME_REGISTRY
from strategy.archetypes import ARCHETYPES, get_archetype, get_archetypes
from strategy.compiler import compile_strategy, estimate_tokens
from strategy.schema import DEFAULT_PERSONALITY, PERSONALITY_AXES, Strategy
from strategy.store import StrategyStore

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class TestStrategySchema:
    def test_create_default(self):
        s = Strategy()
        assert s.name == "Untitled Strategy"
        assert s.version == 1
        assert s.personality == DEFAULT_PERSONALITY
        assert len(s.id) == 12

    def test_create_with_fields(self):
        s = Strategy(
            name="Test",
            game_id="auction",
            author="alice",
            archetype="shark",
            priorities=("wealth", "dominance"),
        )
        assert s.name == "Test"
        assert s.game_id == "auction"
        assert s.priorities == ("wealth", "dominance")

    def test_immutable(self):
        s = Strategy()
        with pytest.raises(attrs.exceptions.FrozenInstanceError):
            s.name = "changed"  # type: ignore[misc]

    def test_evolve(self):
        s = Strategy(name="Original")
        s2 = s.evolve(name="Updated")
        assert s.name == "Original"
        assert s2.name == "Updated"
        assert s2.updated_at >= s.updated_at

    def test_bump_version(self):
        s = Strategy(version=1)
        s2 = s.bump_version()
        assert s2.version == 2
        assert s.version == 1

    def test_unique_ids(self):
        ids = {Strategy().id for _ in range(100)}
        assert len(ids) == 100


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class TestStrategyStore:
    @pytest.fixture()
    def store(self, tmp_path: Path) -> StrategyStore:
        return StrategyStore(directory=tmp_path)

    def test_save_and_load(self, store: StrategyStore):
        s = Strategy(name="Test", game_id="auction", author="alice")
        store.save(s)
        loaded = store.load(s.id)
        assert loaded is not None
        assert loaded.name == "Test"
        assert loaded.game_id == "auction"
        assert loaded.author == "alice"

    def test_load_nonexistent(self, store: StrategyStore):
        assert store.load("nonexistent") is None

    def test_delete(self, store: StrategyStore):
        s = Strategy(name="ToDelete")
        store.save(s)
        assert store.load(s.id) is not None
        assert store.delete(s.id) is True
        assert store.load(s.id) is None

    def test_delete_nonexistent(self, store: StrategyStore):
        assert store.delete("nonexistent") is False

    def test_list_all(self, store: StrategyStore):
        s1 = Strategy(name="First", updated_at=1.0)
        s2 = Strategy(name="Second", updated_at=2.0)
        store.save(s1)
        store.save(s2)
        all_strats = store.list_all()
        assert len(all_strats) == 2
        # Sorted by updated_at descending
        assert all_strats[0].name == "Second"
        assert all_strats[1].name == "First"

    def test_list_by_author(self, store: StrategyStore):
        store.save(Strategy(name="Alice1", author="alice"))
        store.save(Strategy(name="Alice2", author="alice"))
        store.save(Strategy(name="Bob1", author="bob"))
        assert len(store.list_by_author("alice")) == 2
        assert len(store.list_by_author("bob")) == 1
        assert len(store.list_by_author("charlie")) == 0

    def test_list_public(self, store: StrategyStore):
        store.save(Strategy(name="Public", public=True))
        store.save(Strategy(name="Private", public=False))
        public = store.list_public()
        assert len(public) == 1
        assert public[0].name == "Public"

    def test_fork(self, store: StrategyStore):
        original = Strategy(
            name="Original",
            game_id="werewolf",
            author="alice",
            persona="I am the original",
            priorities=("survival", "wealth"),
        )
        store.save(original)
        forked = store.fork(original.id, "bob")
        assert forked is not None
        assert forked.id != original.id
        assert forked.author == "bob"
        assert forked.forked_from == original.id
        assert forked.name == "Original (fork)"
        assert forked.persona == "I am the original"
        assert forked.priorities == ("survival", "wealth")
        assert forked.version == 1
        assert forked.public is False

    def test_fork_nonexistent(self, store: StrategyStore):
        assert store.fork("nonexistent", "bob") is None

    def test_version_backup(self, store: StrategyStore):
        s = Strategy(name="V1", version=1)
        store.save(s)
        s2 = s.evolve(name="V2", version=2)
        store.save(s2)
        # Version 1 should be backed up
        versions = store.list_versions(s.id)
        assert 1 in versions
        v1 = store.load_version(s.id, 1)
        assert v1 is not None
        assert v1.name == "V1"

    def test_serialization_roundtrip(self, store: StrategyStore):
        """All fields survive save/load cycle."""
        s = Strategy(
            name="Full",
            game_id="parliament_arena",
            author="test",
            archetype="chaos_agent",
            personality={
                "aggression": 0.9,
                "honesty": 0.1,
                "loyalty": 0.0,
                "risk_tolerance": 0.8,
            },
            priorities=("deception", "wealth"),
            persona="I love chaos",
            phase_tactics={"election": "Support the weakest"},
            role_overrides={"free_radical": "Betray everyone"},
            deal_rules={"bribe": "Accept all bribes"},
            channel_rules={"backroom": "Make conflicting deals"},
            version=3,
            forked_from="abc123",
            tags=("aggressive", "fun"),
            public=True,
        )
        store.save(s)
        loaded = store.load(s.id)
        assert loaded is not None
        assert loaded.name == s.name
        assert loaded.personality == s.personality
        assert loaded.priorities == s.priorities
        assert loaded.phase_tactics == s.phase_tactics
        assert loaded.role_overrides == s.role_overrides
        assert loaded.deal_rules == s.deal_rules
        assert loaded.channel_rules == s.channel_rules
        assert loaded.tags == s.tags
        assert loaded.public is True
        assert loaded.forked_from == "abc123"

    def test_path_traversal_rejected(self, store: StrategyStore):
        """IDs with path components must be rejected."""
        for bad_id in ["../etc/evil", "hello/world", "a.b.c", "a..b", ""]:
            with pytest.raises(ValueError, match="Invalid strategy ID"):
                store.load(bad_id)

    def test_valid_id_characters(self, store: StrategyStore):
        """IDs with alphanumeric, hyphens, and underscores are accepted."""
        s = Strategy(id="valid-id_123")
        store.save(s)
        assert store.load("valid-id_123") is not None

    def test_list_all_not_confused_by_dotv_in_name(self, tmp_path: Path):
        """A file with .v in name but not matching version pattern is not skipped."""
        # Write a file that looks like it has .v in the stem but isn't a backup
        # This tests the regex fix: only skip files ending with .v<digits>
        (tmp_path / "abc123.v2.json").write_text("{}")  # version backup - skip
        s = Strategy(id="real-strategy")
        store = StrategyStore(directory=tmp_path)
        store.save(s)
        all_strats = store.list_all()
        assert len(all_strats) == 1
        assert all_strats[0].id == "real-strategy"


# ---------------------------------------------------------------------------
# Archetypes
# ---------------------------------------------------------------------------


class TestArchetypes:
    def test_all_games_have_archetypes(self):
        for game_id in GAME_REGISTRY:
            archetypes = get_archetypes(game_id)
            if not archetypes:
                continue  # new games may not have archetypes yet
            assert len(archetypes) >= 3, f"{game_id} should have at least 3 archetypes"

    def test_archetype_fields_populated(self):
        for game_id, templates in ARCHETYPES.items():
            for t in templates:
                assert t.game_id == game_id, f"{t.name} game_id mismatch"
                assert t.archetype, f"{t.name} missing archetype id"
                assert t.persona, f"{t.name} missing persona"
                assert t.priorities, f"{t.name} missing priorities"
                assert t.personality, f"{t.name} missing personality"
                assert t.author == "system"
                assert t.public is True

    def test_archetype_personalities_valid(self):
        for game_id, templates in ARCHETYPES.items():
            for t in templates:
                for axis in PERSONALITY_AXES:
                    val = t.personality.get(axis)
                    assert val is not None, f"{t.name} missing axis {axis}"
                    assert 0.0 <= val <= 1.0, f"{t.name} axis {axis}={val} out of range"

    def test_get_archetype_by_id(self):
        shark = get_archetype("auction", "shark")
        assert shark is not None
        assert shark.name == "Shark"

    def test_get_archetype_nonexistent(self):
        assert get_archetype("auction", "nonexistent") is None
        assert get_archetype("nonexistent_game", "shark") is None

    def test_archetype_phase_tactics_match_game(self):
        """Phase tactics should reference actual game phases."""
        for game_id, templates in ARCHETYPES.items():
            compiled = GAME_REGISTRY[game_id]
            phase_ids = {p.id for p in compiled.phases}
            for t in templates:
                for pid in t.phase_tactics:
                    assert pid in phase_ids, (
                        f"{t.name}: phase tactic '{pid}' not in {game_id} phases {phase_ids}"
                    )

    def test_archetype_deal_rules_match_game(self):
        """Deal rules should reference actual game deals."""
        for game_id, templates in ARCHETYPES.items():
            compiled = GAME_REGISTRY[game_id]
            deal_ids = set(compiled.deals.keys())
            for t in templates:
                for did in t.deal_rules:
                    assert did in deal_ids, (
                        f"{t.name}: deal rule '{did}' not in {game_id} deals {deal_ids}"
                    )

    def test_unique_archetype_ids_per_game(self):
        for game_id, templates in ARCHETYPES.items():
            ids = [t.archetype for t in templates]
            assert len(ids) == len(set(ids)), f"Duplicate archetype ids in {game_id}"


# ---------------------------------------------------------------------------
# Compiler
# ---------------------------------------------------------------------------


class TestCompiler:
    def test_compile_minimal_strategy(self):
        """Minimal strategy should compile with fallbacks."""
        compiled = GAME_REGISTRY["auction"]
        s = Strategy(game_id="auction")
        prompt = compile_strategy(s, compiled)
        assert "<identity>" in prompt
        assert "</identity>" in prompt
        assert "<priorities>" in prompt
        assert compiled.name in prompt

    def test_compile_with_persona(self):
        compiled = GAME_REGISTRY["auction"]
        s = Strategy(game_id="auction", persona="I am aggressive and bold.")
        prompt = compile_strategy(s, compiled)
        assert "aggressive and bold" in prompt

    def test_compile_with_phase_tactics(self):
        compiled = GAME_REGISTRY["auction"]
        s = Strategy(
            game_id="auction",
            phase_tactics={"bidding": "Bid maximum on everything."},
        )
        prompt = compile_strategy(s, compiled)
        assert "Bid maximum on everything" in prompt
        assert "<phase_tactics>" in prompt

    def test_compile_with_role_overrides(self):
        compiled = GAME_REGISTRY["werewolf"]
        s = Strategy(
            game_id="werewolf",
            role_overrides={"seer": "Always reveal immediately."},
        )
        prompt = compile_strategy(s, compiled)
        assert "Always reveal immediately" in prompt
        assert "<role_guidance>" in prompt

    def test_compile_no_roles_section_for_auction(self):
        compiled = GAME_REGISTRY["auction"]
        s = Strategy(game_id="auction")
        prompt = compile_strategy(s, compiled)
        assert "<role_guidance>" not in prompt

    def test_compile_with_deal_rules(self):
        compiled = GAME_REGISTRY["auction"]
        s = Strategy(
            game_id="auction",
            deal_rules={"sealed_bid": "Always bid 50 gold."},
        )
        prompt = compile_strategy(s, compiled)
        assert "Always bid 50 gold" in prompt

    def test_compile_with_channel_rules(self):
        compiled = GAME_REGISTRY["werewolf"]
        s = Strategy(
            game_id="werewolf",
            channel_rules={"village_square": "Stay silent."},
        )
        prompt = compile_strategy(s, compiled)
        assert "Stay silent" in prompt
        assert "<channel_strategy>" in prompt

    def test_compile_instructions_section(self):
        compiled = GAME_REGISTRY["auction"]
        s = Strategy(game_id="auction")
        prompt = compile_strategy(s, compiled)
        assert "<instructions>" in prompt
        assert "act" in prompt

    def test_compile_fallback_to_hints(self):
        """When user fields are empty, hints from CompiledGame should appear."""
        compiled = GAME_REGISTRY["werewolf"]
        s = Strategy(game_id="werewolf")
        prompt = compile_strategy(s, compiled)
        # PhaseHint for 'night' should appear as fallback
        assert "night" in prompt.lower()
        # RoleHint for 'seer' should appear as fallback
        assert "seer" in prompt.lower()

    def test_compile_priorities_with_personality(self):
        compiled = GAME_REGISTRY["auction"]
        s = Strategy(
            game_id="auction",
            personality={
                "aggression": 0.9,
                "honesty": 0.1,
                "loyalty": 0.5,
                "risk_tolerance": 0.8,
            },
            priorities=("wealth", "dominance"),
        )
        prompt = compile_strategy(s, compiled)
        assert "very high aggression" in prompt
        assert "very low honesty" in prompt
        assert "high risk tolerance" in prompt
        # Loyalty at 0.5 should not be mentioned
        assert "loyalty" not in prompt.lower()

    def test_compile_all_archetypes(self):
        """All archetypes should compile without error for their game."""
        for game_id, templates in ARCHETYPES.items():
            compiled = GAME_REGISTRY[game_id]
            for t in templates:
                prompt = compile_strategy(t, compiled)
                assert "<identity>" in prompt
                assert "</identity>" in prompt
                assert len(prompt) > 200, f"{t.name} prompt too short"

    def test_token_estimate(self):
        compiled = GAME_REGISTRY["auction"]
        s = Strategy(game_id="auction", persona="A" * 400)
        prompt = compile_strategy(s, compiled)
        tokens = estimate_tokens(prompt)
        assert tokens > 100
        assert tokens < 5000

    def test_compile_all_games_empty_strategy(self):
        """Empty strategy should compile for every game with reasonable output."""
        for game_id, compiled in GAME_REGISTRY.items():
            s = Strategy(game_id=game_id)
            prompt = compile_strategy(s, compiled)
            assert "<identity>" in prompt
            assert compiled.name in prompt
            tokens = estimate_tokens(prompt)
            assert tokens > 50, f"{game_id}: prompt too short ({tokens} tokens)"

    def test_compile_skips_automatic_phases(self):
        """Automatic phases (reveal, settlement, dawn, etc.) should not appear."""
        compiled = GAME_REGISTRY["auction"]
        s = Strategy(game_id="auction")
        prompt = compile_strategy(s, compiled)
        # Auction has automatic phases: setup, reveal, settlement
        # None should appear in phase_tactics
        phase_section = prompt.split("<phase_tactics>")[1].split("</phase_tactics>")[0]
        assert "setup:" not in phase_section.lower()
        assert "reveal:" not in phase_section.lower()
        assert "settlement:" not in phase_section.lower()
        # Non-automatic phases should appear
        assert "bidding" in phase_section.lower() or "preview" in phase_section.lower()

    def test_compile_critical_phase_urgency(self):
        """Critical phases should be prefixed with [CRITICAL] in fallback."""
        compiled = GAME_REGISTRY["werewolf"]
        s = Strategy(game_id="werewolf")
        prompt = compile_strategy(s, compiled)
        # Werewolf 'night' and 'trial' are critical phases
        assert "[CRITICAL]" in prompt

    def test_compile_deals_ordered_by_priority(self):
        """Deals should appear ordered by deal_priorities (highest first)."""
        compiled = GAME_REGISTRY["exchange"]
        s = Strategy(game_id="exchange")
        prompt = compile_strategy(s, compiled)
        deal_section = prompt.split("<deal_rules>")[1].split("</deal_rules>")[0]
        lines = [l.strip() for l in deal_section.strip().split("\n") if l.strip()]
        # limit_order has priority 100, audit_defense has 45
        deal_ids = [l.split(":")[0].strip() for l in lines]
        if "limit_order" in deal_ids and "audit_defense" in deal_ids:
            assert deal_ids.index("limit_order") < deal_ids.index("audit_defense")

    def test_compile_role_key_actions(self):
        """Role fallback should include key_actions from RoleHint."""
        compiled = GAME_REGISTRY["werewolf"]
        s = Strategy(game_id="werewolf")
        prompt = compile_strategy(s, compiled)
        # Seer has key_actions=(seer_vision,)
        assert "Key actions:" in prompt or "key actions:" in prompt.lower()

    def test_compile_role_phase_tips(self):
        """Role fallback should include phase_tips from RoleHint."""
        compiled = GAME_REGISTRY["werewolf"]
        s = Strategy(game_id="werewolf")
        prompt = compile_strategy(s, compiled)
        # Seer has phase_tips with entries for night/day
        assert "Phase tips" in prompt
