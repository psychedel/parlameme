# Game History & Replay System Design

## Overview

The Game History & Replay system allows users to browse past games, view detailed event timelines, and replay games step-by-step. This integrates with Tournament Hub to show tournament match history.

## Goals

1. **Browse game history** - Filter by game type, player, date, outcome
2. **View game details** - Full event timeline with filters
3. **Replay games** - Step through events with visual state recreation
4. **Tournament integration** - Link matches to their archives for replay
5. **Statistics** - Player and game statistics derived from archives

## Architecture

### Data Flow

```
┌─────────────────────────────────────────────────────────────┐
│                     ARCHIVE SYSTEM                          │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Archive = {seed + decisions} → Deterministic Replay        │
│                                                             │
│  Storage:                                                   │
│  ├── data/archives/{archive-id}.edn  (complete archives)    │
│  ├── data/sessions.edn (session metadata + archive refs)    │
│  └── data/stats.edn (computed stats cache)                  │
│                                                             │
└─────────────────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────┐
│                     REPLAY ENGINE                           │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  (replay archive compiled-game)                             │
│     │                                                       │
│     ├── Apply seed → initial state                          │
│     ├── For each decision:                                  │
│     │   └── Apply → intermediate state + events             │
│     └── Return: [state₀, state₁, ... stateₙ]               │
│                                                             │
│  Features:                                                  │
│  - Step forward/backward through states                     │
│  - Jump to specific round/phase                             │
│  - View all events at each state                            │
│  - Highlight changes between states                         │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Component Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                   GAME CLIENT                                │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Views:                                                     │
│  ┌─────────┐  ┌──────────┐  ┌─────────┐  ┌──────────────┐ │
│  │ Lobby   │  │ Tourney  │  │ Game    │  │ History      │ │
│  │         │  │ Hub      │  │ View    │  │ Browser      │ │
│  └─────────┘  └──────────┘  └─────────┘  └──────────────┘ │
│                                              │              │
│                                              ▼              │
│                                    ┌──────────────────┐    │
│                                    │ Replay Viewer    │    │
│                                    │ (modal or page)  │    │
│                                    └──────────────────┘    │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## API Endpoints

### Existing (from History Browser)

```
GET /api/sessions
  ?status=active|completed|abandoned
  ?game-type=mafia|duel|...
  ?limit=50&offset=0
  
GET /api/sessions/:id
  → Full session details with events

GET /api/sessions/:id/events
  ?category=deal|vote|phase|...
  ?round=1
  
GET /api/stats/leaderboard
  ?game-type=duel
  ?min-games=3
```

### New Endpoints for Replay

```
GET /api/archives/:id
  → Full archive with seed + decisions

GET /api/archives/:id/replay
  ?step=0..n  (optional, for specific state)
  → Computed state at step N (server-side replay)

GET /api/archives/:id/events
  → All events extracted from archive
  
POST /api/archives/:id/verify
  → Verify archive hash matches stored hash
```

## State Management

### New DB Keys

```clojure
;; In game/db.cljs
:history {
  ;; Browse view
  :sessions []           ; [{:session-id :game-type :status :winner :players ...}]
  :sessions-loading? false
  :sessions-filter {:status nil :game-type nil :player nil}
  
  ;; Selected session
  :selected-session nil
  :session-detail nil
  :session-events []
  :events-filter {:category nil :actor nil :search ""}
  
  ;; Replay state
  :replay {
    :archive nil         ; Full archive data
    :compiled-game nil   ; Compiled game for replay
    :states []           ; [state₀, state₁, ... stateₙ]
    :current-step 0      ; Current replay position
    :playing? false      ; Auto-play mode
    :speed 1000          ; ms between steps
  }
}
```

### Subscriptions

```clojure
;; Browse
:history/sessions
:history/sessions-loading?
:history/filters

;; Detail
:history/selected-session
:history/session-detail
:history/session-events
:history/events-filtered

;; Replay
:history/replay-archive
:history/replay-states
:history/replay-current-step
:history/replay-current-state
:history/replay-playing?
:history/replay-can-step-back?
:history/replay-can-step-forward?
:history/replay-progress         ; 0.0 - 1.0
```

### Events

```clojure
;; Browse
:history/fetch-sessions
:history/sessions-loaded
:history/set-filter

;; Detail
:history/select-session
:history/load-session-detail
:history/session-detail-loaded
:history/filter-events

;; Replay
:history/start-replay          ; Load archive and compute states
:history/replay-loaded
:history/step-forward
:history/step-backward
:history/jump-to-step
:history/toggle-play
:history/set-speed
:history/close-replay
```

## UI Components

### History Browser View

```
┌────────────────────────────────────────────────────────────┐
│ ← Back                     📜 Game History                 │
├────────────────────────────────────────────────────────────┤
│ 🔍 [Search games...                                    ]   │
│                                                            │
│ Game: [All ▼]  Status: [All ▼]  Player: [All ▼]          │
├────────────────────────────────────────────────────────────┤
│                                                            │
│ ┌────────────────────────────────────────────────────┐   │
│ │ 🎭 Mafia Game #123           Completed  2h ago     │   │
│ │ Players: alice, bob, carol   Winner: 🐺 Mafia      │   │
│ │ Duration: 45m  Rounds: 8     [View] [Replay]       │   │
│ └────────────────────────────────────────────────────┘   │
│                                                            │
│ ┌────────────────────────────────────────────────────┐   │
│ │ ⚔️ Duel #456                  Completed  5h ago     │   │
│ │ Players: dave vs eve         Winner: dave          │   │
│ │ Duration: 12m  Rounds: 3     [View] [Replay]       │   │
│ └────────────────────────────────────────────────────┘   │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

### Session Detail View

```
┌────────────────────────────────────────────────────────────┐
│ ← Back to History          🎭 Mafia Game #123              │
├────────────────────────────────────────────────────────────┤
│ Status: ✓ Completed  |  Duration: 45m  |  Rounds: 8       │
│ Winner: 🐺 Mafia Team                                      │
├────────────────────────────────────────────────────────────┤
│ 👥 Players (6)                                             │
│ ┌──────────────────────────────────────────────────────┐ │
│ │ alice (Villager) - Eliminated R3                      │ │
│ │ bob (Detective) - Survived                            │ │
│ │ carol (Mafia) - Won                                   │ │
│ │ dave (Mafia) - Won                                    │ │
│ │ eve (Doctor) - Eliminated R5                          │ │
│ │ frank (Villager) - Eliminated R7                      │ │
│ └──────────────────────────────────────────────────────┘ │
├────────────────────────────────────────────────────────────┤
│ 📜 Event Timeline                     [▶ Start Replay]    │
│ ┌──────────────────────────────────────────────────────┐ │
│ │ Filter: [All ▼]  Actor: [All ▼]  🔍 Search...        │ │
│ ├──────────────────────────────────────────────────────┤ │
│ │ R1 🌅 Day Phase Started                               │ │
│ │ R1 🗳️ alice voted for carol                          │ │
│ │ R1 🗳️ bob voted for dave                             │ │
│ │ R1 💀 No one was eliminated (tie)                     │ │
│ │ R1 🌙 Night Phase Started                             │ │
│ │ R1 🔪 Mafia targeted alice                           │ │
│ │ R1 💊 Doctor protected bob                           │ │
│ │ ...                                                   │ │
│ └──────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────┘
```

### Replay Viewer (Modal/Overlay)

```
┌────────────────────────────────────────────────────────────┐
│ 🎬 Replay: Mafia Game #123                        [Close] │
├────────────────────────────────────────────────────────────┤
│                                                            │
│  ┌─────────────────────────────────────────────────────┐  │
│  │                                                     │  │
│  │              GAME STATE VISUALIZATION               │  │
│  │                                                     │  │
│  │   (Uses existing game-view components but in        │  │
│  │    read-only replay mode with historical state)     │  │
│  │                                                     │  │
│  │   Players, Resources, Phase, Round etc.             │  │
│  │                                                     │  │
│  └─────────────────────────────────────────────────────┘  │
│                                                            │
├────────────────────────────────────────────────────────────┤
│ Step 15/48  |  Round 3  |  Phase: Night                   │
│ ┌──────────────────────────────────────────────────────┐ │
│ │ ⏮  ◀  ▶  ⏭  |  ⏸ Pause  |  Speed: [1x ▼]            │ │
│ │                                                      │ │
│ │ ░░░░░░░░░░░░░░░░░░▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓ │ │
│ │ 0                  15                              48 │ │
│ └──────────────────────────────────────────────────────┘ │
├────────────────────────────────────────────────────────────┤
│ 📝 Event at this step:                                    │
│ 🔪 carol (Mafia) targeted alice                          │
└────────────────────────────────────────────────────────────┘
```

## Tournament Integration

### Link Matches to Archives

When a tournament match completes, store archive reference:

```clojure
;; In tournament/sessions.clj
(defn complete-match!
  [tournament-id match-id result]
  (let [archive-id (generate-archive-id game-session)
        ;; Save archive
        _ (archive/store! archive-id archive-data)
        ;; Update match with archive reference
        match-update {:status :completed
                      :winner (:winner result)
                      :archive-id archive-id}]
    (update-match! tournament-id match-id match-update)))
```

### Tournament Detail → Match Replay

```clojure
;; In tournament detail view
(defn match-card [{:keys [match]}]
  (let [{:keys [archive-id winner status]} match]
    [:div.match-card
     ;; ... match info ...
     (when (and archive-id (= status :completed))
       [:button {:on-click #(rf/dispatch [:history/start-replay archive-id])}
        "▶ Watch Replay"])]))
```

## Implementation Phases

### Phase 1: Core History Browser
1. Create `src/cljs/parlameme/history/` module (leverage existing code)
2. Add history route to game client
3. Implement sessions list with filters
4. Implement session detail view with event timeline

### Phase 2: Replay Engine
1. Backend: Add archive fetch endpoint
2. Backend: Implement server-side replay (optional, for verification)
3. Frontend: Add replay state management
4. Frontend: Implement replay controls (play/pause/step)

### Phase 3: Replay Viewer UI
1. Create replay modal component
2. Integrate existing game-view in read-only mode
3. Add timeline scrubber and controls
4. Add speed controls and keyboard shortcuts

### Phase 4: Tournament Integration
1. Update match completion to store archive-id
2. Add "Watch Replay" button to tournament matches
3. Link tournament bracket matches to history

## Technical Considerations

### Client-side vs Server-side Replay

**Option A: Client-side replay (recommended for v1)**
- Load archive + compiled game to client
- Replay in browser using `parlameme.archive/replay`
- Pros: Fast, no server load, works offline
- Cons: Requires compiled game on client, memory usage

**Option B: Server-side replay**
- Request state at step N from server
- Server computes and returns state
- Pros: Less client memory, verified states
- Cons: More server load, network latency

**Decision**: Start with client-side for speed, add server verification later.

### State Snapshots

For large games, computing all states upfront may be slow. Consider:

1. **Lazy computation**: Compute states on-demand as user navigates
2. **Checkpointing**: Store state every N steps, replay from nearest checkpoint
3. **Progressive loading**: Show first few states immediately, compute rest in background

### Archive Format

Archives are minimal for blockchain efficiency:

```clojure
{:version 1
 :rules-hash "sha256-abc..."  ; Compiled game hash
 :seed 12345                  ; RNG seed
 :players [:alice :bob]
 :decisions [                 ; Only player decisions
   [:deal :alice :bribe :bob {:amount 10}]
   [:respond :bob "deal-0" :accept]
   ...]}
```

Replay reconstructs full state from this minimal data.

## Files to Create/Modify

| File | Changes |
|------|---------|
| `src/cljs/parlameme/history/` | NEW module (or integrate into game) |
| `src/cljs/parlameme/game/db.cljs` | Add :history state |
| `src/cljs/parlameme/game/views.cljs` | Add history route |
| `src/clj/parlameme/archive/api.clj` | NEW - archive API endpoints |
| `src/clj/parlameme/tournament/sessions.clj` | Store archive-id on match complete |

## Testing

1. **Unit tests**: Replay produces same final state
2. **Integration tests**: Full flow from history browse to replay
3. **E2E tests**: Playwright test for replay UI controls
4. **Archive verification**: Hash matches after replay

## Open Questions

1. **Storage**: Keep archives in-memory or always on disk?
2. **Retention**: How long to keep archives? (Forever for blockchain anchoring)
3. **Privacy**: Should all players see full replay? (Team games may hide private info)
4. **Export**: Allow users to download archive files?
