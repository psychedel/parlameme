"""Base L2 Escrow Bridge — optional blockchain integration.

Fail-open design (same pattern as pg.py):
- Disabled if ESCROW_MODE not set or "mock"
- All operations silently skip when disabled
- JSON ledger remains the source of truth

Modes:
- disabled: no blockchain, ledger-only (default)
- mock: in-memory simulation for tests
- sepolia: Base Sepolia testnet
- base: Base mainnet (production)

Requires optional dependency: web3 (pip install web3)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from engine.ledger import Ledger

log = logging.getLogger(__name__)

try:
    from eth_account import Account
    from eth_account.messages import encode_defunct
    from web3 import Web3

    HAS_WEB3 = True
except ImportError:
    HAS_WEB3 = False

# ---------------------------------------------------------------------------
# Contract ABI (minimal — only the functions we call)
# ---------------------------------------------------------------------------

_ESCROW_ABI = [
    # Events
    {
        "type": "event",
        "name": "Deposit",
        "inputs": [
            {"name": "player", "type": "address", "indexed": True},
            {"name": "amount", "type": "uint256", "indexed": False},
            {"name": "txHash", "type": "bytes32", "indexed": False},
        ],
    },
    {
        "type": "event",
        "name": "Withdrawal",
        "inputs": [
            {"name": "player", "type": "address", "indexed": True},
            {"name": "amount", "type": "uint256", "indexed": False},
            {"name": "nonce", "type": "uint256", "indexed": False},
        ],
    },
    {
        "type": "event",
        "name": "Anchored",
        "inputs": [
            {"name": "root", "type": "bytes32", "indexed": True},
            {"name": "entryCount", "type": "uint256", "indexed": False},
            {"name": "timestamp", "type": "uint256", "indexed": False},
        ],
    },
    # Read functions
    {
        "type": "function",
        "name": "getBalance",
        "inputs": [{"name": "player", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
    },
    {
        "type": "function",
        "name": "getNonce",
        "inputs": [{"name": "player", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
    },
    {
        "type": "function",
        "name": "totalDeposits",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
    },
    {
        "type": "function",
        "name": "anchorCount",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
    },
    {
        "type": "function",
        "name": "getAnchor",
        "inputs": [{"name": "index", "type": "uint256"}],
        "outputs": [
            {
                "type": "tuple",
                "components": [
                    {"name": "root", "type": "bytes32"},
                    {"name": "entryCount", "type": "uint256"},
                    {"name": "timestamp", "type": "uint256"},
                    {"name": "blockNumber", "type": "uint256"},
                ],
            }
        ],
        "stateMutability": "view",
    },
    # Write functions
    {
        "type": "function",
        "name": "anchor",
        "inputs": [
            {"name": "root", "type": "bytes32"},
            {"name": "entryCount", "type": "uint256"},
        ],
        "outputs": [],
        "stateMutability": "nonpayable",
    },
]


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_CHAIN_CONFIG = {
    "sepolia": {
        "rpc": "https://sepolia.base.org",
        "chain_id": 84532,
        "usdc": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
        "confirmations": 6,
    },
    "base": {
        "rpc": "https://mainnet.base.org",
        "chain_id": 8453,
        "usdc": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "confirmations": 12,
    },
}

# USDC has 6 decimals
USDC_DECIMALS = 6
USDC_UNIT = 10**USDC_DECIMALS  # 1 USDC = 1_000_000

# Withdrawal signature expiry
WITHDRAWAL_EXPIRY_SECONDS = 30 * 60  # 30 minutes (matches contract)

# Deposit polling interval
DEPOSIT_POLL_INTERVAL = 15  # seconds

# Anchoring interval
ANCHOR_INTERVAL = 3600  # 1 hour
ANCHOR_MIN_ENTRIES = 10  # minimum new entries before anchoring


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DepositEvent:
    """On-chain deposit detected."""

    player: str  # checksummed address
    amount: int  # USDC raw (6 decimals)
    tx_hash: str
    block_number: int
    confirmations: int


@dataclass(frozen=True)
class WithdrawalAuth:
    """Signed withdrawal authorization for a player."""

    player: str
    amount: int
    nonce: int
    expires_at: int
    signature: str  # hex-encoded
    contract: str
    chain_id: int


@dataclass
class EscrowStatus:
    """Current escrow bridge status."""

    mode: str = "disabled"
    contract: str = ""
    chain_id: int = 0
    total_deposits: int = 0
    anchor_count: int = 0
    last_anchor_time: float = 0.0
    last_poll_block: int = 0
    pending_deposits: int = 0


# ---------------------------------------------------------------------------
# Mock implementation (for tests)
# ---------------------------------------------------------------------------


class MockEscrow:
    """In-memory escrow for testing — no blockchain needed."""

    def __init__(self) -> None:
        self.balances: dict[str, int] = {}
        self.nonces: dict[str, int] = {}
        self.deposits: list[DepositEvent] = []
        self.anchors: list[tuple[str, int]] = []
        self._ledger: Ledger | None = None

    def simulate_deposit(self, player: str, amount: int) -> DepositEvent:
        """Simulate a deposit event (for testing)."""
        self.balances[player] = self.balances.get(player, 0) + amount
        evt = DepositEvent(
            player=player,
            amount=amount,
            tx_hash=hashlib.sha256(f"mock-{player}-{amount}-{time.time()}".encode()).hexdigest(),
            block_number=len(self.deposits) + 1,
            confirmations=12,
        )
        self.deposits.append(evt)
        if self._ledger is not None:
            self._ledger.append("deposit", player, amount, ref=f"mock:{evt.tx_hash[:16]}")
        return evt

    def sign_withdrawal(self, player: str, amount: int) -> WithdrawalAuth | str:
        """Create mock withdrawal auth."""
        balance = self.balances.get(player, 0)
        if balance < amount:
            return f"Insufficient on-chain balance: {balance} < {amount}"
        nonce = self.nonces.get(player, 0)
        self.balances[player] -= amount
        self.nonces[player] = nonce + 1
        if self._ledger is not None:
            self._ledger.append("withdraw", player, amount, ref=f"mock:withdrawal-{nonce}")
        return WithdrawalAuth(
            player=player,
            amount=amount,
            nonce=nonce,
            expires_at=int(time.time()) + WITHDRAWAL_EXPIRY_SECONDS,
            signature="0x" + "ab" * 65,  # mock signature
            contract="0x" + "00" * 20,
            chain_id=0,
        )

    def anchor(self, root: str, entry_count: int) -> str:
        """Mock anchor — just records it."""
        self.anchors.append((root, entry_count))
        return f"mock:anchor-{len(self.anchors)}"

    def get_status(self) -> EscrowStatus:
        return EscrowStatus(
            mode="mock",
            total_deposits=sum(self.balances.values()),
            anchor_count=len(self.anchors),
        )


# ---------------------------------------------------------------------------
# Live implementation (Base L2 via web3.py)
# ---------------------------------------------------------------------------


class EscrowBridge:
    """Base L2 escrow bridge. Fail-open singleton.

    Usage:
        escrow = EscrowBridge()
        escrow.connect(mode="sepolia", contract="0x...", signer_key="0x...")
    """

    def __init__(self) -> None:
        self._mode: str = "disabled"
        self._w3: Any = None
        self._contract: Any = None
        self._contract_addr: str = ""
        self._signer_key: str = ""
        self._signer_addr: str = ""
        self._chain_id: int = 0
        self._confirmations: int = 12
        self._last_poll_block: int = 0
        self._last_anchor_entry_count: int = 0
        self._last_anchor_time: float = 0.0
        self._ledger: Ledger | None = None
        self._poll_task: Any = None
        self._anchor_task: Any = None
        # Player ID ↔ address mapping (platform manages this)
        self._player_addresses: dict[str, str] = {}  # player_id → address
        self._address_players: dict[str, str] = {}  # address → player_id

    @property
    def enabled(self) -> bool:
        return self._mode not in ("disabled", "mock")

    @property
    def mode(self) -> str:
        return self._mode

    def connect(
        self,
        mode: str | None = None,
        contract: str | None = None,
        signer_key: str | None = None,
        rpc_url: str | None = None,
    ) -> None:
        """Initialize escrow bridge. Call once on startup."""
        if not mode or mode == "disabled":
            log.info("Escrow bridge disabled (ESCROW_MODE not set)")
            return

        if mode == "mock":
            self._mode = "mock"
            log.info("Escrow bridge: mock mode (no blockchain)")
            return

        if not HAS_WEB3:
            log.warning(
                "web3 not installed — escrow bridge disabled. "
                "Install with: pip install web3"
            )
            return

        if mode not in _CHAIN_CONFIG:
            log.error("Unknown escrow mode: %s (expected: sepolia, base)", mode)
            return

        if not contract:
            log.error("ESCROW_CONTRACT not set for mode %s", mode)
            return

        if not signer_key:
            log.error("PLATFORM_SIGNER_KEY not set — escrow bridge disabled")
            return

        cfg = _CHAIN_CONFIG[mode]
        url = rpc_url or cfg["rpc"]

        try:
            w3 = Web3(Web3.HTTPProvider(url))
            if not w3.is_connected():
                log.error("Cannot connect to %s — escrow bridge disabled", url)
                return

            self._w3 = w3
            self._contract_addr = Web3.to_checksum_address(contract)
            self._contract = w3.eth.contract(
                address=self._contract_addr, abi=_ESCROW_ABI
            )
            self._signer_key = signer_key
            self._signer_addr = Account.from_key(signer_key).address
            self._chain_id = cfg["chain_id"]
            self._confirmations = cfg["confirmations"]
            self._mode = mode
            self._last_poll_block = w3.eth.block_number

            chain_id = w3.eth.chain_id
            total = self._contract.functions.totalDeposits().call()
            anchors = self._contract.functions.anchorCount().call()

            log.info(
                "Escrow bridge connected: mode=%s chain=%d contract=%s "
                "signer=%s total_deposits=%d anchors=%d",
                mode,
                chain_id,
                self._contract_addr[:10] + "...",
                self._signer_addr[:10] + "...",
                total,
                anchors,
            )
        except Exception:
            log.exception("Escrow bridge connection failed — disabled")
            self._mode = "disabled"

    def set_ledger(self, ledger: Ledger) -> None:
        """Set ledger reference for deposit crediting."""
        self._ledger = ledger

    def register_player(self, player_id: str, address: str) -> None:
        """Map a player ID to an Ethereum address."""
        addr = Web3.to_checksum_address(address) if HAS_WEB3 else address
        self._player_addresses[player_id] = addr
        self._address_players[addr] = player_id

    def get_player_address(self, player_id: str) -> str | None:
        """Get Ethereum address for a player ID."""
        return self._player_addresses.get(player_id)

    # ------------------------------------------------------------------
    # Deposit monitoring
    # ------------------------------------------------------------------

    async def poll_deposits(self) -> list[DepositEvent]:
        """Poll for new deposit events since last check.

        Returns list of confirmed deposits. Credits ledger automatically.
        """
        if not self.enabled or self._contract is None:
            return []

        try:
            current_block = self._w3.eth.block_number
            safe_block = current_block - self._confirmations
            if safe_block <= self._last_poll_block:
                return []

            deposit_filter = self._contract.events.Deposit.create_filter(
                from_block=self._last_poll_block + 1,
                to_block=safe_block,
            )
            events = deposit_filter.get_all_entries()
            self._last_poll_block = safe_block

            deposits = []
            for evt in events:
                dep = DepositEvent(
                    player=evt.args.player,
                    amount=evt.args.amount,
                    tx_hash=evt.transactionHash.hex(),
                    block_number=evt.blockNumber,
                    confirmations=current_block - evt.blockNumber,
                )
                deposits.append(dep)

                # Credit ledger
                player_id = self._address_players.get(dep.player, dep.player)
                if self._ledger is not None:
                    self._ledger.append(
                        "deposit", player_id, dep.amount, ref=f"tx:{dep.tx_hash[:16]}"
                    )
                log.info(
                    "Deposit confirmed: %s → %d USDC (tx:%s)",
                    player_id,
                    dep.amount / USDC_UNIT,
                    dep.tx_hash[:16],
                )

            return deposits

        except Exception:
            log.exception("Deposit polling failed — will retry next cycle")
            return []

    # ------------------------------------------------------------------
    # Withdrawal signing
    # ------------------------------------------------------------------

    def sign_withdrawal(self, player_id: str, amount: int) -> WithdrawalAuth | str:
        """Sign a withdrawal authorization for a player.

        Args:
            player_id: Player requesting withdrawal.
            amount: Amount in USDC raw units (6 decimals).

        Returns:
            WithdrawalAuth on success, error string on failure.
        """
        if not self.enabled:
            return "Escrow bridge not enabled"

        address = self._player_addresses.get(player_id)
        if not address:
            return f"No Ethereum address registered for player {player_id}"

        # Check on-chain balance
        try:
            on_chain = self._contract.functions.getBalance(address).call()
        except Exception:
            log.exception("Failed to check on-chain balance for %s", player_id)
            return "Failed to check on-chain balance"

        if on_chain < amount:
            return f"Insufficient on-chain balance: {on_chain} < {amount}"

        # Check ledger balance
        if self._ledger is not None:
            ledger_bal = self._ledger.balance(player_id)
            if ledger_bal < amount:
                return f"Insufficient ledger balance: {ledger_bal} < {amount}"

        try:
            nonce = self._contract.functions.getNonce(address).call()
            expires_at = int(time.time()) + WITHDRAWAL_EXPIRY_SECONDS

            # Reproduce contract's message hash:
            # keccak256(abi.encodePacked(player, amount, nonce, expiresAt, chainId, contract))
            message_hash = Web3.solidity_keccak(
                ["address", "uint256", "uint256", "uint256", "uint256", "address"],
                [address, amount, nonce, expires_at, self._chain_id, self._contract_addr],
            )

            # Sign with EIP-191 prefix (matches contract's toEthSignedMessageHash)
            message = encode_defunct(primitive=message_hash)
            signed = Account.sign_message(message, private_key=self._signer_key)

            # Record in ledger
            if self._ledger is not None:
                self._ledger.append(
                    "withdraw", player_id, amount, ref=f"nonce:{nonce}"
                )

            return WithdrawalAuth(
                player=address,
                amount=amount,
                nonce=nonce,
                expires_at=expires_at,
                signature=signed.signature.hex(),
                contract=self._contract_addr,
                chain_id=self._chain_id,
            )

        except Exception:
            log.exception("Withdrawal signing failed for %s", player_id)
            return "Withdrawal signing failed"

    # ------------------------------------------------------------------
    # Merkle anchoring
    # ------------------------------------------------------------------

    def anchor_ledger(self, ledger: Ledger | None = None) -> str | None:
        """Anchor current ledger Merkle root on-chain.

        Returns tx hash on success, None on skip/failure.
        """
        if not self.enabled:
            return None

        source = ledger or self._ledger
        if source is None:
            return None

        from engine.merkle import ledger_merkle_root

        entries = source.entries()
        root_hex, count = ledger_merkle_root(entries)

        if count <= self._last_anchor_entry_count:
            return None  # no new entries

        if count - self._last_anchor_entry_count < ANCHOR_MIN_ENTRIES:
            # Not enough new entries, skip unless forced
            if time.time() - self._last_anchor_time < ANCHOR_INTERVAL:
                return None

        try:
            root_bytes = bytes.fromhex(root_hex)

            # Build transaction
            tx = self._contract.functions.anchor(root_bytes, count).build_transaction(
                {
                    "from": self._signer_addr,
                    "nonce": self._w3.eth.get_transaction_count(self._signer_addr),
                    "chainId": self._chain_id,
                }
            )

            # Sign and send
            signed_tx = self._w3.eth.account.sign_transaction(
                tx, private_key=self._signer_key
            )
            tx_hash = self._w3.eth.send_raw_transaction(signed_tx.raw_transaction)

            self._last_anchor_entry_count = count
            self._last_anchor_time = time.time()

            log.info(
                "Anchored Merkle root: %s (%d entries) tx:%s",
                root_hex[:16] + "...",
                count,
                tx_hash.hex()[:16],
            )
            return tx_hash.hex()

        except Exception:
            log.exception("Merkle anchoring failed — will retry next cycle")
            return None

    # ------------------------------------------------------------------
    # On-chain queries
    # ------------------------------------------------------------------

    def get_on_chain_balance(self, player_id: str) -> int | None:
        """Get player's on-chain escrow balance."""
        if not self.enabled:
            return None
        address = self._player_addresses.get(player_id)
        if not address:
            return None
        try:
            return self._contract.functions.getBalance(address).call()
        except Exception:
            log.exception("Failed to read on-chain balance for %s", player_id)
            return None

    def get_last_anchor(self) -> dict[str, Any] | None:
        """Get the most recent on-chain anchor."""
        if not self.enabled:
            return None
        try:
            count = self._contract.functions.anchorCount().call()
            if count == 0:
                return None
            anchor = self._contract.functions.getAnchor(count - 1).call()
            return {
                "index": count - 1,
                "root": anchor[0].hex(),
                "entry_count": anchor[1],
                "timestamp": anchor[2],
                "block_number": anchor[3],
            }
        except Exception:
            log.exception("Failed to read last anchor")
            return None

    def get_status(self) -> EscrowStatus:
        """Get current escrow bridge status."""
        status = EscrowStatus(mode=self._mode)
        if not self.enabled:
            return status

        status.contract = self._contract_addr
        status.chain_id = self._chain_id
        status.last_poll_block = self._last_poll_block
        status.last_anchor_time = self._last_anchor_time

        try:
            status.total_deposits = self._contract.functions.totalDeposits().call()
            status.anchor_count = self._contract.functions.anchorCount().call()
        except Exception:
            log.exception("Failed to read escrow status")

        return status

    # ------------------------------------------------------------------
    # Background tasks
    # ------------------------------------------------------------------

    async def start_background_tasks(self) -> None:
        """Start deposit polling and periodic anchoring loops."""
        if not self.enabled:
            return

        async def _deposit_poll_loop():
            while True:
                await asyncio.sleep(DEPOSIT_POLL_INTERVAL)
                await self.poll_deposits()

        async def _anchor_loop():
            while True:
                await asyncio.sleep(ANCHOR_INTERVAL)
                self.anchor_ledger()

        self._poll_task = asyncio.ensure_future(_deposit_poll_loop())
        self._anchor_task = asyncio.ensure_future(_anchor_loop())
        log.info(
            "Escrow background tasks started: poll=%ds anchor=%ds",
            DEPOSIT_POLL_INTERVAL,
            ANCHOR_INTERVAL,
        )

    async def stop_background_tasks(self) -> None:
        """Stop background tasks on shutdown."""
        for task in (self._poll_task, self._anchor_task):
            if task and not task.done():
                task.cancel()
        # Final anchor on shutdown
        if self.enabled:
            self.anchor_ledger()
            log.info("Final anchor submitted on shutdown")


# ---------------------------------------------------------------------------
# Module-level singleton (same pattern as pg.py)
# ---------------------------------------------------------------------------

escrow_bridge = EscrowBridge()
