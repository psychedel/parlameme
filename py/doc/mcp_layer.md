# MCP Layer — AI Agent Interface

The MCP (Model Context Protocol) layer is the bridge between AI agents and the game engine. It translates JSON-RPC requests into engine operations and formats engine state into structured text for LLM consumption. The layer is stateful per-agent, dynamically generates tools from compiled games, and provides optimized patterns for agent loops.

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  AI Agent (Claude, GPT, etc.)                                    │
│  Speaks JSON-RPC 2.0 over HTTP                                   │
└────────────────────────┬─────────────────────────────────────────┘
                         │ POST /mcp/agent/{agent_id}
                         ▼
┌──────────────────────────────────────────────────────────────────┐
│  MCPServer (mcp/server.py)                                       │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │ Agent State Machine (mcp/agents.py)                       │    │
│  │ lobby ──► in_game ──► lobby                               │    │
│  │   │       in_tournament ──► in_game ──► in_tournament     │    │
│  │   └─────► spectating ──► lobby                            │    │
│  └──────────────────────────────────────────────────────────┘    │
│                                                                   │
│  ┌───────────────┐  ┌───────────────┐  ┌────────────────────┐   │
│  │ Schema Gen    │  │ Formatters    │  │ Handler Modules    │   │
│  │ (schema.py)   │  │ (formatters)  │  │ analytics/channels │   │
│  │ CompiledGame  │  │ State → Text  │  │ helpers/history    │   │
│  │ → Tool[]      │  │ for LLMs     │  │ ledger/spectator   │   │
│  └───────┬───────┘  └───────┬───────┘  └────────┬───────────┘   │
│          └──────────────────┼──────────────────────┘              │
│                             ▼                                     │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │ Tokens (tokens.py) │ Mechanics (mechanics.py)             │    │
│  │ HMAC invite tokens  │ Effect/Expr → human text            │    │
│  └──────────────────────────────────────────────────────────┘    │
└────────────────────────┬─────────────────────────────────────────┘
                         │ calls GameRuntime (pure, stateless)
                         ▼
┌──────────────────────────────────────────────────────────────────┐
│  GameSession (server/sessions.py)                                │
│  Async lock + subscribe/notify + timeout management              │
│  Wraps GameRuntime, owns GameState                               │
└──────────────────────────────────────────────────────────────────┘
```

## Module Responsibilities

### `mcp/server.py` (~1460 lines) — The Router

The main entry point. Routes JSON-RPC requests, manages game/lobby lifecycle, and dispatches to handlers.

**Key responsibilities:**
- JSON-RPC 2.0 compliance (method routing, notifications, error codes)
- Agent registration via `agents.register_agent()`
- Tool list generation based on agent state (`_get_tools_for_agent`)
- Lobby system: `_pending_games` dict, auto-start on min_players
- Game tool dispatch: `_handle_game_tool` (deals/votes/channels/speech_acts)
- Combined `act` tool: observe + execute in one call
- Long-poll `wait_for_turn`: subscribe to session events, return on state change
- Tournament integration: join_match, wait_for_match, report results

**Dependency injection:** MCPServer receives `SessionStore` and `TournamentStore` protocols — it never imports `server.sessions` directly. This allows testing with mock stores.

### `mcp/schema.py` (~680 lines) — Dynamic Tool Generation

Converts `CompiledGame` definitions into MCP tool schemas at registration time.

**Key functions:**
- `classify_parties()` — maps custom party names to canonical MCP params (actor/responder/target)
- `deal_to_tool()` — DealDef → Tool with JSON Schema, outcome summary, guard info
- `vote_to_tool()`, `channel_to_tool()`, `speech_act_to_tool()` — analogous converters
- `generate_game_tools()` — all tools for a game (deals + votes + channels + speech_acts + universal)
- `filter_tools_for_phase()` — runtime filtering: only tools available in current phase for this player

**Party classification types:**
```
immediate   — single actor (e.g., sealed_bid)
bilateral   — actor + responder (e.g., otc_trade)
multilateral — actor + responders with count constraint (e.g., coalition)
```

**Phase filtering logic:**
- Deals: must be in `phase.allows` AND pass `_player_can_use_deal` (filter + guard + usage)
- Votes: must be in `phase.allows`
- Channels: phase filter + `can_write_channel` check
- Speech acts: must be in `phase.allows`, pass `_player_can_use_speech_act` (filter + usage + cost)
- Universal tools (get_status, act, etc.): always shown
- Contextual tools (respond, endorse, respond_to_inquire): shown only when relevant pending actions exist

### `mcp/agents.py` (~115 lines) — Agent State Machine

Tracks each agent's lifecycle in the platform.

**States and transitions:**
```
                     ┌──────────────────────┐
                     │       LOBBY          │
                     │  (list/create/join)  │
                     └──────┬───────┬───────┘
                            │       │
              join_game/    │       │  spectate_game
              create_game   │       │
                            ▼       ▼
                     ┌──────────┐  ┌──────────┐
                     │ IN_GAME  │  │SPECTATING│
                     │(play/act)│  │(observe) │
                     └──────┬───┘  └──────┬───┘
                            │             │
               leave_game   │  leave_     │
                            │  spectate   │
                            ▼             ▼
                     ┌──────────────────────┐
                     │       LOBBY          │
                     └──────────────────────┘

  Tournament path:
  LOBBY → IN_TOURNAMENT → IN_GAME (match) → IN_TOURNAMENT → ...
```

**`TournamentContext`** (frozen dataclass): preserves `tournament_id` + `match_id` while agent plays a tournament match. When the match ends, `back_to_tournament()` restores the tournament state.

**Global registry:** Module-level `_agents: dict[str, AgentState]`. Functions: `register_agent`, `get_agent`, `remove_agent`, `list_agents`, `cleanup_stale`. Stale timeout: 30 minutes.

### `mcp/formatters.py` (~720 lines) — The View Layer

Converts raw game state into structured text optimized for LLM consumption. This is the most important module for AI agent performance — the quality of formatting directly impacts how well agents play.

**Key functions:**

| Function | Purpose |
|----------|---------|
| `format_status()` | Full game view: phase, players, resources, ACTION REQUIRED, role hints |
| `format_available_actions()` | Categorized actions: Responses (URGENT), Pending Votes, Deals, Speech Acts, Phase Control |
| `format_deal_result()` | Result + resource deltas + contextual error tips |
| `format_vote_result()` | Result + auto-complete/auto-advance notifications |
| `format_history()` | Recent game events |
| `build_context_line()` | Key game variables (data-driven from VarHints or fallback) |
| `can_player_use_deal()` | Filter + guard + usage check (returns False on eval errors) |
| `can_player_use_speech_act()` | Actor filter + usage + cost affordability check |
| `format_usage_limit()` | `[1/2 round]` or `[EXHAUSTED: 2/2 round]` |
| `compute_advance_readiness()` | BLOCKED / READY / OPTIONAL |

**Design principles:**
- **ACTION REQUIRED at top** — pending deals/votes that need this player's response
- **Categorized actions** — Responses (URGENT) before Deals before Votes
- **Resource deltas** — `gold: 100 → 90 (-10)` after every action
- **Error tips** — `usage_limit` → "try advance_phase", `guard_failed` → "use available_actions"
- **EXHAUSTED label** — clear signal when usage limits are spent
- **Context from annotations** — VarHints, PhaseHints, RoleHints from ContextConfig

### `mcp/mechanics.py` (~613 lines) — Deep Mechanical Information

Auto-describes effects and expressions for AI agents. Used by `deal_mechanics` tool and enriched `game_rules`.

**Key functions:**
- `describe_effect(effect)` — handles all 37+ effect types → human-readable text
- `describe_expr(expr)` — Expr AST → readable conditions (e.g., `resource_of(responder, asset) >= qty`)
- `describe_deal_mechanics(id, deal)` — full breakdown: parties, params, stakes, outcomes
- `describe_vote_mechanics(id, vote)` — options, threshold, tally method, outcomes
- `describe_speech_act_mechanics(id, sa)` — type, cost, verification, endorsement
- `outcome_summary(outcomes)` — compact one-liner for tool descriptions
- `speech_act_verification_summary(sa)` — verification triggers + reward/punishment

**Safety:** Never crashes on unknown types. Uses `OutcomeDef.doc` when available, auto-generates as fallback. Handles Expr resources in effects.

### `mcp/tokens.py` (~120 lines) — Invite Tokens

HMAC-SHA256 signed tokens for secure game invitations.

**Token structure:** `{base64url_payload}.{base64url_signature}`

**Payload fields:** `agent_id`, `session_id`, `player_id`, `game_type`, `host`, `issued_at`, `expires_at`

**Security properties:**
- Agent-bound: token is only valid for the agent it was created for
- Time-limited: configurable expiry (default 24h)
- Constant-time comparison: `hmac.compare_digest` prevents timing attacks
- Secret from env: `GAME_TOKEN_SECRET`, falls back to random (non-persistent)

### Handler Modules (`mcp/handlers/`)

Each handler module follows a consistent pattern:

```python
TOOLS: list[Tool] = [...]           # Tool schemas with descriptions
HANDLERS: dict[str, Callable] = {}  # name → async handler function
```

| Module | Scope | Tools |
|--------|-------|-------|
| `analytics.py` | Global | my_stats, platform_stats, player_head_to_head, game_balance_report, leaderboard |
| `channels.py` | In-game | list_channels, get_messages, get_all_messages |
| `helpers.py` | In-game | game_summary, role_guidance, game_rules, deal_mechanics |
| `history.py` | Global | my_game_history, get_game_replay, list_public_replays |
| `ledger.py` | Global | ledger_balance, ledger_history, ledger_verify, ledger_status |
| `spectator.py` | Mixed | spectate_game (lobby), leave_spectate/status/view (spectating) |

## Request Lifecycle

```
1. Agent sends JSON-RPC request
   POST /mcp/agent/{agent_id}
   {"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"act","arguments":{...}}}

2. MCPServer.handle_request()
   ├─ register_agent(agent_id)  → AgentState (created or existing)
   ├─ route by method:
   │   ├─ "initialize" → protocol handshake
   │   ├─ "tools/list" → _get_tools_for_agent(agent) → filtered tool list
   │   └─ "tools/call" → _handle_tools_call(agent, params)
   │       ├─ "/" in name → _handle_game_tool (deal/vote/channel/speech_act)
   │       └─ no "/" → _handle_platform_tool (lobby/in-game/tournament/global)
   └─ wrap result in JSON-RPC response

3. Game tool execution (e.g., "auction/sealed_bid")
   ├─ session = _get_session(agent.session_id)
   ├─ _exec_deal(session, agent, deal_id, args)
   │   ├─ build kwargs from party classification mapping
   │   ├─ state_before = session.state
   │   ├─ result = await session.execute_deal(deal_id, **kwargs)
   │   └─ formatters.format_deal_result(result, before, after, player_id)
   └─ return formatted result as MCP content

4. Agent receives response
   {"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"..."}]}}
```

## Lobby System

The lobby system handles game creation when not all players are available upfront.

```
create_game (< min_players)
  └─ _pending_games[session_id] = {compiled, players, host}

join_game → _join_pending_game
  ├─ impersonation check (no other agent claims same player_id)
  ├─ append player
  └─ if len(players) >= min_players → _start_pending_game
      ├─ create session + start
      ├─ on error: restore pending (atomic safety)
      └─ on success: delete pending

leave_game (from lobby)
  ├─ remove player from pending
  ├─ if host left → reassign host to next player
  └─ if empty → delete pending

start_game (manual, host only)
  └─ _start_pending_game (requires >= min_players)
```

## Optimized Agent Loop (Phase 6)

Two tools designed to minimize API round-trips:

### `act(action?, args?)`
Combined observe + execute. Without args: returns status + available actions. With action: executes it, then returns result + updated status + available actions. Reduces 3 calls to 1.

```
Agent                          MCP Server
  │                               │
  │─── act() ────────────────────►│  (observe only)
  │◄── status + actions ──────────│
  │                               │
  │─── act("sealed_bid",{...}) ──►│  (execute + observe)
  │◄── result + status + actions ─│
```

### `wait_for_turn(timeout?)`
Long-poll until game state changes. Returns immediately if agent has pending actions. Subscribes to session events, wakes on phase change, new deal/vote, or game end. Max 60s timeout.

```
Agent                          MCP Server
  │                               │
  │─── wait_for_turn(60) ────────►│  (subscribe to session events)
  │         ... waiting ...        │
  │◄── trigger + status ──────────│  (new_phase/deal_proposed/game_ended)
```

## Tool Visibility by State

| State | Visible Tools |
|-------|---------------|
| `lobby` | list_games, list_sessions, create_game, join_game, activate_game, my_status, tournament tools, spectate_game, global tools |
| `in_game` (lobby) | get_status, leave_game, start_game (host only), global tools |
| `in_game` (active) | Phase-filtered game tools + universal tools + global tools |
| `in_tournament` | get_tournament_status, get_standings, get_my_matches, join_match, wait_for_match, leave_tournament, global tools |
| `spectating` | leave_spectate, spectate_status, spectate_view, global tools |

Global tools (always available): my_stats, platform_stats, leaderboard, player_head_to_head, game_balance_report, ledger tools, history tools.

## Design Decisions

### Why stateful agents?
MCP spec allows stateless request-response, but game agents need persistent identity. The agent state machine tracks which game/tournament each agent is in, preventing conflicts (one agent in one game at a time) and enabling features like tournament flow.

### Why dynamic tool generation?
Each game defines different deals, votes, channels, and speech acts. Generating tools from `CompiledGame` means:
- No manual tool definitions per game
- Tools automatically include correct parameters, types, constraints
- Phase filtering shows only what's currently available
- New games get full MCP support with zero MCP code

### Why format as markdown text?
LLMs process structured text better than raw JSON. The formatters convert game state into markdown with:
- Headers for sections (`### Deals`)
- Bold for action names (`**sealed_bid**`)
- Inline hints for params (`amount: number 1-100`)
- Progress indicators (`[3/5 voted]`)
- Urgency markers (`### Responses (URGENT)`)

### Why separate schema.py and formatters.py?
`schema.py` generates static tool definitions at registration time. `formatters.py` generates dynamic text at request time. They serve different lifecycle phases and have different consumers (tool list vs. tool output).

### Why Protocol-based dependency injection?
MCPServer accepts `SessionStore` and `TournamentStore` protocols rather than concrete types. This decouples the MCP layer from the server layer, enabling:
- Unit tests with mock stores
- Alternative storage backends
- Clean dependency graph (MCP → protocols ← implementations)

## Common Patterns

### Adding a new handler module

```python
# mcp/handlers/my_module.py
from mcp.schema import Tool

TOOLS: list[Tool] = [
    Tool(name="my_tool", description="...", inputSchema={...}, _meta={"type": "query"}),
]

async def handle_my_tool(server, agent, args):
    # ... implementation ...
    return {"content": [{"type": "text", "text": "result"}]}

HANDLERS = {"my_tool": handle_my_tool}
```

Then register in `server.py`:
```python
from .handlers import my_module
# Add to _GLOBAL_HANDLERS or route in _handle_platform_tool
```

### Adding a game tool via DSL

No MCP code needed. Define a deal/vote/speech_act in the game's DSL builder, and `schema.py` automatically generates the tool. Phase filtering, usage limits, and formatting all work automatically.

### Error responses

```python
# Simple error
return _error("Not in a game.")

# Structured error with code and suggestion
return _error(
    "Usage limit reached.",
    code="usage_limit",
    suggestion="Try a different action or advance_phase.",
)
```

## Gotchas

1. **`act` action names don't have game prefix.** Through `tools/call`, tools are `auction/sealed_bid`. Through `act(action=...)`, just `sealed_bid`. The `_dispatch_action` method handles unprefixed names.

2. **`except Exception: return False`** in filter evaluation (formatters.py). If an Expr guard or filter raises, the action is conservatively hidden (not shown). Previous behavior was `return True` which silently allowed broken guards.

3. **Pending lobby vs. active session.** `_tool_get_status`, `_in_game_tools`, and `_tool_leave_game` all check `_pending_games` first. An agent in `in_game` state might be in a lobby (pending) or an active game (session). The two paths have different tool sets and behaviors.

4. **Party classification affects param mapping.** The `_meta.party_mapping` in tool schemas maps DSL party names to MCP param names. `_exec_deal` reads this mapping to build correct `kwargs` for `session.execute_deal()`.

5. **`_tool_cache` is keyed by `compiled.id`.** All games of the same type share one tool list. Phase filtering happens at request time, not at cache time.

6. **Token agent binding.** Tokens are bound to a specific `agent_id` at creation. A token created for agent "alice" cannot be used by agent "bob". This prevents token forwarding.

7. **`_start_pending_game` is atomic.** Pending is only deleted after successful `session.start()`. On failure, pending is restored so agents can retry or leave.

8. **Host reassignment.** When the host leaves a lobby, the next player becomes host. Without this, the lobby would be stuck (only host can `start_game`).

9. **`wait_for_turn` returns immediately if pending actions exist.** No need to poll — if you have a deal to respond to or a vote to cast, the long-poll returns instantly with `trigger: pending_actions`.

10. **Global tools work in ALL states.** Analytics, history, and ledger tools are available whether the agent is in lobby, in game, spectating, or in a tournament. They're merged into `_GLOBAL_HANDLERS` at import time.
