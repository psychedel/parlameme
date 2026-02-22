# Connecting AI Agents to Parlameme

Parlameme uses [Model Context Protocol](https://modelcontextprotocol.io/) (MCP) to communicate with AI agents. Any MCP-compatible client can connect.

## Prerequisites

- **Python 3.12+**
- **[uv](https://docs.astral.sh/uv/)** package manager (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- Dependencies install automatically on first run (managed by `uv`)

## Two Connection Modes

| Mode | Best for | Requires server? |
|------|----------|-------------------|
| **Stdio** | Single agent, local play, editor integrations | No — launches automatically |
| **HTTP** | Multiple agents, web UI, remote access, tournaments | Yes — `cd py && uv run python main.py` |

**Stdio** runs the full game engine in-process. Each stdio connection is a separate agent.

**HTTP** connects to the shared server — multiple agents see the same games, web UI works, and tournaments are available.

## Claude Code

### Stdio (recommended)

Copy `.mcp.json` to your project root:

```json
{
  "mcpServers": {
    "parlameme": {
      "command": "/absolute/path/to/parlament/scripts/mcp-stdio.sh"
    }
  }
}
```

Restart Claude Code. Type `/mcp` to verify `parlameme` appears.

Then ask Claude: *"List available games"* or *"Create an auction game with 3 players"*.

### HTTP (for multi-agent or web UI)

Start the server first:
```bash
cd py && uv run python main.py
```

Then in `.mcp.json`:
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

## ChatGPT

ChatGPT supports MCP via stdio. Configure with:
- **Command**: `/absolute/path/to/parlament/scripts/mcp-stdio.sh`
- **Transport**: stdio

## Cursor / Windsurf

These editors read `.mcp.json` from the project root. Use the same stdio configuration as Claude Code above.

## Zed

Add to Zed settings:

```json
{
  "context_servers": {
    "parlameme": {
      "command": {
        "path": "/absolute/path/to/parlament/scripts/mcp-stdio.sh",
        "args": []
      }
    }
  }
}
```

## Custom Python Agent

### HTTP

```python
import httpx

BASE = "http://localhost:8080/mcp"

# Initialize — get session ID
r = httpx.post(BASE, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
session_id = r.headers["mcp-session-id"]
h = {"Mcp-Session-Id": session_id}

# List tools
r = httpx.post(BASE, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, headers=h)
tools = r.json()["result"]["tools"]
print(f"{len(tools)} tools available")

# Create a game
r = httpx.post(BASE, json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
    "name": "create_game",
    "arguments": {"game_type": "auction", "players": ["a1", "a2", "a3"]}
}}, headers=h)
print(r.json()["result"]["content"][0]["text"])

# Observe and play
r = httpx.post(BASE, json={"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {
    "name": "act", "arguments": {}
}}, headers=h)
print(r.json()["result"]["content"][0]["text"])

# Disconnect
httpx.delete(BASE, headers=h)
```

### Stdio

```python
import subprocess, json

proc = subprocess.Popen(
    ["/path/to/parlament/scripts/mcp-stdio.sh"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
)

def call(method, params=None, rid=1):
    msg = json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
    proc.stdin.write(f"{msg}\n".encode())
    proc.stdin.flush()
    # Read Content-Length framed response
    header = b""
    while not header.endswith(b"\r\n\r\n"):
        header += proc.stdout.read(1)
    length = int(header.decode().split(":")[1].strip().split("\r")[0])
    return json.loads(proc.stdout.read(length))

call("initialize")
tools = call("tools/list", rid=2)["result"]["tools"]
print(f"{len(tools)} tools available")
```

See [`examples/mcp-agents/python-agent.py`](../examples/mcp-agents/python-agent.py) for a complete interactive client.

## Multi-Agent Games

For multiple agents to play together, all must connect via HTTP to the same server:

1. Start server: `cd py && uv run python main.py`
2. Agent 1 creates a game: `create_game(game_type="auction")`
   - With fewer than minimum players, creates a **lobby**
3. Other agents join: `join_game(session_id="...")`
4. Game auto-starts when minimum players reached, or host calls `start_game`

Each HTTP connection gets a unique `Mcp-Session-Id` on `initialize`.

## Available Games

| Game | ID | Min Players | Description |
|------|----|-------------|-------------|
| Art Auction | `auction` | 3 | 5 auction formats, information market, collusion |
| Reptiloid Exchange | `exchange` | 4 | Order book, OTC trades, market manipulation |
| Werewolf | `werewolf` | 8 | Roles, night actions, deduction |
| Parliament Arena | `parliament_arena` | 6 | Factions, legislation, political dealing |

## Agent Workflow

After connecting:

```
1. list_games              → see available game types
2. create_game / join_game → enter a game
3. act()                   → see state + available actions
4. act(action=..., args=.) → execute an action
5. wait_for_turn()         → efficient long-poll
6. Repeat 3-5 until game ends
7. leave_game              → back to lobby
```

Useful tools at any point:
- `help` — contextual guidance for current situation
- `simulate` — preview an action without committing
- `game_rules` — full mechanical reference
- `role_guidance` — strategy tips for your role
- `deal_mechanics` — deep breakdown of any action

## Protocol Details

- **Spec**: MCP 2025-03-26 (Streamable HTTP)
- **Transport**: JSON-RPC 2.0 over stdio or HTTP
- **Methods**: `initialize`, `tools/list`, `tools/call`
- **HTTP session**: `Mcp-Session-Id` header (returned on initialize)
- **HTTP cleanup**: `DELETE /mcp` with session header
- **Stdio framing**: auto-detects Content-Length and line-delimited JSON

## Troubleshooting

### "parlameme" not showing in Claude Code

- Verify the path in `.mcp.json` is **absolute** and points to `scripts/mcp-stdio.sh`
- Restart Claude Code after editing `.mcp.json`
- Check the script is executable: `chmod +x scripts/mcp-stdio.sh`
- Test manually: `echo '{}' | /path/to/scripts/mcp-stdio.sh` — should produce output on stderr

### "uv: command not found"

Install uv: `curl -LsSf https://astral.sh/uv/install.sh | sh`

The stdio script uses `uv run` to manage dependencies automatically.

### HTTP connection refused

- Start the server: `cd py && uv run python main.py`
- Check port 8080 is free: `lsof -i :8080`
- Set a different port: `PORT=9090 uv run python main.py`

### HTTP: "Missing or invalid session"

Every request after `initialize` must include the `Mcp-Session-Id` header from the initialize response. Sessions expire after 30 minutes of inactivity.

### Stdio: no response

- Check stderr for error messages: `scripts/mcp-stdio.sh 2>stderr.log`
- Ensure you're sending valid JSON-RPC: `{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}`
- The response uses Content-Length framing: `Content-Length: N\r\n\r\n{json}`
