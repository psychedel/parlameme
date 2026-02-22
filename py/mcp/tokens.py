"""HMAC-SHA256 invite tokens for MCP game sessions."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time

log = logging.getLogger(__name__)

_SECRET: bytes = b""


def _get_secret() -> bytes:
    """Lazy-load secret from env on first use."""
    global _SECRET
    if not _SECRET:
        env = os.environ.get("GAME_TOKEN_SECRET", "")
        if env:
            _SECRET = env.encode()
        else:
            log.warning(
                "GAME_TOKEN_SECRET not set — using random secret "
                "(tokens will not survive restarts)"
            )
            _SECRET = os.urandom(32)
    return _SECRET


DEFAULT_EXPIRY_HOURS = 24


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


def _sign(payload_bytes: bytes) -> str:
    sig = hmac.new(_get_secret(), payload_bytes, hashlib.sha256).digest()
    return _b64url_encode(sig)


def create_token(
    agent_id: str,
    session_id: str,
    player_id: str,
    game_type: str,
    *,
    host: bool = False,
    expiry_hours: int = DEFAULT_EXPIRY_HOURS,
) -> str:
    """Create a signed invite token bound to a specific agent."""
    now = time.time()
    payload = {
        "agent_id": agent_id,
        "session_id": session_id,
        "player_id": player_id,
        "game_type": game_type,
        "host": host,
        "issued_at": now,
        "expires_at": now + expiry_hours * 3600,
    }
    payload_bytes = json.dumps(payload, sort_keys=True).encode()
    payload_b64 = _b64url_encode(payload_bytes)
    signature = _sign(payload_bytes)
    return f"{payload_b64}.{signature}"


def verify_token(token: str, agent_id: str) -> dict | None:
    """Verify token signature, expiry, and agent binding.

    Returns payload dict on success, None on failure.
    """
    parts = token.split(".")
    if len(parts) != 2:
        return None

    payload_b64, provided_sig = parts
    try:
        payload_bytes = _b64url_decode(payload_b64)
        expected_sig = _sign(payload_bytes)
    except (ValueError, UnicodeDecodeError) as e:
        log.debug("Token decode failed: %s", e)
        return None

    # Constant-time comparison
    if not hmac.compare_digest(provided_sig, expected_sig):
        return None

    try:
        payload = json.loads(payload_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        log.debug("Token payload parse failed: %s", e)
        return None

    # Check expiry
    if time.time() > payload.get("expires_at", 0):
        return None

    # Check agent binding
    if payload.get("agent_id") != agent_id:
        return None

    return payload


def set_secret(secret: bytes) -> None:
    """Set the HMAC secret explicitly (for testing or startup)."""
    global _SECRET
    _SECRET = secret
