# Server Layer — Web Application & Session Management

The server layer is the top-level integration point that wires together the game engine, MCP interface, NiceGUI web UI, and persistence subsystems. It manages game session lifecycles, push-based UI updates, replay viewing, tournament pages, and AI agent play.

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  Browser (NiceGUI WebSocket)        AI Agent (HTTP JSON-RPC)     │
└─────────┬───────────────────────────────────┬────────────────────┘
          │ WebSocket (SSE push)              │ POST /mcp
          ▼                                   ▼
┌──────────────────────────────────────────────────────────────────┐
│  FastAPI / NiceGUI App (server/app.py)                           │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────────────┐ │
│  │  UI Pages     │  │  API Router  │  │  MCP HTTP Transport    │ │
│  │  (NiceGUI)    │  │  /health     │  │  /mcp (streamable)     │ │
│  │  /, /play,    │  │  /mcp/agent  │  │  /mcp/agent/{id}       │ │
│  │  /spectate,   │  │              │  │  (legacy)              │ │
│  │  /replay      │  │              │  │                        │ │
│  └──────┬───────┘  └──────────────┘  └────────────────────────┘ │
│         │                                                        │
│         ▼                                                        │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  GameSession (server/sessions.py)                         │   │
│  │  • asyncio.Lock for all mutations                         │   │
│  │  • Listener callbacks for push updates                    │   │
│  │  • Phase timeout watchdog                                 │   │
│  │  • Auto-advance when all actions exhausted                │   │
│  │  • Archive + ledger on game end                           │   │
│  └──────────────────────────────────────────────────────────┘   │
│         │                                                        │
│  ┌──────┴──────┐  ┌──────────────┐  ┌────────────────────────┐  │
│  │  Engine     │  │  Persistence │  │  Analytics Cache       │  │
│  │  (pure,     │  │  (debounced  │  │  (TTL, Glicko-2)       │  │
│  │  stateless) │  │  JSON)       │  │                        │  │
│  └─────────────┘  └──────────────┘  └────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

## Modules

### server/app.py (~1106 lines)

Main application file. Wires everything together.

**Responsibilities:**
- Game catalog derived from `REGISTRY`
- MCP server initialization with session/tournament store adapters
- Page routes: lobby (`/`), play (`/play/{sid}/{pid}`), spectate, replay
- MCP HTTP endpoints: legacy `/mcp/agent/{id}` and streamable `/mcp`
- Startup/shutdown hooks: ledger, persistence recovery, tournaments, cleanup loop

**Key patterns:**
- `_SessionStoreAdapter` / `_TournamentStoreAdapter` — bridge MCPServer to session/tournament stores
- `_mcp_sessions: dict[str, str]` — maps Mcp-Session-Id → agent_id for streamable transport
- `_cleanup_mcp_sessions()` — removes stale sessions (30min timeout), called from background loop
- Batch requests reorder `initialize` first so subsequent messages use the new session

### server/sessions.py (~624 lines)

Core game session management.

**GameSession class:**
- All state mutations protected by `asyncio.Lock`
- Notify pattern: mutate under lock, then call `_notify()` OUTSIDE lock
- Listener callbacks for push updates (NiceGUI UI, spectators, agents)
- Phase timeout watchdog via `asyncio.Task` (configurable per-phase or default 300s)
- Auto-advance when all usage limits exhausted for all players
- Archive creation + ledger crediting + chronicle saving on game end
- PostgreSQL sync (fail-open) on archive save

**Global session store:**
- `_sessions: dict[str, GameSession]` — in-memory registry
- `create_session()` / `get_session()` / `remove_session()` / `list_sessions()`
- `remove_session()` cancels phase timer before removing
- `recover_sessions()` replays decisions from persisted data, starts phase timer

**Session lifecycle:**
```
create_session() → GameSession.__init__()
    ↓
session.start() → run_setup() + _start_phase_timer() + _notify()
    ↓
execute_deal() / cast_vote() / advance_phase() / ...
    ↓ (each: lock → mutate → check victory → maybe archive → unlock → notify)
    ↓
Game ends → _maybe_archive() → save JSON + credit winner + chronicle + PG sync
    ↓
remove_session() → _cancel_phase_timer() + remove from _sessions + persistence
```

### server/persistence.py (~127 lines)

Debounced JSON persistence for session recovery across server restarts.

**SessionStore:**
- Tracks: game_type, players, seed, decisions, timestamps
- Debounced writes (2s delay) coalesce rapid updates
- Stale detection on load (>1h inactive)
- `touch()` called from `_notify()` on every state change
- `flush()` called on shutdown for clean persistence

**Design principle:** In-memory sessions are source of truth while running. Persistence store is only for restart recovery decisions.

### server/analytics.py (~357 lines)

Cached statistics engine with Glicko-2 ratings.

**StatsCache:**
- TTL-based refresh (5 min default)
- Reads all archives, computes player stats, ratings, game type stats
- Per-player: games, wins, streaks, recent form, tier
- Platform-level: total games, unique players, decision breakdown
- Head-to-head comparison
- Syncs to PostgreSQL on recompute (fail-open)

### server/replay.py (~82 lines)

Step-through game replay from archives.

**ReplayController:**
- Pre-computes ALL intermediate states on creation (instant navigation)
- Forward/back/go_to/to_start/to_end
- State diff computation between adjacent steps
- Observer selector (omniscient or per-player view)

### server/components/

UI components extracted from pages for reuse.

| Component | File | Description |
|-----------|------|-------------|
| `action_panel` | action_panel.py | Deals, votes, speech acts, pending responses, advance button |
| `entity_card` | entity_card.py | Player card with resources, attrs, groups, deltas |
| `chat_panel` | chat_panel.py | Multi-channel messaging with write filtering |
| `game_info` | game_info.py | Rules, phases, deals, victory conditions |
| `history` | history.py | Decision history timeline |
| `replay_controls` | replay_controls.py | Replay transport (play/pause/step/seek) |
| `layout` | layout.py | Page layout wrapper with navigation |
| `ui_kit` | ui_kit.py | Glass cards, status chips, section headers, empty states |

**action_panel.py** is the most complex (~824 lines):
- Uses same filter functions as MCP formatters (`can_player_use_deal`, etc.)
- Renders param inputs with type-aware widgets (number, keyword, player, text)
- Shows outcome previews from `mcp.mechanics`
- Phase context banners from `ContextConfig`
- Role guidance (collapsible) from `RoleHint`
- Advance readiness indicator (BLOCKED / READY / optional)

### server/pages/

Page routes registered via `register(games)` pattern.

| Page | Route | Description |
|------|-------|-------------|
| analytics | `/analytics` | Platform stats, leaderboard, game type breakdown |
| tournaments | `/tournaments`, `/tournaments/{id}` | Create, browse, manage tournaments; bracket view for SE |
| strategies | `/strategies`, `/strategies/{id}` | Strategy editor (system prompt + game selection) |
| workshop | `/workshop`, `/workshop/{id}` | Strategy workshop with live editing |
| agent_play | `/workshop/play/{strategy_id}` | Watch AI agent play with split view (game state + decision log) |

## Push Update Model

NiceGUI uses WebSocket connections. State updates are pushed to browsers via the listener/subscriber pattern:

```python
# Page subscribes on load
session.subscribe(on_state_change)
ui.context.client.on_disconnect(lambda: session.unsubscribe(on_state_change))

# Any mutation triggers push
async def execute_deal(self, ...):
    async with self._lock:
        # ... mutate state ...
    await self._notify()  # calls all listeners OUTSIDE lock

# Listener triggers UI refresh
def on_state_change(_new_state):
    game_view.refresh()  # NiceGUI re-renders the @ui.refreshable
```

**Critical rule:** Always unsubscribe on disconnect to prevent listener leaks.

## Phase Timeout System

Each interactive phase has a timeout (default 300s, configurable via `PhaseDef.duration`).

```
_start_phase_timer()
    ↓ creates asyncio.Task
    ↓ sleeps for duration
    ↓
_phase_timeout_handler(duration, phase_id, round_num)
    ↓ acquires lock
    ↓ guards: phase changed? round changed? game ended?
    ↓
    ├── _expire_pending_deals() — auto-reject all pending
    ├── auto-vote for non-voters (first option)
    └── advance_phase() + check victory
```

**Timer lifecycle:**
- Started in `session.start()`, `advance_phase()`, `_maybe_auto_advance()`
- Cancelled in `_cancel_phase_timer()`, called from `_start_phase_timer()` (restart) and `remove_session()`
- Recovered sessions start timer via `recover_sessions()`
- Guard in handler prevents stale timer from acting on wrong phase/round

## MCP Streamable HTTP Transport

Implements MCP spec 2025-03-26 via single `/mcp` endpoint.

**Session management:**
- `POST /mcp` with `initialize` → creates agent + session, returns `Mcp-Session-Id` header
- Subsequent requests include `Mcp-Session-Id` header
- `DELETE /mcp` → terminates session and removes agent
- `GET /mcp` → 405 (SSE stream not supported)

**Batch handling:**
- Array of JSON-RPC messages processed sequentially
- `initialize` messages reordered to front (at most one)
- Non-notification responses collected into array
- Single response unwrapped from array

**Stale cleanup:**
- Background loop every 5 minutes
- Removes agents not seen for 30 minutes
- Removes corresponding MCP sessions

## Agent Play System

The agent play page (`/workshop/play/{strategy_id}`) runs an AI agent in-process:

```
Strategy (system prompt) + Provider (Anthropic/Ollama) + Bridge (in-process MCP)
    ↓
AgentRunner.run_game() — async loop
    ↓ each turn: observe → think → act
    ↓ on_turn callback → update UI
    ↓
Split view: Game State (left) | Agent Log (right)
```

**Lifecycle management:**
- Start: create session → subscribe → create bridge/provider/runner → background task
- Stop: cancel task → unsubscribe listener → remove session
- Restart: stop + brief pause + start fresh
- On disconnect: unsubscribe via `on_disconnect` handler

## Design Decisions

1. **Notify outside lock** — prevents deadlock when listeners call back into session methods. Lock protects state mutation only; notification is fire-and-forget.

2. **Archive-first persistence** — sessions are ephemeral. Archives (seed + decisions) are the durable record. Session persistence is only for restart recovery, not long-term storage.

3. **Debounced persistence** — rapid state changes (multiple deals per second) coalesce into fewer disk writes. 2s delay balances durability vs performance.

4. **Same filters for UI and MCP** — `action_panel.py` imports filter functions from `mcp/formatters.py`. Humans and AI agents see identical action availability.

5. **Pre-computed replay** — `ReplayController` computes all states upfront. This makes forward/back navigation instant at the cost of memory (one `GameState` per decision).

## Common Gotchas

1. **Always unsubscribe on disconnect** — Missing `on_disconnect → unsubscribe` causes listener leaks. Every `subscribe()` must have a corresponding cleanup path.

2. **Recovered sessions need timers** — `recover_sessions()` must call `_start_phase_timer()` after replacing state. Without this, recovered games hang forever without timeouts.

3. **Remove cancels timer** — `remove_session()` must cancel the phase timer. Without this, a timeout handler may fire on a deleted session.

4. **Phase timer guards** — The timeout handler checks phase_id + round_num + status before acting. This prevents stale timers from corrupting state after phase has already advanced.

5. **Batch initialize ordering** — MCP batch requests with `initialize` must process it first, regardless of position in the array. Otherwise subsequent messages lack a valid session.

6. **Auto-advance checks all action types** — `_maybe_auto_advance()` checks deals, votes, AND speech acts. Missing any type causes premature phase advancement.

7. **Lock scope vs notify scope** — State mutation happens inside `async with self._lock`. Notification happens outside. Never call `_notify()` inside the lock.

8. **Persistence touch on every notify** — `_notify()` calls `_persistence.touch()` to update activity timestamp and persist decisions. This is how session state survives restarts.

9. **Archive saves are fail-safe** — `_maybe_archive()` wraps everything in try/except. A failed PG sync or chronicle save won't prevent the archive JSON from being written.

10. **Session counter is process-scoped** — `_session_counter = itertools.count(1)` resets on restart. Session IDs like `game-1` may collide with recovered sessions. In practice, recovered sessions keep their original IDs.
