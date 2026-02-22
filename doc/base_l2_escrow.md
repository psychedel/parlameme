# Base L2 Escrow Integration

## Status: Deployed & Tested on Base Sepolia

The escrow system integrates with Base L2 (Coinbase's Ethereum L2) for real-money USDC operations.

### Deployed Contracts

| Network | Contract Address | Explorer |
|---------|-----------------|----------|
| **Base Sepolia** | `0x5c3214cA4C9CEfD4f91df3e5aae5610148dCBC48` | [Basescan](https://sepolia.basescan.org/address/0x5c3214cA4C9CEfD4f91df3e5aae5610148dCBC48) |
| **Base Sepolia** | Same | [Blockscout](https://base-sepolia.blockscout.com/address/0x5c3214ca4c9cefd4f91df3e5aae5610148dcbc48) |
| Base Mainnet | Not deployed yet | - |

**Deployment Info:** `contracts/deployments/base-sepolia.json`

### Tested Functions (2026-01-03)

| Function | Status | Details |
|----------|--------|---------|
| `deposit()` | ✅ Passed | 1 USDC deposited, balance tracked correctly |
| `withdraw()` | ✅ Passed | EIP-191 signature verification works |
| `anchor()` | ✅ Passed | Merkle root stored and retrievable |
| `getAnchor()` | ✅ Passed | Returns root, entryCount, timestamp, blockNumber |

**Test Transactions:**
- Deposit: `0x43769c7a78207fc19a79a05b3461578ef9a341e6c415ab1910d84d15b83004a1`
- Withdraw: `0x304f7acafcbcb5abbe110771c6ae0c278c57a5791c242c15c4b66eb6f124b6be`
- Anchor: `0x6131188acc531eece11a48e41858907369f034bf277a3d22b542051615bcfb85`

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        PLATFORM (Clojure)                        │
├─────────────────────────────────────────────────────────────────┤
│  Escrow API        Ledger              Game Engine (Flow v3)    │
│  /api/escrow/*     Balance tracking    Stakes & settlements     │
│         │                │                                       │
│         └────────────────┼───────────────────────────────────────│
│                          ▼                                       │
│              BaseEscrow / BaseEscrowV2 (web3j)                   │
│              - Deposit listener (WS/polling)                     │
│              - Withdrawal signing (EIP-712)                      │
│              - Merkle anchoring                                  │
│              - Gasless withdrawals (V2, EIP-2771)                │
└──────────────────────────┬───────────────────────────────────────┘
                           │ JSON-RPC / WebSocket
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                      BASE L2 BLOCKCHAIN                          │
│  ParlamemeEscrow.sol / ParlamemeEscrowV2.sol    USDC (Circle)   │
└─────────────────────────────────────────────────────────────────┘
```

## Source Files

| File | Description |
|------|-------------|
| `src/clj/parlameme/escrow/protocol.clj` | IEscrow protocol + mock impl |
| `src/clj/parlameme/escrow/base.clj` | Base L2 implementation (V1 & V2) |
| `src/clj/parlameme/escrow/api.clj` | HTTP API endpoints |
| `src/clj/parlameme/ledger/core.clj` | Balance tracking & operations |
| `src/clj/parlameme/ledger/merkle.clj` | Merkle tree for anchoring |
| `contracts/ParlamemeEscrow.sol` | Smart contract (V1) |
| `contracts/ParlamemeEscrowV2.sol` | Smart contract with gasless (V2) |

## Key Features

### Deposits
- Event listening via WebSocket or polling fallback
- 12-block confirmation requirement
- Automatic ledger crediting

### Withdrawals
- EIP-712 typed data signatures
- 30-minute expiration, nonce-based replay protection
- V2: Gasless withdrawals (platform pays gas)

### Anchoring
- Periodic Merkle root anchoring to chain
- Cryptographic proof of ledger integrity
- Entry verification against on-chain roots

## HTTP Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/escrow/status` | System status |
| GET | `/api/escrow/mode` | Current mode (mock/sepolia/base) |
| GET | `/api/escrow/balance/:player` | Player balance |
| POST | `/api/escrow/withdraw` | Request withdrawal signature |
| POST | `/api/escrow/anchor` | Anchor ledger to chain |
| GET | `/api/escrow/verify/:entry-id` | Verify entry against anchor |

## Current Mode

Development uses **mock mode** (`parlameme.escrow.protocol/mock-escrow`) - no blockchain, instant operations. Switch to real escrow via environment variables.

## Configuration

Copy `.env.example` to `.env` and configure:

```bash
# Use testnet
ESCROW_MODE=sepolia
ESCROW_CONTRACT_SEPOLIA=0x5c3214cA4C9CEfD4f91df3e5aae5610148dCBC48
PLATFORM_SIGNER_KEY=<your-private-key>

# Or for mainnet (after deployment)
ESCROW_MODE=base
ESCROW_CONTRACT_MAINNET=<mainnet-address>
MAINNET_PLATFORM_SIGNER_KEY=<production-key>
```

## CLI Tool

Use `escrow-cli.sh` for testing:

```bash
# Check contract status
~/.claude/scripts/escrow-cli.sh --chain-status

# Check on-chain balance
~/.claude/scripts/escrow-cli.sh --chain-balance 0x...

# View anchored Merkle roots
~/.claude/scripts/escrow-cli.sh --chain-anchors
```

## Withdrawal Signature (Best Practices)

Based on [OpenZeppelin ECDSA](https://docs.openzeppelin.com/contracts/5.x/api/utils/cryptography) and [Foundry cast wallet sign](https://getfoundry.sh/cast/reference/wallet/sign/):

### Contract uses EIP-191 (Ethereum Signed Message)

```solidity
// ParlamemeEscrow.sol
bytes32 messageHash = keccak256(abi.encodePacked(
    msg.sender,     // address (20 bytes)
    amount,         // uint256 (32 bytes)  
    nonce,          // uint256 (32 bytes)
    expiresAt,      // uint256 (32 bytes)
    block.chainid,  // uint256 (32 bytes)
    address(this)   // address (20 bytes)
));
bytes32 ethSignedHash = messageHash.toEthSignedMessageHash();
```

### Signing with Foundry cast

```bash
# 1. Build packed data (abi.encodePacked format)
PACKED="0x${WALLET:2}$(printf '%064x' $AMOUNT)$(printf '%064x' $NONCE)$(printf '%064x' $EXPIRES)$(printf '%064x' $CHAIN_ID)${CONTRACT:2}"

# 2. Hash it
MESSAGE_HASH=$(cast keccak256 "$PACKED")

# 3. Sign (cast adds EIP-191 prefix automatically)
SIGNATURE=$(cast wallet sign --private-key $KEY "$MESSAGE_HASH")

# 4. Call withdraw
cast send $CONTRACT "withdraw(uint256,uint256,uint256,bytes)" $AMOUNT $NONCE $EXPIRES $SIGNATURE
```

**Important:** Do NOT use `--no-hash` flag. The contract expects the Ethereum Signed Message prefix.

## Testing in Mock Mode

```clojure
(require '[parlameme.escrow.protocol :as proto])

;; Simulate deposit
(proto/simulate-deposit! (proto/get-escrow) :alice 10000)

;; Check balance
(require '[parlameme.ledger.core :as ledger])
(ledger/get-balance :alice)  ; => 10000
```
