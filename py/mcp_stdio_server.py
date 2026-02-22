"""Standalone MCP stdio server — reads JSON-RPC from stdin, writes to stdout.

Usage:
    uv run python mcp_stdio_server.py

Works with Claude Code (command mode), ChatGPT, and any MCP client that
speaks stdio transport.  No NiceGUI or web server required — runs the full
game engine in-process with in-memory session storage.

Protocol auto-detection: accepts both Content-Length framing and bare
line-delimited JSON (Claude Code sends the latter).
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any

# ---------------------------------------------------------------------------
# Logging — stderr only (stdout is the MCP transport)
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("mcp_stdio")


# ---------------------------------------------------------------------------
# In-memory session / tournament stores (satisfy MCPServer protocols)
# ---------------------------------------------------------------------------

from engine.runtime.state import CompiledGame
from server.sessions import GameSession

_sessions: dict[str, GameSession] = {}


class _MemorySessionStore:
    """In-memory session store satisfying MCPServer.SessionStore protocol."""

    def get(self, session_id: str) -> GameSession | None:
        return _sessions.get(session_id)

    def list_all(self) -> dict[str, GameSession]:
        return dict(_sessions)

    def create(
        self, session_id: str, compiled: CompiledGame, player_ids: list[str]
    ) -> GameSession:
        session = GameSession(session_id, compiled, player_ids)
        _sessions[session_id] = session
        return session

    def remove(self, session_id: str) -> None:
        _sessions.pop(session_id, None)


class _MemoryTournamentStore:
    """Stub tournament store — tournaments not available in stdio mode."""

    def get(self, tournament_id: str):
        return None

    def list_all(self) -> dict:
        return {}

    def create(self, **kwargs):
        raise RuntimeError("Tournaments not available in stdio mode")

    def remove(self, tournament_id: str) -> None:
        pass


# ---------------------------------------------------------------------------
# Build the MCP server
# ---------------------------------------------------------------------------


def _build_server():
    from games import REGISTRY
    from mcp.server import MCPServer

    server = MCPServer(
        sessions=_MemorySessionStore(),
        tournaments=_MemoryTournamentStore(),
    )
    for game_id, compiled in REGISTRY.items():
        server.register_game(compiled)
    return server


# ---------------------------------------------------------------------------
# Stdio transport — auto-detect Content-Length vs line-delimited JSON
# ---------------------------------------------------------------------------


async def _read_message(reader: asyncio.StreamReader) -> dict | None:
    """Read one JSON-RPC message from stdin.

    Auto-detects:
    - Content-Length framing (standard MCP): "Content-Length: N\\r\\n\\r\\n{...}"
    - Line-delimited JSON (Claude Code): "{...}\\n"
    """
    # Peek at first byte to detect framing
    try:
        peek = await reader.read(1)
    except asyncio.CancelledError:
        return None
    if not peek:
        return None  # EOF

    if peek == b"{":
        # Line-delimited JSON — read until newline
        rest = await reader.readline()
        line = peek + rest
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            log.error("Invalid JSON line: %s", line[:200])
            return None

    elif peek == b"C" or peek in (b"c", b" "):
        # Content-Length framing — read header
        header_rest = await reader.readline()
        header_line = (peek + header_rest).decode("utf-8", errors="replace").strip()
        if not header_line.lower().startswith("content-length:"):
            log.error("Expected Content-Length header, got: %s", header_line)
            return None
        length = int(header_line.split(":", 1)[1].strip())

        # Read remaining headers until empty line
        while True:
            line = await reader.readline()
            if not line or line.strip() == b"":
                break

        # Read body
        body = await reader.readexactly(length)
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            log.error("Invalid JSON body: %s", body[:200])
            return None
    else:
        # Skip whitespace / unknown bytes and retry
        return await _read_message(reader)


def _write_message(msg: dict) -> None:
    """Write a JSON-RPC message to stdout with Content-Length framing."""
    payload = json.dumps(msg)
    encoded = payload.encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(encoded)}\r\n\r\n".encode("utf-8"))
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

AGENT_ID = "stdio-agent"


async def _main():
    server = _build_server()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await asyncio.get_event_loop().connect_read_pipe(lambda: protocol, sys.stdin)

    log.info("MCP stdio server ready — reading from stdin")

    while True:
        msg = await _read_message(reader)
        if msg is None:
            log.info("EOF on stdin — shutting down")
            break

        # Handle batch (array of messages)
        if isinstance(msg, list):
            responses = []
            for m in msg:
                if isinstance(m, dict):
                    resp = await server.handle_request(AGENT_ID, m)
                    if resp is not None:
                        responses.append(resp)
            if responses:
                _write_message(responses if len(responses) > 1 else responses[0])
            continue

        # Single message
        if isinstance(msg, dict):
            resp = await server.handle_request(AGENT_ID, msg)
            if resp is not None:
                _write_message(resp)


def main():
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        log.info("Interrupted")


if __name__ == "__main__":
    main()
