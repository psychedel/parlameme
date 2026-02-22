#!/usr/bin/env python3
"""
Parlameme — Python MCP Agent Example

Connects to the Parlameme MCP server via HTTP and plays a game interactively.

Requirements:
    pip install httpx

Usage:
    # Start server first: cd py && uv run python main.py

    # Interactive mode
    python python-agent.py --interactive

    # Create and observe a game
    python python-agent.py --game auction --players alice,bob,charlie
"""

import argparse
import json
import sys

import httpx


class ParlamemeAgent:
    """MCP client for Parlameme game server."""

    def __init__(self, base_url: str = "http://localhost:8080"):
        self.base_url = base_url.rstrip("/")
        self.endpoint = f"{self.base_url}/mcp"
        self.session_id: str | None = None
        self.request_id = 0

    def _call(self, method: str, params: dict | None = None) -> dict:
        """Make a JSON-RPC call to the MCP endpoint."""
        self.request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "id": self.request_id,
        }
        if params:
            payload["params"] = params

        headers = {"Content-Type": "application/json"}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id

        response = httpx.post(
            self.endpoint,
            json=payload,
            headers=headers,
            timeout=60.0,
        )
        response.raise_for_status()

        # Capture session ID from initialize response
        if "mcp-session-id" in response.headers:
            self.session_id = response.headers["mcp-session-id"]

        result = response.json()
        if "error" in result:
            raise Exception(f"MCP Error: {result['error']}")

        return result.get("result", {})

    def initialize(self) -> dict:
        """Initialize MCP session."""
        return self._call("initialize")

    def list_tools(self) -> list[dict]:
        """List available MCP tools."""
        return self._call("tools/list").get("tools", [])

    def call_tool(self, name: str, **arguments) -> str:
        """Call an MCP tool and return the text content."""
        result = self._call("tools/call", {
            "name": name,
            "arguments": arguments,
        })
        content = result.get("content", [])
        return content[0].get("text", "") if content else str(result)

    def disconnect(self):
        """Terminate MCP session."""
        if self.session_id:
            try:
                httpx.delete(
                    self.endpoint,
                    headers={"Mcp-Session-Id": self.session_id},
                    timeout=5.0,
                )
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser(description="Parlameme MCP Agent")
    parser.add_argument("--url", default="http://localhost:8080",
                        help="Server URL (default: http://localhost:8080)")
    parser.add_argument("--game", help="Game type to create (auction, exchange, werewolf, parliament_arena)")
    parser.add_argument("--players", help="Comma-separated player IDs")
    parser.add_argument("--interactive", "-i", action="store_true",
                        help="Interactive mode")
    args = parser.parse_args()

    agent = ParlamemeAgent(args.url)

    # Initialize session
    print("Connecting to Parlameme...")
    info = agent.initialize()
    server = info.get("serverInfo", {})
    print(f"Connected: {server.get('name', '?')} v{server.get('version', '?')}")
    print(f"Session: {agent.session_id}")
    print()

    # Show available tools
    tools = agent.list_tools()
    print(f"Available tools ({len(tools)}):")
    for t in tools:
        print(f"  {t['name']}: {t.get('description', '')[:70]}")
    print()

    # Create game if requested
    if args.game:
        game_args = {"game_type": args.game}
        if args.players:
            game_args["players"] = args.players.split(",")
        print(agent.call_tool("create_game", **game_args))
        print()

        # Show game state
        print(agent.call_tool("act"))
        print()

    # Interactive mode
    if args.interactive:
        print("=== Interactive Mode ===")
        print("Commands:")
        print("  tools         — list available tools")
        print("  act           — observe game state")
        print("  act <action>  — execute action (e.g., act advance_phase)")
        print("  call <tool> [json] — call any tool with optional JSON args")
        print("  help          — get contextual guidance")
        print("  quit          — disconnect and exit")
        print()

        while True:
            try:
                cmd = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                break

            if not cmd:
                continue

            parts = cmd.split(maxsplit=2)
            action = parts[0].lower()

            try:
                if action == "quit":
                    break
                elif action == "tools":
                    for t in agent.list_tools():
                        print(f"  {t['name']}")
                elif action == "act":
                    if len(parts) > 1:
                        act_args = {"action": parts[1]}
                        if len(parts) > 2:
                            act_args["args"] = json.loads(parts[2])
                        print(agent.call_tool("act", **act_args))
                    else:
                        print(agent.call_tool("act"))
                elif action == "help":
                    print(agent.call_tool("help"))
                elif action == "call" and len(parts) >= 2:
                    tool_name = parts[1]
                    tool_args = json.loads(parts[2]) if len(parts) > 2 else {}
                    print(agent.call_tool(tool_name, **tool_args))
                else:
                    # Try as direct tool call
                    try:
                        tool_args = json.loads(parts[1]) if len(parts) > 1 else {}
                        print(agent.call_tool(action, **tool_args))
                    except Exception:
                        print(f"Unknown command: {action}. Try 'help' or 'tools'.")
            except Exception as e:
                print(f"Error: {e}")
            print()

    agent.disconnect()
    print("Disconnected.")


if __name__ == "__main__":
    main()
