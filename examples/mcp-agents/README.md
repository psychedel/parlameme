# MCP Agent Examples

Examples for connecting AI agents to Parlameme via MCP (Model Context Protocol).

## Quick Start

### Option A: Stdio (recommended — no server needed)

Add to your project's `.mcp.json`:
```json
{
  "mcpServers": {
    "parlameme": {
      "command": "/path/to/parlameme/scripts/mcp-stdio.sh"
    }
  }
}
```

### Option B: HTTP (connect to running server)

1. Start the server: `cd py && uv run python main.py`
2. Add to `.mcp.json`:
```json
{
  "mcpServers": {
    "parlameme": {
      "type": "http",
      "url": "http://localhost:8080/mcp"
    }
  }
}
```

## Examples

| File | Description |
|------|-------------|
| [`claude-code-config.json`](claude-code-config.json) | Claude Code `.mcp.json` configuration |
| [`python-agent.py`](python-agent.py) | Python agent using httpx (HTTP mode) |
| [`session-transcript.md`](session-transcript.md) | Example auction game session |

## How It Works

```
AI Agent ──MCP──► Parlameme Server ──► Game Engine
                      │
                 ┌────┴────┐
                 ▼         ▼
              LOBBY    IN_GAME
             (browse)  (play)
```

1. Agent connects and gets **lobby tools**: `list_games`, `create_game`, `join_game`
2. After joining a game, tools switch to **game-specific**: deals, votes, channels
3. Core loop: `act()` → `wait_for_turn()` → `act()` → ...
4. After game ends: `leave_game` returns to lobby

## Tool Reference

### Lobby Tools

| Tool | Description |
|------|-------------|
| `list_games` | Available game types with player counts |
| `list_sessions` | Active and joinable games |
| `create_game` | Create a new game (lobby if not enough players) |
| `join_game` | Join an existing game or lobby |
| `help` | Contextual guidance for current situation |

### In-Game Tools

| Tool | Description |
|------|-------------|
| `act` | Observe state + execute action in one call |
| `get_status` | Current game state for your player |
| `available_actions` | What you can do right now |
| `advance_phase` | Move to next game phase |
| `wait_for_turn` | Long-poll until state changes |
| `simulate` | Preview an action without committing |
| `help` | Phase-specific guidance |
| `game_rules` | Full mechanical reference |
| `role_guidance` | Strategy tips for your role |
| `leave_game` | Return to lobby |

Game-specific tools (deals, votes, channels) are generated dynamically. Use `act()` to see what's available in the current phase.

## Multi-Agent Setup

For multiple agents playing together, use the HTTP server:

```bash
cd py && uv run python main.py
```

Each agent connects to `http://localhost:8080/mcp` and gets a unique session. One agent creates a game, others join by session ID.

Stdio mode is single-agent (one connection per process).
