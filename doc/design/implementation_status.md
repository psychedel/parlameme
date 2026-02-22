# Unified Flow Architecture — Implementation Status

**Updated:** 2026-02-02 (All Core Features Complete)
**Architecture:** Archive-first with Flow Log Hash Chain

## Current State

### Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                SIMPLIFIED ARCHITECTURE                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  flow/ (FOUNDATION LAYER)                                       │
│  ├── state.cljc      - entities, resources, groups, phases     │
│  ├── effects.cljc    - base effects (multimethod)              │
│  ├── expr.cljc       - expression evaluation                   │
│  ├── events.cljc     - event sourcing (in-memory)              │
│  ├── runtime.cljc    - flow execution engine                   │
│  ├── action.clj      - unified action API (multimethod)        │
│  └── schema.cljc     - Malli schemas                           │
│           ▲                                                     │
│           │ extends                                             │
│  ┌────────┴─────────────────────────────────────────────┐      │
│  │                                                       │      │
│  v3/runtime/ (GAME LAYER)        tournament/ (TOURNAMENT)│      │
│  ├── state.cljc                  ├── runtime.cljc        │      │
│  ├── effects.cljc                ├── effects.cljc        │      │
│  ├── core.cljc                   ├── dsl.cljc            │      │
│  ├── escrow.clj                  ├── sessions.clj        │      │
│  ├── channels.clj                ├── mcp.clj             │      │
│  └── expr.cljc                   └── schema.cljc         │      │
│                                                                 │
│  Persistence (Archive-First):                                   │
│  ├── archive.cljc    - deterministic replay from seed+decisions│
│  ├── ledger/atom_store.clj - Atom + EDN for balances           │
│  └── data/*.edn      - EDN files for persistence               │
│                                                                 │
│  Unified modules:                                               │
│  ├── result.cljc     - unified Result type {:ok? true/false}   │
│  ├── errors.cljc     - unified error registry (~90 codes)      │
│  ├── membership.cljc - flow membership enforcement             │
│  └── constants.clj   - shared constants                        │
│                                                                 │
│  MCP (Model Context Protocol):                                  │
│  ├── state.clj       - agent state management                  │
│  ├── stateful.clj    - MCP server with state machine           │
│  ├── schema.cljc     - dynamic tool schema generation          │
│  └── handlers/       - escrow.clj, spectator.clj, channels.clj │
│                                                                 │
│  Sessions (Lifecycle):                                          │
│  ├── v3/sessions.clj      - game session management            │
│  └── tournament/sessions.clj - tournament session management   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Design Philosophy: Archive-First

**Key insight:** Archives (seed + decisions) are the source of truth, not databases.

```
┌─────────────────────────────────────────────────────────────────┐
│                    ARCHIVE-FIRST PHILOSOPHY                     │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  MINIMAL ARCHIVE (~1-2KB per game):                             │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ {:version 1                                              │   │
│  │  :rules-hash "sha256-abc..."  ;; 32 bytes               │   │
│  │  :seed 12345                  ;; 4 bytes                │   │
│  │  :players [:alice :bob]       ;; ~20 bytes/player       │   │
│  │  :decisions [                 ;; ~30 bytes/decision     │   │
│  │    [:deal :alice :bribe :bob {:amount 10}]              │   │
│  │    [:respond :bob "deal-0" :accept]                     │   │
│  │    [:vote :carol "vote-0" :yes]]}                       │   │
│  └─────────────────────────────────────────────────────────┘   │
│                          │                                      │
│                          ▼                                      │
│                 DETERMINISTIC REPLAY                            │
│    (replay archive compiled-game) → identical final state       │
│                                                                 │
│  Benefits:                                                      │
│  • Blockchain-friendly: ~400 bytes compressed                   │
│  • Verifiable: replay produces identical state                  │
│  • Auditable: decisions form complete audit trail               │
│  • Simple: no database required for replay                      │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### What Was Removed (XTDB)

| Component | Lines | Replacement |
|-----------|-------|-------------|
| xtdb/node.clj | ~400 | None needed |
| xtdb/events.clj | ~300 | :decisions in state |
| xtdb/platform.clj | ~400 | TBD (stats) |
| xtdb/api.clj | ~400 | TBD (HTTP API) |
| xtdb/temporal*.clj | ~850 | Not needed |
| flow/xtdb.clj | ~750 | In-memory + archive |
| ledger/xtdb_store.clj | ~400 | atom_store.clj |
| ledger/anchoring.clj | ~350 | Deferred |

**Total removed:** ~7500 lines of XTDB-related code

### Current Persistence Strategy

| Data | Storage | Recovery |
|------|---------|----------|
| Ledger balances | `data/ledger.edn` | Load on startup |
| Game sessions | Session log (hash chain) | Replay from log |
| Tournament sessions | In-memory (Atom) | Archive replay |
| Messages | Session log | Replay from log |
| Stats/Leaderboards | Not persisted | Compute from archives |

#### Flow Log (Hash Chain) — NEW

Universal hash-chained log for all flows via `parlameme.flow.log`:

```
data/flow-logs/
  {flow-id}.jsonl         ; append-only JSONL with hash chain
```

Entry format (blockchain-compatible):
```clojure
{:v 1                        ; schema version
 :seq 0                      ; sequence number
 :type :meta|:event|:state-snapshot|:completion
 :ts 1737718523456           ; timestamp ms
 :data {...}                 ; type-specific payload
 :prev "0000...64chars"      ; previous entry hash (genesis = zeros)
 :hash "abcd...64chars"}     ; SHA-256 of canonical entry
```

Features:
- **Crash-safe**: fsync after each append
- **Tamper-evident**: hash chain links all entries
- **Merkle root**: computed on finalization for anchoring
- **Universal**: works for games, tournaments, and matches
- **Archive API**: HTTP endpoints at `/api/archive/*`

#### Flow Archive Cache

Completed flows are cached in `parlameme.flow.archive`:

```clojure
{:flow-id "game-123"
 :flow-type :game
 :status :complete
 :entry-count 15
 :merkle-root "abc123..."
 :created-at 1737718523456
 :completed-at 1737718600000
 :anchored? false
 :anchor-info nil}
```

HTTP API at `/api/archive`:
- `GET /stats` — archive statistics
- `GET /flows` — list all archived flows
- `GET /flow/:id` — flow metadata
- `POST /anchor` — trigger batch anchoring

### What's Fully Implemented

#### Core Game Engine
- ✅ Flow v3 DSL (game, resource, deal, vote, phase, victory)
- ✅ Deterministic runtime with seeded RNG
- ✅ Decision recording for replay
- ✅ Archive creation/replay/verification
- ✅ Communication channels (in-state)

#### Ledger System
- ✅ Atom-based storage with EDN persistence
- ✅ All ledger operations (deposit, withdraw, stake, transfer)
- ✅ Balance verification
- ✅ Game settlements

#### Flow Log & Archive
- ✅ Universal flow log (`parlameme.flow.log`) for all flow types
- ✅ Append-only JSONL with fsync
- ✅ SHA-256 hash chain for integrity verification
- ✅ Per-flow locks for concurrent writes
- ✅ Merkle root computation on finalization
- ✅ Flow archive cache (`parlameme.flow.archive`)
- ✅ Archive HTTP API (`parlameme.flow.anchoring_api`)
- ✅ Integration with v3/sessions.clj and tournament/sessions.clj

#### Tournament System
- ✅ Tournament DSL (round-robin, single-elimination)
- ✅ Tournament runtime with match spawning
- ✅ Match result synchronization
- ✅ Tournament MCP tools

#### MCP Integration
- ✅ Stateful MCP server (lobby/game states)
- ✅ Dynamic tool schema generation
- ✅ Agent state machine
- ✅ Tournament tools

### What's Missing

#### Critical (Recovery) — COMPLETE ✅
- ✅ Session persistence for server restart (session log)
- ✅ Runtime recovery from session log — replay decisions on startup
- ✅ Stale game detection (>2 hours threshold)
- ✅ Auto-refund stakes for unrecoverable/stale games

#### Stats — COMPLETE ✅
- ✅ Player statistics (wins, losses, win rate, by game type)
- ✅ Leaderboards (top players, min games threshold)
- ✅ Platform analytics (total games, active players, recent activity)
- ✅ HTTP API at `/api/history/*`
- ✅ TTL-based caching (1 hour refresh, persisted to `data/stats.edn`)

#### Deferred (Blockchain)
- 🔻 Merkle anchoring to Base L2
- 🔻 Archive storage on Arweave/IPFS

## Server Startup Flow (Simplified)

```
1. Game loader (register games)
2. Ledger load from EDN
3. v3/sessions init (membership provider)
4. Tournament system init (membership provider)
5. MCP stateful init (completion callbacks)
6. Sente start (WebSocket)
7. HTTP server start
```

Compare to previous (with XTDB):
```diff
- 1. XTDB start
+ 1. Game loader (register games)
- 2. Game loader (register games)
+ 2. Ledger load from EDN
- 3. Ledger recovery
- 4. Game state recovery (stale games, orphaned stakes)
- 5. Flow handlers registration (tournament, match, game)
- 6. Flow recovery (tournaments, matches from XTDB)
  7. v3/sessions init (membership provider)
  8. Tournament system init (membership provider)
  9. MCP stateful init (completion callbacks)
  10. Sente start (WebSocket)
- 11. MCP cleanup task start
  12. HTTP server start
```

## Code Metrics

| Module | Lines | Purpose |
|--------|-------|---------|
| v3/sessions.clj | ~1000 | Game session management |
| tournament/sessions.clj | ~1000 | Tournament session management |
| mcp/stateful.clj | ~1300 | MCP server with state machine |
| archive.cljc | ~300 | Deterministic replay |
| ledger/core.clj | ~220 | Ledger API |
| ledger/atom_store.clj | ~200 | Atom-based storage |
| v3/channels.clj | ~100 | In-state messages |

## Recommended Next Steps

### Phase 1: Complete Session Recovery — DONE ✅
1. ✅ Session log with hash chain
2. ✅ `recover-runtime` function to replay log decisions
3. ✅ Auto-recovery on server startup for incomplete logs
4. ✅ Stale game detection (>2 hours threshold)
5. ✅ Auto-refund stakes for unrecoverable/stale games

### Phase 2: Multilateral Deals — DONE ✅
1. ✅ DSL with `:responders` (multiple responders with count constraints)
2. ✅ Completion rules: `:all`, `:threshold`, `:majority`, `:any-reject`
3. ✅ Runtime handles multilateral responses with proper state tracking
4. ✅ WebSocket events for multilateral deals
5. ✅ MCP tool schema generation for multilateral deals
6. ✅ Full test coverage (13 tests, 45 assertions)
7. ✅ Generic party classification — custom party names (e.g., `:leader`/`:partners`) supported
8. ✅ MCP reverse mapping — canonical params ↔ custom party names via `classify-deal-parties`

### Phase 3: Stats — DONE ✅
1. ✅ Stats computed from archives on demand
2. ✅ TTL-based caching (1 hour, persisted to disk)
3. ✅ HTTP API for leaderboards at `/api/history/leaderboard`
4. ✅ Player stats, game type stats, platform stats

### Phase 3.5: Engine Hardening (MCP Testing) — DONE ✅

Fixes discovered during comprehensive MCP testing of all 7 game types:

1. ✅ **Generic party classification** (`mcp/schema.cljc`) — `classify-deal-parties` analyzes party structure by features (`:count`, `:excludes`) not names
2. ✅ **Vote param resolution** (`runtime/core.cljc`) — `start-vote` stores `:params`, `complete-vote` merges via `make-ctx`
3. ✅ **Vote auto-cast** (`mcp/server.clj`) — auto-finds pending vote when no `instance_id` provided
4. ✅ **Compiler phase warnings** (`compiler/phases/analyze.cljc`) — `warn-once-phases` + `warn-guard-circular-deps`
5. ✅ **DSL extensions** — deal guards, option guards, set attrs, persistent reveals, deception, `:not-in-group` filter, `every-in-group?` predicate
6. ✅ **Param filter enforcement** — `validate-param-filters` + `get-available-param-targets` in runtime
7. ✅ **Vote outcome fallback** — `complete-vote` falls back to `:selected` for plurality votes
8. ✅ **coll? safety** — `contains?`/`not-contains?` check `coll?` before iterating

### Phase 4: Blockchain Anchoring (DEFERRED)
1. Merkle root anchoring to Base L2
2. Archive storage on Arweave/IPFS
3. Verification API

## E2E Testing Observations (2026-01-24)

### Test Session: Full Tournament E2E

**Setup:**
- Round-robin tournament with 4 players (player-alpha, player-beta, player-gamma, player-delta)
- 6 matches total (each player plays every other player)
- Game type: duel (2 players, combat game)

**Results:**
- Tournament completed successfully
- Final standings: player-alpha (9pts, 3 wins) > player-beta (6pts, 2 wins) > player-delta (3pts, 1 win) > player-gamma (0pts)
- All 7 archives created (1 tournament + 6 match games)
- Merkle roots computed for all flows
- Hash chain integrity verified via `flow-log/verify-flow`

### What Works Well
- **Tournament lifecycle**: Create → register → start → play matches → complete
- **Auto-spawn matches**: When round completes, next round matches spawn automatically
- **Flow log finalization**: Each match gets merkle root on completion
- **Tournament completion**: Final match triggers tournament completion with merkle root
- **Victory detection**: Auto-completes game when victory condition met (knockout)
- **Standings calculation**: Points, wins, games correctly computed
- **Archive persistence**: All flows saved to `data/flows/complete/` as `.transit` files
- **Archive API via REPL**: `archive/list-archives`, `archive/recent-archives`, `flow-log/read-flow` work correctly

### Issues Found

1. **REPL changes don't broadcast to WebSocket clients** — RESOLVED
   - Actions via REPL (`start-deal!`, `respond-deal!`) update server state
   - But UI doesn't receive updates (shows stale state)
   - Root cause: REPL path bypasses WebSocket broadcast
   - **Solution**: Use `*-and-broadcast!` functions:
     - `start-deal-and-broadcast!` — starts deal + broadcasts state
     - `respond-deal-and-broadcast!` — responds + broadcasts + checks victory
     - `advance-phase-and-broadcast!` — advances phase + broadcasts
     - `complete-game-and-broadcast!` — completes game + broadcasts win

2. **MCP agent state not synced with tournament auto-join**
   - Tournament auto-creates game sessions and adds players
   - But MCP agents remain in "lobby" state (`my_status` returns `{:state "lobby"}`)
   - `join_game` fails with "game-already-started"
   - Impact: MCP agents can't play tournament matches without manual state sync
   - Fix needed: Tournament should update MCP agent state when spawning matches

3. **History HTTP API shows stale/different data than REPL**
   - `archive/list-archives {:flow-type :tournament}` returns 3 tournaments
   - `GET /api/history/flows?flow_type=tournament` returns only 1
   - `rebuild-cache!` doesn't fix HTTP API response
   - Likely using separate cache or transforming IDs differently
   - Impact: History browser may show incomplete data

4. **Game completion not auto-reported to tournament** — RESOLVED
   - First match (played fully via REPL) completed with victory
   - Tournament match status remained `:pending`
   - Root cause: REPL path doesn't trigger completion callback
   - **Solution**: Use `respond-deal-and-broadcast!` which calls `broadcast-win!`
     that triggers `check-and-broadcast-v3-win!` → `complete-game!` → callbacks

5. **Player membership stale entries**
   - `player-sessions` atom retains entries after session ends
   - Causes "Player already in game" errors for subsequent games
   - Workaround: manually `dissoc` from `:player-sessions`
   - Fix needed: ensure `leave-session!` or completion clears membership

6. **Shield resource exceeds cap** — FIXED
   - Duel game: shield should cap at 5, but reached 8 via repeated defend
   - Bounds `[0 5]` not being enforced on `:boost` effect
   - **Fix**: Resource bounds now enforced in all boost operations including
     stakes effects (`:return-stakes`, `:transfer-stakes`, `:distribute`)

7. **Riemann not running (port 5555 refused)**
   - Attempted to monitor via Riemann dashboard
   - Server not started / not configured
   - Low priority: monitoring is optional feature

### Recommendations

1. ~~**Critical**: Fix MCP agent state sync for tournament matches~~ **RESOLVED**
   - ✅ `sync-mcp-agent-to-game!` in `tournament/sessions.clj` syncs agents when match spawns
   - ✅ `return-agent-to-tournament!` in `mcp/state.clj` returns agents after match
   - ✅ Both completion callbacks registered (MCP + tournament)

2. ~~**High**: Add WebSocket broadcast to REPL session operations~~ **RESOLVED**
   - Use `*-and-broadcast!` functions in `v3/sessions.clj` for REPL with WebSocket sync

3. ~~**High**: Fix History HTTP API cache sync~~ **RESOLVED**
   - ✅ Removed cache from `flow-archive.clj` - now reads files directly
   - ✅ Fixed `parse-int-bounded` nil handling in `api/history.clj`

4. ~~**Medium**: Enforce resource bounds in `:boost` effect~~ **RESOLVED**
   - ✅ Fixed in `v3/runtime/effects.cljc` - `:bounds` enforced in `:boost` effect

5. ~~**Medium**: Setup Riemann monitoring~~ **RESOLVED**
   - ✅ Riemann server at `~/riemann/` - start with `./bin/riemann etc/riemann.config`
   - ✅ Dashboard available at `:3000/riemann.html`
   - ✅ Client auto-connects when server is running

6. ~~**Low**: Fix player membership stale entries after game completion~~ **RESOLVED**
   - ✅ Membership cleanup happens in `do-complete` and `do-cancel`
   - ✅ Added cleanup in `rollback-tournament-start!` for tournament rollback

## Known Limitations

1. **No temporal queries** — Cannot query "balance at time X"
2. **No admin API** — Platform stats endpoints removed (use REPL)

## Known Issues and Bugs

### Election Race Game Testing (2026-02-03)

**Fixed:**
1. **MAX-ROUNDS constant bug** — Clojure constants (`def ^:const`) used in DSL quoted expressions don't resolve. Symbols remain as-is in quoted forms, causing NPE in expression evaluator. **Fix:** Use literal values in quoted expressions instead of constant references.

**Open UI Issues:**
1. **Icons render as text** — Action icons (eye, coin, thumbs-up, handshake, whisper, sword, target, scroll) display as text instead of graphic icons. Likely missing icon font or SVG rendering.

2. **Keyword params show as textbox** — Parameters with `:type :keyword` and `:values` (e.g., offer, policy, pact-type, intensity, promise-type) render as textbox instead of dropdown/select. UI should detect `:values` array and render `<select>`.

3. **WebSocket errors not sent to client** — `game/sente.clj` catch block logs errors but doesn't send error response to client. UI shows "Loading..." indefinitely with no feedback.

**Platform Issues:**
1. **Round-robin tournaments incompatible with 4+ player games** — Round-robin generates 2-player matches by design. Multi-player games need different tournament format or bracket system.

2. **MCP activate_game expects `:token` not `:invite_token`** — Parameter name mismatch in MCP tool schema vs handler implementation.

## Comprehensive UI/MCP Testing (2026-02-03)

### Testing Methodology
Full end-to-end testing of all 7 games via both UI (Playwright browser automation) and MCP/REPL.

### Games Tested

| Game | Min Players | UI Works | Actions Tested | Issues Found |
|------|-------------|----------|----------------|--------------|
| Duel | 2 | ✅ | attack, defend, rest, chat | None |
| Mafia | 6 | ✅ | claim-role, accuse, buy-vote | String param serialization bug |
| Werewolf | 10 | ✅ | convert | UI doesn't show player role |
| Resistance | 5 | ✅ | endorse, object, challenge | None |
| Auction | 3 | ✅ | appraise, buy-intel | None |
| Parliament Arena | 6 | ✅ | promise | None |
| Election Race | 4 | ✅ | (viewed from previous session) | None |

### Tournament System Tested
- ✅ Tournament Hub UI displays correctly
- ✅ Tournament list with filtering (status, type)
- ✅ Tournament details view (participants, standings, matches)
- ✅ Create tournament via REPL
- ✅ Register participants via REPL
- ✅ Start tournament via REPL
- ✅ Auto-spawn match sessions
- ✅ Live tournament appears in UI

### Critical Bug Found

**String Parameter Serialization Bug (Mafia game)**

When a deal parameter has `:type :string` (e.g., `:reason` in Mafia's `accuse` deal), the string value is incorrectly serialized as a keyword instead of a quoted string.

**Reproduction:**
1. Start Mafia game
2. Use `accuse` action with reason text "He was acting suspicious last night!"
3. Server sends WebSocket message with: `:params {:reason :He was acting suspicious last night!, :target :bot_beta_3376}`

**Expected:** `:params {:reason "He was acting suspicious last night!", :target :bot_beta_3376}`

**Error on client:**
```
Error: The map literal starting with :reason contains 9 form(s). Map literals must contain an even number of forms.
```

**Impact:** Any deal with string parameters (accuse, taunt, promise, statement, etc.) breaks EDN parsing on client, leaving UI in "Loading..." state.

**Location:** Likely in `v3/sente.clj` or WebSocket serialization layer where string params are converted to keywords.

### UX Issues Found

1. **Werewolf: Player role not shown in UI** — Player has role `:alpha-wolf` in server state but UI only shows resources (Trust, Suspicion, Influence). Should show role panel like Mafia does.

2. **Chat: "bad :dispatch value" error** — After sending chat message, console shows `re-frame: ignoring bad :dispatch value`. Message still sends successfully.

3. **Phase transitions require page refresh** — After clicking "Next Phase", sometimes UI doesn't update until page is refreshed. WebSocket state sync issue.

4. **re-frame-10x devtools intercepts clicks** — The devtools overlay intercepts pointer events, blocking game actions. Workaround: hide devtools via JavaScript.

### What Works Well

1. **Lobby UI** — Clean game selection, space selection, name entry
2. **Bot system** — "Add N Bots" button correctly fills remaining player slots
3. **Resource display** — All game resources render correctly with icons
4. **Action palette** — Available actions update based on phase and filters
5. **Target selection** — Multi-target buttons work correctly
6. **Chat channels** — Channel selector and message send work
7. **Tournament Hub** — Complete tournament browsing and details view
8. **Session recovery** — UI correctly restores session from localStorage

### Dot Notation Verification

Confirmed that dot notation syntax (`:actor.energy` instead of `[:actor :energy]`) works correctly:
- Games reloaded via `loader/reload!` use new syntax
- Compiled filters show: `(>= :actor.energy 2)`
- Victory conditions show: `(<= :actor.health 0)`
- Expression evaluation works at runtime

## Related Documents

- `unified_flow_decisions.md` — Architecture decisions
- `tournament_integration.md` — Tournament system design
- `../flow_v3_specification.md` — DSL specification
