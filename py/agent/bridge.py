"""In-process MCP bridge — zero-overhead tool calling.

Calls MCPServer.handle_request() directly instead of going through HTTP.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mcp.server import MCPServer

log = logging.getLogger(__name__)


class InProcessBridge:
    """Bridge between AgentRunner and MCPServer — no network overhead."""

    def __init__(self, mcp: MCPServer, agent_id: str):
        self.mcp = mcp
        self.agent_id = agent_id
        self._req_counter = 0

    async def call_tool(
        self, name: str, args: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Call an MCP tool and return the result content.

        Returns the ``result`` dict on success, or the ``error`` dict on failure.
        """
        self._req_counter += 1
        request = {
            "jsonrpc": "2.0",
            "id": self._req_counter,
            "method": "tools/call",
            "params": {"name": name, "arguments": args or {}},
        }
        response = await self.mcp.handle_request(self.agent_id, request)
        if response is None:
            return {"error": {"code": -1, "message": "No response (notification?)"}}
        if "error" in response:
            log.warning(
                "MCP tool %s error for %s: %s", name, self.agent_id, response["error"]
            )
            return response["error"]
        result = response.get("result", {})
        if not result:
            log.debug("MCP tool %s returned empty result for %s", name, self.agent_id)
        return result

    async def list_tools(self) -> list[dict[str, Any]]:
        """Get currently available tools for this agent's state."""
        self._req_counter += 1
        request = {
            "jsonrpc": "2.0",
            "id": self._req_counter,
            "method": "tools/list",
            "params": {},
        }
        response = await self.mcp.handle_request(self.agent_id, request)
        if response is None:
            return []
        return response.get("result", {}).get("tools", [])

    async def initialize(self) -> dict[str, Any]:
        """Send initialize handshake."""
        self._req_counter += 1
        request = {
            "jsonrpc": "2.0",
            "id": self._req_counter,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "agent-runner", "version": "0.1.0"},
            },
        }
        response = await self.mcp.handle_request(self.agent_id, request)
        return response.get("result", {}) if response else {}
