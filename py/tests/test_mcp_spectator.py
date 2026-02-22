"""Tests for MCP spectating system."""

from __future__ import annotations

import pytest

from engine.runtime.state import (
    SPECTATOR_ID,
    AttrDef,
    ChannelDef,
    CompiledGame,
    Entity,
    GameState,
    Group,
    GroupTypeDef,
    Message,
    PhaseDef,
    ResourceDef,
    Visibility,
    view_for,
)
from mcp.agents import AgentState
from mcp.agents import reset_all as reset_agents
from mcp.handlers.spectator import (
    handle_leave_spectate,
    handle_spectate_game,
    handle_spectate_status,
    handle_spectate_view,
)


@pytest.fixture(autouse=True)
def _clean():
    reset_agents()
    yield
    reset_agents()


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

_COMPILED = CompiledGame(
    id="mafia_test",
    name="Mafia Test",
    min_players=4,
    max_players=8,
    resources={
        "health": ResourceDef(id="health", initial=10, visibility=Visibility.PUBLIC),
        "gold": ResourceDef(id="gold", initial=50, visibility=Visibility.PRIVATE),
    },
    attrs_defs={
        "role": AttrDef(id="role", visibility=Visibility.PRIVATE),
        "faction": AttrDef(id="faction", visibility=Visibility.PUBLIC),
    },
    group_types={"mafia_family": GroupTypeDef(id="mafia_family")},
    channels={
        "town_square": ChannelDef(
            id="town_square", type="public", description="Public chat"
        ),
        "mafia_chat": ChannelDef(
            id="mafia_chat",
            type="group",
            group="mafia_family",
            description="Mafia private chat",
        ),
    },
    phases=(PhaseDef(id="day"), PhaseDef(id="night")),
)


def _game_state() -> GameState:
    state = GameState(
        phase="day",
        round=3,
        entities={
            "alice": Entity(
                id="alice",
                resources={"health": 8, "gold": 50},
                attrs_={"role": "detective", "faction": "town"},
                groups=frozenset(),
            ),
            "bob": Entity(
                id="bob",
                resources={"health": 10, "gold": 30},
                attrs_={"role": "mafioso", "faction": "mafia"},
                groups=frozenset({"mafia_family-0"}),
            ),
        },
        groups={
            "mafia_family-0": Group(
                id="mafia_family-0",
                type="mafia_family",
                members=frozenset({"bob"}),
            ),
        },
        messages=(
            Message(
                id="msg-0",
                channel="town_square",
                sender="alice",
                content="I suspect bob",
                round=2,
                phase="day",
            ),
            Message(
                id="msg-1",
                channel="mafia_chat",
                sender="bob",
                content="Target alice tonight",
                round=2,
                phase="night",
            ),
        ),
    )
    return state


class _FakeSession:
    def __init__(self, state: GameState | None = None):
        self.state = state or _game_state()
        self.compiled = _COMPILED
        self.player_ids = ["alice", "bob"]


class _FakeServer:
    def __init__(self, session=None):
        self._session = session or _FakeSession()

    def _get_session(self, session_id: str):
        return self._session


# ===========================================================================
# view_for with spectator sentinel
# ===========================================================================


class TestSpectatorViewFor:
    def test_spectator_sees_only_public_resources(self):
        state = _game_state()
        view = view_for(state, SPECTATOR_ID, _COMPILED)

        # health is PUBLIC → visible
        assert view["entities"]["alice"]["resources"]["health"] == 8
        assert view["entities"]["bob"]["resources"]["health"] == 10

        # gold is PRIVATE → NOT visible to spectator
        assert "gold" not in view["entities"]["alice"]["resources"]
        assert "gold" not in view["entities"]["bob"]["resources"]

    def test_spectator_sees_only_public_attrs(self):
        state = _game_state()
        view = view_for(state, SPECTATOR_ID, _COMPILED)

        # faction is PUBLIC → visible
        assert view["entities"]["alice"]["attrs"]["faction"] == "town"

        # role is PRIVATE → NOT visible to spectator
        assert "role" not in view["entities"]["alice"]["attrs"]
        assert "role" not in view["entities"]["bob"]["attrs"]

    def test_spectator_sees_public_messages_only(self):
        state = _game_state()
        view = view_for(state, SPECTATOR_ID, _COMPILED)

        channels = [m["channel"] for m in view["messages"]]
        assert "town_square" in channels
        assert "mafia_chat" not in channels

    def test_spectator_ignores_reveals(self):
        state = _game_state()
        # Add a reveal that would normally let someone see a private attr
        state = state.add_reveal(SPECTATOR_ID, "alice", "role", True)
        view = view_for(state, SPECTATOR_ID, _COMPILED)

        # Spectator should NOT see role even with a reveal
        assert "role" not in view["entities"]["alice"]["attrs"]

    def test_player_still_sees_own_private(self):
        """Ensure spectator changes didn't break normal player view."""
        state = _game_state()
        view = view_for(state, "alice", _COMPILED)

        assert view["entities"]["alice"]["resources"]["gold"] == 50
        assert view["entities"]["alice"]["attrs"]["role"] == "detective"
        # Alice shouldn't see bob's private data
        assert "gold" not in view["entities"]["bob"]["resources"]
        assert "role" not in view["entities"]["bob"]["attrs"]

    def test_player_still_sees_reveals(self):
        """Reveals still work for normal players."""
        state = _game_state()
        state = state.add_reveal("alice", "bob", "role", True)
        view = view_for(state, "alice", _COMPILED)
        assert view["entities"]["bob"]["attrs"]["role"] == "mafioso"


# ===========================================================================
# Spectator handler tests
# ===========================================================================


class TestSpectateGame:
    @pytest.mark.asyncio
    async def test_start_spectating(self):
        server = _FakeServer()
        agent = AgentState(agent_id="spectator-1")

        result = await handle_spectate_game(server, agent, {"session_id": "game-1"})
        text = result["content"][0]["text"]

        assert "spectating" in text.lower()
        assert agent.state == "spectating"
        assert agent.session_id == "game-1"

    @pytest.mark.asyncio
    async def test_cannot_spectate_from_in_game(self):
        server = _FakeServer()
        agent = AgentState(agent_id="player-1")
        agent.to_game("game-1", "alice", "mafia_test")

        result = await handle_spectate_game(server, agent, {"session_id": "game-2"})
        assert result.get("isError") is True
        assert agent.state == "in_game"  # unchanged

    @pytest.mark.asyncio
    async def test_session_not_found(self):
        server = _FakeServer()
        server._session = None  # no sessions
        server._get_session = lambda sid: None
        agent = AgentState(agent_id="spectator-1")

        result = await handle_spectate_game(server, agent, {"session_id": "nope"})
        assert result.get("isError") is True


class TestLeaveSpectate:
    @pytest.mark.asyncio
    async def test_leave(self):
        agent = AgentState(agent_id="spectator-1")
        agent.to_spectating("game-1")

        result = await handle_leave_spectate(_FakeServer(), agent, {})
        text = result["content"][0]["text"]
        assert "lobby" in text.lower()
        assert agent.state == "lobby"

    @pytest.mark.asyncio
    async def test_not_spectating(self):
        agent = AgentState(agent_id="test")
        result = await handle_leave_spectate(_FakeServer(), agent, {})
        assert result.get("isError") is True


class TestSpectateStatus:
    @pytest.mark.asyncio
    async def test_shows_status(self):
        agent = AgentState(agent_id="spectator-1")
        agent.to_spectating("game-1")

        result = await handle_spectate_status(_FakeServer(), agent, {})
        text = result["content"][0]["text"]
        assert "Mafia Test" in text
        assert "day" in text
        assert "Round: 3" in text


class TestSpectateView:
    @pytest.mark.asyncio
    async def test_public_only_view(self):
        agent = AgentState(agent_id="spectator-1")
        agent.to_spectating("game-1")

        result = await handle_spectate_view(_FakeServer(), agent, {})
        text = result["content"][0]["text"]

        # Public data visible
        assert "alice" in text
        assert "bob" in text
        assert "health" in text

        # Private data hidden
        assert "detective" not in text
        assert "mafioso" not in text
        assert "gold" not in text.split("###")[1] if "###" in text else True

        # Public messages visible, private hidden
        assert "I suspect bob" in text
        assert "Target alice tonight" not in text

    @pytest.mark.asyncio
    async def test_with_history(self):
        from engine.runtime.state import HistoryEntry

        state = _game_state()
        state = state.add_history("deal_executed", deal="attack", actor="alice")
        session = _FakeSession(state)
        server = _FakeServer(session)

        agent = AgentState(agent_id="spectator-1")
        agent.to_spectating("game-1")

        result = await handle_spectate_view(server, agent, {"include_history": True})
        text = result["content"][0]["text"]
        assert "Recent Events" in text
        assert "deal_executed" in text


# ===========================================================================
# Tool list integration
# ===========================================================================


class TestSpectatorToolList:
    def test_lobby_has_spectate_game(self):
        from mcp.server import MCPServer

        server = MCPServer()
        agent = AgentState(agent_id="test")
        tools = server._get_tools_for_agent(agent)
        names = {t.name for t in tools}
        assert "spectate_game" in names

    def test_spectating_state_tools(self):
        from mcp.server import MCPServer

        server = MCPServer()
        agent = AgentState(agent_id="test")
        agent.state = "spectating"
        agent.session_id = "game-1"
        tools = server._get_tools_for_agent(agent)
        names = {t.name for t in tools}

        # Spectating tools
        assert "leave_spectate" in names
        assert "spectate_status" in names
        assert "spectate_view" in names

        # Global tools also available
        assert "my_stats" in names
        assert "platform_stats" in names

        # NOT game action tools
        assert "advance_phase" not in names
        assert "get_status" not in names
