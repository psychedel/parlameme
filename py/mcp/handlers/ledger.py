"""Ledger MCP tools — balance, history, verify, status.

Exposes hash-chain ledger to agents for economic decision-making.
Available in ALL agent states (global tools).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.schema import Tool

if TYPE_CHECKING:
    from mcp.agents import AgentState

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS: list[Tool] = [
    Tool(
        name="ledger_balance",
        description="Your current ledger balance",
        _meta={"type": "global"},
    ),
    Tool(
        name="ledger_history",
        description="Your recent ledger transactions",
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "default": 20,
                    "description": "Max entries to return",
                },
            },
        },
        _meta={"type": "global"},
    ),
    Tool(
        name="ledger_verify",
        description="Verify ledger hash-chain integrity",
        _meta={"type": "global"},
    ),
    Tool(
        name="ledger_status",
        description="Ledger overview: total entries, your balance, chain status",
        _meta={"type": "global"},
    ),
]

# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def handle_ledger_balance(server: Any, agent: AgentState, args: dict) -> dict:
    ledger = server._ledger
    if ledger is None:
        return _error("Ledger not available.")
    balance = ledger.balance(agent.agent_id)
    return _content(f"Balance: {balance}")


async def handle_ledger_history(server: Any, agent: AgentState, args: dict) -> dict:
    ledger = server._ledger
    if ledger is None:
        return _error("Ledger not available.")

    limit = args.get("limit", 20)
    entries = ledger.entries(agent.agent_id)
    entries = entries[-limit:]

    if not entries:
        return _content("No transactions yet.")

    lines = ["## Your Transactions"]
    for e in reversed(entries):
        sign = "+" if e.amount >= 0 else ""
        ref = f" (ref: {e.ref})" if e.ref else ""
        lines.append(f"- #{e.seq} [{e.type}] {sign}{e.amount}{ref}")
    return _content("\n".join(lines))


async def handle_ledger_verify(server: Any, agent: AgentState, args: dict) -> dict:
    ledger = server._ledger
    if ledger is None:
        return _error("Ledger not available.")

    ok = ledger.verify()
    total = len(ledger)
    if ok:
        return _content(f"Ledger integrity: OK ({total} entries, chain valid)")
    return _content(f"Ledger integrity: CORRUPTED ({total} entries)")


async def handle_ledger_status(server: Any, agent: AgentState, args: dict) -> dict:
    ledger = server._ledger
    if ledger is None:
        return _error("Ledger not available.")

    balance = ledger.balance(agent.agent_id)
    total = len(ledger)
    ok = ledger.verify()
    all_bal = ledger.all_balances()

    lines = [
        "## Ledger Status",
        f"Total entries: {total}",
        f"Chain integrity: {'OK' if ok else 'CORRUPTED'}",
        f"Your balance: {balance}",
        f"Active accounts: {len(all_bal)}",
    ]
    return _content("\n".join(lines))


# ---------------------------------------------------------------------------
# Routing table
# ---------------------------------------------------------------------------

HANDLERS: dict[str, Any] = {
    "ledger_balance": handle_ledger_balance,
    "ledger_history": handle_ledger_history,
    "ledger_verify": handle_ledger_verify,
    "ledger_status": handle_ledger_status,
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _content(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


def _error(message: str) -> dict:
    return {"content": [{"type": "text", "text": f"Error: {message}"}], "isError": True}
