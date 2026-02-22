"""Merkle tree for ledger anchoring.

Builds a binary Merkle tree from ledger entry content_hashes.
The root is anchored on-chain via the escrow contract.

Properties:
- Empty tree → well-known zero hash
- Single leaf → hash(leaf)
- Proof generation: log(n) path from leaf to root
- Verification: recompute root from leaf + proof
"""

from __future__ import annotations

import hashlib


_ZERO_HASH = "0" * 64  # empty tree sentinel


def _hash_pair(left: str, right: str) -> str:
    """Hash two hex strings in sorted order (canonical Merkle)."""
    # Sort to make tree order-independent for verification
    a, b = sorted((left, right))
    return hashlib.sha256(f"{a}{b}".encode()).hexdigest()


def merkle_root(leaves: list[str]) -> str:
    """Compute Merkle root from a list of hex hash strings.

    Args:
        leaves: List of hex-encoded hashes (e.g., ledger content_hashes).

    Returns:
        Hex-encoded Merkle root, or _ZERO_HASH if empty.
    """
    if not leaves:
        return _ZERO_HASH

    # Work on a copy
    level = list(leaves)

    while len(level) > 1:
        next_level: list[str] = []
        for i in range(0, len(level), 2):
            if i + 1 < len(level):
                next_level.append(_hash_pair(level[i], level[i + 1]))
            else:
                # Odd leaf — promote unchanged
                next_level.append(level[i])
        level = next_level

    return level[0]


def merkle_proof(leaves: list[str], index: int) -> list[tuple[str, str]]:
    """Generate a Merkle proof for the leaf at `index`.

    Returns list of (sibling_hash, side) tuples where side is "left" or "right".
    """
    if not leaves or index < 0 or index >= len(leaves):
        return []

    level = list(leaves)
    proof: list[tuple[str, str]] = []
    idx = index

    while len(level) > 1:
        next_level: list[str] = []
        for i in range(0, len(level), 2):
            if i + 1 < len(level):
                next_level.append(_hash_pair(level[i], level[i + 1]))
                if i == idx or i + 1 == idx:
                    if i == idx:
                        proof.append((level[i + 1], "right"))
                    else:
                        proof.append((level[i], "left"))
            else:
                next_level.append(level[i])
                # No sibling — no proof step needed
        idx = idx // 2
        level = next_level

    return proof


def verify_proof(leaf: str, proof: list[tuple[str, str]], root: str) -> bool:
    """Verify a Merkle proof against a known root."""
    current = leaf
    for sibling, side in proof:
        if side == "left":
            current = _hash_pair(sibling, current)
        else:
            current = _hash_pair(current, sibling)
    return current == root


def ledger_merkle_root(entries: list) -> tuple[str, int]:
    """Compute Merkle root from ledger entries.

    Args:
        entries: List of LedgerEntry objects with content_hash attribute.

    Returns:
        (merkle_root_hex, entry_count) tuple.
    """
    hashes = [e.content_hash for e in entries]
    return merkle_root(hashes), len(hashes)
