"""Tests for MCP Streamable HTTP transport (spec 2025-03-26).

Tests the /mcp endpoint with session management via Mcp-Session-Id header.
Uses a lightweight FastAPI test app with the same _api router.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from games import REGISTRY
from mcp.agents import reset_all as reset_agents

# We need to import the app module to get the router and MCP server wired up.
# Import triggers module-level setup (mcp server, game registration, etc.)
from server.app import _api, _mcp_sessions, mcp
from server.sessions import (
    create_session,
    list_sessions,
    remove_session,
)
from tournament.sessions import reset_all as reset_tournaments

# ---------------------------------------------------------------------------
# Test app — just the API router, no NiceGUI pages
# ---------------------------------------------------------------------------

_test_app = FastAPI()
_test_app.include_router(_api)


@pytest.fixture(autouse=True)
def _clean():
    reset_agents()
    reset_tournaments()
    _mcp_sessions.clear()
    for sid in list(list_sessions()):
        remove_session(sid)
    yield
    reset_agents()
    reset_tournaments()
    _mcp_sessions.clear()
    for sid in list(list_sessions()):
        remove_session(sid)


@pytest.fixture
def client():
    return TestClient(_test_app)


def _jsonrpc(method: str, params: dict | None = None, req_id: int = 1) -> dict:
    msg: dict = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def _notification(method: str, params: dict | None = None) -> dict:
    msg: dict = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    return msg


# ---------------------------------------------------------------------------
# Helper: initialize and get session
# ---------------------------------------------------------------------------


def _initialize(client: TestClient) -> tuple[dict, str]:
    """Send initialize request, return (response_body, session_id)."""
    resp = client.post("/mcp", json=_jsonrpc("initialize"))
    assert resp.status_code == 200
    session_id = resp.headers.get("mcp-session-id")
    assert session_id is not None
    return resp.json(), session_id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestStreamableHTTPInitialize:
    def test_initialize_returns_session_id(self, client):
        body, session_id = _initialize(client)
        assert body["jsonrpc"] == "2.0"
        assert body["id"] == 1
        result = body["result"]
        assert result["protocolVersion"] == "2025-03-26"
        assert result["serverInfo"]["name"] == "parlameme"
        assert "tools" in result["capabilities"]
        assert len(session_id) > 10  # UUID

    def test_initialize_creates_agent(self, client):
        _, session_id = _initialize(client)
        assert session_id in _mcp_sessions
        agent_id = _mcp_sessions[session_id]
        assert agent_id.startswith("http-")

    def test_multiple_initializes_create_separate_sessions(self, client):
        _, sid1 = _initialize(client)
        _, sid2 = _initialize(client)
        assert sid1 != sid2
        assert _mcp_sessions[sid1] != _mcp_sessions[sid2]


class TestStreamableHTTPSession:
    def test_tools_list_with_session(self, client):
        _, session_id = _initialize(client)
        resp = client.post(
            "/mcp",
            json=_jsonrpc("tools/list"),
            headers={"Mcp-Session-Id": session_id},
        )
        assert resp.status_code == 200
        body = resp.json()
        tools = body["result"]["tools"]
        assert len(tools) > 0
        tool_names = [t["name"] for t in tools]
        assert "list_games" in tool_names

    def test_tools_call_list_games(self, client):
        _, session_id = _initialize(client)
        resp = client.post(
            "/mcp",
            json=_jsonrpc("tools/call", {"name": "list_games", "arguments": {}}),
            headers={"Mcp-Session-Id": session_id},
        )
        assert resp.status_code == 200
        body = resp.json()
        content = body["result"]["content"][0]["text"]
        assert "auction" in content.lower() or "Available" in content

    def test_request_without_session_returns_400(self, client):
        resp = client.post("/mcp", json=_jsonrpc("tools/list"))
        assert resp.status_code == 400

    def test_request_with_invalid_session_returns_400(self, client):
        resp = client.post(
            "/mcp",
            json=_jsonrpc("tools/list"),
            headers={"Mcp-Session-Id": "nonexistent-session"},
        )
        assert resp.status_code == 400

    def test_session_header_echoed_in_response(self, client):
        _, session_id = _initialize(client)
        resp = client.post(
            "/mcp",
            json=_jsonrpc("tools/list"),
            headers={"Mcp-Session-Id": session_id},
        )
        assert resp.headers.get("mcp-session-id") == session_id


class TestStreamableHTTPNotification:
    def test_notification_returns_202(self, client):
        _, session_id = _initialize(client)
        resp = client.post(
            "/mcp",
            json=_notification("notifications/initialized"),
            headers={"Mcp-Session-Id": session_id},
        )
        assert resp.status_code == 202


class TestStreamableHTTPBatch:
    def test_batch_request(self, client):
        _, session_id = _initialize(client)
        batch = [
            _jsonrpc("tools/list", req_id=10),
            _jsonrpc("tools/call", {"name": "list_games", "arguments": {}}, req_id=11),
        ]
        resp = client.post(
            "/mcp",
            json=batch,
            headers={"Mcp-Session-Id": session_id},
        )
        assert resp.status_code == 200
        body = resp.json()
        # Batch of 2 requests → array of 2 responses
        assert isinstance(body, list)
        assert len(body) == 2
        ids = {r["id"] for r in body}
        assert ids == {10, 11}

    def test_batch_with_notification(self, client):
        _, session_id = _initialize(client)
        batch = [
            _notification("notifications/initialized"),
            _jsonrpc("tools/list", req_id=20),
        ]
        resp = client.post(
            "/mcp",
            json=batch,
            headers={"Mcp-Session-Id": session_id},
        )
        assert resp.status_code == 200
        body = resp.json()
        # Only 1 request has response (notification is silent)
        assert isinstance(body, dict)  # single response unwrapped
        assert body["id"] == 20

    def test_batch_all_notifications(self, client):
        _, session_id = _initialize(client)
        batch = [
            _notification("notifications/initialized"),
            _notification("notifications/initialized"),
        ]
        resp = client.post(
            "/mcp",
            json=batch,
            headers={"Mcp-Session-Id": session_id},
        )
        assert resp.status_code == 202

    def test_batch_with_initialize_first(self, client):
        """Batch containing initialize + tools/list: initialize processed first."""
        batch = [
            _jsonrpc("initialize", req_id=1),
            _jsonrpc("tools/list", req_id=2),
        ]
        resp = client.post("/mcp", json=batch)
        assert resp.status_code == 200
        session_id = resp.headers.get("mcp-session-id")
        assert session_id is not None
        body = resp.json()
        assert isinstance(body, list)
        assert len(body) == 2
        # Both should succeed (initialize first, then tools/list with new session)
        ids = {r["id"] for r in body}
        assert ids == {1, 2}
        for r in body:
            assert "error" not in r, f"Unexpected error in response: {r}"

    def test_batch_with_initialize_not_first(self, client):
        """Batch where initialize is NOT first: still works because we reorder."""
        batch = [
            _jsonrpc("tools/list", req_id=10),
            _jsonrpc("initialize", req_id=11),
        ]
        resp = client.post("/mcp", json=batch)
        assert resp.status_code == 200
        session_id = resp.headers.get("mcp-session-id")
        assert session_id is not None
        body = resp.json()
        assert isinstance(body, list)
        assert len(body) == 2
        # initialize response should succeed
        init_resp = next(r for r in body if r["id"] == 11)
        assert "error" not in init_resp
        # tools/list should also succeed (initialize processed first)
        list_resp = next(r for r in body if r["id"] == 10)
        assert "error" not in list_resp


class TestStreamableHTTPGetDelete:
    def test_get_returns_405(self, client):
        resp = client.get("/mcp")
        assert resp.status_code == 405

    def test_delete_terminates_session(self, client):
        _, session_id = _initialize(client)
        assert session_id in _mcp_sessions

        resp = client.delete("/mcp", headers={"Mcp-Session-Id": session_id})
        assert resp.status_code == 200
        assert session_id not in _mcp_sessions

        # Subsequent request with deleted session fails
        resp = client.post(
            "/mcp",
            json=_jsonrpc("tools/list"),
            headers={"Mcp-Session-Id": session_id},
        )
        assert resp.status_code == 400

    def test_delete_without_session_is_ok(self, client):
        resp = client.delete("/mcp")
        assert resp.status_code == 200


class TestStreamableHTTPErrors:
    def test_invalid_json_returns_parse_error(self, client):
        resp = client.post(
            "/mcp",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["error"]["code"] == -32700

    def test_empty_batch_returns_error(self, client):
        resp = client.post("/mcp", json=[])
        assert resp.status_code == 400


class TestStreamableHTTPGameFlow:
    """Test a full game flow through the Streamable HTTP endpoint."""

    def test_create_and_play_game(self, client):
        _, session_id = _initialize(client)
        headers = {"Mcp-Session-Id": session_id}

        # Create auction game with 3 players
        resp = client.post(
            "/mcp",
            json=_jsonrpc(
                "tools/call",
                {
                    "name": "create_game",
                    "arguments": {
                        "game_type": "auction",
                        "players": ["alice", "bob", "charlie"],
                    },
                },
            ),
            headers=headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        content = body["result"]["content"][0]["text"]
        assert "Game created" in content

        # Get status
        resp = client.post(
            "/mcp",
            json=_jsonrpc(
                "tools/call",
                {"name": "get_status", "arguments": {}},
            ),
            headers=headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "result" in body

        # Leave game
        resp = client.post(
            "/mcp",
            json=_jsonrpc(
                "tools/call",
                {"name": "leave_game", "arguments": {}},
            ),
            headers=headers,
        )
        assert resp.status_code == 200


class TestBackwardCompat:
    """Ensure old /mcp/agent/{id} endpoint still works."""

    def test_old_endpoint_still_works(self, client):
        resp = client.post(
            "/mcp/agent/test-agent",
            json=_jsonrpc("initialize"),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["result"]["protocolVersion"] == "2025-03-26"
