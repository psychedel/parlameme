"""Tests for MCP ledger tools."""

from __future__ import annotations

import pytest

from engine.ledger import MemoryLedger
from mcp.agents import AgentState
from mcp.agents import reset_all as reset_agents
from mcp.handlers.ledger import (
    handle_ledger_balance,
    handle_ledger_history,
    handle_ledger_status,
    handle_ledger_verify,
)


@pytest.fixture(autouse=True)
def _clean():
    reset_agents()
    yield
    reset_agents()


class _FakeServer:
    def __init__(self, ledger=None):
        self._ledger = ledger


def _agent(name: str = "alice") -> AgentState:
    return AgentState(agent_id=name)


# ===========================================================================
# Ledger balance
# ===========================================================================


class TestLedgerBalance:
    @pytest.mark.asyncio
    async def test_zero_balance(self):
        ledger = MemoryLedger()
        result = await handle_ledger_balance(_FakeServer(ledger), _agent(), {})
        text = result["content"][0]["text"]
        assert "Balance: 0" in text

    @pytest.mark.asyncio
    async def test_after_deposit(self):
        ledger = MemoryLedger()
        ledger.append("deposit", "alice", 500)
        result = await handle_ledger_balance(_FakeServer(ledger), _agent(), {})
        text = result["content"][0]["text"]
        assert "500" in text

    @pytest.mark.asyncio
    async def test_no_ledger(self):
        result = await handle_ledger_balance(_FakeServer(None), _agent(), {})
        assert result.get("isError") is True


# ===========================================================================
# Ledger history
# ===========================================================================


class TestLedgerHistory:
    @pytest.mark.asyncio
    async def test_empty(self):
        ledger = MemoryLedger()
        result = await handle_ledger_history(_FakeServer(ledger), _agent(), {})
        text = result["content"][0]["text"]
        assert "No transactions" in text

    @pytest.mark.asyncio
    async def test_with_entries(self):
        ledger = MemoryLedger()
        ledger.append("deposit", "alice", 100, ref="initial")
        ledger.append("game_credit", "alice", 50, ref="game-1")
        ledger.append("deposit", "bob", 200)  # other player

        result = await handle_ledger_history(_FakeServer(ledger), _agent(), {})
        text = result["content"][0]["text"]
        assert "deposit" in text
        assert "game_credit" in text
        assert "+100" in text
        assert "+50" in text
        assert "bob" not in text  # only alice's entries

    @pytest.mark.asyncio
    async def test_limit(self):
        ledger = MemoryLedger()
        for i in range(10):
            ledger.append("deposit", "alice", i + 1)

        result = await handle_ledger_history(
            _FakeServer(ledger), _agent(), {"limit": 3}
        )
        text = result["content"][0]["text"]
        # Should show last 3 entries (seq 8, 9, 10)
        assert "#10" in text
        assert "#1 " not in text


# ===========================================================================
# Ledger verify
# ===========================================================================


class TestLedgerVerify:
    @pytest.mark.asyncio
    async def test_valid_chain(self):
        ledger = MemoryLedger()
        ledger.append("deposit", "alice", 100)
        ledger.append("deposit", "bob", 200)

        result = await handle_ledger_verify(_FakeServer(ledger), _agent(), {})
        text = result["content"][0]["text"]
        assert "OK" in text
        assert "2 entries" in text

    @pytest.mark.asyncio
    async def test_empty_chain(self):
        ledger = MemoryLedger()
        result = await handle_ledger_verify(_FakeServer(ledger), _agent(), {})
        text = result["content"][0]["text"]
        assert "OK" in text
        assert "0 entries" in text


# ===========================================================================
# Ledger status
# ===========================================================================


class TestLedgerStatus:
    @pytest.mark.asyncio
    async def test_overview(self):
        ledger = MemoryLedger()
        ledger.append("deposit", "alice", 100)
        ledger.append("deposit", "bob", 200)
        ledger.append("game_credit", "alice", 50)

        result = await handle_ledger_status(_FakeServer(ledger), _agent(), {})
        text = result["content"][0]["text"]
        assert "Total entries: 3" in text
        assert "Your balance: 150" in text  # 100 + 50
        assert "Active accounts: 2" in text
        assert "OK" in text


# ===========================================================================
# Tool list integration
# ===========================================================================


class TestLedgerToolsInSchema:
    def test_lobby_includes_ledger_tools(self):
        from mcp.server import MCPServer

        server = MCPServer()
        agent = AgentState(agent_id="test")
        tools = server._get_tools_for_agent(agent)
        names = {t.name for t in tools}
        assert "ledger_balance" in names
        assert "ledger_history" in names
        assert "ledger_verify" in names
        assert "ledger_status" in names

    def test_spectating_includes_ledger_tools(self):
        from mcp.server import MCPServer

        server = MCPServer()
        agent = AgentState(agent_id="test")
        agent.state = "spectating"
        agent.session_id = "game-1"
        tools = server._get_tools_for_agent(agent)
        names = {t.name for t in tools}
        assert "ledger_balance" in names
