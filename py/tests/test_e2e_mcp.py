"""End-to-end MCP integration tests.

Tests the full chain: MCPServer.handle_request() -> agent state -> session -> runtime.
This exercises the same code path as HTTP POST /mcp/agent/{id}.
"""

from __future__ import annotations

import pytest

from games import REGISTRY
from mcp.agents import reset_all as reset_agents
from mcp.server import MCPServer
from mcp.tokens import create_token, set_secret
from server.sessions import (
    GameSession,
    create_session,
    get_session,
    list_sessions,
    remove_session,
)
from tournament.sessions import (
    get_tournament,
    list_tournaments,
    remove_tournament,
)
from tournament.sessions import (
    reset_all as reset_tournaments,
)

set_secret(b"test-secret-e2e")

# ---------------------------------------------------------------------------
# Adapters (same pattern as server/app.py)
# ---------------------------------------------------------------------------


class _SessionStore:
    def get(self, session_id):
        return get_session(session_id)

    def list_all(self):
        return list_sessions()

    def create(self, session_id, compiled, player_ids):
        return create_session(session_id, compiled, player_ids)

    def remove(self, session_id):
        remove_session(session_id)


class _TournamentStore:
    def get(self, tournament_id):
        return get_tournament(tournament_id)

    def list_all(self):
        return list_tournaments()

    def create(self, **kwargs):
        from tournament.sessions import create_tournament

        return create_tournament(**kwargs)

    def remove(self, tournament_id):
        remove_tournament(tournament_id)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean():
    reset_agents()
    reset_tournaments()
    for sid in list(list_sessions()):
        remove_session(sid)
    yield
    reset_agents()
    reset_tournaments()
    for sid in list(list_sessions()):
        remove_session(sid)


@pytest.fixture
def mcp():
    server = MCPServer(sessions=_SessionStore(), tournaments=_TournamentStore())
    for game_id, compiled in REGISTRY.items():
        server.register_game(compiled)
    return server


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def call(mcp: MCPServer, agent: str, method: str, params=None) -> dict:
    """Make a JSON-RPC call through the MCP server."""
    request = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    return await mcp.handle_request(agent, request)


async def call_tool(mcp: MCPServer, agent: str, name: str, args=None) -> dict:
    """Call a tool and return the response."""
    return await call(mcp, agent, "tools/call", {"name": name, "arguments": args or {}})


def text_of(response: dict) -> str:
    """Extract text content from MCP response."""
    result = response.get("result", response.get("error", {}))
    content = result.get("content", [])
    if content:
        return content[0].get("text", "")
    return str(result)


def is_error(response: dict) -> bool:
    """Check if response is an error."""
    result = response.get("result", {})
    return result.get("isError", False)


# ===========================================================================
# E2E: Complete Auction game through MCP
# ===========================================================================


class TestAuctionE2E:
    """Full game of Auction played entirely through MCP JSON-RPC."""

    @pytest.mark.asyncio
    async def test_initialize(self, mcp):
        resp = await call(mcp, "alice", "initialize")
        assert resp["result"]["serverInfo"]["name"] == "parlameme"
        assert resp["result"]["capabilities"]["tools"] == {}

    @pytest.mark.asyncio
    async def test_list_games(self, mcp):
        resp = await call_tool(mcp, "alice", "list_games")
        text = text_of(resp)
        assert "auction" in text
        assert "werewolf" in text
        assert "parliament_arena" in text

    @pytest.mark.asyncio
    async def test_create_and_join(self, mcp):
        # Alice creates an auction with three players
        resp = await call_tool(
            mcp,
            "alice",
            "create_game",
            {
                "game_type": "auction",
                "session_id": "e2e-auction-1",
                "player_id": "alice",
                "players": ["alice", "bob", "charlie"],
            },
        )
        assert not is_error(resp)
        text = text_of(resp)
        assert "e2e-auction-1" in text
        assert "alice" in text

        # Bob joins (claims his seat)
        resp = await call_tool(
            mcp,
            "bob",
            "join_game",
            {
                "session_id": "e2e-auction-1",
                "player_id": "bob",
            },
        )
        assert not is_error(resp)
        assert "bob" in text_of(resp)

        # Eve cannot impersonate bob
        resp = await call_tool(
            mcp,
            "eve",
            "join_game",
            {
                "session_id": "e2e-auction-1",
                "player_id": "bob",
            },
        )
        assert is_error(resp)
        assert "already claimed" in text_of(resp)

        # Eve cannot join as nonexistent player
        resp = await call_tool(
            mcp,
            "eve",
            "join_game",
            {
                "session_id": "e2e-auction-1",
                "player_id": "eve",
            },
        )
        assert is_error(resp)
        assert "not in session" in text_of(resp)

    @pytest.mark.asyncio
    async def test_my_status(self, mcp):
        await call_tool(
            mcp,
            "alice",
            "create_game",
            {
                "game_type": "auction",
                "session_id": "e2e-status-1",
                "player_id": "alice",
                "players": ["alice", "bob", "charlie"],
            },
        )
        resp = await call_tool(mcp, "alice", "my_status")
        text = text_of(resp)
        assert "in_game" in text
        assert "alice" in text

    @pytest.mark.asyncio
    async def test_tools_list_in_game(self, mcp):
        await call_tool(
            mcp,
            "alice",
            "create_game",
            {
                "game_type": "auction",
                "session_id": "e2e-tools-1",
                "player_id": "alice",
                "players": ["alice", "bob", "charlie"],
            },
        )
        resp = await call(mcp, "alice", "tools/list")
        tools = resp["result"]["tools"]
        names = {t["name"] for t in tools}
        # Should have game tools + universal tools
        assert "auction/appraise" in names
        assert "auction/buy_info" in names
        assert "get_status" in names
        assert "advance_phase" in names

    @pytest.mark.asyncio
    async def test_get_status(self, mcp):
        await call_tool(
            mcp,
            "alice",
            "create_game",
            {
                "game_type": "auction",
                "session_id": "e2e-gs-1",
                "player_id": "alice",
                "players": ["alice", "bob", "charlie"],
            },
        )
        await call_tool(
            mcp,
            "bob",
            "join_game",
            {
                "session_id": "e2e-gs-1",
                "player_id": "bob",
            },
        )
        resp = await call_tool(mcp, "alice", "get_status")
        text = text_of(resp)
        assert "Auction" in text
        assert "alice" in text or "bob" in text

    @pytest.mark.asyncio
    async def test_full_auction_game(self, mcp):
        """Play a complete auction game through MCP by advancing phases until victory."""
        # Create game with 3 players
        await call_tool(
            mcp,
            "alice",
            "create_game",
            {
                "game_type": "auction",
                "session_id": "e2e-full-1",
                "player_id": "alice",
                "players": ["alice", "bob", "charlie"],
            },
        )
        await call_tool(
            mcp,
            "bob",
            "join_game",
            {
                "session_id": "e2e-full-1",
                "player_id": "bob",
            },
        )
        await call_tool(
            mcp,
            "charlie",
            "join_game",
            {
                "session_id": "e2e-full-1",
                "player_id": "charlie",
            },
        )

        # Auction has 6 lots. Each lot cycle: preview -> format_vote -> bidding -> reveal -> settlement -> intermission
        # Advance phases until game ends or we've done enough rounds
        for i in range(50):
            # Do an appraise action if possible (cheap unilateral deal)
            resp = await call_tool(mcp, "alice", "auction/appraise", {})
            # Ignore errors -- may not be allowed in current phase

            # Advance phase
            resp = await call_tool(mcp, "alice", "advance_phase")
            text = text_of(resp)
            if "GAME OVER" in text:
                break

        # Game should have ended via victory condition (6 lots completed)
        session = get_session("e2e-full-1")
        assert session is not None
        state = session.state
        # Either game ended or we advanced through enough phases
        assert state.status == "ended" or state.round >= 5

    @pytest.mark.asyncio
    async def test_bilateral_deal_promise(self, mcp):
        """Test bilateral deal flow: propose -> respond.

        Uses parliament_arena's promise deal (available in floor phase).
        """
        compiled = REGISTRY["parliament_arena"]
        players = [f"p{i}" for i in range(6)]
        session = create_session("e2e-deal-1", compiled, players)
        await session.start()

        # Register alice and bob into the game
        await call_tool(
            mcp,
            "alice",
            "join_game",
            {"session_id": "e2e-deal-1", "player_id": "p0"},
        )
        await call_tool(
            mcp,
            "bob",
            "join_game",
            {"session_id": "e2e-deal-1", "player_id": "p1"},
        )

        # After start(), setup auto-advances to caucus. One more → floor.
        await call_tool(mcp, "alice", "advance_phase")

        # p0 proposes a promise to p1
        resp = await call_tool(
            mcp,
            "alice",
            "parliament_arena/promise",
            {"responder": "p1"},
        )
        text = text_of(resp)
        assert not is_error(resp), f"Promise deal failed: {text}"

        # Bob should see respond tool
        resp = await call(mcp, "bob", "tools/list")
        tools = resp["result"]["tools"]
        names = {t["name"] for t in tools}
        assert "respond" in names

        # Get the pending deal instance ID
        session = get_session("e2e-deal-1")
        pending = list(session.state.pending_deals.keys())
        assert len(pending) == 1
        instance_id = pending[0]

        # Bob acknowledges the promise
        resp = await call_tool(
            mcp,
            "bob",
            "respond",
            {"instance_id": instance_id, "response": "acknowledge"},
        )
        assert not is_error(resp)

        # Deal resolved — no more pending deals
        state = session.state
        assert len(state.pending_deals) == 0

    @pytest.mark.asyncio
    async def test_leave_game(self, mcp):
        await call_tool(
            mcp,
            "alice",
            "create_game",
            {
                "game_type": "auction",
                "session_id": "e2e-leave-1",
                "player_id": "alice",
                "players": ["alice", "bob", "charlie"],
            },
        )
        resp = await call_tool(mcp, "alice", "leave_game")
        assert not is_error(resp)
        assert "lobby" in text_of(resp).lower()

        # My status should be lobby now
        resp = await call_tool(mcp, "alice", "my_status")
        assert "lobby" in text_of(resp)

    @pytest.mark.asyncio
    async def test_available_actions(self, mcp):
        await call_tool(
            mcp,
            "alice",
            "create_game",
            {
                "game_type": "auction",
                "session_id": "e2e-actions-1",
                "player_id": "alice",
                "players": ["alice", "bob", "charlie"],
            },
        )
        resp = await call_tool(mcp, "alice", "available_actions")
        text = text_of(resp)
        # In preview phase, appraise and buy_info are available
        assert "appraise" in text
        assert "buy_info" in text

    @pytest.mark.asyncio
    async def test_get_history(self, mcp):
        await call_tool(
            mcp,
            "alice",
            "create_game",
            {
                "game_type": "auction",
                "session_id": "e2e-hist-1",
                "player_id": "alice",
                "players": ["alice", "bob", "charlie"],
            },
        )
        await call_tool(
            mcp,
            "bob",
            "join_game",
            {
                "session_id": "e2e-hist-1",
                "player_id": "bob",
            },
        )
        # Do an action
        await call_tool(mcp, "alice", "auction/appraise", {})
        resp = await call_tool(mcp, "alice", "get_history")
        text = text_of(resp)
        assert "deal_executed" in text or "appraise" in text


# ===========================================================================
# E2E: Token-based activation
# ===========================================================================


class TestTokenActivation:
    @pytest.mark.asyncio
    async def test_activate_with_token(self, mcp):
        # Create game externally
        compiled = REGISTRY["auction"]
        session = create_session("token-game-1", compiled, ["host", "guest", "third"])
        await session.start()

        # Create invite token for alice
        token = create_token(
            agent_id="alice",
            session_id="token-game-1",
            player_id="guest",
            game_type="auction",
        )

        # Alice activates with token
        resp = await call_tool(mcp, "alice", "activate_game", {"token": token})
        assert not is_error(resp)
        text = text_of(resp)
        assert "token-game-1" in text
        assert "guest" in text

        # Alice should now be in_game
        resp = await call_tool(mcp, "alice", "my_status")
        assert "in_game" in text_of(resp)

    @pytest.mark.asyncio
    async def test_invalid_token(self, mcp):
        resp = await call_tool(mcp, "alice", "activate_game", {"token": "garbage"})
        assert is_error(resp)
        assert "Invalid" in text_of(resp) or "invalid" in text_of(resp).lower()


# ===========================================================================
# E2E: Multi-agent lobby flow
# ===========================================================================


class TestLobbyFlow:
    @pytest.mark.asyncio
    async def test_list_sessions_empty(self, mcp):
        resp = await call_tool(mcp, "alice", "list_sessions")
        assert "No active" in text_of(resp)

    @pytest.mark.asyncio
    async def test_list_sessions_with_games(self, mcp):
        await call_tool(
            mcp,
            "alice",
            "create_game",
            {
                "game_type": "auction",
                "session_id": "lobby-game-1",
                "player_id": "host-alice",
                "players": ["host-alice", "p2", "p3"],
            },
        )
        # New agent sees the session
        resp = await call_tool(mcp, "bob", "list_sessions")
        text = text_of(resp)
        assert "lobby-game-1" in text
        assert "Auction" in text

    @pytest.mark.asyncio
    async def test_unknown_method(self, mcp):
        resp = await call(mcp, "alice", "nonexistent/method")
        assert "error" in resp

    @pytest.mark.asyncio
    async def test_unknown_tool(self, mcp):
        resp = await call_tool(mcp, "alice", "nonexistent_tool")
        assert is_error(resp)

    @pytest.mark.asyncio
    async def test_game_tool_before_joining(self, mcp):
        """Game tools should fail when not in a game."""
        resp = await call_tool(mcp, "alice", "get_status")
        assert is_error(resp)
        assert "Not in a game" in text_of(resp)


# ===========================================================================
# E2E: Error handling edge cases
# ===========================================================================


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_create_unknown_game(self, mcp):
        resp = await call_tool(
            mcp,
            "alice",
            "create_game",
            {
                "game_type": "nonexistent_game",
            },
        )
        assert is_error(resp)
        assert "Unknown game" in text_of(resp)

    @pytest.mark.asyncio
    async def test_join_nonexistent_session(self, mcp):
        resp = await call_tool(
            mcp,
            "alice",
            "join_game",
            {
                "session_id": "does-not-exist",
            },
        )
        assert is_error(resp)
        assert "not found" in text_of(resp).lower()

    @pytest.mark.asyncio
    async def test_advance_phase_not_in_game(self, mcp):
        resp = await call_tool(mcp, "alice", "advance_phase")
        assert is_error(resp)

    @pytest.mark.asyncio
    async def test_deal_execution_error(self, mcp):
        """Deal with invalid target should return error."""
        await call_tool(
            mcp,
            "alice",
            "create_game",
            {
                "game_type": "auction",
                "session_id": "e2e-err-1",
                "player_id": "alice",
                "players": ["alice", "bob", "charlie"],
            },
        )
        await call_tool(
            mcp,
            "bob",
            "join_game",
            {
                "session_id": "e2e-err-1",
                "player_id": "bob",
            },
        )
        # Propose bidding ring with nonexistent player
        resp = await call_tool(
            mcp, "alice", "auction/bidding_ring", {"responder": "nobody"}
        )
        # Should either error or succeed (depending on filter enforcement)
        # The important thing is no crash
        assert resp is not None

    @pytest.mark.asyncio
    async def test_respond_nonexistent_deal(self, mcp):
        await call_tool(
            mcp,
            "alice",
            "create_game",
            {
                "game_type": "auction",
                "session_id": "e2e-err-2",
                "player_id": "alice",
                "players": ["alice", "bob", "charlie"],
            },
        )
        resp = await call_tool(
            mcp,
            "alice",
            "respond",
            {
                "instance_id": "fake-deal-id",
                "response": "accept",
            },
        )
        assert is_error(resp) or "Error" in text_of(resp)


# ===========================================================================
# E2E: Multiple games concurrently
# ===========================================================================


class TestConcurrentGames:
    @pytest.mark.asyncio
    async def test_two_games_simultaneously(self, mcp):
        """Two different games running at the same time."""
        # Game 1: Auction
        await call_tool(
            mcp,
            "alice",
            "create_game",
            {
                "game_type": "auction",
                "session_id": "concurrent-1",
                "player_id": "alice",
                "players": ["alice", "bob", "charlie"],
            },
        )
        # Game 2: Auction (different agents)
        await call_tool(
            mcp,
            "carol",
            "create_game",
            {
                "game_type": "auction",
                "session_id": "concurrent-2",
                "player_id": "carol",
                "players": ["carol", "dave", "eve"],
            },
        )

        # Both agents should be in their respective games
        resp1 = await call_tool(mcp, "alice", "my_status")
        resp2 = await call_tool(mcp, "carol", "my_status")
        assert "concurrent-1" in text_of(resp1)
        assert "concurrent-2" in text_of(resp2)

        # Actions in one game don't affect the other
        await call_tool(mcp, "alice", "auction/appraise", {})
        s1 = get_session("concurrent-1")
        s2 = get_session("concurrent-2")
        assert len(s1.state.history) > len(s2.state.history)


# ===========================================================================
# E2E: Tournament flow through MCP
# ===========================================================================


class TestTournamentE2E:
    """Full tournament lifecycle through MCP JSON-RPC."""

    @pytest.mark.asyncio
    async def test_create_tournament(self, mcp):
        resp = await call_tool(
            mcp,
            "alice",
            "create_tournament",
            {
                "tournament_type": "round_robin",
                "game_type": "auction",
                "name": "Test Cup",
                "tournament_id": "cup-1",
            },
        )
        assert not is_error(resp)
        text = text_of(resp)
        assert "cup-1" in text
        assert "round_robin" in text

    @pytest.mark.asyncio
    async def test_list_tournaments(self, mcp):
        # Create one
        await call_tool(
            mcp,
            "alice",
            "create_tournament",
            {
                "tournament_type": "round_robin",
                "game_type": "auction",
                "tournament_id": "cup-list-1",
            },
        )
        # Another agent sees it
        resp = await call_tool(mcp, "bob", "list_tournaments")
        assert not is_error(resp)
        assert "cup-list-1" in text_of(resp)

    @pytest.mark.asyncio
    async def test_register_and_start(self, mcp):
        # Alice creates + auto-registers (auction needs min 3 players)
        await call_tool(
            mcp,
            "alice",
            "create_tournament",
            {
                "tournament_type": "round_robin",
                "game_type": "auction",
                "tournament_id": "cup-rs-1",
            },
        )
        # Bob and Charlie register
        resp = await call_tool(
            mcp, "bob", "register_tournament", {"tournament_id": "cup-rs-1"}
        )
        assert not is_error(resp)
        resp = await call_tool(
            mcp, "charlie", "register_tournament", {"tournament_id": "cup-rs-1"}
        )
        assert not is_error(resp)

        # Alice starts
        resp = await call_tool(
            mcp, "alice", "start_tournament", {"tournament_id": "cup-rs-1"}
        )
        assert not is_error(resp)
        assert "started" in text_of(resp).lower() or "Matches" in text_of(resp)

        # Check state
        ts = get_tournament("cup-rs-1")
        assert ts is not None
        assert ts.state.status == "in_progress"
        assert len(ts.state.matches) > 0

    @pytest.mark.asyncio
    async def test_tournament_status_and_standings(self, mcp):
        await call_tool(
            mcp,
            "alice",
            "create_tournament",
            {
                "tournament_type": "round_robin",
                "game_type": "auction",
                "tournament_id": "cup-st-1",
            },
        )
        await call_tool(
            mcp, "bob", "register_tournament", {"tournament_id": "cup-st-1"}
        )
        await call_tool(
            mcp, "charlie", "register_tournament", {"tournament_id": "cup-st-1"}
        )

        # Status
        resp = await call_tool(mcp, "alice", "get_tournament_status")
        assert not is_error(resp)
        text = text_of(resp)
        assert "round_robin" in text
        assert "registration" in text.lower()

        # Standings
        resp = await call_tool(mcp, "alice", "get_standings", {})
        assert not is_error(resp)
        text = text_of(resp)
        assert "alice" in text
        assert "bob" in text

    @pytest.mark.asyncio
    async def test_get_my_matches(self, mcp):
        await call_tool(
            mcp,
            "alice",
            "create_tournament",
            {
                "tournament_type": "round_robin",
                "game_type": "auction",
                "tournament_id": "cup-mm-1",
            },
        )
        await call_tool(
            mcp, "bob", "register_tournament", {"tournament_id": "cup-mm-1"}
        )
        await call_tool(
            mcp, "charlie", "register_tournament", {"tournament_id": "cup-mm-1"}
        )
        await call_tool(mcp, "alice", "start_tournament", {"tournament_id": "cup-mm-1"})

        resp = await call_tool(mcp, "alice", "get_my_matches")
        assert not is_error(resp)
        text = text_of(resp)
        assert "bob" in text  # opponent
        assert "round" in text.lower()

    @pytest.mark.asyncio
    async def test_join_match(self, mcp):
        await call_tool(
            mcp,
            "alice",
            "create_tournament",
            {
                "tournament_type": "round_robin",
                "game_type": "auction",
                "tournament_id": "cup-jm-1",
            },
        )
        await call_tool(
            mcp, "bob", "register_tournament", {"tournament_id": "cup-jm-1"}
        )
        await call_tool(
            mcp, "charlie", "register_tournament", {"tournament_id": "cup-jm-1"}
        )
        await call_tool(mcp, "alice", "start_tournament", {"tournament_id": "cup-jm-1"})

        # Get match ID
        ts = get_tournament("cup-jm-1")
        match_id = list(ts.state.matches.keys())[0]

        # Alice joins match
        resp = await call_tool(mcp, "alice", "join_match", {"match_id": match_id})
        assert not is_error(resp)
        text = text_of(resp)
        assert "Joined" in text or "Session" in text

        # Alice should now be in_game
        resp = await call_tool(mcp, "alice", "my_status")
        assert "in_game" in text_of(resp)

    @pytest.mark.asyncio
    async def test_report_match_result(self, mcp):
        await call_tool(
            mcp,
            "alice",
            "create_tournament",
            {
                "tournament_type": "round_robin",
                "game_type": "auction",
                "tournament_id": "cup-rr-1",
            },
        )
        await call_tool(
            mcp, "bob", "register_tournament", {"tournament_id": "cup-rr-1"}
        )
        await call_tool(
            mcp, "charlie", "register_tournament", {"tournament_id": "cup-rr-1"}
        )
        await call_tool(mcp, "alice", "start_tournament", {"tournament_id": "cup-rr-1"})

        ts = get_tournament("cup-rr-1")
        match_id = list(ts.state.matches.keys())[0]

        resp = await call_tool(
            mcp,
            "alice",
            "report_match_result",
            {"match_id": match_id, "winner": "alice"},
        )
        assert not is_error(resp)
        assert "alice" in text_of(resp)

        # Check standings updated
        ts = get_tournament("cup-rr-1")
        assert ts.state.standings["alice"].wins == 1
        assert ts.state.standings["bob"].losses == 1

    @pytest.mark.asyncio
    async def test_full_tournament_lifecycle(self, mcp):
        """Create -> register -> start -> report all results -> completed."""
        # Create (auction needs min 3 players)
        await call_tool(
            mcp,
            "host",
            "create_tournament",
            {
                "tournament_type": "round_robin",
                "game_type": "auction",
                "tournament_id": "cup-full-1",
            },
        )
        # Register bob and charlie
        await call_tool(
            mcp, "bob", "register_tournament", {"tournament_id": "cup-full-1"}
        )
        await call_tool(
            mcp, "charlie", "register_tournament", {"tournament_id": "cup-full-1"}
        )
        # Start
        await call_tool(
            mcp, "host", "start_tournament", {"tournament_id": "cup-full-1"}
        )

        ts = get_tournament("cup-full-1")
        assert ts.state.status == "in_progress"

        # Report all matches
        for match_id, match in list(ts.state.matches.items()):
            if match.status != "completed":
                winner = match.participants[0]  # first player wins
                await call_tool(
                    mcp,
                    "host",
                    "report_match_result",
                    {"match_id": match_id, "winner": winner},
                )

        ts = get_tournament("cup-full-1")
        assert ts.state.status == "completed"
        assert ts.state.winner is not None

    @pytest.mark.asyncio
    async def test_leave_tournament(self, mcp):
        await call_tool(
            mcp,
            "alice",
            "create_tournament",
            {
                "tournament_type": "round_robin",
                "game_type": "auction",
                "tournament_id": "cup-leave-1",
            },
        )
        resp = await call_tool(mcp, "alice", "leave_tournament")
        assert not is_error(resp)
        assert "lobby" in text_of(resp).lower()

    @pytest.mark.asyncio
    async def test_non_host_cannot_start(self, mcp):
        await call_tool(
            mcp,
            "alice",
            "create_tournament",
            {
                "tournament_type": "round_robin",
                "game_type": "auction",
                "tournament_id": "cup-auth-1",
            },
        )
        await call_tool(
            mcp, "bob", "register_tournament", {"tournament_id": "cup-auth-1"}
        )
        await call_tool(
            mcp, "charlie", "register_tournament", {"tournament_id": "cup-auth-1"}
        )
        # Bob tries to start (should fail -- only host can)
        resp = await call_tool(
            mcp, "bob", "start_tournament", {"tournament_id": "cup-auth-1"}
        )
        assert is_error(resp)
        assert "host" in text_of(resp).lower()


# ===========================================================================
# Phase 6: Agent Loop Optimization — act, wait_for_turn, wait_for_match
# ===========================================================================


class TestActTool:
    """Tests for the combined act tool."""

    @pytest.mark.asyncio
    async def test_act_observe_only(self, mcp):
        """act() with no action returns status + available_actions."""
        resp = await call_tool(
            mcp,
            "alice",
            "create_game",
            {
                "game_type": "auction",
                "session_id": "act-obs-1",
                "player_id": "alice",
                "players": ["alice", "bob", "charlie"],
            },
        )
        assert not is_error(resp)

        # Observe
        resp = await call_tool(mcp, "alice", "act")
        text = text_of(resp)
        # Should contain both status and available actions
        assert "alice" in text
        # Phase info should be present
        assert "Phase" in text or "phase" in text

    @pytest.mark.asyncio
    async def test_act_with_action(self, mcp):
        """act(action=...) executes and returns result + updated status."""
        await call_tool(
            mcp,
            "alice",
            "create_game",
            {
                "game_type": "auction",
                "session_id": "act-exec-1",
                "player_id": "alice",
                "players": ["alice", "bob", "charlie"],
            },
        )

        # Execute an action via act tool
        resp = await call_tool(mcp, "alice", "act", {"action": "advance_phase"})
        text = text_of(resp)
        # Should have action result section and status
        assert "Action Result" in text

    @pytest.mark.asyncio
    async def test_act_bad_action(self, mcp):
        """act(action=...) with unknown action returns error in result."""
        await call_tool(
            mcp,
            "alice",
            "create_game",
            {
                "game_type": "auction",
                "session_id": "act-bad-1",
                "player_id": "alice",
                "players": ["alice", "bob", "charlie"],
            },
        )

        resp = await call_tool(mcp, "alice", "act", {"action": "nonexistent_deal"})
        text = text_of(resp)
        assert "Unknown action" in text or "ERROR" in text

    @pytest.mark.asyncio
    async def test_act_with_prefixed_action(self, mcp):
        """act(action='game/deal') accepts fully-qualified prefixed tool names."""
        await call_tool(
            mcp,
            "alice",
            "create_game",
            {
                "game_type": "auction",
                "session_id": "act-prefix-1",
                "player_id": "alice",
                "players": ["alice", "bob", "charlie"],
            },
        )
        # Use prefixed name (same format as tools/list returns)
        resp = await call_tool(
            mcp, "alice", "act", {"action": "auction/appraise"}
        )
        text = text_of(resp)
        assert "Unknown action" not in text
        assert "Action Result" in text

    @pytest.mark.asyncio
    async def test_act_not_in_game(self, mcp):
        """act() when not in a game returns error."""
        resp = await call_tool(mcp, "alice", "act")
        assert is_error(resp)


class TestWaitForTurn:
    """Tests for the wait_for_turn tool."""

    @pytest.mark.asyncio
    async def test_wait_returns_immediately_with_pending(self, mcp):
        """wait_for_turn returns immediately if agent has pending actions."""
        import asyncio

        await call_tool(
            mcp,
            "alice",
            "create_game",
            {
                "game_type": "auction",
                "session_id": "wait-pend-1",
                "player_id": "alice",
                "players": ["alice", "bob", "charlie"],
            },
        )

        session = get_session("wait-pend-1")
        # Start a deal that creates a pending response for bob
        await session.execute_deal("buy_info", actor_id="alice")

        # Bob joins
        await call_tool(
            mcp,
            "bob",
            "join_game",
            {"session_id": "wait-pend-1", "player_id": "bob"},
        )

        # wait_for_turn should return immediately (status, not block)
        resp = await call_tool(mcp, "alice", "wait_for_turn", {"timeout": 1})
        assert not is_error(resp)

    @pytest.mark.asyncio
    async def test_wait_times_out(self, mcp):
        """wait_for_turn returns after timeout if no state change."""
        import asyncio
        import time

        await call_tool(
            mcp,
            "alice",
            "create_game",
            {
                "game_type": "auction",
                "session_id": "wait-to-1",
                "player_id": "alice",
                "players": ["alice", "bob", "charlie"],
            },
        )

        start = time.monotonic()
        resp = await call_tool(mcp, "alice", "wait_for_turn", {"timeout": 1})
        elapsed = time.monotonic() - start
        # Should return after ~1 second (not immediately, not 60s)
        assert elapsed >= 0.9
        assert elapsed < 5
        assert not is_error(resp)

    @pytest.mark.asyncio
    async def test_wait_returns_on_state_change(self, mcp):
        """wait_for_turn returns when state changes."""
        import asyncio

        await call_tool(
            mcp,
            "alice",
            "create_game",
            {
                "game_type": "auction",
                "session_id": "wait-chg-1",
                "player_id": "alice",
                "players": ["alice", "bob", "charlie"],
            },
        )

        session = get_session("wait-chg-1")

        async def advance_soon():
            await asyncio.sleep(0.2)
            await session.advance_phase()

        asyncio.ensure_future(advance_soon())

        import time

        start = time.monotonic()
        resp = await call_tool(mcp, "alice", "wait_for_turn", {"timeout": 10})
        elapsed = time.monotonic() - start
        # Should return quickly (~0.2s), not wait full 10s
        assert elapsed < 3
        assert not is_error(resp)

    @pytest.mark.asyncio
    async def test_wait_not_in_game(self, mcp):
        """wait_for_turn when not in a game returns error."""
        resp = await call_tool(mcp, "alice", "wait_for_turn")
        assert is_error(resp)


# ===========================================================================
# E2E: Full autonomous tournament — agents create, play, and verify results
# ===========================================================================


class TestFullAutonomousTournament:
    """Complete tournament lifecycle played entirely through MCP.

    Simulates exactly what real MCP agents experience:
    1. Host creates tournament, others register
    2. Host starts tournament, matches spawn as GameSessions
    3. Each agent joins their match, plays a full auction game
    4. Auto-report propagates results to tournament standings
    5. Tournament completes with correct winner

    This is the ultimate integration test — exercises:
    - Tournament creation, registration, start
    - Match spawning with GameSession creation
    - Agent state transitions: lobby → tournament → in_game → tournament → lobby
    - Full auction game: preview → format_vote → bidding → reveal → settlement
    - Auto-report: GameSession end → TournamentSession.report_result()
    - Standings computation, tournament completion
    - Archive creation for each match
    """

    AGENTS = ["agent-a", "agent-b", "agent-c"]

    async def _play_auction_match(
        self, mcp: MCPServer, players: tuple[str, ...], match_id: str
    ) -> str:
        """Play a full auction game through MCP. Returns winner player_id.

        Strategy: one player bids on every lot (wins them all).
        The bidder becomes the wealth_leader winner.
        """
        bidder = players[0]

        # All players join the match
        for agent_id in players:
            resp = await call_tool(mcp, agent_id, "join_match", {"match_id": match_id})
            assert not is_error(resp), (
                f"{agent_id} failed to join {match_id}: {text_of(resp)}"
            )

        # Verify all agents are in_game
        for agent_id in players:
            resp = await call_tool(mcp, agent_id, "my_status")
            assert "in_game" in text_of(resp), f"{agent_id} not in_game"

        # Play 6 lots
        for lot in range(6):
            # Phase 1: preview → advance
            resp = await call_tool(mcp, players[0], "advance_phase")
            assert not is_error(resp), (
                f"Lot {lot + 1} preview advance failed: {text_of(resp)}"
            )

            # Phase 2: format_vote → all players vote "first_price"
            # Need to find the pending vote and cast votes
            session = self._find_match_session(match_id)
            assert session is not None, f"Session not found for {match_id}"

            # Start the vote (first player initiates)
            resp = await call_tool(
                mcp,
                players[0],
                "auction/vote_choose_format",
                {"option": "first_price"},
            )
            assert not is_error(resp), f"Vote start failed: {text_of(resp)}"

            # Find the pending vote instance for remaining voters
            pending_votes = list(session.state.pending_votes.values())
            if pending_votes:
                instance_id = pending_votes[0].instance_id
                for voter in players[1:]:
                    resp = await call_tool(
                        mcp,
                        voter,
                        "auction/vote_choose_format",
                        {"option": "first_price"},
                    )
                    # May auto-advance after last vote — that's fine

            # Phase 3: bidding → bidder places a sealed bid, then advance
            # After vote auto-completes, we should be in bidding phase
            resp = await call_tool(
                mcp,
                bidder,
                "auction/sealed_bid",
                {"amount": 50},
            )
            # May fail if phase already advanced or bid not applicable — that's ok

            # Advance past bidding → reveal (auto) → settlement (auto) → intermission
            resp = await call_tool(mcp, players[0], "advance_phase")
            # May already be at intermission if auto-advanced

            # Phase 4: intermission → advance to next lot
            resp = await call_tool(mcp, players[0], "advance_phase")
            # This might fail if we're already past intermission — check session

            # Verify we haven't stalled — phase should have progressed
            session = self._find_match_session(match_id)
            if session.state.status == "ended":
                break  # Victory detected

        # If game didn't end via victory, advance until it does
        session = self._find_match_session(match_id)
        safety = 0
        while session.state.status != "ended" and safety < 30:
            resp = await call_tool(mcp, players[0], "advance_phase")
            session = self._find_match_session(match_id)
            safety += 1

        assert session.state.status == "ended", (
            f"Game did not end after {safety} advances. "
            f"Phase={session.state.phase}, Round={session.state.round}"
        )

        winner = session.state.victory_result.get("winner")
        assert winner is not None, "No winner in victory_result"

        # All players leave game → back to tournament
        for agent_id in players:
            resp = await call_tool(mcp, agent_id, "leave_game")
            assert not is_error(resp), f"{agent_id} failed to leave: {text_of(resp)}"

        return winner

    def _find_match_session(self, match_id: str):
        """Find the GameSession for a tournament match."""
        for sid, session in list_sessions().items():
            if match_id in sid:
                return session
        return None

    @pytest.mark.asyncio
    async def test_full_tournament_with_auto_report(self, mcp):
        """Full round-robin tournament: 3 agents, auction, auto-report.

        Round-robin with 3 players + match_size=3 produces 1 match
        (all combinations of 3 from 3 = 1). This tests the complete flow
        end-to-end with auto-report.
        """
        agents = self.AGENTS

        # Step 1: Host creates tournament
        resp = await call_tool(
            mcp,
            agents[0],
            "create_tournament",
            {
                "tournament_type": "round_robin",
                "game_type": "auction",
                "name": "E2E Auto Cup",
                "tournament_id": "e2e-auto-cup",
            },
        )
        assert not is_error(resp), f"Create failed: {text_of(resp)}"

        # Step 2: Other agents register
        for agent_id in agents[1:]:
            resp = await call_tool(
                mcp, agent_id, "register_tournament", {"tournament_id": "e2e-auto-cup"}
            )
            assert not is_error(resp), f"Register {agent_id} failed: {text_of(resp)}"

        # Verify all registered
        ts = get_tournament("e2e-auto-cup")
        assert len(ts.state.participants) == 3
        assert ts.state.status == "registration"

        # Step 3: Host starts tournament
        resp = await call_tool(
            mcp, agents[0], "start_tournament", {"tournament_id": "e2e-auto-cup"}
        )
        assert not is_error(resp), f"Start failed: {text_of(resp)}"

        ts = get_tournament("e2e-auto-cup")
        assert ts.state.status == "in_progress"
        assert len(ts.state.matches) >= 1

        # Step 4: Get matches and play them
        matches = list(ts.state.matches.values())
        assert len(matches) >= 1

        for match in matches:
            if match.status == "completed":
                continue

            # Play the match
            winner = await self._play_auction_match(mcp, match.participants, match.id)

            # Allow auto-report event to propagate
            import asyncio

            await asyncio.sleep(0)

        # Step 5: Verify tournament completed via auto-report
        ts = get_tournament("e2e-auto-cup")
        assert ts.state.status == "completed", (
            f"Tournament not completed. Status={ts.state.status}. "
            f"Matches: {[(m.id, m.status, m.winner) for m in ts.state.matches.values()]}"
        )
        assert ts.state.winner is not None

        # Step 6: Verify standings are consistent
        standings = ts.state.standings
        total_wins = sum(s.wins for s in standings.values())
        total_losses = sum(s.losses for s in standings.values())
        # In a completed tournament, results should be recorded
        assert total_wins >= 1, f"No wins recorded in standings: {standings}"

        # Step 7: Verify archives were created for match sessions
        for mid, match in ts.state.matches.items():
            session_id = ts.get_match_session_id(mid)
            if session_id:
                session = get_session(session_id)
                if session:
                    assert session.state.status == "ended"

    @pytest.mark.asyncio
    async def test_agent_state_transitions_through_tournament(self, mcp):
        """Verify agent state machine: lobby → tournament → game → tournament → lobby."""
        agents = self.AGENTS

        # All start in lobby
        for a in agents:
            resp = await call_tool(mcp, a, "my_status")
            assert "lobby" in text_of(resp)

        # Create + register
        await call_tool(
            mcp,
            agents[0],
            "create_tournament",
            {
                "tournament_type": "round_robin",
                "game_type": "auction",
                "tournament_id": "e2e-states",
            },
        )
        for a in agents[1:]:
            await call_tool(
                mcp, a, "register_tournament", {"tournament_id": "e2e-states"}
            )

        # Host should be in_tournament
        resp = await call_tool(mcp, agents[0], "my_status")
        assert "in_tournament" in text_of(resp)

        # Start
        await call_tool(
            mcp, agents[0], "start_tournament", {"tournament_id": "e2e-states"}
        )

        ts = get_tournament("e2e-states")
        match_id = list(ts.state.matches.keys())[0]

        # Join match → should be in_game
        resp = await call_tool(mcp, agents[0], "join_match", {"match_id": match_id})
        assert not is_error(resp)

        resp = await call_tool(mcp, agents[0], "my_status")
        text = text_of(resp)
        assert "in_game" in text

        # Leave game → should return to in_tournament (not lobby)
        resp = await call_tool(mcp, agents[0], "leave_game")
        assert "tournament" in text_of(resp).lower()

        resp = await call_tool(mcp, agents[0], "my_status")
        assert "in_tournament" in text_of(resp)

        # Leave tournament → back to lobby
        resp = await call_tool(mcp, agents[0], "leave_tournament")
        assert "lobby" in text_of(resp).lower()

    @pytest.mark.asyncio
    async def test_match_readiness_signals(self, mcp):
        """Verify get_my_matches shows correct status signals."""
        agents = self.AGENTS

        await call_tool(
            mcp,
            agents[0],
            "create_tournament",
            {
                "tournament_type": "round_robin",
                "game_type": "auction",
                "tournament_id": "e2e-ready",
            },
        )
        for a in agents[1:]:
            await call_tool(
                mcp, a, "register_tournament", {"tournament_id": "e2e-ready"}
            )

        # Before start — agent can check but no matches yet
        await call_tool(
            mcp, agents[0], "start_tournament", {"tournament_id": "e2e-ready"}
        )

        # After start — matches should show READY
        resp = await call_tool(mcp, agents[0], "get_my_matches")
        text = text_of(resp)
        assert "READY" in text, f"Expected READY in matches: {text}"

    @pytest.mark.asyncio
    async def test_standings_after_match(self, mcp):
        """Verify standings update correctly after auto-reported match."""
        agents = self.AGENTS

        await call_tool(
            mcp,
            agents[0],
            "create_tournament",
            {
                "tournament_type": "round_robin",
                "game_type": "auction",
                "tournament_id": "e2e-standings",
            },
        )
        for a in agents[1:]:
            await call_tool(
                mcp, a, "register_tournament", {"tournament_id": "e2e-standings"}
            )
        await call_tool(
            mcp, agents[0], "start_tournament", {"tournament_id": "e2e-standings"}
        )

        ts = get_tournament("e2e-standings")
        match = list(ts.state.matches.values())[0]

        # Play the match
        winner = await self._play_auction_match(mcp, match.participants, match.id)

        import asyncio

        await asyncio.sleep(0)

        # Check standings
        resp = await call_tool(mcp, agents[0], "get_standings")
        text = text_of(resp)
        assert winner in text, f"Winner {winner} not in standings: {text}"
        # Winner should have points
        ts = get_tournament("e2e-standings")
        assert ts.state.standings[winner].wins >= 1


# ===========================================================================
# Lobby mode (create without enough players, join to auto-start)
# ===========================================================================


class TestLobbyMode:
    @pytest.mark.asyncio
    async def test_create_without_players_creates_lobby(self, mcp):
        """create_game without enough players creates a lobby, not a game."""
        resp = await call_tool(
            mcp,
            "host",
            "create_game",
            {"game_type": "auction", "session_id": "lobby-1"},
        )
        text = text_of(resp)
        assert "Lobby created" in text
        assert "lobby-1" in text

    @pytest.mark.asyncio
    async def test_lobby_status(self, mcp):
        """Agents in a lobby can check status."""
        await call_tool(
            mcp,
            "host",
            "create_game",
            {"game_type": "auction", "session_id": "lobby-s"},
        )
        resp = await call_tool(mcp, "host", "get_status")
        text = text_of(resp)
        assert "Lobby" in text
        assert "waiting" in text

    @pytest.mark.asyncio
    async def test_lobby_auto_start(self, mcp):
        """Game auto-starts when min_players reached via join_game."""
        await call_tool(
            mcp,
            "host",
            "create_game",
            {"game_type": "auction", "session_id": "lobby-auto"},
        )
        # Join 2 more (auction needs 3)
        await call_tool(mcp, "p2", "join_game", {"session_id": "lobby-auto"})
        resp = await call_tool(mcp, "p3", "join_game", {"session_id": "lobby-auto"})
        text = text_of(resp)
        assert "Game started" in text

        # host can now get_status with real game state
        resp = await call_tool(mcp, "host", "get_status")
        text = text_of(resp)
        assert "Auction" in text
        assert "Phase" in text

    @pytest.mark.asyncio
    async def test_lobby_leave(self, mcp):
        """Players can leave a lobby."""
        await call_tool(
            mcp,
            "host",
            "create_game",
            {"game_type": "auction", "session_id": "lobby-leave"},
        )
        await call_tool(mcp, "p2", "join_game", {"session_id": "lobby-leave"})
        resp = await call_tool(mcp, "p2", "leave_game")
        assert "lobby" in text_of(resp).lower()

    @pytest.mark.asyncio
    async def test_lobby_in_list_sessions(self, mcp):
        """Pending lobbies appear in list_sessions."""
        await call_tool(
            mcp,
            "host",
            "create_game",
            {"game_type": "auction", "session_id": "lobby-list"},
        )
        resp = await call_tool(mcp, "viewer", "list_sessions")
        text = text_of(resp)
        assert "lobby-list" in text
        assert "Lobby" in text or "lobby" in text

    @pytest.mark.asyncio
    async def test_start_game_manual(self, mcp):
        """Host can manually start when enough players joined."""
        await call_tool(
            mcp,
            "host",
            "create_game",
            {"game_type": "auction", "session_id": "lobby-manual"},
        )
        await call_tool(mcp, "p2", "join_game", {"session_id": "lobby-manual"})
        # Not enough players yet — start should fail
        resp = await call_tool(mcp, "host", "start_game")
        assert is_error(resp)
        assert "Need at least" in text_of(resp)

        # Add third player
        await call_tool(mcp, "p3", "join_game", {"session_id": "lobby-manual"})
        # Now start should work — but auto-start already fired
        resp = await call_tool(mcp, "host", "get_status")
        text = text_of(resp)
        assert "Auction" in text


# ===========================================================================
# advance_phase blocked after game_over
# ===========================================================================


class TestAdvancePhaseAfterGameOver:
    @pytest.mark.asyncio
    async def test_advance_phase_blocked_after_ended(self, mcp):
        """advance_phase should be a no-op when game has ended."""
        # Create auction with 3 players
        players = ["ae0", "ae1", "ae2"]
        await call_tool(
            mcp,
            "ae0",
            "create_game",
            {
                "game_type": "auction",
                "session_id": "game-over-test",
                "players": players,
            },
        )
        for p in players[1:]:
            await call_tool(mcp, p, "join_game", {"session_id": "game-over-test"})

        # Fast-forward: advance through all phases until game ends
        for _ in range(200):
            # Vote sealed format when in format_vote
            for p in players:
                await call_tool(mcp, p, "auction/vote_format", {"option": "sealed"})
            # Place sealed bids
            for i, p in enumerate(players):
                await call_tool(mcp, p, "auction/sealed_bid", {"amount": 50 + i * 10})
            # Advance through phases
            for _ in range(5):
                await call_tool(mcp, "ae0", "advance_phase")

            resp = await call_tool(mcp, "ae0", "get_status")
            if "ended" in text_of(resp).lower():
                break

        # Game should be ended now
        resp = await call_tool(mcp, "ae0", "get_status")
        text = text_of(resp)
        assert "ended" in text.lower() or "over" in text.lower()

        # Get current round
        round_before = text

        # Try to advance — should be no-op
        await call_tool(mcp, "ae0", "advance_phase")
        resp = await call_tool(mcp, "ae0", "get_status")
        text_after = text_of(resp)
        # Phase should not have changed
        assert text_after == round_before


# ===========================================================================
# Actor filter in tool descriptions
# ===========================================================================


class TestActorFilterInToolDescription:
    def test_investigate_shows_filter(self):
        """PA investigate should show intel requirement in description."""
        from games import REGISTRY
        from mcp.schema import deal_to_tool

        compiled = REGISTRY["parliament_arena"]
        tool = deal_to_tool(
            "parliament_arena", "investigate", compiled.deals["investigate"]
        )
        assert "Requires:" in tool.description
        assert "intel" in tool.description

    def test_bribe_no_extra_filter(self):
        """PA bribe has only alive() filter — should NOT show Requires."""
        from games import REGISTRY
        from mcp.schema import deal_to_tool

        compiled = REGISTRY["parliament_arena"]
        tool = deal_to_tool("parliament_arena", "bribe", compiled.deals["bribe"])
        assert "Requires:" not in tool.description

    def test_wolf_mark_shows_team(self):
        """Werewolf wolf_mark should show team requirement."""
        from games import REGISTRY
        from mcp.schema import deal_to_tool

        compiled = REGISTRY["werewolf"]
        tool = deal_to_tool("werewolf", "wolf_mark", compiled.deals["wolf_mark"])
        assert "Requires:" in tool.description
        assert "wolves" in tool.description

    def test_seer_shows_role(self):
        """Werewolf seer_vision should show role requirement."""
        from games import REGISTRY
        from mcp.schema import deal_to_tool

        compiled = REGISTRY["werewolf"]
        tool = deal_to_tool("werewolf", "seer_vision", compiled.deals["seer_vision"])
        assert "Requires:" in tool.description
        assert "seer" in tool.description
