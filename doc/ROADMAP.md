# Parlameme Roadmap

## Overview

Parlameme is a data-driven game engine with Flow v3 DSL, designed for transparent, verifiable multiplayer games with real-money stakes.

```
Phase 1: Base Escrow (MVP)          ← Current
Phase 2: 0G Integration (Trustless)
Phase 3: Full Decentralization
```

---

## Phase 1: Base L2 Escrow (MVP)

**Status:** Implementation complete, deployment pending

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     PLATFORM (Clojure)                       │
├─────────────────────────────────────────────────────────────┤
│  Game Engine    │  Ledger (EDN)   │  Escrow API             │
│  Flow v3 DSL    │  Balance track  │  Deposits/Withdrawals   │
└────────┬────────┴────────┬────────┴────────┬────────────────┘
         │                 │                 │
         │    ┌────────────┴──────────┐      │
         │    │   Merkle Anchoring    │      │
         │    │   (proof of history)  │      │
         │    └────────────┬──────────┘      │
         │                 │                 │
         └─────────────────┼─────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                    BASE L2 (Coinbase)                        │
│  ParlamemeEscrow.sol    │    USDC    │    Merkle Roots      │
└─────────────────────────────────────────────────────────────┘
```

### Components

| Component | Status | Description |
|-----------|--------|-------------|
| Flow v3 DSL | ✅ Done | Game definition language |
| Game Runtime | ✅ Done | Execution engine |
| Archive Persistence | ✅ Done | Deterministic replay |
| Ledger System | ✅ Done | Balance tracking |
| Escrow Protocol | ✅ Done | IEscrow interface |
| Base Integration | ✅ Done | web3j + contracts |
| Merkle Anchoring | ✅ Done | On-chain proofs |
| Contract Deployment | 🔄 Pending | Sepolia → Mainnet |

### Deployment Steps

1. Get Base Sepolia ETH from faucet
2. Deploy ParlamemeEscrow.sol to Sepolia
3. Test deposits/withdrawals
4. Audit contracts
5. Deploy to Base Mainnet

### Trust Model

- **Money (USDC):** Trustless - on-chain escrow
- **Game History:** Verifiable - deterministic archives
- **Merkle Anchors:** Verifiable - hash on-chain, archives provide proof

---

## Phase 2: 0G Integration (Trustless History)

**Status:** Research complete, implementation planned

### Why 0G (Zero Gravity)?

0G is a modular AI blockchain optimized for high-throughput data availability and storage.

#### Performance Comparison

| Metric | 0G | Celestia | EigenDA | Ethereum |
|--------|-----|----------|---------|----------|
| **TPS** | 11,000/shard | ~700 | N/A | ~15 |
| **Finality** | <1 sec | 6s + 10min | ~12 min | ~12 min |
| **DA throughput** | 10-30 MB/s | ~1.3 MB/s | 100 MB/s | ~0.08 MB/s |
| **Block time** | 500ms | 12 sec | N/A | 12 sec |

#### Cost Comparison

| Type | 0G | Ethereum | Notes |
|------|-----|----------|-------|
| Storage | ~$5/TB/month | ~$32K/KB | 0G is ~6M× cheaper |
| Transaction gas | Low (TBD) | $1-50 | 0G fees not finalized |

### Benefits for Parlameme

1. **Full Transparency** - All game archives on-chain, publicly auditable
2. **Trustless Verification** - Anyone can verify game was fair via replay
3. **Low Latency** - 500ms blocks sufficient for turn-based games
4. **Cheap Storage** - $5/TB enables full archive storage
5. **EVM Compatible** - Reuse Solidity knowledge

### Implementation Plan

#### Step 2.1: Testnet Pilot
- Deploy archive storage contract to Galileo testnet
- Record one complete game archive
- Verify retrieval and proof generation
- Measure actual costs and latency

#### Step 2.2: Cross-chain Bridge
- Design verification protocol between Base ↔ 0G
- Implement merkle proof submission from 0G to Base
- Enable dispute resolution using 0G archives

#### Step 2.3: Production Migration
- Audit 0G integration
- Deploy to 0G mainnet
- Gradual migration of archives

---

## Phase 3: Full Decentralization

**Status:** Future planning

### Goals

1. **Decentralized Game Hosting** - Multiple platform operators
2. **On-chain Game Logic** - Smart contract execution
3. **DAO Governance** - Community-driven development
4. **Token Economy** - Platform token for staking/rewards

---

## Timeline

| Phase | Target | Dependencies |
|-------|--------|--------------|
| 1.1 Contract Deploy | Q1 2026 | Testnet ETH |
| 1.2 Mainnet Launch | Q1 2026 | Audit complete |
| 2.1 0G Testnet Pilot | Q2 2026 | Phase 1 stable |
| 2.2 Cross-chain Bridge | Q2-Q3 2026 | Pilot success |
| 2.3 0G Production | Q3 2026 | Bridge tested |
| 3.x Decentralization | 2027+ | Community growth |

---

## References

### Documentation
- [Base L2 Escrow](base_l2_escrow.md) - Escrow implementation details
- [Stateful MCP](stateful_mcp_architecture.md) - AI agent integration
- [Flow v3 Spec](flow_v3_specification.md) - Game DSL specification

### External
- [Base Docs](https://docs.base.org/)
- [0G Docs](https://docs.0g.ai/)
