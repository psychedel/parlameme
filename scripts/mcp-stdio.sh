#!/bin/sh
# MCP stdio server — launches standalone game engine process.
# Works with Claude Code, ChatGPT, Cursor, Zed, and any MCP client.
cd "$(dirname "$0")/../py" && exec uv run python mcp_stdio_server.py "$@"
