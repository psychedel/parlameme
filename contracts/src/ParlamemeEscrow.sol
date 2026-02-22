// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import "@openzeppelin/contracts/utils/cryptography/MessageHashUtils.sol";
import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/**
 * @title ParlamemeEscrow
 * @notice Escrow contract for Parlameme game platform on Base L2
 * @dev Handles USDC deposits, withdrawals with platform signatures, and Merkle root anchoring
 *
 * Flow:
 * 1. Player deposits USDC → Deposit event emitted → Platform credits ledger
 * 2. Player requests withdrawal → Platform signs authorization → Player calls withdraw()
 * 3. Platform periodically anchors Merkle roots of ledger state
 */
contract ParlamemeEscrow is Ownable, ReentrancyGuard {
    using SafeERC20 for IERC20;
    using ECDSA for bytes32;
    using MessageHashUtils for bytes32;

    // =============================================================================
    // State
    // =============================================================================

    /// @notice USDC token contract
    IERC20 public immutable usdc;

    /// @notice Platform signer address (authorizes withdrawals)
    address public platformSigner;

    /// @notice Player balances in USDC (6 decimals)
    mapping(address => uint256) public balances;

    /// @notice Withdrawal nonces per player (prevent replay)
    mapping(address => uint256) public nonces;

    /// @notice Anchored Merkle roots
    bytes32[] public anchoredRoots;

    /// @notice Anchor metadata
    struct Anchor {
        bytes32 root;
        uint256 entryCount;
        uint256 timestamp;
        uint256 blockNumber;
    }
    mapping(uint256 => Anchor) public anchors;

    /// @notice Withdrawal expiry window (30 minutes)
    uint256 public constant WITHDRAWAL_EXPIRY = 30 minutes;

    /// @notice Minimum deposit amount (1 USDC = 1_000_000)
    uint256 public constant MIN_DEPOSIT = 1_000_000;

    // =============================================================================
    // Events
    // =============================================================================

    event Deposit(address indexed player, uint256 amount, bytes32 txHash);
    event Withdrawal(address indexed player, uint256 amount, uint256 nonce);
    event Anchored(bytes32 indexed root, uint256 entryCount, uint256 timestamp);
    event PlatformSignerUpdated(address indexed oldSigner, address indexed newSigner);

    // =============================================================================
    // Errors
    // =============================================================================

    error InvalidAmount();
    error InvalidSignature();
    error WithdrawalExpired();
    error InvalidNonce();
    error InsufficientBalance();
    error TransferFailed();

    // =============================================================================
    // Constructor
    // =============================================================================

    /**
     * @param _usdc USDC token address
     * @param _platformSigner Initial platform signer
     */
    constructor(address _usdc, address _platformSigner) Ownable(msg.sender) {
        usdc = IERC20(_usdc);
        platformSigner = _platformSigner;
    }

    // =============================================================================
    // Deposit
    // =============================================================================

    /**
     * @notice Deposit USDC into escrow
     * @param amount Amount in USDC (6 decimals)
     * @dev Emits Deposit event that platform listens for
     */
    function deposit(uint256 amount) external nonReentrant {
        if (amount < MIN_DEPOSIT) revert InvalidAmount();

        // Transfer USDC from player
        usdc.safeTransferFrom(msg.sender, address(this), amount);

        // Credit balance
        balances[msg.sender] += amount;

        // Emit event with tx hash for platform tracking
        emit Deposit(msg.sender, amount, blockhash(block.number - 1));
    }

    /**
     * @notice Deposit USDC on behalf of another player
     * @param player Player to credit
     * @param amount Amount in USDC
     */
    function depositFor(address player, uint256 amount) external nonReentrant {
        if (amount < MIN_DEPOSIT) revert InvalidAmount();
        if (player == address(0)) revert InvalidAmount();

        usdc.safeTransferFrom(msg.sender, address(this), amount);
        balances[player] += amount;

        emit Deposit(player, amount, blockhash(block.number - 1));
    }

    // =============================================================================
    // Withdrawal
    // =============================================================================

    /**
     * @notice Withdraw USDC with platform authorization
     * @param amount Amount to withdraw
     * @param nonce Current nonce (from platform)
     * @param expiresAt Signature expiry timestamp
     * @param signature Platform signature
     */
    function withdraw(
        uint256 amount,
        uint256 nonce,
        uint256 expiresAt,
        bytes calldata signature
    ) external nonReentrant {
        // Validate expiry
        if (block.timestamp > expiresAt) revert WithdrawalExpired();

        // Validate nonce
        if (nonce != nonces[msg.sender]) revert InvalidNonce();

        // Validate balance
        if (balances[msg.sender] < amount) revert InsufficientBalance();

        // Verify platform signature
        bytes32 messageHash = keccak256(abi.encodePacked(
            msg.sender,
            amount,
            nonce,
            expiresAt,
            block.chainid,
            address(this)
        ));
        bytes32 ethSignedHash = messageHash.toEthSignedMessageHash();

        if (ethSignedHash.recover(signature) != platformSigner) {
            revert InvalidSignature();
        }

        // Update state
        balances[msg.sender] -= amount;
        nonces[msg.sender]++;

        // Transfer USDC
        usdc.safeTransfer(msg.sender, amount);

        emit Withdrawal(msg.sender, amount, nonce);
    }

    // =============================================================================
    // Anchoring
    // =============================================================================

    /**
     * @notice Anchor a Merkle root of ledger state
     * @param root Merkle root hash
     * @param entryCount Number of entries in tree
     * @dev Only platform signer can anchor
     */
    function anchor(bytes32 root, uint256 entryCount) external {
        require(msg.sender == platformSigner || msg.sender == owner(), "Unauthorized");

        uint256 index = anchoredRoots.length;
        anchoredRoots.push(root);

        anchors[index] = Anchor({
            root: root,
            entryCount: entryCount,
            timestamp: block.timestamp,
            blockNumber: block.number
        });

        emit Anchored(root, entryCount, block.timestamp);
    }

    /**
     * @notice Get anchor count
     */
    function anchorCount() external view returns (uint256) {
        return anchoredRoots.length;
    }

    /**
     * @notice Get anchor by index
     */
    function getAnchor(uint256 index) external view returns (Anchor memory) {
        return anchors[index];
    }

    // =============================================================================
    // Admin
    // =============================================================================

    /**
     * @notice Update platform signer
     * @param newSigner New signer address
     */
    function setPlatformSigner(address newSigner) external onlyOwner {
        require(newSigner != address(0), "Invalid signer");
        emit PlatformSignerUpdated(platformSigner, newSigner);
        platformSigner = newSigner;
    }

    /**
     * @notice Emergency withdrawal by owner
     * @param token Token to withdraw (use address(0) for ETH)
     * @param amount Amount to withdraw
     */
    function emergencyWithdraw(address token, uint256 amount) external onlyOwner {
        if (token == address(0)) {
            payable(owner()).transfer(amount);
        } else {
            IERC20(token).safeTransfer(owner(), amount);
        }
    }

    // =============================================================================
    // View Functions
    // =============================================================================

    /**
     * @notice Get player balance
     */
    function getBalance(address player) external view returns (uint256) {
        return balances[player];
    }

    /**
     * @notice Get next nonce for player
     */
    function getNonce(address player) external view returns (uint256) {
        return nonces[player];
    }

    /**
     * @notice Get total USDC in escrow
     */
    function totalDeposits() external view returns (uint256) {
        return usdc.balanceOf(address(this));
    }
}
