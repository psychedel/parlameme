"""Tests for the new engine primitives:
- Visibility system (view_for)
- Outcome guards (_resolve_outcome)
- Commitment system (_fire_commitments)
- Open function registry
- Meta-effects (Cond, Maybe, Repeat)
"""

import pytest

from engine.dsl.builder import Game
from engine.expr import actor, alive, count_where, find_by_role, in_group, target
from engine.expr.evaluator import Context, evaluate
from engine.expr.registry import FunctionRegistry, fn_registry
from engine.runtime.core import GameRuntime
from engine.runtime.effects import (
    Boost,
    Broadcast,
    Cond,
    Damage,
    Each,
    Eliminate,
    Maybe,
    Notify,
    Repeat,
    Reveal,
    SendMessage,
    SetAttr,
    When,
    apply_effects,
)
from engine.runtime.state import (
    AttrDef,
    ChannelDef,
    CompiledGame,
    Entity,
    GameState,
    OutcomeDef,
    PendingDeal,
    ResourceDef,
    Visibility,
    can_write_channel,
    view_for,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _simple_game(**overrides):
    """Build a minimal game for testing."""
    g = Game("test", "Test Game", players=(2, 6))
    g = g.resource("health", initial=100, bounds=(0, None))
    g = g.resource("gold", initial=50)
    g = g.attr("role", visibility="private")
    g = g.attr("team", visibility="private")
    g = g.attr("stance", initial="neutral")
    g = g.deal(
        "attack",
        actor=alive(),
        target=alive(),
        effects=[Damage("target", "health", 20)],
    )
    g = g.phase("action", allows=["attack"])
    g = g.victory(
        "knockout",
        when=count_where(actor.health <= 0) > 0,
        type="distribution",
        score=actor.health,
        priority=1,
    )
    return g


# ===========================================================================
# Visibility Tests
# ===========================================================================


class TestVisibility:
    def test_visibility_enum(self):
        assert Visibility.PUBLIC.value == "public"
        assert Visibility.PRIVATE.value == "private"
        assert Visibility.HIDDEN.value == "hidden"

    def test_resource_def_visibility(self):
        rdef = ResourceDef(id="gold", visibility=Visibility.PRIVATE)
        assert rdef.visibility == Visibility.PRIVATE

    def test_attr_def_visibility(self):
        adef = AttrDef(id="role", visibility=Visibility.HIDDEN)
        assert adef.visibility == Visibility.HIDDEN

    def test_builder_string_to_enum(self):
        """Builder accepts strings and converts to Visibility enum."""
        g = Game("t", players=(2, 2))
        g = g.resource("gold", visibility="private")
        g = g.attr("role", visibility="hidden")
        g = g.deal("noop", actor=alive(), effects=[Boost("actor", "gold", 1)])
        g = g.phase("p", allows=["noop"])
        g = g.victory("v", when=actor.health > 0, type="single")
        compiled = g.build()
        assert compiled.resources["gold"].visibility == Visibility.PRIVATE
        assert compiled.attrs_defs["role"].visibility == Visibility.HIDDEN

    def test_view_for_public_resources(self):
        """Public resources are visible to everyone."""
        g = _simple_game().build()
        rt = GameRuntime(g)
        state = rt.start_game(["alice", "bob"])

        view = view_for(state, "alice", g)
        assert view["entities"]["alice"]["resources"]["health"] == 100
        assert view["entities"]["bob"]["resources"]["health"] == 100

    def test_view_for_private_attr_self(self):
        """Private attrs visible to owner only."""
        g = _simple_game().build()
        rt = GameRuntime(g)
        state = rt.start_game(["alice", "bob"])
        state = state.set_attr("alice", "role", "detective")
        state = state.set_attr("bob", "role", "mafioso")

        # Alice sees her own role
        view_alice = view_for(state, "alice", g)
        assert view_alice["entities"]["alice"]["attrs"]["role"] == "detective"

        # Alice does NOT see Bob's role
        assert "role" not in view_alice["entities"]["bob"]["attrs"]

    def test_view_for_hidden_attr(self):
        """Hidden attrs never visible."""
        g = (
            Game("t", players=(2, 2))
            .resource("health", initial=100, bounds=(0, None))
            .attr("marked", visibility="hidden", initial=False)
            .deal("noop", actor=alive(), effects=[Boost("actor", "health", 1)])
            .phase("p", allows=["noop"])
            .victory("v", when=actor.health > 0, type="single")
            .build()
        )
        rt = GameRuntime(g)
        state = rt.start_game(["alice", "bob"])
        state = state.set_attr("alice", "marked", True)

        # Not visible even to self
        view = view_for(state, "alice", g)
        assert "marked" not in view["entities"]["alice"]["attrs"]

    def test_view_for_reveal_overrides(self):
        """Reveals override private visibility."""
        g = _simple_game().build()
        rt = GameRuntime(g)
        state = rt.start_game(["alice", "bob"])
        state = state.set_attr("bob", "role", "mafioso")

        # Before reveal: alice can't see bob's role
        view = view_for(state, "alice", g)
        assert "role" not in view["entities"]["bob"]["attrs"]

        # After reveal
        state = state.add_reveal("alice", "bob", "role", True)
        view = view_for(state, "alice", g)
        assert view["entities"]["bob"]["attrs"]["role"] == "mafioso"

    def test_view_for_reveal_fake_value(self):
        """RevealAs shows fake value."""
        g = _simple_game().build()
        rt = GameRuntime(g)
        state = rt.start_game(["alice", "bob"])
        state = state.set_attr("bob", "role", "mafioso")

        # Reveal with fake value (deception)
        state = state.add_reveal("alice", "bob", "role", "innocent")
        view = view_for(state, "alice", g)
        assert view["entities"]["bob"]["attrs"]["role"] == "innocent"

    def test_view_for_internal_vars_hidden(self):
        """Vars starting with _ are hidden from view."""
        g = _simple_game().build()
        rt = GameRuntime(g)
        state = rt.start_game(["alice", "bob"])
        state = state.set_game_var("round_phase", "day")
        state = state.set_game_var("_internal", "secret")

        view = view_for(state, "alice", g)
        assert "round_phase" in view["vars"]
        assert "_internal" not in view["vars"]


# ===========================================================================
# Outcome Guard Tests
# ===========================================================================


class TestOutcomeGuards:
    def test_resolve_outcome_no_guards(self):
        """Without guards, first outcome is selected."""
        g = (
            Game("t", players=(2, 2))
            .resource("health", initial=100, bounds=(0, None))
            .deal(
                "investigate",
                actor=alive(),
                target=alive(),
                outcomes={
                    "innocent": OutcomeDef(effects=(Broadcast("innocent"),)),
                    "guilty": OutcomeDef(effects=(Broadcast("guilty"),)),
                },
            )
            .phase("p", allows=["investigate"])
            .victory("v", when=actor.health > 0, type="single")
            .build()
        )
        rt = GameRuntime(g)
        state = rt.start_game(["alice", "bob"])

        deal = g.deals["investigate"]
        ctx = Context(
            state=state, compiled=g, bindings={"actor": "alice", "target": "bob"}
        )
        outcome_id, outcome = rt._resolve_outcome(deal, ctx)
        assert outcome_id == "innocent"

    def test_resolve_outcome_with_guards(self):
        """Guards select outcome by priority."""
        g = (
            Game("t", players=(2, 2))
            .resource("health", initial=100, bounds=(0, None))
            .attr("team", visibility="private")
            .deal(
                "investigate",
                actor=alive(),
                target=alive(),
                outcomes={
                    "innocent": OutcomeDef(
                        effects=(Broadcast("innocent"),),
                        guard=target.team == "town",
                        priority=10,
                    ),
                    "guilty": OutcomeDef(
                        effects=(Broadcast("guilty"),),
                        guard=target.team == "mafia",
                        priority=5,
                    ),
                    "unknown": OutcomeDef(effects=(Broadcast("unknown"),)),
                },
            )
            .phase("p", allows=["investigate"])
            .victory("v", when=actor.health > 0, type="single")
            .build()
        )
        rt = GameRuntime(g)
        state = rt.start_game(["alice", "bob"])
        state = state.set_attr("bob", "team", "mafia")

        deal = g.deals["investigate"]
        ctx = Context(
            state=state, compiled=g, bindings={"actor": "alice", "target": "bob"}
        )
        outcome_id, outcome = rt._resolve_outcome(deal, ctx)
        assert outcome_id == "guilty"

    def test_resolve_outcome_fallback_to_default(self):
        """When no guard matches, fall back to unguarded outcome."""
        g = (
            Game("t", players=(2, 2))
            .resource("health", initial=100, bounds=(0, None))
            .attr("team", visibility="private")
            .deal(
                "investigate",
                actor=alive(),
                target=alive(),
                outcomes={
                    "mafia": OutcomeDef(
                        effects=(Broadcast("mafia!"),),
                        guard=target.team == "mafia",
                        priority=10,
                    ),
                    "default": OutcomeDef(effects=(Broadcast("nothing"),)),
                },
            )
            .phase("p", allows=["investigate"])
            .victory("v", when=actor.health > 0, type="single")
            .build()
        )
        rt = GameRuntime(g)
        state = rt.start_game(["alice", "bob"])
        state = state.set_attr("bob", "team", "town")

        deal = g.deals["investigate"]
        ctx = Context(
            state=state, compiled=g, bindings={"actor": "alice", "target": "bob"}
        )
        outcome_id, _ = rt._resolve_outcome(deal, ctx)
        assert outcome_id == "default"


# ===========================================================================
# Commitment Tests
# ===========================================================================


class TestCommitments:
    def test_commitment_fires_on_eliminate(self):
        """Commitment with eliminate trigger fires when entity is eliminated."""
        g = (
            Game("t", players=(2, 4))
            .resource("health", initial=100, bounds=(0, None))
            .attr("marked_for_revenge", initial=False)
            .deal("kill", actor=alive(), target=alive(), effects=[Eliminate("target")])
            .commitment(
                "revenge",
                trigger="eliminate",
                guard=actor.marked_for_revenge == True,
                effects=[Broadcast("Revenge triggered!")],
            )
            .phase("p", allows=["kill"])
            .victory(
                "v",
                when=count_where(actor.health <= 0) > 0,
                type="distribution",
                score=actor.health,
            )
            .build()
        )
        rt = GameRuntime(g)
        state = rt.start_game(["alice", "bob"])
        state = state.set_attr("bob", "marked_for_revenge", True)

        result = rt.start_deal(state, "kill", actor_id="alice", target_id="bob")
        assert result["ok"]
        new_state = result["state"]
        assert not new_state.is_active("bob")
        # Check that revenge broadcast was triggered
        broadcasts = [h for h in new_state.history if h.type == "broadcast"]
        assert any("Revenge" in h.data.get("message", "") for h in broadcasts)

    def test_commitment_once_fires_only_once(self):
        """Once-commitment doesn't fire again."""
        g = (
            Game("t", players=(2, 4))
            .resource("health", initial=100, bounds=(0, None))
            .deal("kill", actor=alive(), target=alive(), effects=[Eliminate("target")])
            .commitment(
                "announce",
                trigger="eliminate",
                effects=[Broadcast("Someone was eliminated!")],
                once=True,
            )
            .phase("p", allows=["kill"])
            .victory(
                "v",
                when=count_where(actor.health <= 0) > 0,
                type="distribution",
                score=actor.health,
            )
            .build()
        )
        rt = GameRuntime(g)
        state = rt.start_game(["alice", "bob", "carol"])

        # First kill
        result = rt.start_deal(state, "kill", actor_id="alice", target_id="bob")
        state = result["state"]
        assert "announce" in state.commitments_fired

        # Second kill
        result = rt.start_deal(state, "kill", actor_id="alice", target_id="carol")
        state = result["state"]
        # Count broadcasts — should be only 1 (once)
        broadcasts = [
            h
            for h in state.history
            if h.type == "broadcast" and "eliminated" in h.data.get("message", "")
        ]
        assert len(broadcasts) == 1

    def test_commitment_depth_guard(self):
        """Mutual elimination doesn't loop infinitely."""
        # alice and bob kill each other via commitments
        g = (
            Game("t", players=(2, 4))
            .resource("health", initial=100, bounds=(0, None))
            .attr("partner")
            .deal("kill", actor=alive(), target=alive(), effects=[Eliminate("target")])
            .commitment(
                "linked_fate", trigger="eliminate", effects=[Eliminate("actor")]
            )  # eliminate the triggering entity
            .phase("p", allows=["kill"])
            .victory(
                "v",
                when=count_where(actor.health <= 0) > 0,
                type="distribution",
                score=actor.health,
            )
            .build()
        )
        rt = GameRuntime(g)
        state = rt.start_game(["alice", "bob", "carol"])

        # Kill bob → commitment eliminates bob again (no-op) but doesn't loop
        result = rt.start_deal(state, "kill", actor_id="alice", target_id="bob")
        assert result["ok"]
        # Should not raise RuntimeError from infinite recursion


# ===========================================================================
# Function Registry Tests
# ===========================================================================


class TestFunctionRegistry:
    def test_custom_function_registration(self):
        """Games can register custom functions."""
        custom = FunctionRegistry()

        @custom.register("double", doc="Double a number", min_args=1, max_args=1)
        def _double(args, ctx):
            val = evaluate(args[0], ctx)
            return val * 2

        assert custom.has("double")
        assert not custom.has("triple")

    def test_registry_list_functions(self):
        """Can list all registered functions."""
        fns = fn_registry.list_functions()
        names = [f.name for f in fns]
        assert "alive?" in names
        assert "count_where" in names
        assert "find_by_role" in names

    def test_registry_arg_validation(self):
        """Registry validates argument counts."""
        custom = FunctionRegistry()

        @custom.register("need_two", min_args=2, max_args=2)
        def _need_two(args, ctx):
            return True

        with pytest.raises(ValueError, match="needs >= 2"):
            custom.call("need_two", (1,), None)

    def test_unknown_function_error(self):
        """Calling unknown function gives clear error."""
        custom = FunctionRegistry()
        with pytest.raises(ValueError, match="Unknown function: foo"):
            custom.call("foo", (), None)


# ===========================================================================
# Meta-Effect Tests
# ===========================================================================


class TestMetaEffects:
    def _make_state_and_ctx(self):
        """Create a simple state + context for testing effects."""
        g = _simple_game().build()
        rt = GameRuntime(g)
        state = rt.start_game(["alice", "bob"])
        ctx = Context(
            state=state, compiled=g, bindings={"actor": "alice", "target": "bob"}
        )
        return state, ctx, g

    def test_cond_first_matching_branch(self):
        """Cond executes first matching branch."""
        state, ctx, g = self._make_state_and_ctx()
        state = state.set_attr("alice", "stance", "aggressive")
        ctx = Context(state=state, compiled=g, bindings={"actor": "alice"})

        effect = Cond(
            branches=(
                (actor.stance == "defensive", (Boost("actor", "health", 50),)),
                (actor.stance == "aggressive", (Damage("actor", "health", 10),)),
                (None, (Boost("actor", "health", 5),)),
            )
        )
        new_state = apply_effects([effect], state, ctx)
        assert new_state.get_resource("alice", "health") == 90  # 100 - 10

    def test_cond_default_branch(self):
        """Cond falls through to default (None guard)."""
        state, ctx, g = self._make_state_and_ctx()
        effect = Cond(
            branches=(
                (actor.stance == "aggressive", (Damage("actor", "health", 10),)),
                (None, (Boost("actor", "health", 5),)),
            )
        )
        new_state = apply_effects([effect], state, ctx)
        assert new_state.get_resource("alice", "health") == 105  # neutral → default

    def test_repeat(self):
        """Repeat executes effects N times."""
        state, ctx, _ = self._make_state_and_ctx()
        effect = Repeat(times=3, effects=(Boost("actor", "health", 10),))
        new_state = apply_effects([effect], state, ctx)
        assert new_state.get_resource("alice", "health") == 130  # 100 + 3*10

    def test_repeat_with_expr(self):
        """Repeat with expression for times."""
        state, ctx, g = self._make_state_and_ctx()
        state = state.set_game_var("count", 2)
        ctx = Context(state=state, compiled=g, bindings={"actor": "alice"})
        from engine.expr.core import Ref

        effect = Repeat(
            times=Ref("game", "count"), effects=(Boost("actor", "health", 5),)
        )
        new_state = apply_effects([effect], state, ctx)
        assert new_state.get_resource("alice", "health") == 110  # 100 + 2*5

    def test_maybe_deterministic(self):
        """Maybe uses deterministic RNG."""
        state, ctx, _ = self._make_state_and_ctx()
        # probability=1.0 always fires
        effect = Maybe(probability=1.0, effects=(Boost("actor", "health", 10),))
        new_state = apply_effects([effect], state, ctx)
        assert new_state.get_resource("alice", "health") == 110

        # probability=0.0 never fires
        effect = Maybe(probability=0.0, effects=(Boost("actor", "health", 10),))
        new_state = apply_effects([effect], state, ctx)
        assert new_state.get_resource("alice", "health") == 100


# ===========================================================================
# Messaging Tests
# ===========================================================================


def _messaging_game(**overrides):
    """Build a game with channels for messaging tests."""
    return (
        Game("msg_test", "Messaging Test", players=(2, 6))
        .resource("health", initial=100, bounds=(0, None))
        .resource("suspicion", initial=0, visibility="public")
        .attr("role", visibility="private")
        .attr("team", visibility="private")
        .group("secret_society", visible=False, knows_members=True)
        # Public channel — everyone can read/write
        .channel("town_square", type="public", description="Public discussion")
        # Group channel — only group members
        .channel(
            "secret_chat",
            type="group",
            group="secret_society",
            description="Secret society members only",
        )
        # Phase-restricted channel
        .channel(
            "night_whispers",
            type="public",
            phases=["night"],
            description="Only available at night",
        )
        # Channel with effects — whispering costs suspicion
        .channel(
            "whisper",
            type="private",
            effects=[Boost("actor", "suspicion", 1)],
            description="Private whisper — adds suspicion",
        )
        # Channel with write filter — only alive can write
        .channel(
            "alive_only",
            type="public",
            write_filter=alive(),
            description="Only living players can speak",
        )
        .deal(
            "noop",
            actor=alive(),
            effects=[Boost("actor", "health", 0)],
        )
        .phase(
            "day", allows=["noop"], channels=["town_square", "whisper", "alive_only"]
        )
        .phase("night", allows=["noop"], channels=["night_whispers", "secret_chat"])
        .victory(
            "v",
            when=count_where(actor.health <= 0) > 0,
            type="distribution",
            score=actor.health,
        )
        .build()
    )


class TestMessaging:
    def test_send_message_public(self):
        """Send a message to public channel."""
        g = _messaging_game()
        rt = GameRuntime(g)
        state = rt.start_game(["alice", "bob"])

        result = rt.send_message(state, "town_square", "alice", "Hello everyone!")
        assert result["ok"]
        new_state = result["state"]
        assert len(new_state.messages) == 1
        msg = new_state.messages[0]
        assert msg.sender == "alice"
        assert msg.content == "Hello everyone!"
        assert msg.channel == "town_square"
        assert msg.id == "msg-0"

    def test_send_message_records_decision(self):
        """Messages are recorded as decisions for deterministic replay."""
        g = _messaging_game()
        rt = GameRuntime(g)
        state = rt.start_game(["alice", "bob"])

        result = rt.send_message(state, "town_square", "alice", "Strategy talk")
        new_state = result["state"]
        decisions = [d for d in new_state.decisions if d["type"] == "message"]
        assert len(decisions) == 1
        assert decisions[0]["sender"] == "alice"
        assert decisions[0]["content"] == "Strategy talk"

    def test_send_message_deterministic_ids(self):
        """Message IDs are deterministic (msg-0, msg-1, ...)."""
        g = _messaging_game()
        rt = GameRuntime(g)
        state = rt.start_game(["alice", "bob"])

        result = rt.send_message(state, "town_square", "alice", "First")
        state = result["state"]
        result = rt.send_message(state, "town_square", "bob", "Second")
        state = result["state"]

        assert state.messages[0].id == "msg-0"
        assert state.messages[1].id == "msg-1"

    def test_inactive_sender_rejected(self):
        """Dead players can't send messages."""
        g = _messaging_game()
        rt = GameRuntime(g)
        state = rt.start_game(["alice", "bob"])
        state = state.deactivate("alice")

        result = rt.send_message(state, "town_square", "alice", "From the grave!")
        assert not result["ok"]
        assert result["error"]["code"] == "sender_inactive"

    def test_unknown_channel_rejected(self):
        """Unknown channels are rejected (unless private: pattern)."""
        g = _messaging_game()
        rt = GameRuntime(g)
        state = rt.start_game(["alice", "bob"])

        result = rt.send_message(state, "nonexistent", "alice", "Hello?")
        assert not result["ok"]
        assert result["error"]["code"] == "unknown_channel"

    def test_adhoc_private_channel_allowed(self):
        """Ad-hoc private channels (private:a:b) are allowed."""
        g = _messaging_game()
        rt = GameRuntime(g)
        state = rt.start_game(["alice", "bob"])

        result = rt.send_message(state, "private:alice:bob", "alice", "Secret message")
        assert result["ok"]
        new_state = result["state"]
        assert new_state.messages[0].channel == "private:alice:bob"

    def test_group_channel_enforced(self):
        """Group channel rejects non-members."""
        g = _messaging_game()
        rt = GameRuntime(g)
        state = rt.start_game(["alice", "bob"])
        # No one is in the group yet
        result = rt.send_message(state, "secret_chat", "alice", "Am I in?")
        assert not result["ok"]
        assert result["error"]["code"] == "not_in_group"

    def test_group_channel_allows_members(self):
        """Group channel allows members."""
        from engine.runtime.state import Group

        g = _messaging_game()
        rt = GameRuntime(g)
        state = rt.start_game(["alice", "bob"])
        state = state.add_group(
            Group(
                id="secret_society-0",
                type="secret_society",
                members=frozenset({"alice"}),
            )
        )

        result = rt.send_message(state, "secret_chat", "alice", "Secret!")
        assert result["ok"]

    def test_phase_filter(self):
        """Phase-restricted channels enforce phase."""
        g = _messaging_game()
        rt = GameRuntime(g)
        state = rt.start_game(["alice", "bob"])
        # State starts at phase "day", night_whispers requires "night"
        result = rt.send_message(state, "night_whispers", "alice", "Psst...")
        assert not result["ok"]
        assert result["error"]["code"] == "channel_not_available"

    def test_write_filter(self):
        """Write filter rejects non-matching senders."""
        g = _messaging_game()
        rt = GameRuntime(g)
        state = rt.start_game(["alice", "bob"])
        state = state.deactivate("alice")

        result = rt.send_message(state, "alive_only", "alice", "Can I speak?")
        # Should fail — alice is not alive, and alive_only has write_filter=alive()
        # But actually sender_inactive check comes first
        assert not result["ok"]

    def test_channel_effects_applied(self):
        """Channel effects are applied on message send."""
        g = _messaging_game()
        rt = GameRuntime(g)
        state = rt.start_game(["alice", "bob"])

        # Whisper channel has effect: Boost("actor", "suspicion", 1)
        result = rt.send_message(state, "private:alice:bob", "alice", "Whisper...")
        # Ad-hoc private channels don't have effects (no ChannelDef)
        assert result["ok"]

        # Use the defined whisper channel with effects
        result = rt.send_message(state, "whisper", "alice", "Secret whisper")
        # whisper channel is type=private but doesn't require private: prefix
        # in the runtime, type=private just means restricted visibility
        assert result["ok"]
        new_state = result["state"]
        assert new_state.get_resource("alice", "suspicion") == 1

    def test_view_for_filters_messages_public(self):
        """view_for shows public messages to everyone."""
        g = _messaging_game()
        rt = GameRuntime(g)
        state = rt.start_game(["alice", "bob"])

        result = rt.send_message(state, "town_square", "alice", "Public message")
        state = result["state"]

        view_alice = view_for(state, "alice", g)
        view_bob = view_for(state, "bob", g)
        assert len(view_alice["messages"]) == 1
        assert len(view_bob["messages"]) == 1
        assert view_alice["messages"][0]["content"] == "Public message"

    def test_view_for_filters_private_messages(self):
        """view_for hides private messages from non-participants."""
        g = _messaging_game()
        rt = GameRuntime(g)
        state = rt.start_game(["alice", "bob", "carol"])

        result = rt.send_message(state, "private:alice:bob", "alice", "Just for Bob")
        state = result["state"]

        view_alice = view_for(state, "alice", g)
        view_bob = view_for(state, "bob", g)
        view_carol = view_for(state, "carol", g)

        assert len(view_alice["messages"]) == 1
        assert len(view_bob["messages"]) == 1
        assert len(view_carol["messages"]) == 0  # carol can't see private:alice:bob

    def test_view_for_filters_group_messages(self):
        """view_for shows group messages only to group members."""
        from engine.runtime.state import Group

        g = _messaging_game()
        rt = GameRuntime(g)
        state = rt.start_game(["alice", "bob", "carol"])
        state = state.add_group(
            Group(
                id="secret_society-0",
                type="secret_society",
                members=frozenset({"alice", "bob"}),
            )
        )

        result = rt.send_message(state, "secret_chat", "alice", "For members only")
        state = result["state"]

        view_alice = view_for(state, "alice", g)
        view_bob = view_for(state, "bob", g)
        view_carol = view_for(state, "carol", g)

        assert len(view_alice["messages"]) == 1
        assert len(view_bob["messages"]) == 1
        assert len(view_carol["messages"]) == 0

    def test_send_message_effect(self):
        """SendMessage effect works as part of deal outcomes."""
        g = (
            Game("t", players=(2, 2))
            .resource("health", initial=100, bounds=(0, None))
            .channel("arena", type="public")
            .deal(
                "taunt",
                actor=alive(),
                target=alive(),
                effects=[
                    SendMessage(
                        channel="arena",
                        sender="actor",
                        content="{actor} taunts {target}!",
                    ),
                ],
            )
            .phase("p", allows=["taunt"])
            .victory("v", when=actor.health > 0, type="single")
            .build()
        )
        rt = GameRuntime(g)
        state = rt.start_game(["alice", "bob"])

        result = rt.start_deal(state, "taunt", actor_id="alice", target_id="bob")
        assert result["ok"]
        new_state = result["state"]
        assert len(new_state.messages) == 1
        assert new_state.messages[0].content == "alice taunts bob!"
        assert new_state.messages[0].channel == "arena"


class TestParamValidation:
    """Tests for deal parameter validation against ParamDef specs."""

    @pytest.fixture()
    def game_with_params(self):
        g = (
            Game("t", players=(2, 2))
            .resource("gold", initial=100, bounds=(0, None))
            .deal(
                "bid",
                actor=alive(),
                params={
                    "amount": {"type": "number", "min": 10, "max": 50, "label": "Bid"},
                },
                stakes={"actor": [("gold", "amount")]},
                effects=[Notify("actor", "You bid {amount}")],
            )
            .deal(
                "choose",
                actor=alive(),
                params={
                    "option": {
                        "type": "string",
                        "options": ("red", "blue", "green"),
                        "label": "Color",
                    },
                },
                effects=[Notify("actor", "You chose {option}")],
            )
            .deal(
                "with_default",
                actor=alive(),
                params={
                    "amount": {
                        "type": "number",
                        "min": 1,
                        "default": 5,
                        "label": "Amount",
                    },
                },
                effects=[Notify("actor", "Default amount used")],
            )
            .phase("p", allows=["bid", "choose", "with_default"])
            .victory("v", when=actor.gold <= 0, type="single")
            .build()
        )
        rt = GameRuntime(g)
        state = rt.start_game(["alice", "bob"])
        return rt, state

    def test_missing_required_param(self, game_with_params):
        rt, state = game_with_params
        r = rt.start_deal(state, "bid", actor_id="alice")
        assert not r["ok"]
        assert r["error"]["code"] == "missing_param"
        assert "amount" in r["error"]["message"]

    def test_wrong_type_number(self, game_with_params):
        rt, state = game_with_params
        r = rt.start_deal(state, "bid", actor_id="alice", params={"amount": "abc"})
        assert not r["ok"]
        assert r["error"]["code"] == "invalid_param"
        assert "expected number" in r["error"]["message"]

    def test_below_min(self, game_with_params):
        rt, state = game_with_params
        r = rt.start_deal(state, "bid", actor_id="alice", params={"amount": 5})
        assert not r["ok"]
        assert "must be >= 10" in r["error"]["message"]

    def test_above_max(self, game_with_params):
        rt, state = game_with_params
        r = rt.start_deal(state, "bid", actor_id="alice", params={"amount": 999})
        assert not r["ok"]
        assert "must be <= 50" in r["error"]["message"]

    def test_valid_number(self, game_with_params):
        rt, state = game_with_params
        r = rt.start_deal(state, "bid", actor_id="alice", params={"amount": 25})
        assert r["ok"]

    def test_invalid_option(self, game_with_params):
        rt, state = game_with_params
        r = rt.start_deal(state, "choose", actor_id="alice", params={"option": "pink"})
        assert not r["ok"]
        assert "must be one of" in r["error"]["message"]

    def test_valid_option(self, game_with_params):
        rt, state = game_with_params
        r = rt.start_deal(state, "choose", actor_id="alice", params={"option": "red"})
        assert r["ok"]

    def test_wrong_type_string(self, game_with_params):
        rt, state = game_with_params
        r = rt.start_deal(state, "choose", actor_id="alice", params={"option": 42})
        assert not r["ok"]
        assert "expected string" in r["error"]["message"]

    def test_default_applied(self, game_with_params):
        rt, state = game_with_params
        r = rt.start_deal(state, "with_default", actor_id="alice")
        assert r["ok"]


# ===========================================================================
# Immediate Deal Outcome Guard Tests
# ===========================================================================


class TestImmediateDealOutcomeGuard:
    """Outcome guards must be checked for immediate (single-party) deals too."""

    def test_immediate_deal_outcome_guard_blocks(self):
        """Immediate deal with failing outcome guard returns GUARD_FAILED."""
        g = (
            Game("t", players=(2, 2))
            .resource("gold", initial=10)
            .deal(
                "expensive_action",
                actor=alive(),
                outcomes={
                    "ok": OutcomeDef(
                        effects=(Boost("actor", "gold", 100),),
                        guard=actor.gold >= 50,  # actor only has 10
                    ),
                },
            )
            .phase("p", allows=["expensive_action"])
            .victory("v", when=actor.gold > 0, type="single")
            .build()
        )
        rt = GameRuntime(g)
        state = rt.start_game(["alice", "bob"])
        result = rt.start_deal(state, "expensive_action", actor_id="alice")
        assert not result["ok"]
        assert result["error"]["code"] == "guard_failed"

    def test_immediate_deal_outcome_guard_passes(self):
        """Immediate deal with passing outcome guard succeeds."""
        g = (
            Game("t", players=(2, 2))
            .resource("gold", initial=100)
            .deal(
                "cheap_action",
                actor=alive(),
                outcomes={
                    "ok": OutcomeDef(
                        effects=(Boost("actor", "gold", 10),),
                        guard=actor.gold >= 50,  # actor has 100
                    ),
                },
            )
            .phase("p", allows=["cheap_action"])
            .victory("v", when=actor.gold > 0, type="single")
            .build()
        )
        rt = GameRuntime(g)
        state = rt.start_game(["alice", "bob"])
        result = rt.start_deal(state, "cheap_action", actor_id="alice")
        assert result["ok"]
        assert result["state"].get_resource("alice", "gold") == 110


# ===========================================================================
# RNG next_range Tests
# ===========================================================================


class TestRngNextRange:
    def test_next_range_returns_bounded_value(self):
        from engine.runtime.rng import DeterministicRNG

        rng = DeterministicRNG(42)
        for _ in range(100):
            val, rng = rng.next_range(10)
            assert 0 <= val < 10

    def test_next_range_deterministic(self):
        from engine.runtime.rng import DeterministicRNG

        rng1 = DeterministicRNG(123)
        rng2 = DeterministicRNG(123)
        for _ in range(50):
            v1, rng1 = rng1.next_range(7)
            v2, rng2 = rng2.next_range(7)
            assert v1 == v2


# ===========================================================================
# Victory Tie-Breaking Tests
# ===========================================================================


class TestVictoryTieBreaking:
    def test_distribution_victory_deterministic_tiebreak(self):
        """When scores are tied, winner is determined by sorted entity ID."""
        from engine.expr import game
        from engine.expr.core import Lit

        g = (
            Game("t", players=(2, 4))
            .resource("score", initial=100)
            .phase("p")
            .victory(
                "v",
                when=game.round >= 1,  # always true (round starts at 1)
                type="distribution",
                score=actor.score,
            )
            .build()
        )
        rt = GameRuntime(g)
        # Create players with same score — "alice" < "bob" alphabetically
        state = rt.start_game(["bob", "alice"])
        # Both have score=100, tie → alphabetically first wins
        result = rt.check_victory(state)
        assert result is not None
        assert result["winner"] == "alice"  # sorted first
        assert result["scores"]["alice"] == result["scores"]["bob"]


# ===========================================================================
# Reveal Key Helpers Tests
# ===========================================================================


class TestRevealKeyHelpers:
    def test_reveal_key_creates_tuple(self):
        from engine.runtime.state import reveal_key, public_reveal_key, PUBLIC_OBSERVER

        rk = reveal_key("alice", "bob", "role")
        assert rk == ("alice", "bob", "role")

        pk = public_reveal_key("bob", "role")
        assert pk == (PUBLIC_OBSERVER, "bob", "role")
        assert pk == ("public", "bob", "role")

    def test_reveal_key_works_in_view_for(self):
        """Public reveals are visible to all observers via public_reveal_key."""
        from engine.runtime.state import public_reveal_key

        g = (
            Game("t", players=(2, 2))
            .resource("gold", initial=50, visibility="private")
            .phase("p")
            .victory("v", when=actor.gold > 0, type="single")
            .build()
        )
        rt = GameRuntime(g)
        state = rt.start_game(["alice", "bob"])

        # Before public reveal: bob can't see alice's gold
        v = view_for(state, "bob", g)
        assert "gold" not in v["entities"]["alice"]["resources"]

        # Add public reveal
        state = state.add_reveal("public", "alice", "gold", True)
        v = view_for(state, "bob", g)
        assert v["entities"]["alice"]["resources"]["gold"] == 50


# ===========================================================================
# can_write_channel with Expr write_filter Tests
# ===========================================================================


class TestCanWriteChannel:
    def test_write_filter_with_expr(self):
        """can_write_channel correctly evaluates Expr write_filter."""
        g = (
            Game("t", players=(2, 4))
            .resource("gold", initial=50)
            .attr("role", initial="villager")
            .channel("council", write_filter=actor.role == "mayor")
            .phase("p")
            .victory("v", when=actor.gold > 9999, type="single")
            .build()
        )
        rt = GameRuntime(g)
        state = rt.start_game(["alice", "bob"])
        state = state.set_attr("alice", "role", "mayor")

        # Alice is mayor — can write
        assert can_write_channel(state, "alice", "council", g) is True
        # Bob is villager — cannot write
        assert can_write_channel(state, "bob", "council", g) is False

    def test_write_filter_none_allows_all(self):
        """Channel without write_filter allows all active players."""
        g = (
            Game("t", players=(2, 4))
            .resource("gold", initial=50)
            .channel("public_chat")
            .phase("p")
            .victory("v", when=actor.gold > 9999, type="single")
            .build()
        )
        rt = GameRuntime(g)
        state = rt.start_game(["alice", "bob"])

        assert can_write_channel(state, "alice", "public_chat", g) is True
        assert can_write_channel(state, "bob", "public_chat", g) is True

    def test_inactive_player_cannot_write(self):
        """Dead players cannot write to any channel."""
        g = (
            Game("t", players=(2, 4))
            .resource("gold", initial=50)
            .channel("public_chat")
            .phase("p")
            .victory("v", when=actor.gold > 9999, type="single")
            .build()
        )
        rt = GameRuntime(g)
        state = rt.start_game(["alice", "bob"])
        state = state.deactivate("alice")

        assert can_write_channel(state, "alice", "public_chat", g) is False


# ===========================================================================
# Vote Tally with Fractional Weights Tests
# ===========================================================================


class TestVoteTallyWeights:
    def test_fractional_weight_preserved(self):
        """Vote weights with fractional parts are not truncated."""
        from engine.runtime.state import PendingVote, VoteDef

        g = (
            Game("t", players=(2, 4))
            .resource("gold", initial=50)
            .vote("test_vote", options=("yes", "no"), threshold="majority")
            .phase("p", allows=("test_vote",))
            .victory("v", when=actor.gold > 9999, type="single")
            .build()
        )
        rt = GameRuntime(g)
        state = rt.start_game(["alice", "bob", "charlie"])

        # Start vote
        result = rt.start_vote(state, "test_vote", proposer_id="alice")
        assert result["ok"]
        state = result["state"]
        iid = result["instance_id"]

        # Modify weights: alice=1.5, bob=0.5, charlie=1.0
        pending = state.pending_votes[iid]
        import attrs
        new_pending = attrs.evolve(pending, weights={"alice": 1.5, "bob": 0.5, "charlie": 1.0})
        state = attrs.evolve(state, pending_votes={iid: new_pending})

        # Alice votes yes (weight 1.5), bob votes no (weight 0.5)
        result = rt.cast_vote(state, iid, "alice", "yes")
        assert result["ok"]
        state = result["state"]

        result = rt.cast_vote(state, iid, "bob", "no")
        assert result["ok"]
        state = result["state"]

        # Charlie votes no (weight 1.0). Total: yes=1.5, no=1.5 → tie
        result = rt.cast_vote(state, iid, "charlie", "no")
        assert result["ok"]
        tally = result["tally"]
        assert tally["counts"]["yes"] == 1.5
        assert tally["counts"]["no"] == 1.5
        assert tally["tie"] is True

    def test_small_weight_not_rounded_to_zero(self):
        """Weight of 0.5 should count as 0.5, not 0 (old int() bug)."""
        from engine.runtime.state import PendingVote

        g = (
            Game("t", players=(2, 4))
            .resource("gold", initial=50)
            .vote("test_vote", options=("yes", "no"), threshold="majority")
            .phase("p", allows=("test_vote",))
            .victory("v", when=actor.gold > 9999, type="single")
            .build()
        )
        rt = GameRuntime(g)
        state = rt.start_game(["alice", "bob"])

        result = rt.start_vote(state, "test_vote", proposer_id="alice")
        state = result["state"]
        iid = result["instance_id"]

        # Set bob's weight to 0.5
        import attrs
        pending = state.pending_votes[iid]
        new_pending = attrs.evolve(pending, weights={"bob": 0.5})
        state = attrs.evolve(state, pending_votes={iid: new_pending})

        # Alice votes yes (default weight 1.0), bob votes yes (weight 0.5)
        result = rt.cast_vote(state, iid, "alice", "yes")
        state = result["state"]
        result = rt.cast_vote(state, iid, "bob", "yes")
        assert result["ok"]
        tally = result["tally"]
        # With int() bug: bob's weight would be 0, total yes=1
        # With float fix: bob's weight is 0.5, total yes=1.5
        assert tally["counts"]["yes"] == 1.5


# ===========================================================================
# PendingDeal target preservation Tests
# ===========================================================================


class TestPendingDealTarget:
    def test_target_preserved_in_pending_deal(self):
        """target_id is stored in PendingDeal and available during respond."""
        from engine.runtime.effects import Boost, Transfer

        g = (
            Game("t", players=(2, 4))
            .resource("gold", initial=100)
            .deal(
                "gift",
                parties={"proposer": {}, "responder": {"excludes": ("proposer",)}},
                params={},
                responses=["accept", "reject"],
                outcomes={
                    "accept": {"effects": (Transfer("proposer", "target", "gold", 10),)},
                    "reject": {"effects": ()},
                },
            )
            .phase("p", allows=("gift",))
            .victory("v", when=actor.gold > 9999, type="single")
            .build()
        )
        rt = GameRuntime(g)
        state = rt.start_game(["alice", "bob", "charlie"])

        # Start deal with target
        result = rt.start_deal(
            state, "gift",
            actor_id="alice",
            responder_id="bob",
            target_id="charlie",
        )
        assert result["ok"]
        state = result["state"]
        iid = result["instance_id"]

        # Verify target is stored in PendingDeal
        pending = state.pending_deals[iid]
        assert pending.target == "charlie"

    def test_target_in_decision_for_replay(self):
        """target_id is recorded in the decision dict for archive replay."""
        g = (
            Game("t", players=(2, 4))
            .resource("gold", initial=100)
            .deal(
                "gift",
                parties={"proposer": {}, "responder": {"excludes": ("proposer",)}},
                responses=["accept", "reject"],
                outcomes={
                    "accept": {"effects": ()},
                    "reject": {"effects": ()},
                },
            )
            .phase("p", allows=("gift",))
            .victory("v", when=actor.gold > 9999, type="single")
            .build()
        )
        rt = GameRuntime(g)
        state = rt.start_game(["alice", "bob", "charlie"])

        result = rt.start_deal(
            state, "gift",
            actor_id="alice",
            responder_id="bob",
            target_id="charlie",
        )
        state = result["state"]

        # Check that last decision includes target
        last_decision = state.decisions[-1]
        assert last_decision["target"] == "charlie"
