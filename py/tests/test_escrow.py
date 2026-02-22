"""Tests for escrow integration: Merkle tree, escrow bridge, MCP handlers."""

from __future__ import annotations

import unittest

from engine.ledger import MemoryLedger
from engine.merkle import (
    _ZERO_HASH,
    _hash_pair,
    ledger_merkle_root,
    merkle_proof,
    merkle_root,
    verify_proof,
)


# ===========================================================================
# Merkle tree tests
# ===========================================================================


class TestMerkleTree(unittest.TestCase):
    """Merkle tree construction and verification."""

    def test_empty_tree(self):
        assert merkle_root([]) == _ZERO_HASH

    def test_single_leaf(self):
        leaf = "abc123"
        root = merkle_root([leaf])
        assert root == leaf

    def test_two_leaves(self):
        a, b = "aaa", "bbb"
        root = merkle_root([a, b])
        assert root == _hash_pair(a, b)

    def test_four_leaves_balanced(self):
        leaves = ["a", "b", "c", "d"]
        root = merkle_root(leaves)
        left = _hash_pair("a", "b")
        right = _hash_pair("c", "d")
        expected = _hash_pair(left, right)
        assert root == expected

    def test_three_leaves_odd(self):
        """Odd number of leaves — last one promoted."""
        leaves = ["a", "b", "c"]
        root = merkle_root(leaves)
        left = _hash_pair("a", "b")
        # "c" promoted as-is, then paired with left
        expected = _hash_pair(left, "c")
        assert root == expected

    def test_deterministic(self):
        """Same leaves always produce same root."""
        leaves = ["x", "y", "z", "w"]
        r1 = merkle_root(leaves)
        r2 = merkle_root(leaves)
        assert r1 == r2

    def test_different_leaves_different_root(self):
        r1 = merkle_root(["a", "b"])
        r2 = merkle_root(["c", "d"])
        assert r1 != r2

    def test_hash_pair_canonical_order(self):
        """Hash pair should produce same result regardless of argument order."""
        h1 = _hash_pair("aaa", "bbb")
        h2 = _hash_pair("bbb", "aaa")
        assert h1 == h2

    def test_large_tree(self):
        """Tree with many leaves produces valid root."""
        leaves = [f"leaf-{i}" for i in range(100)]
        root = merkle_root(leaves)
        assert isinstance(root, str)
        assert len(root) == 64  # SHA-256 hex

    def test_does_not_mutate_input(self):
        leaves = ["a", "b", "c"]
        original = list(leaves)
        merkle_root(leaves)
        assert leaves == original


class TestMerkleProof(unittest.TestCase):
    """Merkle proof generation and verification."""

    def test_proof_single_leaf(self):
        leaves = ["abc"]
        proof = merkle_proof(leaves, 0)
        root = merkle_root(leaves)
        assert verify_proof("abc", proof, root)

    def test_proof_two_leaves(self):
        leaves = ["a", "b"]
        root = merkle_root(leaves)

        proof_a = merkle_proof(leaves, 0)
        assert verify_proof("a", proof_a, root)

        proof_b = merkle_proof(leaves, 1)
        assert verify_proof("b", proof_b, root)

    def test_proof_four_leaves(self):
        leaves = ["a", "b", "c", "d"]
        root = merkle_root(leaves)

        for i, leaf in enumerate(leaves):
            proof = merkle_proof(leaves, i)
            assert verify_proof(leaf, proof, root), f"Proof failed for leaf {i}"

    def test_proof_invalid_leaf(self):
        leaves = ["a", "b", "c", "d"]
        root = merkle_root(leaves)
        proof = merkle_proof(leaves, 0)
        # Wrong leaf should fail verification
        assert not verify_proof("wrong", proof, root)

    def test_proof_invalid_index(self):
        assert merkle_proof([], 0) == []
        assert merkle_proof(["a"], -1) == []
        assert merkle_proof(["a"], 1) == []

    def test_proof_large_tree(self):
        leaves = [f"leaf-{i}" for i in range(64)]
        root = merkle_root(leaves)
        # Verify a few random positions
        for idx in [0, 15, 31, 63]:
            proof = merkle_proof(leaves, idx)
            assert verify_proof(leaves[idx], proof, root)


class TestLedgerMerkleRoot(unittest.TestCase):
    """Integration: Merkle root from actual ledger entries."""

    def test_empty_ledger(self):
        ledger = MemoryLedger()
        root, count = ledger_merkle_root(ledger.entries())
        assert root == _ZERO_HASH
        assert count == 0

    def test_ledger_with_entries(self):
        ledger = MemoryLedger()
        ledger.append("deposit", "alice", 1000)
        ledger.append("deposit", "bob", 500)
        ledger.append("game_credit", "alice", 200, ref="game-1")

        root, count = ledger_merkle_root(ledger.entries())
        assert count == 3
        assert root != _ZERO_HASH
        assert len(root) == 64

    def test_different_entries_different_root(self):
        l1 = MemoryLedger()
        l1.append("deposit", "alice", 1000)

        l2 = MemoryLedger()
        l2.append("deposit", "bob", 1000)

        r1, _ = ledger_merkle_root(l1.entries())
        r2, _ = ledger_merkle_root(l2.entries())
        assert r1 != r2

    def test_proof_for_ledger_entry(self):
        """Can prove a specific ledger entry is in the Merkle tree."""
        ledger = MemoryLedger()
        ledger.append("deposit", "alice", 1000)
        ledger.append("deposit", "bob", 500)
        ledger.append("game_credit", "alice", 200)
        ledger.append("withdraw", "bob", 100)

        entries = ledger.entries()
        leaves = [e.content_hash for e in entries]
        root = merkle_root(leaves)

        # Prove entry #2 (bob's deposit)
        proof = merkle_proof(leaves, 1)
        assert verify_proof(entries[1].content_hash, proof, root)


# ===========================================================================
# Mock escrow tests
# ===========================================================================


class TestMockEscrow(unittest.TestCase):
    """MockEscrow for testing without blockchain."""

    def setUp(self):
        from engine.escrow import MockEscrow

        self.escrow = MockEscrow()

    def test_simulate_deposit(self):
        evt = self.escrow.simulate_deposit("alice", 1_000_000)
        assert evt.player == "alice"
        assert evt.amount == 1_000_000
        assert self.escrow.balances["alice"] == 1_000_000

    def test_simulate_deposit_with_ledger(self):
        ledger = MemoryLedger()
        self.escrow._ledger = ledger
        self.escrow.simulate_deposit("alice", 5_000_000)
        assert ledger.balance("alice") == 5_000_000

    def test_sign_withdrawal(self):
        self.escrow.simulate_deposit("alice", 10_000_000)
        auth = self.escrow.sign_withdrawal("alice", 5_000_000)
        assert not isinstance(auth, str), f"Expected auth, got error: {auth}"
        assert auth.amount == 5_000_000
        assert auth.nonce == 0
        assert self.escrow.balances["alice"] == 5_000_000

    def test_withdrawal_insufficient_balance(self):
        self.escrow.simulate_deposit("alice", 1_000_000)
        result = self.escrow.sign_withdrawal("alice", 5_000_000)
        assert isinstance(result, str)
        assert "Insufficient" in result

    def test_withdrawal_increments_nonce(self):
        self.escrow.simulate_deposit("alice", 10_000_000)
        auth1 = self.escrow.sign_withdrawal("alice", 1_000_000)
        auth2 = self.escrow.sign_withdrawal("alice", 1_000_000)
        assert auth1.nonce == 0
        assert auth2.nonce == 1

    def test_withdrawal_with_ledger(self):
        ledger = MemoryLedger()
        self.escrow._ledger = ledger
        self.escrow.simulate_deposit("alice", 10_000_000)
        self.escrow.sign_withdrawal("alice", 3_000_000)
        # Deposit + withdrawal
        assert len(ledger.entries()) == 2
        assert ledger.balance("alice") == 7_000_000

    def test_mock_anchor(self):
        tx = self.escrow.anchor("abcdef1234567890" * 4, 42)
        assert tx.startswith("mock:")
        assert len(self.escrow.anchors) == 1
        assert self.escrow.anchors[0] == ("abcdef1234567890" * 4, 42)

    def test_get_status(self):
        self.escrow.simulate_deposit("alice", 5_000_000)
        self.escrow.anchor("root", 1)
        status = self.escrow.get_status()
        assert status.mode == "mock"
        assert status.total_deposits == 5_000_000
        assert status.anchor_count == 1


# ===========================================================================
# EscrowBridge tests (disabled mode, no blockchain)
# ===========================================================================


class TestEscrowBridgeDisabled(unittest.TestCase):
    """EscrowBridge in disabled mode — all operations are no-ops."""

    def setUp(self):
        from engine.escrow import EscrowBridge

        self.bridge = EscrowBridge()

    def test_default_disabled(self):
        assert not self.bridge.enabled
        assert self.bridge.mode == "disabled"

    def test_connect_none(self):
        self.bridge.connect(mode=None)
        assert not self.bridge.enabled

    def test_connect_disabled(self):
        self.bridge.connect(mode="disabled")
        assert not self.bridge.enabled

    def test_sign_withdrawal_disabled(self):
        result = self.bridge.sign_withdrawal("alice", 1000)
        assert isinstance(result, str)
        assert "not enabled" in result

    def test_on_chain_balance_disabled(self):
        assert self.bridge.get_on_chain_balance("alice") is None

    def test_last_anchor_disabled(self):
        assert self.bridge.get_last_anchor() is None

    def test_anchor_ledger_disabled(self):
        assert self.bridge.anchor_ledger() is None

    def test_status_disabled(self):
        status = self.bridge.get_status()
        assert status.mode == "disabled"

    def test_register_player_disabled(self):
        # Should not crash even when web3 not available
        # (address validation is skipped when HAS_WEB3 is False)
        self.bridge.register_player("alice", "0x1234567890abcdef1234567890abcdef12345678")
        assert self.bridge.get_player_address("alice") is not None


# ===========================================================================
# MCP handler tests
# ===========================================================================


class _FakeAgent:
    def __init__(self, agent_id: str = "alice"):
        self.agent_id = agent_id


class _FakeServer:
    def __init__(self):
        self._ledger = MemoryLedger()


class TestEscrowMCPHandlers(unittest.IsolatedAsyncioTestCase):
    """MCP escrow handler tests (mock/disabled mode)."""

    async def test_escrow_status_disabled(self):
        from mcp.handlers.escrow import handle_escrow_status

        server = _FakeServer()
        agent = _FakeAgent()
        result = await handle_escrow_status(server, agent, {})
        text = result["content"][0]["text"]
        assert "disabled" in text

    async def test_escrow_balance_disabled(self):
        from mcp.handlers.escrow import handle_escrow_balance

        server = _FakeServer()
        server._ledger.append("deposit", "alice", 500)
        agent = _FakeAgent()
        result = await handle_escrow_balance(server, agent, {})
        text = result["content"][0]["text"]
        assert "500" in text
        assert "not connected" in text.lower() or "Escrow not connected" in text

    async def test_request_withdrawal_disabled(self):
        from mcp.handlers.escrow import handle_request_withdrawal

        server = _FakeServer()
        agent = _FakeAgent()
        result = await handle_request_withdrawal(server, agent, {"amount": 1000})
        assert result.get("isError")
        text = result["content"][0]["text"]
        assert "not enabled" in text.lower() or "ESCROW_MODE" in text

    async def test_register_wallet_valid(self):
        from mcp.handlers.escrow import handle_register_wallet

        server = _FakeServer()
        agent = _FakeAgent()
        result = await handle_register_wallet(
            server, agent, {"address": "0x1234567890abcdef1234567890abcdef12345678"}
        )
        text = result["content"][0]["text"]
        assert "registered" in text.lower()

    async def test_register_wallet_invalid(self):
        from mcp.handlers.escrow import handle_register_wallet

        server = _FakeServer()
        agent = _FakeAgent()
        result = await handle_register_wallet(server, agent, {"address": "not-an-address"})
        assert result.get("isError")

    async def test_request_withdrawal_invalid_amount(self):
        from mcp.handlers.escrow import handle_request_withdrawal

        server = _FakeServer()
        agent = _FakeAgent()
        result = await handle_request_withdrawal(server, agent, {"amount": -1})
        assert result.get("isError")

    async def test_request_withdrawal_zero(self):
        from mcp.handlers.escrow import handle_request_withdrawal

        server = _FakeServer()
        agent = _FakeAgent()
        result = await handle_request_withdrawal(server, agent, {"amount": 0})
        assert result.get("isError")
