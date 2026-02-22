"""Escrow MCP tools — on-chain balance, withdrawal requests, status.

Available in ALL agent states (global tools). Only active when escrow bridge
is connected (mode != "disabled").
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
        name="escrow_status",
        description="On-chain escrow status: mode, total deposits, last anchor, chain integrity",
        _meta={"type": "global"},
    ),
    Tool(
        name="escrow_balance",
        description="Your on-chain USDC escrow balance (vs ledger balance)",
        _meta={"type": "global"},
    ),
    Tool(
        name="request_withdrawal",
        description="Request USDC withdrawal — returns signed authorization for on-chain execution",
        inputSchema={
            "type": "object",
            "properties": {
                "amount": {
                    "type": "integer",
                    "description": "Amount in USDC raw units (1 USDC = 1000000)",
                },
            },
            "required": ["amount"],
        },
        _meta={"type": "global"},
    ),
    Tool(
        name="register_wallet",
        description="Link your Ethereum wallet address to your player account",
        inputSchema={
            "type": "object",
            "properties": {
                "address": {
                    "type": "string",
                    "description": "Your Ethereum address (0x...)",
                },
            },
            "required": ["address"],
        },
        _meta={"type": "global"},
    ),
]

# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def handle_escrow_status(server: Any, agent: AgentState, args: dict) -> dict:
    from engine.escrow import escrow_bridge

    status = escrow_bridge.get_status()

    if status.mode == "disabled":
        return _content(
            "## Escrow Status\n"
            "Mode: **disabled** (no blockchain connected)\n\n"
            "The ledger tracks balances locally. "
            "On-chain escrow requires ESCROW_MODE configuration."
        )

    lines = [
        "## Escrow Status",
        f"Mode: **{status.mode}**",
        f"Contract: `{status.contract}`",
        f"Chain ID: {status.chain_id}",
        f"Total deposits: {status.total_deposits / 1_000_000:.2f} USDC",
        f"Anchors on-chain: {status.anchor_count}",
    ]

    # Last anchor info
    anchor = escrow_bridge.get_last_anchor()
    if anchor:
        import datetime

        ts = datetime.datetime.fromtimestamp(anchor["timestamp"], tz=datetime.timezone.utc)
        lines.append(f"Last anchor: {ts.isoformat()} ({anchor['entry_count']} entries)")
        lines.append(f"Anchor root: `{anchor['root'][:16]}...`")

    # Ledger integrity
    ledger = server._ledger
    if ledger:
        ok = ledger.verify()
        lines.append(f"Ledger integrity: {'OK' if ok else 'CORRUPTED'} ({len(ledger)} entries)")

    return _content("\n".join(lines))


async def handle_escrow_balance(server: Any, agent: AgentState, args: dict) -> dict:
    from engine.escrow import USDC_UNIT, escrow_bridge

    player_id = agent.agent_id

    # Ledger balance (always available)
    ledger = server._ledger
    ledger_bal = ledger.balance(player_id) if ledger else 0

    lines = [
        "## Balance",
        f"Ledger balance: {ledger_bal}",
    ]

    # On-chain balance (if escrow enabled and wallet registered)
    if escrow_bridge.mode != "disabled":
        on_chain = escrow_bridge.get_on_chain_balance(player_id)
        if on_chain is not None:
            lines.append(f"On-chain escrow: {on_chain / USDC_UNIT:.2f} USDC ({on_chain} raw)")
            addr = escrow_bridge.get_player_address(player_id)
            if addr:
                lines.append(f"Wallet: `{addr}`")
        else:
            lines.append("On-chain: No wallet registered (use `register_wallet` first)")
    else:
        lines.append("On-chain: Escrow not connected")

    return _content("\n".join(lines))


async def handle_request_withdrawal(server: Any, agent: AgentState, args: dict) -> dict:
    from engine.escrow import escrow_bridge

    if escrow_bridge.mode == "disabled":
        return _error("Escrow bridge not enabled. Configure ESCROW_MODE to use withdrawals.")

    amount = args.get("amount")
    if not amount or amount <= 0:
        return _error("Amount must be a positive integer (USDC raw units, 1 USDC = 1000000)")

    result = escrow_bridge.sign_withdrawal(agent.agent_id, amount)
    if isinstance(result, str):
        return _error(result)

    lines = [
        "## Withdrawal Authorization",
        f"Amount: {result.amount / 1_000_000:.2f} USDC",
        f"Nonce: {result.nonce}",
        f"Expires: {result.expires_at}",
        "",
        "### Call on-chain",
        f"Contract: `{result.contract}`",
        f"Chain ID: {result.chain_id}",
        "```",
        f"withdraw({result.amount}, {result.nonce}, {result.expires_at}, {result.signature})",
        "```",
        "",
        "Submit this transaction before expiry to receive your USDC.",
    ]
    return _content("\n".join(lines))


async def handle_register_wallet(server: Any, agent: AgentState, args: dict) -> dict:
    from engine.escrow import escrow_bridge

    address = args.get("address", "").strip()
    if not address or not address.startswith("0x") or len(address) != 42:
        return _error("Invalid Ethereum address. Must be 42-character hex string starting with 0x.")

    try:
        escrow_bridge.register_player(agent.agent_id, address)
    except Exception as e:
        return _error(f"Failed to register wallet: {e}")

    return _content(
        f"Wallet registered: `{address}` → player `{agent.agent_id}`\n\n"
        "You can now use `escrow_balance` and `request_withdrawal`."
    )


# ---------------------------------------------------------------------------
# Routing table
# ---------------------------------------------------------------------------

HANDLERS: dict[str, Any] = {
    "escrow_status": handle_escrow_status,
    "escrow_balance": handle_escrow_balance,
    "request_withdrawal": handle_request_withdrawal,
    "register_wallet": handle_register_wallet,
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _content(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


def _error(message: str) -> dict:
    return {"content": [{"type": "text", "text": f"Error: {message}"}], "isError": True}
