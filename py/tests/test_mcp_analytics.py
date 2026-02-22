"""Tests for MCP analytics, history, and replay tools."""

from __future__ import annotations

from unittest import mock

import pytest

from mcp.agents import AgentState
from mcp.agents import reset_all as reset_agents
from mcp.handlers.analytics import (
    handle_game_balance,
    handle_head_to_head,
    handle_leaderboard,
    handle_my_stats,
    handle_platform_stats,
)
from mcp.handlers.history import (
    handle_get_game_replay,
    handle_list_public_replays,
    handle_my_game_history,
)
from server.analytics import invalidate_cache


@pytest.fixture(autouse=True)
def _clean():
    reset_agents()
    yield
    reset_agents()


# ---------------------------------------------------------------------------
# Fake data
# ---------------------------------------------------------------------------

_ARCHIVES = [
    {
        "game_id": "duel",
        "players": ["alice", "bob"],
        "decisions": [
            {"type": "deal", "deal_id": "attack", "actor": "alice", "target": "bob"},
            {"type": "advance_phase"},
        ],
        "metadata": {"winner": "alice", "session_id": "game-1"},
        "timestamp": 1000,
    },
    {
        "game_id": "duel",
        "players": ["alice", "carol"],
        "decisions": [
            {"type": "deal", "deal_id": "attack", "actor": "carol", "target": "alice"},
        ],
        "metadata": {"winner": "carol", "session_id": "game-2"},
        "timestamp": 2000,
    },
    {
        "game_id": "mafia",
        "players": ["alice", "bob", "carol", "dave"],
        "decisions": [
            {"type": "vote", "vote_id": "lynch", "voter": "alice", "option": "bob"},
            {"type": "advance_phase"},
            {"type": "deal", "deal_id": "investigate", "actor": "alice"},
        ],
        "metadata": {"winner": "alice", "session_id": "game-3"},
        "timestamp": 3000,
    },
]

_ARCHIVE_SUMMARIES = [
    {
        "session_id": "game-3",
        "game_id": "mafia",
        "players": ["alice", "bob", "carol", "dave"],
        "decisions": 3,
        "timestamp": 3000,
        "metadata": {"winner": "alice", "session_id": "game-3"},
        "path": "/fake/game-3.json",
    },
    {
        "session_id": "game-2",
        "game_id": "duel",
        "players": ["alice", "carol"],
        "decisions": 1,
        "timestamp": 2000,
        "metadata": {"winner": "carol", "session_id": "game-2"},
        "path": "/fake/game-2.json",
    },
    {
        "session_id": "game-1",
        "game_id": "duel",
        "players": ["alice", "bob"],
        "decisions": 2,
        "timestamp": 1000,
        "metadata": {"winner": "alice", "session_id": "game-1"},
        "path": "/fake/game-1.json",
    },
]


def _patch_analytics():
    """Patch analytics to use fake archives."""
    invalidate_cache()
    return mock.patch(
        "server.analytics.load_all_archives", return_value=list(_ARCHIVES)
    )


def _patch_history():
    """Patch history list_archives in the handler module where it's imported."""
    return mock.patch(
        "mcp.handlers.history.list_archives", return_value=list(_ARCHIVE_SUMMARIES)
    )


class _FakeServer:
    pass


def _agent(name: str = "alice") -> AgentState:
    return AgentState(agent_id=name)


# ===========================================================================
# Analytics handlers
# ===========================================================================


class TestMyStats:
    @pytest.mark.asyncio
    async def test_returns_stats(self):
        with _patch_analytics():
            result = await handle_my_stats(_FakeServer(), _agent("alice"), {})
        text = result["content"][0]["text"]
        assert "alice" in text
        assert "Rating" in text
        assert "Wins" in text or "wins" in text.lower()

    @pytest.mark.asyncio
    async def test_no_games(self):
        with _patch_analytics():
            result = await handle_my_stats(_FakeServer(), _agent("unknown"), {})
        text = result["content"][0]["text"]
        assert "No stats" in text


class TestPlatformStats:
    @pytest.mark.asyncio
    async def test_returns_overview(self):
        with _patch_analytics():
            result = await handle_platform_stats(_FakeServer(), _agent(), {})
        text = result["content"][0]["text"]
        assert "Total games: 3" in text
        assert "duel" in text


class TestHeadToHead:
    @pytest.mark.asyncio
    async def test_with_games(self):
        with _patch_analytics():
            result = await handle_head_to_head(
                _FakeServer(), _agent("alice"), {"opponent": "bob"}
            )
        text = result["content"][0]["text"]
        assert "alice" in text
        assert "bob" in text
        assert "wins" in text.lower()

    @pytest.mark.asyncio
    async def test_no_opponent(self):
        with _patch_analytics():
            result = await handle_head_to_head(
                _FakeServer(), _agent("alice"), {"opponent": "nobody"}
            )
        text = result["content"][0]["text"]
        assert "No games" in text


class TestGameBalance:
    @pytest.mark.asyncio
    async def test_known_game(self):
        with _patch_analytics():
            result = await handle_game_balance(
                _FakeServer(), _agent(), {"game_type": "duel"}
            )
        text = result["content"][0]["text"]
        assert "duel" in text
        assert "Games played: 2" in text

    @pytest.mark.asyncio
    async def test_unknown_game(self):
        with _patch_analytics():
            result = await handle_game_balance(
                _FakeServer(), _agent(), {"game_type": "nonexistent"}
            )
        text = result["content"][0]["text"]
        assert "No data" in text


class TestLeaderboard:
    @pytest.mark.asyncio
    async def test_returns_ranked(self):
        with _patch_analytics():
            result = await handle_leaderboard(_FakeServer(), _agent(), {"limit": 5})
        text = result["content"][0]["text"]
        assert "Leaderboard" in text
        # alice has 2 wins out of 3, should be ranked
        assert "alice" in text

    @pytest.mark.asyncio
    async def test_empty(self):
        invalidate_cache()
        with mock.patch("server.analytics.load_all_archives", return_value=[]):
            result = await handle_leaderboard(_FakeServer(), _agent(), {})
        text = result["content"][0]["text"]
        assert "No players" in text


# ===========================================================================
# History handlers
# ===========================================================================


class TestMyGameHistory:
    @pytest.mark.asyncio
    async def test_returns_own_games(self):
        with _patch_history():
            result = await handle_my_game_history(
                _FakeServer(), _agent("alice"), {"limit": 10}
            )
        text = result["content"][0]["text"]
        assert "game-1" in text
        assert "game-3" in text
        assert "WIN" in text

    @pytest.mark.asyncio
    async def test_no_games(self):
        with _patch_history():
            result = await handle_my_game_history(_FakeServer(), _agent("unknown"), {})
        text = result["content"][0]["text"]
        assert "No completed games" in text

    @pytest.mark.asyncio
    async def test_limit(self):
        with _patch_history():
            result = await handle_my_game_history(
                _FakeServer(), _agent("alice"), {"limit": 1}
            )
        text = result["content"][0]["text"]
        # Should only have 1 game (most recent first)
        assert text.count("**game-") == 1


class TestListPublicReplays:
    @pytest.mark.asyncio
    async def test_all_replays(self):
        with _patch_history():
            result = await handle_list_public_replays(_FakeServer(), _agent(), {})
        text = result["content"][0]["text"]
        assert "game-1" in text
        assert "game-3" in text

    @pytest.mark.asyncio
    async def test_filter_by_game_type(self):
        with _patch_history():
            result = await handle_list_public_replays(
                _FakeServer(), _agent(), {"game_type": "mafia"}
            )
        text = result["content"][0]["text"]
        assert "game-3" in text
        assert "game-1" not in text


class TestGetGameReplay:
    @pytest.mark.asyncio
    async def test_found(self):
        import json
        import tempfile
        from pathlib import Path

        # Create a real temp archive file
        archive_data = _ARCHIVES[0]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(archive_data, f)
            path = f.name

        summaries = [
            {**_ARCHIVE_SUMMARIES[2], "path": path},
        ]

        with mock.patch("mcp.handlers.history.list_archives", return_value=summaries):
            result = await handle_get_game_replay(
                _FakeServer(), _agent(), {"session_id": "game-1"}
            )
        Path(path).unlink()

        text = result["content"][0]["text"]
        assert "Game Replay" in text
        assert "alice" in text
        assert "Decisions" in text

    @pytest.mark.asyncio
    async def test_not_found(self):
        with _patch_history():
            result = await handle_get_game_replay(
                _FakeServer(), _agent(), {"session_id": "nonexistent"}
            )
        assert result.get("isError") is True
        assert "No archive" in result["content"][0]["text"]


# ===========================================================================
# Tool list integration
# ===========================================================================


class TestGlobalToolsInSchema:
    def test_lobby_includes_global_tools(self):
        from mcp.server import MCPServer

        server = MCPServer()
        agent = AgentState(agent_id="test")
        tools = server._get_tools_for_agent(agent)
        names = {t.name for t in tools}
        assert "my_stats" in names
        assert "platform_stats" in names
        assert "leaderboard" in names
        assert "my_game_history" in names
        assert "list_public_replays" in names

    def test_tournament_includes_global_tools(self):
        from mcp.server import MCPServer

        server = MCPServer()
        agent = AgentState(agent_id="test")
        agent.to_tournament("t-1")
        tools = server._get_tools_for_agent(agent)
        names = {t.name for t in tools}
        assert "my_stats" in names
        assert "platform_stats" in names
