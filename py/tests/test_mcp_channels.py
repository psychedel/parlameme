"""Tests for MCP channel communication tools (list, read, read-all)."""

from __future__ import annotations

import pytest

from engine.runtime.state import (
    ChannelDef,
    CompiledGame,
    Entity,
    GameState,
    Group,
    GroupTypeDef,
    Message,
    PhaseDef,
    ResourceDef,
    can_read_channel,
    can_write_channel,
)
from mcp.agents import AgentState
from mcp.agents import reset_all as reset_agents
from mcp.handlers.channels import (
    handle_get_all_messages,
    handle_get_messages,
    handle_list_channels,
)


@pytest.fixture(autouse=True)
def _clean_agents():
    reset_agents()
    yield
    reset_agents()


# ---------------------------------------------------------------------------
# Test game with multiple channel types
# ---------------------------------------------------------------------------

_COMPILED = CompiledGame(
    id="mafia_test",
    name="Mafia Test",
    min_players=4,
    max_players=8,
    resources={"health": ResourceDef(id="health", initial=10)},
    group_types={"mafia_family": GroupTypeDef(id="mafia_family")},
    channels={
        "town_square": ChannelDef(
            id="town_square",
            type="public",
            description="Public discussion",
        ),
        "mafia_chat": ChannelDef(
            id="mafia_chat",
            type="group",
            group="mafia_family",
            description="Private mafia coordination",
        ),
        "night_only": ChannelDef(
            id="night_only",
            type="public",
            phase_filter=("night",),
            description="Only during night",
        ),
    },
    phases=(
        PhaseDef(id="day"),
        PhaseDef(id="night"),
    ),
)


def _make_state(phase: str = "day") -> GameState:
    """State with 3 players: alice (mafia), bob (town), carol (town)."""
    state = GameState(
        phase=phase,
        entities={
            "alice": Entity(id="alice", groups=frozenset({"mafia_family-0"})),
            "bob": Entity(id="bob"),
            "carol": Entity(id="carol"),
        },
        groups={
            "mafia_family-0": Group(
                id="mafia_family-0",
                type="mafia_family",
                members=frozenset({"alice"}),
            ),
        },
    )
    return state


class _FakeSession:
    """Minimal session stub for handler tests."""

    def __init__(self, state: GameState, compiled: CompiledGame = _COMPILED):
        self.state = state
        self.compiled = compiled


class _FakeServer:
    """Minimal server stub that returns a session."""

    def __init__(self, session: _FakeSession):
        self._session = session

    def _get_session(self, session_id: str):
        return self._session


def _agent(player_id: str) -> AgentState:
    a = AgentState(agent_id=f"agent-{player_id}")
    a.to_game("test-session", player_id, "mafia_test")
    return a


# ===========================================================================
# can_read_channel / can_write_channel (unit tests)
# ===========================================================================


class TestChannelPermissions:
    def test_public_readable_by_all(self):
        state = _make_state()
        assert can_read_channel(state, "alice", "town_square", _COMPILED)
        assert can_read_channel(state, "bob", "town_square", _COMPILED)

    def test_group_channel_members_only(self):
        state = _make_state()
        assert can_read_channel(state, "alice", "mafia_chat", _COMPILED)
        assert not can_read_channel(state, "bob", "mafia_chat", _COMPILED)

    def test_public_writable_by_active(self):
        state = _make_state()
        assert can_write_channel(state, "alice", "town_square", _COMPILED)
        assert can_write_channel(state, "bob", "town_square", _COMPILED)

    def test_group_writable_by_members(self):
        state = _make_state()
        assert can_write_channel(state, "alice", "mafia_chat", _COMPILED)
        assert not can_write_channel(state, "bob", "mafia_chat", _COMPILED)

    def test_inactive_cannot_write(self):
        state = _make_state()
        state = state.deactivate("alice")
        assert not can_write_channel(state, "alice", "town_square", _COMPILED)

    def test_phase_filter_blocks_wrong_phase(self):
        state = _make_state("day")
        assert not can_write_channel(state, "alice", "night_only", _COMPILED)

    def test_phase_filter_allows_correct_phase(self):
        state = _make_state("night")
        assert can_write_channel(state, "alice", "night_only", _COMPILED)


# ===========================================================================
# list_channels handler
# ===========================================================================


class TestListChannels:
    @pytest.mark.asyncio
    async def test_mafia_member_sees_all(self):
        state = _make_state("day")
        server = _FakeServer(_FakeSession(state))
        result = await handle_list_channels(server, _agent("alice"), {})

        text = result["content"][0]["text"]
        assert "town_square" in text
        assert "mafia_chat" in text
        assert "read" in text

    @pytest.mark.asyncio
    async def test_town_member_limited(self):
        state = _make_state("day")
        server = _FakeServer(_FakeSession(state))
        result = await handle_list_channels(server, _agent("bob"), {})

        text = result["content"][0]["text"]
        assert "town_square" in text
        assert "mafia_chat" not in text  # hidden from non-members

    @pytest.mark.asyncio
    async def test_phase_affects_permissions(self):
        # Day phase: night_only has no write access
        state = _make_state("day")
        server = _FakeServer(_FakeSession(state))
        result = await handle_list_channels(server, _agent("alice"), {})
        text = result["content"][0]["text"]
        # night_only should show read but not write during day
        assert "night_only" in text


# ===========================================================================
# get_messages handler
# ===========================================================================


class TestGetMessages:
    @pytest.mark.asyncio
    async def test_read_public_channel(self):
        state = _make_state()
        state = state.add_message(
            Message(
                id="msg-0",
                channel="town_square",
                sender="alice",
                content="Hello!",
                round=1,
                phase="day",
            )
        )
        server = _FakeServer(_FakeSession(state))
        result = await handle_get_messages(
            server, _agent("bob"), {"channel": "town_square"}
        )
        text = result["content"][0]["text"]
        assert "alice" in text
        assert "Hello!" in text

    @pytest.mark.asyncio
    async def test_read_group_channel_as_member(self):
        state = _make_state()
        state = state.add_message(
            Message(
                id="msg-0",
                channel="mafia_chat",
                sender="alice",
                content="Secret plan",
                round=1,
                phase="night",
            )
        )
        server = _FakeServer(_FakeSession(state))
        result = await handle_get_messages(
            server, _agent("alice"), {"channel": "mafia_chat"}
        )
        text = result["content"][0]["text"]
        assert "Secret plan" in text

    @pytest.mark.asyncio
    async def test_read_group_channel_denied(self):
        state = _make_state()
        state = state.add_message(
            Message(
                id="msg-0",
                channel="mafia_chat",
                sender="alice",
                content="Secret plan",
                round=1,
                phase="night",
            )
        )
        server = _FakeServer(_FakeSession(state))
        result = await handle_get_messages(
            server, _agent("bob"), {"channel": "mafia_chat"}
        )
        assert result.get("isError") is True
        assert "cannot read" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_limit_applied(self):
        state = _make_state()
        for i in range(10):
            state = state.add_message(
                Message(
                    id=f"msg-{i}",
                    channel="town_square",
                    sender="alice",
                    content=f"Msg {i}",
                    round=1,
                    phase="day",
                )
            )
        server = _FakeServer(_FakeSession(state))
        result = await handle_get_messages(
            server, _agent("bob"), {"channel": "town_square", "limit": 3}
        )
        text = result["content"][0]["text"]
        # Should only have last 3 messages
        assert "Msg 7" in text
        assert "Msg 8" in text
        assert "Msg 9" in text
        assert "Msg 0" not in text

    @pytest.mark.asyncio
    async def test_empty_channel(self):
        state = _make_state()
        server = _FakeServer(_FakeSession(state))
        result = await handle_get_messages(
            server, _agent("bob"), {"channel": "town_square"}
        )
        text = result["content"][0]["text"]
        assert "No messages" in text


# ===========================================================================
# get_all_messages handler
# ===========================================================================


class TestGetAllMessages:
    @pytest.mark.asyncio
    async def test_sees_only_visible(self):
        state = _make_state()
        state = state.add_message(
            Message(
                id="msg-0",
                channel="town_square",
                sender="alice",
                content="Public hello",
                round=1,
                phase="day",
            )
        )
        state = state.add_message(
            Message(
                id="msg-1",
                channel="mafia_chat",
                sender="alice",
                content="Secret plan",
                round=1,
                phase="day",
            )
        )
        server = _FakeServer(_FakeSession(state))

        # Bob (town) should only see public
        result = await handle_get_all_messages(server, _agent("bob"), {})
        text = result["content"][0]["text"]
        assert "Public hello" in text
        assert "Secret plan" not in text

        # Alice (mafia) should see both
        result = await handle_get_all_messages(server, _agent("alice"), {})
        text = result["content"][0]["text"]
        assert "Public hello" in text
        assert "Secret plan" in text

    @pytest.mark.asyncio
    async def test_channel_labels_in_output(self):
        state = _make_state()
        state = state.add_message(
            Message(
                id="msg-0",
                channel="town_square",
                sender="bob",
                content="Hi all",
                round=1,
                phase="day",
            )
        )
        server = _FakeServer(_FakeSession(state))
        result = await handle_get_all_messages(server, _agent("bob"), {})
        text = result["content"][0]["text"]
        assert "[town_square]" in text

    @pytest.mark.asyncio
    async def test_limit_applied(self):
        state = _make_state()
        for i in range(20):
            state = state.add_message(
                Message(
                    id=f"msg-{i}",
                    channel="town_square",
                    sender="alice",
                    content=f"Msg {i}",
                    round=1,
                    phase="day",
                )
            )
        server = _FakeServer(_FakeSession(state))
        result = await handle_get_all_messages(server, _agent("bob"), {"limit": 5})
        text = result["content"][0]["text"]
        assert "Msg 15" in text
        assert "Msg 19" in text
        assert "Msg 0" not in text


# ===========================================================================
# Tool schema integration
# ===========================================================================


class TestChannelToolsInSchema:
    def test_channel_tools_in_universal(self):
        """Channel query tools appear in universal tools list."""
        from mcp.schema import generate_game_tools

        tools = generate_game_tools(_COMPILED)
        names = {t.name for t in tools}
        assert "list_channels" in names
        assert "get_messages" in names
        assert "get_all_messages" in names

    def test_channel_tools_always_shown(self):
        """Channel query tools are type=query, so always shown in-game."""
        from mcp.schema import filter_tools_for_phase, generate_game_tools

        tools = generate_game_tools(_COMPILED)
        state = _make_state("day")
        filtered = filter_tools_for_phase(tools, state, _COMPILED, "alice")
        names = {t.name for t in filtered}
        assert "list_channels" in names
        assert "get_messages" in names
        assert "get_all_messages" in names
