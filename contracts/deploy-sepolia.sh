#!/bin/bash
# Deploy ParlamemeEscrow to Base Sepolia
#
# Prerequisites:
# 1. Foundry installed (forge, cast)
# 2. Environment variables set (see below)
#
# Base Sepolia USDC: 0x036CbD53842c5426634e7929541eC2318f3dCF7e
# RPC: https://sepolia.base.org
# Faucet: https://www.coinbase.com/faucets/base-ethereum-sepolia-faucet

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Configuration
RPC_URL="${BASE_SEPOLIA_RPC:-https://sepolia.base.org}"
USDC_ADDRESS="0x036CbD53842c5426634e7929541eC2318f3dCF7e"
CHAIN_ID=84532

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}=== ParlamemeEscrow Deployment ===${NC}"
echo ""

# Check required env vars
if [ -z "$DEPLOYER_PRIVATE_KEY" ]; then
    echo -e "${RED}Error: DEPLOYER_PRIVATE_KEY not set${NC}"
    echo ""
    echo "Set your deployer wallet private key:"
    echo "  export DEPLOYER_PRIVATE_KEY=0x..."
    echo ""
    echo "Get testnet ETH from: https://www.coinbase.com/faucets/base-ethereum-sepolia-faucet"
    exit 1
fi

if [ -z "$PLATFORM_SIGNER" ]; then
    echo -e "${YELLOW}Warning: PLATFORM_SIGNER not set, using deployer address${NC}"
    PLATFORM_SIGNER=$(cast wallet address --private-key "$DEPLOYER_PRIVATE_KEY")
fi

echo "Network:         Base Sepolia"
echo "RPC:             $RPC_URL"
echo "USDC:            $USDC_ADDRESS"
echo "Platform Signer: $PLATFORM_SIGNER"
echo ""

# Check if forge is installed
if ! command -v forge &> /dev/null; then
    echo -e "${RED}Foundry not found. Install with:${NC}"
    echo "  curl -L https://foundry.paradigm.xyz | bash"
    echo "  foundryup"
    exit 1
fi

# Check deployer balance
DEPLOYER_ADDRESS=$(cast wallet address --private-key "$DEPLOYER_PRIVATE_KEY")
BALANCE=$(cast balance "$DEPLOYER_ADDRESS" --rpc-url "$RPC_URL" 2>/dev/null || echo "0")
BALANCE_ETH=$(cast from-wei "$BALANCE" 2>/dev/null || echo "0")

echo "Deployer:        $DEPLOYER_ADDRESS"
echo "Balance:         $BALANCE_ETH ETH"
echo ""

if [ "$BALANCE" = "0" ]; then
    echo -e "${RED}Error: Deployer has no ETH for gas${NC}"
    echo ""
    echo "Get testnet ETH from:"
    echo "  https://www.coinbase.com/faucets/base-ethereum-sepolia-faucet"
    exit 1
fi

# Select contract version
VERSION="${1:-v1}"
if [ "$VERSION" = "v2" ]; then
    CONTRACT="ParlamemeEscrowV2"
    # V2 needs trusted forwarder address
    FORWARDER="${TRUSTED_FORWARDER:-0x0000000000000000000000000000000000000000}"
    CONSTRUCTOR_ARGS="$USDC_ADDRESS $PLATFORM_SIGNER $FORWARDER"
else
    CONTRACT="ParlamemeEscrow"
    CONSTRUCTOR_ARGS="$USDC_ADDRESS $PLATFORM_SIGNER"
fi

echo -e "${GREEN}Deploying $CONTRACT...${NC}"
echo ""

# Deploy
DEPLOY_OUTPUT=$(forge create \
    --rpc-url "$RPC_URL" \
    --private-key "$DEPLOYER_PRIVATE_KEY" \
    --constructor-args $CONSTRUCTOR_ARGS \
    "src/${CONTRACT}.sol:${CONTRACT}" \
    --json 2>&1)

# Check if deployment succeeded
if echo "$DEPLOY_OUTPUT" | jq -e '.deployedTo' > /dev/null 2>&1; then
    CONTRACT_ADDRESS=$(echo "$DEPLOY_OUTPUT" | jq -r '.deployedTo')
    TX_HASH=$(echo "$DEPLOY_OUTPUT" | jq -r '.transactionHash')

    echo -e "${GREEN}=== Deployment Successful ===${NC}"
    echo ""
    echo "Contract:  $CONTRACT_ADDRESS"
    echo "Tx Hash:   $TX_HASH"
    echo ""
    echo -e "${YELLOW}Add to your environment:${NC}"
    echo "  export ESCROW_CONTRACT=$CONTRACT_ADDRESS"
    echo ""
    echo "Verify on BaseScan:"
    echo "  https://sepolia.basescan.org/address/$CONTRACT_ADDRESS"
    echo ""

    # Save deployment info
    DEPLOY_FILE="deployments/sepolia-$(date +%Y%m%d-%H%M%S).json"
    mkdir -p deployments
    echo "$DEPLOY_OUTPUT" | jq ". + {contract: \"$CONTRACT\", platformSigner: \"$PLATFORM_SIGNER\", usdc: \"$USDC_ADDRESS\"}" > "$DEPLOY_FILE"
    echo "Deployment saved to: $DEPLOY_FILE"
else
    echo -e "${RED}Deployment failed:${NC}"
    echo "$DEPLOY_OUTPUT"
    exit 1
fi
