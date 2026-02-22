// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import "@openzeppelin/contracts/utils/cryptography/MessageHashUtils.sol";
import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import "@openzeppelin/contracts/metatx/ERC2771Context.sol";

/**
 * @title ParlamemeEscrowV2
 * @notice Escrow contract with EIP-2771 meta-transaction support for gasless operations
 * @dev Inherits ERC2771Context for trusted forwarder pattern
 *
 * GASLESS FLOW (EIP-2771):
 * 1. Player signs ForwardRequest off-chain (no gas needed)
 * 2. Platform (Relayer) submits to MinimalForwarder (platform pays gas)
 * 3. Forwarder verifies signature, calls this contract
 * 4. _msgSender() returns original player address (not relayer)
 *
 * STANDARD FLOW (direct calls still work):
 * 1. Player calls contract directly (player pays gas)
 * 2. msg.sender == player
 */
contract ParlamemeEscrowV2 is Ownable, ReentrancyGuard, ERC2771Context {
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

    /// @notice Meta-tx nonces per player (for gasless ops, separate from withdrawal nonces)
    mapping(address => uint256) public metaTxNonces;

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
    event GaslessWithdrawal(
        address indexed player,
        uint256 amount,
        address indexed relayer
    );
    event Anchored(bytes32 indexed root, uint256 entryCount, uint256 timestamp);
    event PlatformSignerUpdated(
        address indexed oldSigner,
        address indexed newSigner
    );

    // =============================================================================
    // Errors
    // =============================================================================

    error InvalidAmount();
    error InvalidSignature();
    error WithdrawalExpired();
    error InvalidNonce();
    error InsufficientBalance();
    error TransferFailed();
    error ZeroAddress();

    // =============================================================================
    // Constructor
    // =============================================================================

    /**
     * @param _usdc USDC token address
     * @param _platformSigner Initial platform signer
     * @param _trustedForwarder OpenZeppelin MinimalForwarder address
     */
    constructor(
        address _usdc,
        address _platformSigner,
        address _trustedForwarder
    ) Ownable(msg.sender) ERC2771Context(_trustedForwarder) {
        usdc = IERC20(_usdc);
        platformSigner = _platformSigner;
    }

    // =============================================================================
    // ERC2771 Overrides - CRITICAL for meta-tx to work
    // =============================================================================

    /**
     * @dev Returns the sender of the transaction.
     * If called via trusted forwarder, extracts original sender from calldata.
     */
    function _msgSender()
        internal
        view
        override(Context, ERC2771Context)
        returns (address)
    {
        return ERC2771Context._msgSender();
    }

    /**
     * @dev Returns the calldata of the transaction.
     */
    function _msgData()
        internal
        view
        override(Context, ERC2771Context)
        returns (bytes calldata)
    {
        return ERC2771Context._msgData();
    }

    /**
     * @dev Returns the length of the context suffix for ERC2771.
     */
    function _contextSuffixLength()
        internal
        view
        override(Context, ERC2771Context)
        returns (uint256)
    {
        return ERC2771Context._contextSuffixLength();
    }

    // =============================================================================
    // Deposit (works with both direct calls and meta-tx)
    // =============================================================================

    /**
     * @notice Deposit USDC into escrow
     * @param amount Amount in USDC (6 decimals)
     * @dev Uses _msgSender() for ERC2771 compatibility
     */
    function deposit(uint256 amount) external nonReentrant {
        if (amount < MIN_DEPOSIT) revert InvalidAmount();

        address player = _msgSender();

        // Transfer USDC from player
        usdc.safeTransferFrom(player, address(this), amount);

        // Credit balance
        balances[player] += amount;

        // Emit event with tx hash for platform tracking
        emit Deposit(player, amount, blockhash(block.number - 1));
    }

    /**
     * @notice Deposit USDC on behalf of another player
     * @param player Player to credit
     * @param amount Amount in USDC
     */
    function depositFor(address player, uint256 amount) external nonReentrant {
        if (amount < MIN_DEPOSIT) revert InvalidAmount();
        if (player == address(0)) revert ZeroAddress();

        address sender = _msgSender();
        usdc.safeTransferFrom(sender, address(this), amount);
        balances[player] += amount;

        emit Deposit(player, amount, blockhash(block.number - 1));
    }

    // =============================================================================
    // Withdrawal - Standard (player pays gas)
    // =============================================================================

    /**
     * @notice Withdraw USDC with platform authorization (player pays gas)
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
        address player = _msgSender();

        // Validate expiry
        if (block.timestamp > expiresAt) revert WithdrawalExpired();

        // Validate nonce
        if (nonce != nonces[player]) revert InvalidNonce();

        // Validate balance
        if (balances[player] < amount) revert InsufficientBalance();

        // Verify platform signature
        bytes32 messageHash = keccak256(
            abi.encodePacked(
                player,
                amount,
                nonce,
                expiresAt,
                block.chainid,
                address(this)
            )
        );
        bytes32 ethSignedHash = messageHash.toEthSignedMessageHash();

        if (ethSignedHash.recover(signature) != platformSigner) {
            revert InvalidSignature();
        }

        // Update state
        balances[player] -= amount;
        nonces[player]++;

        // Transfer USDC
        usdc.safeTransfer(player, amount);

        emit Withdrawal(player, amount, nonce);
    }

    // =============================================================================
    // Gasless Withdrawal - Platform as Relayer (platform pays gas)
    // =============================================================================

    /**
     * @notice Gasless withdrawal - platform submits on behalf of player
     * @param player Player requesting withdrawal
     * @param amount Amount to withdraw
     * @param playerNonce Player's meta-tx nonce
     * @param expiresAt Signature expiry timestamp
     * @param playerSignature Player's EIP-712 signature authorizing withdrawal
     * @dev Only callable by platform signer (relayer)
     *
     * FLOW:
     * 1. Player signs withdrawal request off-chain
     * 2. Platform verifies signature + ledger balance
     * 3. Platform calls this function (pays gas)
     * 4. USDC transferred directly to player
     */
    function gaslessWithdraw(
        address player,
        uint256 amount,
        uint256 playerNonce,
        uint256 expiresAt,
        bytes calldata playerSignature
    ) external nonReentrant {
        // Only platform can relay
        require(_msgSender() == platformSigner, "Only relayer");

        // Validate expiry
        if (block.timestamp > expiresAt) revert WithdrawalExpired();

        // Validate nonce
        if (playerNonce != metaTxNonces[player]) revert InvalidNonce();

        // Validate balance
        if (balances[player] < amount) revert InsufficientBalance();

        // Verify player signature (EIP-712 typed data)
        bytes32 structHash = keccak256(
            abi.encode(
                keccak256(
                    "GaslessWithdraw(address player,uint256 amount,uint256 nonce,uint256 expiresAt)"
                ),
                player,
                amount,
                playerNonce,
                expiresAt
            )
        );

        bytes32 domainSeparator = keccak256(
            abi.encode(
                keccak256(
                    "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
                ),
                keccak256("ParlamemeEscrow"),
                keccak256("2"),
                block.chainid,
                address(this)
            )
        );

        bytes32 digest = keccak256(
            abi.encodePacked("\x19\x01", domainSeparator, structHash)
        );

        address signer = digest.recover(playerSignature);
        if (signer != player) revert InvalidSignature();

        // Update state
        balances[player] -= amount;
        metaTxNonces[player]++;

        // Transfer USDC to player
        usdc.safeTransfer(player, amount);

        emit GaslessWithdrawal(player, amount, _msgSender());
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
        address sender = _msgSender();
        require(sender == platformSigner || sender == owner(), "Unauthorized");

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
        if (newSigner == address(0)) revert ZeroAddress();
        emit PlatformSignerUpdated(platformSigner, newSigner);
        platformSigner = newSigner;
    }

    /**
     * @notice Emergency withdrawal by owner
     * @param token Token to withdraw (use address(0) for ETH)
     * @param amount Amount to withdraw
     */
    function emergencyWithdraw(
        address token,
        uint256 amount
    ) external onlyOwner {
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
     * @notice Get next withdrawal nonce for player
     */
    function getNonce(address player) external view returns (uint256) {
        return nonces[player];
    }

    /**
     * @notice Get next meta-tx nonce for player (for gasless ops)
     */
    function getMetaTxNonce(address player) external view returns (uint256) {
        return metaTxNonces[player];
    }

    /**
     * @notice Get total USDC in escrow
     */
    function totalDeposits() external view returns (uint256) {
        return usdc.balanceOf(address(this));
    }

    /**
     * @notice Check if address is trusted forwarder
     */
    function isTrustedForwarder(
        address forwarder
    ) public view override returns (bool) {
        return super.isTrustedForwarder(forwarder);
    }

    /**
     * @notice Get trusted forwarder address
     */
    function getTrustedForwarder() public view returns (address) {
        return trustedForwarder();
    }
}
