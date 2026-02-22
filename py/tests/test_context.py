"""Tests for the universal agent context system.

Covers: VarHint rendering, phase tips, role guidance, channel hints,
deal priorities, source hash exclusion, and fallback behavior.
"""

from __future__ import annotations

import attrs
import pytest

from engine.dsl.builder import Game
from engine.expr import actor, alive, game
from engine.runtime.core import GameRuntime
from engine.runtime.state import (
    ChannelHint,
    CompiledGame,
    ContextConfig,
    Entity,
    GameState,
    PhaseDef,
    PhaseHint,
    RoleHint,
    VarHint,
)
from mcp.formatters import _build_context_line, format_available_actions, format_status

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_game(**ctx_kwargs) -> CompiledGame:
    """Build a minimal valid game, optionally with context."""
    g = (
        Game("test", "Test Game", players=(2, 4))
        .resource("gold", initial=100)
        .deal("trade", actor=alive(), effects=[], doc="Trade goods")
        .deal("attack", actor=alive(), effects=[], doc="Attack target")
        .phase("action", allows=["trade", "attack"])
        .victory("end", when=game.round >= 3, type="distribution", score=actor.gold)
    )
    if ctx_kwargs:
        g = g.context(**ctx_kwargs)
    return g.build()


def _state_with_vars(compiled: CompiledGame, **vars_) -> GameState:
    """Create a GameState with the given vars and two active entities."""
    return GameState(
        phase="action",
        phase_index=0,
        round=1,
        entities={
            "alice": Entity(id="alice", active=True, resources={"gold": 100}),
            "bob": Entity(id="bob", active=True, resources={"gold": 100}),
        },
        vars_=vars_,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestContextFallback:
    """Games without .context() still work."""

    def test_no_context_builds_successfully(self):
        compiled = _minimal_game()
        assert compiled.context.game_summary == ""
        assert compiled.context.var_hints == {}

    def test_fallback_context_line_shows_round(self):
        compiled = _minimal_game()
        state = _state_with_vars(compiled)
        state = attrs.evolve(state, round=3)
        phase_def = compiled.phases[0]
        parts = _build_context_line(state, compiled, phase_def)
        assert "Round 3" in parts

    def test_fallback_shows_non_underscore_vars(self):
        compiled = _minimal_game()
        state = _state_with_vars(compiled, score=42, _internal=99)
        phase_def = compiled.phases[0]
        parts = _build_context_line(state, compiled, phase_def)
        assert any("score" in p for p in parts)
        assert not any("_internal" in p for p in parts)


class TestVarHints:
    """VarHint rendering with different formats."""

    def test_progress_format(self):
        compiled = _minimal_game(
            var_hints=[
                VarHint(
                    "current", "Lot", format="progress", max_var="total", priority=100
                ),
            ],
        )
        state = _state_with_vars(compiled, current=2, total=6)
        parts = _build_context_line(state, compiled, compiled.phases[0])
        assert "Lot 2/6" in parts

    def test_currency_format(self):
        compiled = _minimal_game(
            var_hints=[
                VarHint("price", "Price", format="currency", priority=100),
            ],
        )
        state = _state_with_vars(compiled, price=500)
        parts = _build_context_line(state, compiled, compiled.phases[0])
        assert "Price: 500" in parts

    def test_player_format_hides_empty(self):
        compiled = _minimal_game(
            var_hints=[
                VarHint("leader", "Leader", format="player", priority=100),
            ],
        )
        # Empty string → not shown
        state = _state_with_vars(compiled, leader="")
        parts = _build_context_line(state, compiled, compiled.phases[0])
        assert not any("Leader" in p for p in parts)

        # Non-empty → shown
        state = _state_with_vars(compiled, leader="alice")
        parts = _build_context_line(state, compiled, compiled.phases[0])
        assert "Leader: alice" in parts

    def test_phase_filter(self):
        compiled = _minimal_game(
            var_hints=[
                VarHint("bid", "Bid", phases=("bidding",), priority=100),
                VarHint("round", "Round", priority=50),
            ],
        )
        # Phase is "action", not "bidding" → bid hint filtered out
        state = _state_with_vars(compiled, bid=100, round=1)
        parts = _build_context_line(state, compiled, compiled.phases[0])
        assert not any("Bid" in p for p in parts)
        assert any("Round" in p for p in parts)

    def test_priority_ordering(self):
        compiled = _minimal_game(
            var_hints=[
                VarHint("low", "Low", priority=10),
                VarHint("high", "High", priority=90),
                VarHint("mid", "Mid", priority=50),
            ],
        )
        state = _state_with_vars(compiled, low=1, high=2, mid=3)
        parts = _build_context_line(state, compiled, compiled.phases[0])
        # High should come first
        assert parts[0] == "High: 2"
        assert parts[-1] == "Low: 1"


class TestPhaseHints:
    """Phase tips appear in available_actions."""

    def test_phase_summary_in_output(self):
        compiled = _minimal_game(
            phase_hints=[
                PhaseHint(
                    "action", "Choose your action wisely.", tips=("Tip A", "Tip B")
                ),
            ],
        )
        state = _state_with_vars(compiled)
        output = format_available_actions(state, compiled, "alice")
        assert "Choose your action wisely." in output
        assert "Tip: Tip A" in output
        assert "Tip: Tip B" in output

    def test_no_phase_hint_no_crash(self):
        compiled = _minimal_game(
            phase_hints=[
                PhaseHint("other_phase", "Not shown"),
            ],
        )
        state = _state_with_vars(compiled)
        output = format_available_actions(state, compiled, "alice")
        assert "Not shown" not in output


class TestRoleHints:
    """RoleHint appears in role_guidance tool."""

    @pytest.mark.asyncio
    async def test_role_hint_in_guidance(self):
        from mcp.agents import AgentState, reset_all
        from mcp.handlers.helpers import handle_role_guidance

        reset_all()

        compiled = _minimal_game(
            role_hints=[
                RoleHint(
                    "detective",
                    strategy="Investigate at night.",
                    allies=("bodyguard",),
                    threats=("mafia",),
                    key_actions=("investigate",),
                    phase_tips={"action": "Pick the most suspicious player"},
                ),
            ],
        )
        # Need roles defined for this to work
        compiled = attrs.evolve(
            compiled,
            roles={
                "detective": attrs.evolve(
                    __import__("engine.runtime.state", fromlist=["RoleDef"]).RoleDef(
                        id="detective", team="town", doc="Investigator"
                    ),
                )
            },
            attrs_defs={
                "role": __import__(
                    "engine.runtime.state", fromlist=["AttrDef"]
                ).AttrDef(
                    id="role",
                    visibility=__import__(
                        "engine.runtime.state", fromlist=["Visibility"]
                    ).Visibility.PRIVATE,
                ),
            },
        )
        state = _state_with_vars(compiled)
        state = state.set_attr("alice", "role", "detective")

        # Mock server/agent
        class FakeSession:
            def __init__(self):
                self.state = state
                self.compiled = compiled

        class FakeServer:
            def _get_session(self, sid):
                return FakeSession()

        agent = AgentState(
            agent_id="test",
            state="in_game",
            session_id="s1",
            player_id="alice",
        )

        result = await handle_role_guidance(FakeServer(), agent, {})
        text = result["content"][0]["text"]
        assert "Investigate at night." in text
        assert "bodyguard" in text
        assert "mafia" in text
        assert "investigate" in text
        assert "Pick the most suspicious player" in text

        reset_all()


class TestChannelHints:
    """Channel hints enrich tool descriptions."""

    def test_channel_tool_has_hints(self):
        from engine.runtime.state import ChannelDef

        compiled = _minimal_game(
            channel_hints=[
                ChannelHint(
                    "lobby",
                    when_to_use="Public discussion",
                    risk="Everyone can see",
                ),
            ],
        )
        compiled = attrs.evolve(
            compiled,
            channels={
                "lobby": ChannelDef(
                    id="lobby", type="public", description="Game lobby"
                ),
            },
        )

        from mcp.schema import channel_to_tool

        tool = channel_to_tool(
            "test", "lobby", compiled.channels["lobby"], compiled.context
        )
        assert "Public discussion" in tool.description
        assert "Everyone can see" in tool.description


class TestDealPriorities:
    """Deal priorities affect ordering in available_actions."""

    def test_deals_sorted_by_priority(self):
        compiled = _minimal_game(
            deal_priorities={"attack": 100, "trade": 10},
        )
        state = _state_with_vars(compiled)
        output = format_available_actions(state, compiled, "alice")
        # attack (priority 100) should appear before trade (priority 10)
        attack_pos = output.index("**attack**")
        trade_pos = output.index("**trade**")
        assert attack_pos < trade_pos


class TestSourceHash:
    """Changing context doesn't change source hash."""

    def test_same_hash_different_context(self):
        game1 = _minimal_game()
        game2 = _minimal_game(
            game_summary="Totally different summary",
            var_hints=[VarHint("x", "X", priority=100)],
            phase_hints=[PhaseHint("action", "Do things")],
        )
        assert game1.source_hash == game2.source_hash

    def test_changing_mechanics_changes_hash(self):
        game1 = _minimal_game()
        game2 = (
            Game("test", "Test Game", players=(2, 4))
            .resource("gold", initial=200)  # different initial
            .deal("trade", actor=alive(), effects=[], doc="Trade goods")
            .deal("attack", actor=alive(), effects=[], doc="Attack target")
            .phase("action", allows=["trade", "attack"])
            .victory("end", when=game.round >= 3, type="distribution", score=actor.gold)
            .build()
        )
        assert game1.source_hash != game2.source_hash


class TestAuctionContextIntegration:
    """Verify auction game context works end-to-end."""

    def test_auction_has_context(self):
        from games.auction import auction

        assert auction.context.game_summary != ""
        assert auction.context.score_explanation != ""
        assert len(auction.context.var_hints) >= 4
        assert len(auction.context.phase_hints) >= 3
        assert len(auction.context.channel_hints) >= 1

    def test_auction_context_line_with_vars(self):
        """VarHints render correctly when auction vars are present."""
        from games.auction import auction

        # Simulate a state with vars set (as they would be mid-game)
        state = GameState(
            phase="bidding",
            phase_index=3,
            round=1,
            entities={
                "alice": Entity(id="alice", active=True, resources={"gold": 900}),
                "bob": Entity(id="bob", active=True, resources={"gold": 800}),
                "charlie": Entity(id="charlie", active=True, resources={"gold": 950}),
            },
            vars_={
                "current_lot": 2,
                "total_lots": 6,
                "auction_type": "english",
                "highest_bid": 150,
                "highest_bidder": "alice",
                "lot_value": 220,
                "dutch_price": 550,
            },
        )
        phase_def = [p for p in auction.phases if p.id == "bidding"][0]
        parts = _build_context_line(state, auction, phase_def)
        # VarHints should produce structured output
        assert any("Lot" in p and "2/6" in p for p in parts)
        assert any("Format" in p and "english" in p for p in parts)
        assert any("High bid" in p and "150" in p for p in parts)
        assert any("High bidder" in p and "alice" in p for p in parts)
        assert any("Lot value" in p and "220" in p for p in parts)

    def test_werewolf_has_role_hints(self):
        from games.werewolf import werewolf

        assert len(werewolf.context.role_hints) >= 8
        assert "seer" in werewolf.context.role_hints
        seer = werewolf.context.role_hints["seer"]
        assert seer.strategy != ""
        assert "seer_vision" in seer.key_actions

    def test_parliament_arena_has_context(self):
        from games.parliament_arena import parliament_arena

        assert parliament_arena.context.game_summary != ""
        assert len(parliament_arena.context.role_hints) >= 6
        assert len(parliament_arena.context.phase_hints) >= 4
        assert len(parliament_arena.context.channel_hints) >= 3


class TestOutcomePreview:
    """Outcome previews appear in format_available_actions for bilateral deals."""

    def test_bilateral_deal_shows_outcomes(self):
        """Bilateral deals with multiple outcomes show outcome preview line."""
        from engine.runtime.state import OutcomeDef, PartyDef

        g = (
            Game("test", "Test Game", players=(2, 4))
            .resource("gold", initial=100)
            .deal(
                "barter",
                actor=alive(),
                responder=alive(),
                doc="Exchange goods",
                responses=["accept", "reject"],
                outcomes={
                    "accept": {"doc": "Trade completed successfully"},
                    "reject": {"doc": "Trade refused"},
                },
            )
            .phase("action", allows=["barter"])
            .victory("end", when=game.round >= 3, type="distribution", score=actor.gold)
        )
        compiled = g.build()
        state = _state_with_vars(compiled)
        output = format_available_actions(state, compiled, "alice")
        assert "Outcomes:" in output
        assert "accept" in output
        assert "reject" in output
        assert "Trade completed" in output

    def test_immediate_deal_hides_single_ok_outcome(self):
        """Immediate deals with just 'ok' outcome don't show outcome preview."""
        compiled = _minimal_game()
        state = _state_with_vars(compiled)
        output = format_available_actions(state, compiled, "alice")
        assert "Outcomes:" not in output

    def test_pa_bribe_shows_outcomes(self):
        """Parliament Arena bribe deal shows outcome preview in real game."""
        from games.parliament_arena import parliament_arena

        state = GameState(
            phase="floor",
            phase_index=2,
            round=1,
            entities={
                f"p{i}": Entity(
                    id=f"p{i}",
                    active=True,
                    resources={"caps": 100, "reputation": 50, "influence": 30},
                )
                for i in range(6)
            },
        )
        output = format_available_actions(state, parliament_arena, "p0")
        # bribe is a bilateral deal with accept/reject/expose — must show outcomes
        assert "**bribe**" in output, "bribe deal should be available in floor phase"
        assert "Outcomes:" in output


class TestRoleContextInStatus:
    """Role context block appears in format_status."""

    def test_role_hints_shown_in_status(self):
        """format_status includes role context when RoleHints are configured."""
        from engine.runtime.state import AttrDef, RoleDef, Visibility, view_for

        compiled = _minimal_game(
            role_hints=[
                RoleHint(
                    "warrior",
                    strategy="Attack aggressively",
                    allies=("healer",),
                    threats=("assassin",),
                    phase_tips={"action": "Strike first"},
                ),
            ],
        )
        compiled = attrs.evolve(
            compiled,
            roles={"warrior": RoleDef(id="warrior", team="red", doc="Fighter")},
            attrs_defs={
                "role": AttrDef(id="role", visibility=Visibility.PRIVATE),
            },
        )
        state = _state_with_vars(compiled)
        state = state.set_attr("alice", "role", "warrior")

        view = view_for(state, "alice", compiled)
        view["_state"] = state  # injected by server
        output = format_status(view, compiled, "alice")
        assert "### Your Role: warrior" in output
        assert "Strategy: Attack aggressively" in output
        assert "Allies: healer" in output
        assert "Threats: assassin" in output
        assert "Now (action): Strike first" in output

    def test_no_role_hints_no_crash(self):
        """Games without role hints don't crash format_status."""
        from engine.runtime.state import view_for

        compiled = _minimal_game()
        state = _state_with_vars(compiled)
        view = view_for(state, "alice", compiled)
        view["_state"] = state
        output = format_status(view, compiled, "alice")
        assert "### You (" in output
        assert "### Your Role:" not in output

    def test_werewolf_seer_shows_role_context(self):
        """Werewolf seer's role hints appear in format_status."""
        from engine.runtime.state import view_for
        from games.werewolf import werewolf

        state = GameState(
            phase="night",
            phase_index=1,
            round=1,
            entities={
                f"p{i}": Entity(
                    id=f"p{i}",
                    active=True,
                    resources={},
                    attrs_={"role": "villager", "team": "village"},
                )
                for i in range(8)
            },
        )
        state = state.set_attr("p0", "role", "seer")
        state = state.set_attr("p0", "team", "village")

        view = view_for(state, "p0", werewolf)
        view["_state"] = state
        output = format_status(view, werewolf, "p0")
        # Seer role hints must be present (werewolf game defines them)
        assert "seer" in werewolf.context.role_hints, (
            "werewolf should have seer role hint"
        )
        assert "### Your Role: seer" in output
        assert "Strategy:" in output
