#!/usr/bin/env bash
# Test withdrawal with platform signature - FIXED version
# Following OpenZeppelin ECDSA best practices

set -e

# Configuration
PRIVATE_KEY="0xb8a7ad59f4312c15a0f1f0d0a90d241df99277cd7b198c24f9e456806eb5b679"
WALLET="0xb1BB8de8e7AA8412Be0A6C90Ae02AC088f737476"
ESCROW="0x5c3214cA4C9CEfD4f91df3e5aae5610148dCBC48"
RPC="https://sepolia.base.org"
CHAIN_ID="84532"

# Withdrawal params
AMOUNT="500000"  # 0.5 USDC
NONCE="0"
EXPIRES_AT=$(($(date +%s) + 1800))  # 30 min from now

echo "=== Withdrawal Test (v2 - Fixed) ==="
echo "Wallet: $WALLET"
echo "Amount: $AMOUNT (0.5 USDC)"  
echo "Nonce: $NONCE"
echo "Expires: $EXPIRES_AT"
echo "Chain ID: $CHAIN_ID"
echo "Contract: $ESCROW"
echo ""

# Step 1: Create the message hash EXACTLY as the contract does
# Contract uses: keccak256(abi.encodePacked(msg.sender, amount, nonce, expiresAt, block.chainid, address(this)))
echo "Step 1: Creating message hash using abi.encodePacked..."

# abi.encodePacked concatenates without padding:
# address = 20 bytes, uint256 = 32 bytes each
# Total: 20 + 32 + 32 + 32 + 32 + 20 = 168 bytes

# Convert to lowercase for consistency
WALLET_LOWER=$(echo "$WALLET" | tr '[:upper:]' '[:lower:]')
ESCROW_LOWER=$(echo "$ESCROW" | tr '[:upper:]' '[:lower:]')

# Build packed data manually
# address (20 bytes) - remove 0x prefix
WALLET_PACKED="${WALLET_LOWER#0x}"
# uint256 amount (32 bytes) - pad to 64 hex chars
AMOUNT_HEX=$(printf "%064x" $AMOUNT)
# uint256 nonce (32 bytes)
NONCE_HEX=$(printf "%064x" $NONCE)
# uint256 expiresAt (32 bytes)
EXPIRES_HEX=$(printf "%064x" $EXPIRES_AT)
# uint256 chainId (32 bytes)
CHAINID_HEX=$(printf "%064x" $CHAIN_ID)
# address (20 bytes)
ESCROW_PACKED="${ESCROW_LOWER#0x}"

PACKED_DATA="0x${WALLET_PACKED}${AMOUNT_HEX}${NONCE_HEX}${EXPIRES_HEX}${CHAINID_HEX}${ESCROW_PACKED}"

echo "Packed data: $PACKED_DATA"
echo "Length: $((${#PACKED_DATA} - 2)) hex chars = $(((${#PACKED_DATA} - 2) / 2)) bytes"

# Compute keccak256
MESSAGE_HASH=$(/home/user/.foundry/bin/cast keccak256 "$PACKED_DATA")
echo "Message hash: $MESSAGE_HASH"

# Step 2: Sign the message
# cast wallet sign WITHOUT --no-hash will:
# 1. Add "\x19Ethereum Signed Message:\n32" prefix
# 2. Hash the result
# This matches contract's: messageHash.toEthSignedMessageHash()

echo ""
echo "Step 2: Signing with Ethereum Signed Message prefix..."
echo "(cast wallet sign adds the prefix automatically)"

SIGNATURE=$(/home/user/.foundry/bin/cast wallet sign --private-key $PRIVATE_KEY "$MESSAGE_HASH")
echo "Signature: $SIGNATURE"

# Verify signer
echo ""
echo "Step 3: Verifying signature locally..."
RECOVERED=$(/home/user/.foundry/bin/cast wallet verify --address $WALLET "$MESSAGE_HASH" "$SIGNATURE" 2>&1 || echo "FAILED")
echo "Verification: $RECOVERED"

# Step 4: Call withdraw
echo ""
echo "Step 4: Calling withdraw on contract..."
/home/user/.foundry/bin/cast send $ESCROW \
  "withdraw(uint256,uint256,uint256,bytes)" \
  $AMOUNT $NONCE $EXPIRES_AT $SIGNATURE \
  --private-key $PRIVATE_KEY \
  --rpc-url $RPC

echo ""
echo "Step 5: Verifying balances..."
echo "Escrow balance: $(/home/user/.foundry/bin/cast call $ESCROW "balances(address)(uint256)" $WALLET --rpc-url $RPC)"
echo "Wallet USDC: $(/home/user/.foundry/bin/cast call 0x036CbD53842c5426634e7929541eC2318f3dCF7e "balanceOf(address)(uint256)" $WALLET --rpc-url $RPC)"
echo "New nonce: $(/home/user/.foundry/bin/cast call $ESCROW "nonces(address)(uint256)" $WALLET --rpc-url $RPC)"
