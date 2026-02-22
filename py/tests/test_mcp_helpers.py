"""Tests for MCP AI helper tools (game_summary, role_guidance, game_rules)."""

from __future__ import annotations

import pytest

from engine.runtime.state import (
    AttrDef,
    ChannelDef,
    CompiledGame,
    DealDef,
    Entity,
    GameState,
    OutcomeDef,
    PartyDef,
    PhaseDef,
    ResourceDef,
    RoleDef,
    VictoryDef,
    Visibility,
    VoteDef,
)
from mcp.agents import AgentState
from mcp.agents import reset_all as reset_agents
from mcp.handlers.helpers import (
    handle_deal_mechanics,
    handle_game_rules,
    handle_game_summary,
    handle_role_guidance,
)


@pytest.fixture(autouse=True)
def _clean():
    reset_agents()
    yield
    reset_agents()


# ---------------------------------------------------------------------------
# Test game
# ---------------------------------------------------------------------------

_COMPILED = CompiledGame(
    id="mafia_test",
    name="Mafia Test",
    min_players=4,
    max_players=8,
    resources={
        "health": ResourceDef(id="health", initial=10, visibility=Visibility.PUBLIC),
        "gold": ResourceDef(
            id="gold", initial=50, visibility=Visibility.PRIVATE, bounds=(0, 200)
        ),
    },
    attrs_defs={
        "role": AttrDef(id="role", visibility=Visibility.PRIVATE),
        "faction": AttrDef(id="faction", visibility=Visibility.PUBLIC),
    },
    roles={
        "detective": RoleDef(
            id="detective", team="town", doc="Investigates players at night"
        ),
        "mafioso": RoleDef(
            id="mafioso", team="mafia", doc="Eliminates players at night"
        ),
        "citizen": RoleDef(id="citizen", team="town", doc="Regular townsfolk"),
    },
    deals={
        "investigate": DealDef(
            id="investigate",
            parties={"actor": PartyDef(), "target": PartyDef()},
            doc="Investigate a player's alignment",
        ),
        "attack": DealDef(
            id="attack",
            parties={"actor": PartyDef(), "target": PartyDef()},
            response_options=("accept",),
            outcomes={"accept": OutcomeDef(doc="Attack resolves")},
            doc="Attack another player",
        ),
    },
    votes={
        "lynch": VoteDef(
            id="lynch",
            options=("guilty", "innocent"),
            threshold="majority",
            doc="Vote to lynch a player",
        ),
    },
    phases=(
        PhaseDef(id="day", allows=("attack", "lynch")),
        PhaseDef(id="night", allows=("investigate",), automatic=False),
    ),
    victories=(
        VictoryDef(id="town_wins", team="town", message="Town eliminates all mafia"),
        VictoryDef(id="mafia_wins", team="mafia", message="Mafia outnumbers town"),
    ),
    channels={
        "town_square": ChannelDef(
            id="town_square", type="public", description="Public discussion"
        ),
    },
)


def _state() -> GameState:
    return GameState(
        phase="day",
        round=2,
        entities={
            "alice": Entity(
                id="alice",
                resources={"health": 8, "gold": 45},
                attrs_={"role": "detective", "faction": "town"},
            ),
            "bob": Entity(
                id="bob",
                resources={"health": 10, "gold": 50},
                attrs_={"role": "mafioso", "faction": "mafia"},
            ),
            "carol": Entity(
                id="carol",
                active=False,
                resources={"health": 0, "gold": 30},
                attrs_={"role": "citizen", "faction": "town"},
            ),
        },
    )


class _FakeSession:
    def __init__(self, state=None):
        self.state = state or _state()
        self.compiled = _COMPILED
        self.runtime = None


class _FakeServer:
    def __init__(self, session=None):
        self._session = session or _FakeSession()

    def _get_session(self, session_id):
        return self._session


def _agent(player_id: str = "alice") -> AgentState:
    a = AgentState(agent_id=f"agent-{player_id}")
    a.to_game("test-session", player_id, "mafia_test")
    return a


# ===========================================================================
# game_summary
# ===========================================================================


class TestGameSummary:
    @pytest.mark.asyncio
    async def test_basic_summary(self):
        result = await handle_game_summary(_FakeServer(), _agent("alice"), {})
        text = result["content"][0]["text"]

        assert "Mafia Test" in text
        assert "Round 2" in text
        assert "day" in text
        assert "alice" in text
        assert "2/3 active" in text  # carol is eliminated
        assert "detective" in text  # alice's role visible to herself

    @pytest.mark.asyncio
    async def test_pending_actions_shown(self):
        from engine.runtime.state import PendingVote

        state = _state()
        pv = PendingVote(
            instance_id="vote-0",
            vote_id="lynch",
            eligible=("alice", "bob"),
            options=("guilty", "innocent"),
        )
        state = GameState(
            **{
                **{f.name: getattr(state, f.name) for f in state.__attrs_attrs__},
                "pending_votes": {"vote-0": pv},
            }
        )
        session = _FakeSession(state)
        result = await handle_game_summary(_FakeServer(session), _agent("alice"), {})
        text = result["content"][0]["text"]
        assert "vote" in text.lower()
        assert "lynch" in text

    @pytest.mark.asyncio
    async def test_not_in_game(self):
        agent = AgentState(agent_id="test")
        result = await handle_game_summary(_FakeServer(), agent, {})
        assert result.get("isError") is True


# ===========================================================================
# role_guidance
# ===========================================================================


class TestRoleGuidance:
    @pytest.mark.asyncio
    async def test_detective_guidance(self):
        result = await handle_role_guidance(_FakeServer(), _agent("alice"), {})
        text = result["content"][0]["text"]

        assert "detective" in text.lower()
        assert "town" in text  # team
        assert "Investigates" in text  # doc
        assert "town_wins" in text  # relevant victory
        assert "mafia_wins" not in text  # other team's victory filtered

    @pytest.mark.asyncio
    async def test_mafioso_guidance(self):
        result = await handle_role_guidance(_FakeServer(), _agent("bob"), {})
        text = result["content"][0]["text"]

        assert "mafioso" in text.lower()
        assert "mafia" in text
        assert "mafia_wins" in text
        assert "town_wins" not in text

    @pytest.mark.asyncio
    async def test_current_phase_actions(self):
        result = await handle_role_guidance(_FakeServer(), _agent("alice"), {})
        text = result["content"][0]["text"]

        # Day phase allows attack and lynch
        assert "attack" in text
        assert "lynch" in text
        # Night action (investigate) not shown in day phase
        assert "investigate" not in text


# ===========================================================================
# game_rules
# ===========================================================================


class TestGameRules:
    @pytest.mark.asyncio
    async def test_full_rules(self):
        result = await handle_game_rules(_FakeServer(), _agent("alice"), {})
        text = result["content"][0]["text"]

        # Resources
        assert "health" in text
        assert "gold" in text
        assert "0-200" in text  # bounds

        # Roles
        assert "detective" in text
        assert "mafioso" in text
        assert "citizen" in text

        # Phases
        assert "day" in text
        assert "night" in text

        # Deals
        assert "investigate" in text
        assert "attack" in text
        assert "accept" in text  # response option

        # Votes
        assert "lynch" in text
        assert "guilty" in text

        # Victory
        assert "town_wins" in text
        assert "mafia_wins" in text

        # Channels
        assert "town_square" in text

    @pytest.mark.asyncio
    async def test_includes_player_range(self):
        result = await handle_game_rules(_FakeServer(), _agent(), {})
        text = result["content"][0]["text"]
        assert "4-8" in text


# ===========================================================================
# deal_mechanics
# ===========================================================================


class TestDealMechanics:
    @pytest.mark.asyncio
    async def test_deal_mechanics_for_deal(self):
        """deal_mechanics returns detailed breakdown of a deal."""
        result = await handle_deal_mechanics(
            _FakeServer(), _agent("alice"), {"action_id": "attack"}
        )
        text = result["content"][0]["text"]
        assert "attack" in text.lower()
        assert "Mechanics" in text or "mechanic" in text.lower()
        # Should show parties, outcomes
        assert "actor" in text or "target" in text
        assert not result.get("isError")

    @pytest.mark.asyncio
    async def test_deal_mechanics_for_vote(self):
        """deal_mechanics works for votes too."""
        result = await handle_deal_mechanics(
            _FakeServer(), _agent("alice"), {"action_id": "lynch"}
        )
        text = result["content"][0]["text"]
        assert "lynch" in text.lower()
        assert "guilty" in text or "innocent" in text
        assert not result.get("isError")

    @pytest.mark.asyncio
    async def test_deal_mechanics_unknown_action(self):
        """Unknown action_id returns error with available actions list."""
        result = await handle_deal_mechanics(
            _FakeServer(), _agent("alice"), {"action_id": "nonexistent"}
        )
        assert result.get("isError")
        text = result["content"][0]["text"]
        assert "not found" in text.lower() or "Not found" in text
        # Should list available actions
        assert "attack" in text
        assert "investigate" in text
        assert "lynch" in text

    @pytest.mark.asyncio
    async def test_deal_mechanics_empty_action_id(self):
        """Empty action_id returns error."""
        result = await handle_deal_mechanics(
            _FakeServer(), _agent("alice"), {"action_id": ""}
        )
        assert result.get("isError")
        text = result["content"][0]["text"]
        assert "required" in text.lower()

    @pytest.mark.asyncio
    async def test_deal_mechanics_missing_action_id(self):
        """Missing action_id returns error."""
        result = await handle_deal_mechanics(
            _FakeServer(), _agent("alice"), {}
        )
        assert result.get("isError")

    @pytest.mark.asyncio
    async def test_deal_mechanics_not_in_game(self):
        """deal_mechanics requires in_game state."""
        agent = AgentState(agent_id="test")
        result = await handle_deal_mechanics(_FakeServer(), agent, {"action_id": "attack"})
        assert result.get("isError")
        assert "Not in a game" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_deal_mechanics_in_schema(self):
        """deal_mechanics tool appears in filtered tools."""
        from engine.runtime.state import Entity
        from mcp.schema import filter_tools_for_phase, generate_game_tools

        tools = generate_game_tools(_COMPILED)
        state = GameState(
            phase="day",
            entities={"alice": Entity(id="alice")},
        )
        filtered = filter_tools_for_phase(tools, state, _COMPILED, "alice")
        names = {t.name for t in filtered}
        assert "deal_mechanics" in names


# ===========================================================================
# Tool schema integration
# ===========================================================================


class TestHelperToolsInSchema:
    def test_in_game_includes_helper_tools(self):
        from engine.runtime.state import Entity
        from mcp.schema import filter_tools_for_phase, generate_game_tools

        tools = generate_game_tools(_COMPILED)
        state = GameState(
            phase="day",
            entities={"alice": Entity(id="alice")},
        )
        filtered = filter_tools_for_phase(tools, state, _COMPILED, "alice")
        names = {t.name for t in filtered}
        assert "game_summary" in names
        assert "role_guidance" in names
        assert "game_rules" in names
