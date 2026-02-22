# UI Architecture: Technical Vision

## Core Insight

NiceGUI already maintains a persistent WebSocket (Socket.IO) per browser tab. Every UI element mutation is pushed automatically via the `Outbox` pattern. We do NOT need a separate WebSocket layer — we need to wire `GameSession.subscribe()` into NiceGUI's reactive model.

## Current State vs Target

| Aspect | Current | Target |
|--------|---------|--------|
| State delivery | Pull (read `session.state` on click) | Push (subscribe → auto-refresh) |
| Multi-player sync | None (each player sees stale state) | Real-time (all players see changes instantly) |
| MCP ↔ UI sync | Separate paths, no cross-notification | Unified: MCP action → push to UI players |
| Visibility | Shows all entity data | Filtered via `view_for(player_id, compiled)` |
| Votes | No UI | Full vote UI with tally |
| Messages/Chat | Stored but not displayed | Real-time chat panel |
| Replay | Not implemented | Step-through viewer with state diff |
| History | Last 20 entries raw | Timeline component + searchable event log |

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    GameSession                          │
│  state: GameState (immutable, replaced atomically)      │
│  _lock: asyncio.Lock                                    │
│  _listeners: list[callback]                             │
│                                                         │
│  execute_deal() ─┐                                      │
│  respond_deal()  ├─→ update _state → _notify()          │
│  cast_vote()     │                                      │
│  advance_phase() ┘                                      │
│                                                         │
│  _notify() iterates _listeners:                         │
│    ├─ UI client 1 callback → refreshable.refresh()      │
│    ├─ UI client 2 callback → refreshable.refresh()      │
│    └─ Archive callback → record state                   │
└─────────────────────────────────────────────────────────┘
         ↑                              ↑
    NiceGUI Play Page              MCP Server
    (human player)                 (AI agent)
    subscribe on page load         calls session methods
    unsubscribe on disconnect      via handle_request()
```

### The Subscribe Pattern

```python
@ui.page("/play/{session_id}/{player_id}")
async def play_page(session_id: str, player_id: str):
    session = get_session(session_id)
    client = ui.context.client

    # Reactive state holder — refreshables read from this
    view = {"data": session.state.view_for(player_id, session.compiled)}

    # Push callback — fires when ANY player (human or AI) acts
    async def on_state_change(new_state: GameState):
        view["data"] = new_state.view_for(player_id, session.compiled)
        # NiceGUI pushes DOM diff to THIS client's browser
        game_view.refresh()

    session.subscribe(on_state_change)

    # Cleanup on disconnect
    async def cleanup():
        session.unsubscribe(on_state_change)
    client.on_disconnect(cleanup)

    @ui.refreshable
    def game_view():
        render_game(view["data"], session, player_id)
    game_view()
```

Key points:
- `on_state_change` runs inside the correct `Client` context (NiceGUI handles this because the callback was created within the page handler)
- `view_for()` filters by visibility — each player sees only what they should
- When MCP agent acts → `session._notify()` → callback fires → UI refreshes automatically
- Multiple human players on the same session all get pushed updates

### Why Not Separate WebSocket

NiceGUI's Socket.IO connection already handles:
- Reliable delivery with message IDs and history
- Reconnection with state recovery
- Binary transfer for images
- Automatic cleanup on disconnect

Adding a raw WebSocket endpoint would:
- Duplicate the connection (2 WS per client)
- Require manual state serialization
- Lose NiceGUI's DOM diffing (would need client-side rendering)
- Add complexity without benefit

## Page Structure

### 1. Lobby (`/`)

```
┌─ Header ─────────────────────────────────────────────┐
│ Parlameme                             [Dark] [User]  │
├──────────────────────────────────────────────────────┤
│                                                      │
│  ┌─ Available Games ──────────────────────────────┐  │
│  │  [Duel] [Mafia] [Auction] [Election] ...       │  │
│  │  (cards with icon, name, player range, create) │  │
│  └────────────────────────────────────────────────┘  │
│                                                      │
│  ┌─ Active Sessions ─────────────────────────────┐   │
│  │  game-1: Duel | Phase: action | 2/2 players   │   │
│  │  game-2: Mafia | Phase: night | 8/10 players  │   │
│  │  (auto-updates via timer or global subscribe)  │   │
│  └────────────────────────────────────────────────┘  │
│                                                      │
│  ┌─ Completed Games ─────────────────────────────┐   │
│  │  List of archived games with replay links      │   │
│  └────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────┘
```

State management: `ui.timer(2.0, refresh_sessions)` for lobby polling (low frequency, many sessions).

### 2. Play Page (`/play/{session_id}/{player_id}`)

```
┌─ Header ─────────────────────────────────────────────┐
│ [Back] Duel — game-1    Round 2 | Phase: action      │
├──────────────────────────────────────────────────────┤
│                                                      │
│  ┌─ Tabs ────────────────────────────────────────┐   │
│  │ [Game] [Chat] [History]                        │   │
│  │                                                │   │
│  │  ── Game Tab ──────────────────────────────    │   │
│  │  ┌── Players ─────────────────────────────┐   │   │
│  │  │ YOU (alice)          │ bob              │   │   │
│  │  │ Health: ████████ 80  │ Health: ██████ 60│   │   │
│  │  │ Energy: ████ 8       │ Energy: ██████ 12│   │   │
│  │  │ Shield: ██ 15        │ Shield: 0        │   │   │
│  │  │ Stance: aggressive   │ Stance: defensive│   │   │
│  │  └──────────────────────┴─────────────────┘   │   │
│  │                                                │   │
│  │  ┌── Actions ─────────────────────────────┐   │   │
│  │  │ [Attack ▼target] [Defend] [Rest]       │   │   │
│  │  │ [Taunt ▼target]  [Truce ▼target]       │   │   │
│  │  │ [All-Out Attack ▼target]               │   │   │
│  │  │                                        │   │   │
│  │  │ ── Pending ──                          │   │   │
│  │  │ bob proposes truce: [Accept] [Reject]  │   │   │
│  │  └────────────────────────────────────────┘   │   │
│  │                                                │   │
│  │  ── Chat Tab ──────────────────────────────   │   │
│  │  [arena] alice: nice move!                    │   │
│  │  [arena] bob: thanks                          │   │
│  │  [________________] [Send]                    │   │
│  │                                                │   │
│  │  ── History Tab ───────────────────────────   │   │
│  │  Timeline of events                           │   │
│  │                                                │   │
│  └────────────────────────────────────────────────┘  │
│                                                      │
│  ┌─ Footer ──────────────────────────────────────┐   │
│  │ [Advance Phase →]              Phase: action   │   │
│  └────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────┘
```

State management: `session.subscribe()` — push updates on every action from any player.

### 3. Replay Page (`/replay/{archive_id}`)

```
┌─ Header ─────────────────────────────────────────────┐
│ [Back] Replay: Duel — game-1                         │
├──────────────────────────────────────────────────────┤
│                                                      │
│  ┌─ Transport ───────────────────────────────────┐   │
│  │ [|◄] [◄] [▶/❚❚] [►] [►|]   Step 5/23        │   │
│  │ ═══════════●══════════════════════════════     │   │
│  └────────────────────────────────────────────────┘  │
│                                                      │
│  ┌─ Splitter ────────────────────────────────────┐   │
│  │                    │                           │   │
│  │  Game State        │  Changes                  │   │
│  │  (player cards,    │  (diff from prev step)    │   │
│  │   same as play     │  alice.health: 100→80     │   │
│  │   page rendering)  │  alice.energy: 10→8       │   │
│  │                    │  bob.health: 100→80       │   │
│  │                    │                           │   │
│  │  Decision:         │  Timeline (sidebar):      │   │
│  │  alice attacks bob │  ● R1: alice attacks      │   │
│  │  Outcome: default  │  ● R1: bob defends        │   │
│  │                    │  ● R1: advance phase       │   │
│  │                    │  ● R2: alice rests         │   │
│  └────────────────────┴──────────────────────────┘   │
│                                                      │
│  ┌─ View As ─────────────────────────────────────┐   │
│  │ [All] [alice] [bob]  (visibility filter)       │   │
│  └────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────┘
```

State management: purely local — replay state computed from archive, no server push needed.

### 4. Analytics Page (`/analytics`)

Tournament standings, player stats, game balance reports. Separate concern, can use `ui.timer` for periodic refresh or be static.

## Technical Decisions

### 1. URL-Based Player Identity

```
/play/{session_id}/{player_id}
```

NOT cookie-based auth. Each URL is a "seat" at the table. This is simple and matches the game engine model where player IDs are strings. For production, token-based auth can be layered on top.

### 2. Visibility Filtering via view_for()

Every UI render reads from `view_for(player_id, compiled)`, never raw `session.state`. This means:
- Private resources (hidden from others) are filtered before reaching the browser
- No client-side filtering needed (server authoritative)
- Replay can show "as player X saw it" by changing the observer_id

```python
@ui.refreshable
def player_cards():
    view = session.state.view_for(player_id, session.compiled)
    for eid, entity in view["entities"].items():
        render_entity_card(eid, entity, is_self=(eid == player_id))
```

### 3. Action Validation: Server-Authoritative

Do NOT duplicate filter/guard logic in UI. Instead:
1. UI shows all phase-allowed actions (simple)
2. Server validates and returns error if guard/filter fails
3. UI displays error via `ui.notify()`

This keeps UI thin and avoids expression evaluator in browser. MCP agents have the same flow.

Exception: **Usage limits** — can be checked client-side to disable buttons (avoids wasted clicks). This is a UX optimization, not security.

```python
# Show button but disable if usage limit reached
can_attack = not usage_exceeded(session.state, "attack", player_id)
ui.button("Attack", on_click=do_attack).props(
    f"{'disable' if not can_attack else ''}"
)
```

### 4. Chat via Channel System

Messages already exist in `GameState.messages`. The UI reads them filtered by channel visibility:

```python
@ui.refreshable
def chat_panel():
    view = session.state.view_for(player_id, session.compiled)
    for msg in view.get("messages", []):
        ui.chat_message(
            text=msg["content"],
            name=msg["sender"],
            sent=(msg["sender"] == player_id),
        )
```

Sending uses the existing runtime method:
```python
async def send_chat(channel_id: str, content: str):
    async with session._lock:
        result = session.runtime.send_message(
            session._state, channel_id, player_id, content
        )
        if result["ok"]:
            session._state = result["state"]
            await session._notify()
```

### 5. Replay Architecture

```python
class ReplayController:
    """Manages step-through replay of a game archive."""

    def __init__(self, archive: Archive, compiled: CompiledGame):
        self.archive = archive
        self.compiled = compiled
        self.runtime = GameRuntime(compiled)
        # Pre-compute all states (archive is small, replay is fast)
        self._states = self._compute_all_states()
        self.step = 0

    def _compute_all_states(self) -> list[GameState]:
        """Replay archive, capturing state after each decision."""
        states = []
        state = self.runtime.start_game(list(self.archive.players), self.archive.seed)
        state = self.runtime.run_setup(state)
        states.append(state)

        for decision in self.archive.decisions:
            state = apply_decision(self.runtime, state, decision)
            states.append(state)
        return states

    @property
    def current(self) -> GameState:
        return self._states[self.step]

    @property
    def prev(self) -> GameState | None:
        return self._states[self.step - 1] if self.step > 0 else None

    @property
    def current_decision(self) -> dict | None:
        if self.step == 0:
            return None
        return dict(self.archive.decisions[self.step - 1])

    @property
    def total_steps(self) -> int:
        return len(self._states) - 1

    def go_to(self, step: int):
        self.step = max(0, min(step, self.total_steps))

    def diff(self) -> dict:
        """Compute diff between current and previous state."""
        if not self.prev:
            return {}
        return compute_state_diff(self.prev, self.current)
```

This is purely client-side (per browser tab). No server push needed.

### 6. Component Architecture

Composable rendering functions, NOT monolithic page handlers:

```python
# components/entity_card.py
def entity_card(eid: str, entity: dict, is_self: bool, compiled: CompiledGame):
    """Render a single entity card."""
    color = "green" if entity["active"] else "red"
    with ui.card().classes(f"w-64 border-l-4 border-{color}-500"):
        with ui.row().classes("items-center gap-2"):
            ui.label(eid).classes("text-lg font-bold")
            if is_self:
                ui.badge("YOU").props("color=primary")
            ui.badge("ACTIVE" if entity["active"] else "OUT").props(
                f"color={'positive' if entity['active'] else 'negative'}"
            )
        for res_id, val in entity["resources"].items():
            resource_bar(res_id, val, compiled.resources.get(res_id))
        for attr_id, val in entity.get("attrs", {}).items():
            ui.label(f"{attr_id}: {val}").classes("text-xs opacity-70")


# components/action_panel.py
def action_panel(session, player_id: str, on_action: Callable):
    """Render available actions for current phase."""
    ...


# components/chat_panel.py
def chat_panel(messages: list, player_id: str, on_send: Callable):
    """Render chat messages and input."""
    ...


# components/resource_bar.py
def resource_bar(res_id: str, value: float, res_def):
    """Render a resource as labeled progress bar."""
    max_val = res_def.bounds[1] if res_def and res_def.bounds[1] else 100
    with ui.row().classes("items-center gap-2 w-full"):
        ui.label(res_id).classes("text-sm w-20")
        ui.linear_progress(
            value=max(0, float(value)) / float(max_val),
            show_value=False,
        ).classes("flex-grow")
        ui.label(f"{value:.0f}").classes("text-sm w-10 text-right")
```

### 7. File Organization

```
py/server/
    app.py              # FastAPI/NiceGUI app, MCP endpoint, page routing
    sessions.py         # GameSession (unchanged — already has subscribe)
    pages/
        lobby.py        # / page
        play.py         # /play/{session_id}/{player_id} page
        replay.py       # /replay/{archive_id} page
        analytics.py    # /analytics page
    components/
        entity_card.py  # Player card rendering
        action_panel.py # Deal/vote action buttons
        chat_panel.py   # Channel message display + input
        resource_bar.py # Resource progress bars
        timeline.py     # Event timeline (for history tab)
        replay_controls.py  # Transport bar + slider for replay
        state_diff.py   # State diff display
    replay.py           # ReplayController (archive → step-through states)
```

## Implementation Priority

### Phase 1: Push Updates (fixes the core problem)
1. Add `session.subscribe()` in play page
2. Wire `on_state_change` → `refreshable.refresh()`
3. Add `client.on_disconnect()` cleanup
4. Use `view_for()` instead of raw state

### Phase 2: Richer Play UI
1. Extract components (entity_card, action_panel, etc.)
2. Add vote UI (pending votes panel)
3. Add chat panel (read from state.messages)
4. Add send_message integration
5. Add pending deals with response buttons

### Phase 3: Replay System
1. ReplayController class
2. Replay page with transport controls
3. State diff display
4. "View as player X" filter

### Phase 4: Analytics & History
1. Completed games list on lobby
2. Archive save on game completion
3. Analytics page with stats
4. Tournament standings integration

## Non-Goals

- Custom WebSocket layer (NiceGUI already has it)
- Client-side rendering framework (React/Vue — NiceGUI IS Vue)
- Client-side game logic (server authoritative)
- Mobile-specific UI (NiceGUI/Quasar handles responsive basics)
- Internationalization (English only per CLAUDE.md)
