# Parlameme Escrow Contracts

USDC escrow contracts for Base L2 with platform-signed withdrawals and Merkle anchoring.

## Contracts

| Contract | Description |
|----------|-------------|
| `ParlamemeEscrow.sol` | Standard escrow with deposits, withdrawals, anchoring |
| `ParlamemeEscrowV2.sol` | + EIP-2771 gasless withdrawals |

## Quick Deploy to Base Sepolia (Testnet)

### Step 1: Get Testnet ETH (Free)

1. Go to https://www.coinbase.com/faucets/base-ethereum-sepolia-faucet
2. Connect wallet or enter address
3. Request ETH (you'll get ~0.1 ETH)

### Step 2: Create Deployer Wallet

Option A: Use existing wallet
```bash
export DEPLOYER_PRIVATE_KEY=0x...your_private_key...
```

Option B: Generate new wallet
```bash
cast wallet new
# Save the private key securely!
export DEPLOYER_PRIVATE_KEY=0x...generated_key...
```

### Step 3: Deploy

```bash
cd contracts

# V1 (standard)
./deploy-sepolia.sh

# V2 (with gasless)
./deploy-sepolia.sh v2
```

### Step 4: Save Contract Address

After deployment, add to your environment:
```bash
export ESCROW_CONTRACT=0x...deployed_address...
```

## Configuration for Platform

Add to your `.env` or environment:

```bash
# Network
export BASE_NETWORK=sepolia
export BASE_RPC_URL=https://sepolia.base.org

# Contract (from deployment)
export ESCROW_CONTRACT=0x...

# Platform signing key (same as deployer or separate)
export PLATFORM_KEY=...private_key_without_0x...
```

## Contract Functions

### Deposits

```solidity
// Player deposits USDC (requires prior approval)
deposit(uint256 amount)

// Deposit on behalf of another player
depositFor(address player, uint256 amount)
```

### Withdrawals

```solidity
// Withdraw with platform signature
withdraw(uint256 amount, uint256 nonce, uint256 expiresAt, bytes signature)

// V2: Gasless withdrawal (platform pays gas)
gaslessWithdraw(address player, uint256 amount, uint256 nonce, uint256 expiresAt, bytes signature)
```

### Anchoring

```solidity
// Anchor Merkle root of ledger state
anchor(bytes32 root, uint256 entryCount)
```

## Testing Locally

```bash
# Run tests
forge test

# Run with verbosity
forge test -vvv

# Gas report
forge test --gas-report
```

## Verify on BaseScan

```bash
forge verify-contract \
  --chain-id 84532 \
  --compiler-version v0.8.24 \
  --constructor-args $(cast abi-encode "constructor(address,address)" 0x036CbD53842c5426634e7929541eC2318f3dCF7e $PLATFORM_SIGNER) \
  $ESCROW_CONTRACT \
  src/ParlamemeEscrow.sol:ParlamemeEscrow
```

## Addresses

### Base Sepolia (Testnet)
- USDC: `0x036CbD53842c5426634e7929541eC2318f3dCF7e`
- Chain ID: `84532`
- RPC: `https://sepolia.base.org`
- Explorer: `https://sepolia.basescan.org`

### Base Mainnet
- USDC: `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913`
- Chain ID: `8453`
- RPC: `https://mainnet.base.org`
- Explorer: `https://basescan.org`

## Security Notes

1. **Never commit private keys** to git
2. **Platform key** should be stored securely (AWS KMS, Vault)
3. **Signatures expire** after 30 minutes
4. **Nonces prevent** replay attacks
5. **Owner can** emergency withdraw (use with caution)
