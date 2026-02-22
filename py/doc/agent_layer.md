# Agent Layer

The agent layer runs LLM-powered AI agents in game sessions. It provides an in-process bridge to the MCP server (no HTTP overhead), multi-provider LLM integration, a game-playing loop with observation reuse, random-action bots for non-agent players, and a real-time NiceGUI page for watching agents play.

## Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│  agent_play.py (NiceGUI page)                                          │
│  /workshop/play/{strategy_id}                                          │
│                                                                        │
│  ┌──────────────┐  ┌──────────────────┐  ┌──────────────────────────┐ │
│  │  Controls     │  │  Game state (L)   │  │  Agent log (R)          │ │
│  │  provider,    │  │  entity cards,    │  │  turns, reasoning,      │ │
│  │  model, key   │  │  phase, votes,    │  │  actions, errors        │ │
│  │  start/stop   │  │  messages, victory│  │  (reverse-chrono)       │ │
│  └──────┬───────┘  └────────┬──────────┘  └─────────────────────────┘ │
│         │                   │                                          │
│         ▼                   │ subscribe(_on_game_change)               │
│  ┌──────────────────────────┼────────────────────────────────────────┐ │
│  │  AgentRunner (asyncio task)                                       │ │
│  │  loop: wait_for_turn → LLM decision → execute → reuse obs        │ │
│  │  on_turn callback → refresh UI panels                             │ │
│  ├───────────────────────────────────────────────────────────────────┤ │
│  │  BotRunner (asyncio task)                                         │ │
│  │  loop: poll state → random respond/vote for non-agent players     │ │
│  └──────┬────────────────┬───────────────────────────────────────────┘ │
│         │                │                                             │
│         ▼                ▼                                             │
│  InProcessBridge    LLMProvider                                        │
│  (MCPServer direct)  (Anthropic / Ollama)                              │
└────────────────────────────────────────────────────────────────────────┘
```

## File Layout

| File | Purpose | Lines |
|------|---------|-------|
| `agent/__init__.py` | Package exports: `AgentRunner`, `BotRunner`, `InProcessBridge` | ~12 |
| `agent/bridge.py` | In-process MCP bridge — calls MCPServer.handle_request directly | ~80 |
| `agent/providers.py` | LLM provider abstraction — Anthropic + Ollama | ~290 |
| `agent/runner.py` | Game-playing loop with observation reuse: observe → think → act | ~340 |
| `agent/bots.py` | Random-action bots for non-agent players (respond/vote) | ~130 |
| `server/pages/agent_play.py` | NiceGUI page: split view, controls, lifecycle | ~760 |
| `tests/test_agent_runner.py` | 39 tests: bridge, providers, runner, bots, archetypes | ~500 |

---

## InProcessBridge — Zero-Overhead MCP Access

Instead of HTTP requests to `POST /mcp`, the bridge calls `MCPServer.handle_request()` directly as an async Python function. This eliminates serialization overhead and network latency.

```python
class InProcessBridge:
    def __init__(self, mcp: MCPServer, agent_id: str):
        self.mcp = mcp
        self.agent_id = agent_id
        self._req_counter = 0
```

### Methods

| Method | What it does |
|--------|-------------|
| `initialize()` | Sends `initialize` JSON-RPC to register the agent session |
| `call_tool(name, args?)` | Sends `tools/call` and returns the result dict |
| `list_tools()` | Sends `tools/list` and returns available tool schemas |

Every call increments `_req_counter` for unique JSON-RPC `id` values. The bridge constructs a standard JSON-RPC 2.0 envelope:

```python
request = {
    "jsonrpc": "2.0",
    "id": self._req_counter,
    "method": "tools/call",
    "params": {"name": tool_name, "arguments": args or {}},
}
response = await self.mcp.handle_request(self.agent_id, request)
```

Error responses (no `result` key, has `error` key) are returned as-is rather than raising — the runner handles them gracefully.

---

## LLM Providers

`providers.py` defines a protocol and two implementations. Supports Anthropic (primary) and Ollama (free/local).

### LLMProvider Protocol

```python
class LLMProvider:
    async def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        system: str | None = None,
    ) -> LLMResponse: ...

@dataclass
class LLMResponse:
    content: str           # text response
    tool_calls: list[ToolCall]  # requested tool invocations
    stop_reason: str       # "end_turn" | "tool_use" | etc.

@dataclass
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]
```

### AnthropicProvider

- Uses the `anthropic` SDK
- Default model: `claude-haiku-4-5-20251001` (overridable via `AGENT_MODEL` env var)
- Converts MCP tool schemas to Anthropic format (`inputSchema` → `input_schema`)
- Extracts tool calls from `content` blocks of type `tool_use`
- `max_tokens=1024` for concise agent responses

### OllamaProvider

- HTTP client to local Ollama server (`http://localhost:11434/api/chat`)
- Default model: `llama3.2`
- Converts tools to OpenAI function-calling format
- Parses tool calls from response `message.tool_calls`
- Handles string-encoded arguments: Ollama may return tool args as a JSON string instead of a dict — the provider parses these automatically
- No API key required — runs locally

### Factory

```python
def create_provider(provider_type: str, model: str | None = None, **kwargs) -> LLMProvider:
    # "anthropic" → AnthropicProvider(api_key=..., model=...)
    # "ollama"    → OllamaProvider(model=...)
```

### Tool Schema Conversion

MCP tool schemas use `inputSchema`; Anthropic uses `input_schema`; Ollama uses OpenAI `function` format. Two converters handle this:

```python
_convert_tools_to_anthropic(tools)  # inputSchema → input_schema
_convert_tools_to_ollama(tools)     # MCP → {"type": "function", "function": {...}}
```

---

## AgentRunner — Game-Playing Loop

The runner implements a simple but effective agent loop with **observation reuse**: when the LLM calls `act()`, the result already contains updated game status, so the next turn skips the redundant `wait_for_turn` call. No LangChain, no LangGraph — the game engine provides all the structure via MCP tools.

### Configuration

| Constant | Value | Purpose |
|----------|-------|---------|
| `MAX_TURNS` | 100 | Hard limit on turns per game |
| `MAX_CONSECUTIVE_ERRORS` | 5 | Stop after N consecutive errors |
| `HISTORY_COMPACT_INTERVAL` | 10 | Compact message history every N turns |
| `WAIT_TIMEOUT` | 30 | Seconds to wait for turn (long-poll) |

### Construction

```python
runner = AgentRunner(
    strategy=strategy,           # Strategy document
    bridge=bridge,               # InProcessBridge
    provider=provider,           # LLMProvider
    compiled=compiled,           # CompiledGame
    on_turn=callback,            # Optional: called after each turn
)
```

The constructor immediately compiles the strategy into a system prompt:

```python
self.system_prompt = compile_strategy(strategy, compiled)
```

### Main Loop: `run_game()`

The loop manages a `pending_observation` that flows between turns:

```python
async def run_game(self) -> list[TurnEntry]:
    await self.bridge.initialize()
    pending_observation: str | None = None

    while self.turn_count < MAX_TURNS and not self._stopped:
        # 1. If no pending observation, long-poll for our turn
        if pending_observation is None:
            wait_result = await self.bridge.call_tool("wait_for_turn", {"timeout": 30})
            if self._is_game_ended(wait_result):
                break
            pending_observation = _extract_content(wait_result)

        # 2. Execute one turn with the observation
        entry, pending_observation = await self._run_turn(pending_observation)

        # 3. On error, reset observation to force re-observe
        if entry.error:
            pending_observation = None
            consecutive_errors += 1
            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                break
        else:
            consecutive_errors = 0

        # 4. Compact history periodically
        if self.turn_count % HISTORY_COMPACT_INTERVAL == 0:
            self._compact_history()

    return self.turn_log
```

### Single Turn: `_run_turn(observation) → (TurnEntry, next_obs)`

```
1. Context:  append observation to messages
2. Tools:    bridge.list_tools()              # available MCP tools
3. Think:    provider.complete(messages, tools, system_prompt)
4. Record:   save assistant response to messages
5. Act:      bridge.call_tool(tc.name, tc.args)  for each tool call
6. Result:   append tool results to messages
7. Reuse:    if tc.name == "act" → return result as next_obs (skip wait_for_turn)
```

**Observation reuse**: When the LLM calls `act()`, the MCP tool returns both the action result and updated game status. The runner captures this as `next_obs`, which becomes the input for the next turn — eliminating a redundant `wait_for_turn` round-trip. Non-act tools (like `get_messages`) return `next_obs = None`, forcing a fresh `wait_for_turn`.

The runner executes **all tool calls** from a single LLM response (not just the first), stopping early if any result indicates game over.

### Game End Detection

`_is_game_ended(result)` checks multiple signals:

- `result.status == "ended"` — direct status field
- `result.trigger == "game_ended"` — from `wait_for_turn`
- `"Status: ended"` or `"GAME OVER"` in content text — pattern matching in formatted output

### Context Management: History Compaction

LLM context windows are limited. Every 10 turns, `_compact_history()` summarizes old messages:

```
Before: [msg1, msg2, ..., msg18, msg19, msg20]
After:  [summary_of_1_to_14, msg15, msg16, msg17, msg18, msg19, msg20]
```

The summary preserves the last 6 messages intact and condenses everything before into a single message listing recent tool calls. This keeps context under control while preserving enough recent history for coherent decision-making.

### TurnEntry — Decision Log

```python
@dataclass
class TurnEntry:
    turn: int
    timestamp: float
    action: str = ""         # tool name or "think" or "game_ended"
    args: dict = {}          # tool arguments
    reasoning: str = ""      # LLM's text response
    result_summary: str = "" # tool result (truncated to 500 chars)
    error: str = ""          # error message if turn failed
```

The `on_turn` callback receives each entry immediately, enabling real-time UI updates.

---

## BotRunner — Random-Action Bots

When testing a single LLM agent, the other players need to take actions or the game stalls on phases requiring participation (format votes, deal responses). `BotRunner` fills this role.

### Design

```python
class BotRunner:
    def __init__(self, session, compiled, bot_player_ids, seed=42):
    def start(self) -> None     # creates asyncio background task
    def stop(self) -> None      # cancels the task
```

### Behavior

The bot runner is **purely reactive** — it never initiates deals or makes strategic choices:

| Action | When | How |
|--------|------|-----|
| **Respond to deals** | When a bot has a pending deal response (`responders[bot_id] is None`) | Random outcome from `deal_def.outcomes` |
| **Cast votes** | When a bot is eligible but hasn't voted | Random option from `vote_def.options` |

The runner polls every 300ms, checks all bot players, and re-reads the session state between actions. Deterministic seed (`random.Random(seed)`) ensures reproducible behavior.

### What bots don't do

- Don't initiate deals (no `start_deal`)
- Don't advance phases (no `advance_phase`)
- Don't send messages (no `send_message`)
- Don't make strategic decisions — random only

This is intentional: bots exist only to unblock game progression where all-player participation is required.

---

## Agent Play Page — Real-Time UI

`server/pages/agent_play.py` registers the route `/workshop/play/{strategy_id}` and renders a split-panel interface for watching an AI agent play.

### Layout

```
┌─────────────────────────────────────────────────────────────────────┐
│  Header: [← Back] Strategy Name / Game Name       [Edit Strategy]  │
├─────────────────────────────────────────────────────────────────────┤
│  Controls: [Provider ▼] [Model] [API Key ●●●] Status: RUNNING (5) │
│  [▶ Start] [■ Stop] [↻ Restart]                                   │
├────────────────────────────┬────────────────────────────────────────┤
│  Game State (55%)          │  Agent Log (45%)                      │
│                            │                                       │
│  Round 3 | Phase: bidding  │  Turn 5 — sealed_bid                  │
│  Status: ACTIVE            │    amount=50                          │
│                            │    ▸ "I'll bid on this lot because..." │
│  ┌──────┐ ┌──────┐        │    Result: Bid placed                  │
│  │alice │ │bob   │        │                                       │
│  │gold:80│ │gold:60│       │  Turn 4 — act                         │
│  └──────┘ └──────┘        │    ▸ "Let me check the status..."    │
│                            │                                       │
│  Vote: format [2/3]       │  Turn 3 — buy_info                    │
│                            │    lot=4                               │
│                            │    Result: Lot value revealed          │
├────────────────────────────┴────────────────────────────────────────┤
│  Performance Summary (shown after game ends)                        │
│  Turns: 12 | Actions: 8 | Errors: 0 | Result: VICTORY             │
└─────────────────────────────────────────────────────────────────────┘
```

### Lifecycle

| Action | What happens |
|--------|-------------|
| **Start** | Create `GameSession` → `InProcessBridge` → `LLMProvider` → `AgentRunner` → join game via MCP → `BotRunner` for non-agent players → `asyncio.create_task(runner.run_game())` |
| **Stop** | `runner.stop()` + `bot_runner.stop()` + `task.cancel()` |
| **Restart** | Stop + brief pause + Start |
| **Game ends** | Auto-report fires, runner detects game_ended, status → "finished", performance summary renders |
| **Start failure** | Cleanup: remove session, pop `_prev_resources`, show error |

### Bot Players

The agent plays as `agent-0`. The remaining `min_players - 1` seats are filled with `bot-1`, `bot-2`, etc. A `BotRunner` manages these bots, responding randomly to pending deals and casting random votes. This ensures the game progresses through phases that require all-player participation.

### Live Updates

The page subscribes to `GameSession.subscribe()` for state changes. When the game state changes (any player acts, phase advances, etc.), `game_state_view.refresh()` re-renders entity cards with resource deltas highlighted. The `on_turn` callback from `AgentRunner` refreshes the turn log panel after each turn.

### API Key Handling

- API key is entered in the UI via a password input field
- Passed directly to the provider on start — never persisted to browser storage, server-side storage, or logs
- Cleared when the page is navigated away from

---

## Testing

39 tests in `test_agent_runner.py`:

| Test Class | Count | Focus |
|------------|-------|-------|
| `TestInProcessBridge` | 5 | call_tool, error handling, list_tools, initialize, counter |
| `TestProviderUtils` | 8 | Tool schema conversion (Anthropic/Ollama), Ollama string args parsing, extract_text, create_provider factory |
| `TestRunnerUtils` | 5 | Content extraction, result truncation, game-end detection (4 patterns) |
| `TestAgentRunner` | 13 | Turn execution, observation reuse (act vs non-act), callback, stop, game end, history compaction, error limit, system prompt, archetypes |
| `TestBotRunner` | 4 | Import, init, stop, inactive game guard |

Key test patterns:

```python
# Mock MCP server for bridge tests
mcp = MagicMock()
mcp.handle_request = AsyncMock(return_value={"jsonrpc": "2.0", "id": 1, "result": {...}})
bridge = InProcessBridge(mcp, "test-agent")

# Mock LLM provider returning specific tool calls
provider = MagicMock()
provider.complete = AsyncMock(return_value=LLMResponse(
    content="I'll check status",
    tool_calls=[ToolCall(id="tc1", name="get_status", args={})],
    stop_reason="tool_use",
))

# Observation reuse: act() result becomes next turn's observation
entry, next_obs = await runner._run_turn("Phase: bidding\nYour turn.")
assert entry.action == "act"
assert next_obs is not None  # act result reused

# Non-act tool: no observation reuse
entry, next_obs = await runner._run_turn("Game state here")
assert entry.action == "get_messages"
assert next_obs is None  # need fresh wait_for_turn

# All archetypes compile to valid system prompts
for game_id, templates in ARCHETYPES.items():
    for t in templates:
        runner = AgentRunner(strategy=t, bridge=bridge, provider=provider, compiled=compiled)
        assert "<identity>" in runner.system_prompt
```

---

## Design Decisions

### Why no LangChain/LangGraph?

The MCP tool system already provides all the structure an agent needs:
- **Tool discovery**: `tools/list` returns available actions per game state
- **State observation**: `act` tool combines observe + execute; `wait_for_turn` returns full status
- **Turn coordination**: `wait_for_turn` long-polls until the agent can act
- **Error handling**: MCP returns structured errors (wrong phase, invalid args, etc.)

Adding a framework on top would add complexity without meaningful benefit. The agent loop is ~50 lines.

### Why in-process bridge instead of HTTP?

- **Zero overhead**: Direct function call vs HTTP round-trip
- **No serialization**: Python dicts flow through directly
- **Simpler testing**: Mock the MCP server, not an HTTP client
- **Same guarantees**: The bridge constructs valid JSON-RPC envelopes, so the MCP server processes them identically to HTTP requests

### Why observation reuse?

Before: every turn required `wait_for_turn` → `act({})` (observe) → LLM → `act(action, args)` (execute) = 3 MCP calls.

After: `wait_for_turn` returns status → LLM → tool call → result reused as next observation = 1-2 MCP calls per turn.

The `act()` tool already returns updated game status after executing an action. By capturing this and passing it as the next turn's observation, we skip the redundant `wait_for_turn` call on the next iteration. This roughly halves MCP traffic during active play.

### Why compile strategy to system prompt?

The strategy document is a structured data object (personality sliders, phase tactics, deal rules). The LLM needs natural language instructions. The compiler bridges this gap, producing an XML-sectioned system prompt (~1000-1500 tokens) that the LLM can follow. See the Strategy Layer documentation for details.

### Why random bots instead of smart bots?

Bots exist to **unblock** the game, not to play well. Smart bot logic would:
1. Require game-specific strategy per game type
2. Need balancing to avoid being too strong/weak
3. Add maintenance burden as games evolve

Random actions are game-agnostic and sufficient: they cast votes to pass thresholds, respond to deals to clear pending queues, and keep the game flowing. The LLM agent is the one being tested — bot quality doesn't matter for evaluation.
