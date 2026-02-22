# Live Dashboard & Spectator Mode Design

## Overview

Real-time dashboard for observing active games, tournaments, and platform metrics. Includes spectator mode allowing users to watch games in progress without participating.

## Current Infrastructure (What Exists)

### WebSocket/Sente (Production Ready)
- Automatic WebSocket with Ajax fallback
- Keep-alive, auto-reconnection with exponential backoff
- Rate limiting per player
- Player ↔ UID bidirectional mapping
- Connection state tracking
- `broadcast!()` and `send-to-player!()` functions

### Spectator System (80% Implemented)
Located in `src/clj/parlameme/v3/sessions.clj`:

```clojure
;; Already exists in session state:
:spectators #{}
:spectator-settings {:enabled? true 
                     :max-spectators 50
                     :visibility :full}  ; or :public-only

;; Available functions:
(add-spectator! session-id spectator-id)
(remove-spectator! session-id spectator-id)
(get-spectators session-id)
(spectator-in-session? session-id spec-id)
(get-spectator-view session-id)
(get-formatted-spectator-view session-id)
```

### Riemann Metrics (Fully Functional)
- Session lifecycle events
- Player join/leave tracking
- WebSocket connection metrics
- Game start/end with duration
- Event subscription system: `(subscribe-events! callback-fn)`

## What's Missing

| Gap | Impact | Solution |
|-----|--------|----------|
| No spectator WebSocket handlers | Can't join/leave via UI | Add `:v3/watch-game`, `:v3/unwatch-game` |
| State broadcast excludes spectators | Observers get stale data | Extend `broadcast-v3-state!` |
| No privacy enforcement | Spectators see private state | Implement visibility filtering |
| No dashboard UI components | Can't visualize games | Build in `live_dashboard/` module |
| No active games listing | Users can't find games to watch | Add `:v3/list-live-games` |

## Architecture Design

### State Model

```clojure
;; Client app-db structure for live dashboard
{:live-dashboard
 {:view :overview           ; :overview | :game-detail | :tournament-detail
  :active-games []          ; list of observable games
  :active-tournaments []    ; list of active tournaments
  :selected-game nil        ; game being watched
  :spectator-state nil      ; state received as spectator
  :filters {:game-type nil
            :min-players nil
            :show-private false}
  :metrics {:sessions {:active 0 :total 0}
            :spectators {:total 0}
            :connections {:ws 0 :ajax 0}}
  :loading? false
  :error nil}}
```

### WebSocket Messages (New)

**Client → Server:**
```clojure
[:v3/list-live-games {:filters {...}}]     ; Get observable games
[:v3/watch-game {:session-id "..."}]       ; Start watching
[:v3/unwatch-game {:session-id "..."}]     ; Stop watching
[:dashboard/subscribe {:layers [...]}]     ; Subscribe to metrics
[:dashboard/unsubscribe]                   ; Unsubscribe
```

**Server → Client:**
```clojure
[:v3/live-games {:games [...]}]            ; List of active games
[:v3/spectator-state {:state {...}}]       ; Game state for spectator
[:v3/spectator-joined {:spectator-id ...}] ; Someone started watching
[:v3/spectator-left {:spectator-id ...}]   ; Someone stopped watching
[:v3/game-started {:session-id ...}]       ; New game available
[:v3/game-ended {:session-id ...}]         ; Game no longer available
[:dashboard/metrics {:data {...}}]          ; Platform metrics
```

### Visibility Modes

**:full** (default for public games)
- All game state visible
- Player actions shown in real-time
- Deal/vote details revealed after resolution

**:public-only** (for private/competitive games)
- Round and phase only
- History of completed events
- No pending actions or private resources
- Player names but not their state

### State Filtering for Spectators

```clojure
(defn filter-state-for-spectator
  "Filter game state based on visibility settings."
  [state visibility]
  (case visibility
    :full
    (-> state
        (dissoc :pending-deals)  ; Hide unresolved deals
        (update :entities filter-public-entities))
    
    :public-only
    {:phase (:phase state)
     :round (:round state)
     :history (:history state)
     :groups (get-in state [:entities :groups])
     :player-count (count (get-in state [:entities :players]))}
    
    ;; Default: public only
    (filter-state-for-spectator state :public-only)))
```

## Component Design

### 1. Live Dashboard Overview

```
┌─────────────────────────────────────────────────────────────────┐
│  📊 Live Dashboard                              [Auto-refresh ✓]│
├─────────────────────────────────────────────────────────────────┤
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐        │
│  │ 12       │  │ 3        │  │ 47       │  │ 156      │        │
│  │ Active   │  │ Active   │  │ Total    │  │ Players  │        │
│  │ Games    │  │ Tourneys │  │ Spectators │ │ Online   │        │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘        │
├─────────────────────────────────────────────────────────────────┤
│  Filter: [All Games ▼] [Min 3 players ▼] [Show private ☐]      │
├─────────────────────────────────────────────────────────────────┤
│  🎮 Active Games                                                │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ ⚔️ duel-abc123          🎭 mafia-xyz789                 │   │
│  │ 2 players • Round 3     7 players • Day phase           │   │
│  │ 👁 5 watching           👁 23 watching                  │   │
│  │ [Watch]                 [Watch]                         │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  🏆 Active Tournaments                                          │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ 🏆 Weekly Cup           🏆 Beginner League              │   │
│  │ Round Robin • 8/8      Single Elim • 4/16              │   │
│  │ 3 matches active       2 matches active                │   │
│  │ [Watch]                [Watch]                         │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### 2. Game Spectator View

```
┌─────────────────────────────────────────────────────────────────┐
│  ← Back    ⚔️ Strategic Duel: duel-abc123     👁 5 watching     │
├─────────────────────────────────────────────────────────────────┤
│  Phase: NEGOTIATION    Round: 3/10    Time: 2:34               │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│    ┌─────────────┐              ┌─────────────┐                │
│    │  🎭 Alice   │              │  🎭 Bob     │                │
│    │  💰 150     │   VS         │  💰 120     │                │
│    │  ⚡ Active  │              │  ⚡ Active  │                │
│    └─────────────┘              └─────────────┘                │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│  📜 Recent Events                                               │
│  ├─ Round 3: Alice proposed Bribe to Bob                       │
│  ├─ Round 2: Bob accepted Handshake from Alice                 │
│  └─ Round 1: Phase advanced to Negotiation                     │
├─────────────────────────────────────────────────────────────────┤
│  💬 Spectator Chat                          [Spectators only]   │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ viewer42: Nice move by Alice!                           │   │
│  │ guest123: I think Bob will reject                       │   │
│  └─────────────────────────────────────────────────────────┘   │
│  [Type message...                                    ] [Send]   │
└─────────────────────────────────────────────────────────────────┘
```

### 3. Tournament Spectator View

```
┌─────────────────────────────────────────────────────────────────┐
│  ← Back    🏆 Weekly Cup                       👁 12 watching   │
├─────────────────────────────────────────────────────────────────┤
│  Format: Round Robin    Participants: 8    Matches: 12/28      │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  📊 Current Standings                                           │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ 1. Alice    6 pts  (3W-0L)                              │   │
│  │ 2. Bob      4 pts  (2W-1L)                              │   │
│  │ 3. Charlie  4 pts  (2W-1L)                              │   │
│  │ 4. Diana    2 pts  (1W-2L)                              │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  🎮 Live Matches                                                │
│  ┌───────────────────────┐  ┌───────────────────────┐         │
│  │ Alice vs Diana        │  │ Bob vs Eve            │         │
│  │ Round 2 • Negotiation │  │ Round 4 • Resolution  │         │
│  │ 👁 3 [Watch]          │  │ 👁 5 [Watch]          │         │
│  └───────────────────────┘  └───────────────────────┘         │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## Implementation Plan

### Phase 1: Backend WebSocket Handlers (2-3 hours)

**File: `src/clj/parlameme/v3/sente.clj`**

Add handlers:
- `:v3/list-live-games` - Return games with spectating enabled
- `:v3/watch-game` - Add spectator to session, start receiving updates
- `:v3/unwatch-game` - Remove spectator, stop updates

Modify:
- `broadcast-v3-state!` - Include spectators in broadcast loop
- Add `send-spectator-state!` with visibility filtering

### Phase 2: State Filtering (1-2 hours)

**File: `src/clj/parlameme/v3/sessions.clj`**

Add:
- `filter-state-for-spectator` - Apply visibility rules
- `build-spectator-state-message` - Format state for observers
- `list-observable-games` - Query games allowing spectators

### Phase 3: Dashboard Client Module (4-5 hours)

**New files:**
- `src/cljs/parlameme/live_dashboard/events.cljs`
- `src/cljs/parlameme/live_dashboard/subs.cljs`
- `src/cljs/parlameme/live_dashboard/views.cljs`

Components:
- `dashboard-overview` - Main view with stats and game list
- `game-card` - Single game preview with watch button
- `tournament-card` - Tournament preview
- `spectator-view` - Full spectator game view
- `tournament-spectator-view` - Tournament observation

### Phase 4: Integration & Polish (2-3 hours)

- Add dashboard navigation to lobby
- Connect metrics subscription
- Add spectator chat (reuse channel system)
- Test with multiple spectators
- Mobile responsive design

## API Endpoints (HTTP fallback)

For clients that can't use WebSocket:

```
GET  /api/live/games              - List active games
GET  /api/live/games/:id          - Get game state (spectator view)
GET  /api/live/tournaments        - List active tournaments
GET  /api/live/tournaments/:id    - Get tournament state
GET  /api/live/metrics            - Platform statistics
```

## Security Considerations

1. **Rate limiting** - Separate limits for spectators (lower than players)
2. **Visibility enforcement** - Server always filters state, never trust client
3. **Spectator cap** - Max 50 spectators per game (configurable)
4. **Private games** - Games with `spectating: false` not listed
5. **Tournament privacy** - Tournament host controls spectator access

## Files to Modify

| File | Changes |
|------|---------|
| `src/clj/parlameme/v3/sente.clj` | Add spectator handlers |
| `src/clj/parlameme/v3/sessions.clj` | Add spectator state filtering |
| `src/cljs/parlameme/live_dashboard/*` | NEW - Dashboard module |
| `src/cljs/parlameme/game/core.cljs` | Add dashboard require |
| `src/cljs/parlameme/game/db.cljs` | Add dashboard state |
| `src/cljs/parlameme/game/views.cljs` | Add dashboard navigation |

## Verification

1. Start server: `nrepl '(repl/go)'`
2. Open http://localhost:3000/game.html
3. Start a game in one browser tab
4. Open dashboard in another tab
5. Verify game appears in "Active Games"
6. Click "Watch" - verify spectator view loads
7. Make moves in game tab - verify spectator sees updates
8. Check spectator count updates in both views
9. Test with 3+ spectators for load testing
