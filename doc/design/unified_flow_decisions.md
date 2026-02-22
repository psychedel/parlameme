# Unified Flow Architecture — Finalized Decisions

## Summary

This document captures all architectural decisions made during the unification of Game and Tournament systems.

**Date:** 2026-01-17
**Updated:** 2026-01-24
**Status:** Approved and Implemented

---

## Decision 1: Flow Hierarchy (Variant C)

**Question:** How do Game, Tournament, and potential future types relate?

**Decision:** Everything is a Flow with `:flow/type`

```clojure
{:flow/id :parliament-arena
 :flow/type :game        ;; or :tournament, :league, :campaign
 :flow/name "Parliament Arena"
 ...}
```

**Rationale:**
- Unified runtime with polymorphic dispatch
- New types (leagues, campaigns) added without changing core
- Common primitives (entities, resources, phases, effects) reused

---

## Decision 2: Match Template (Variant C.1)

**Question:** How to define which game is used for tournament matches?

**Decision:** Reference with version

```clojure
:match-template {:flow-id :parliament-arena
                 :version "abc123"}  ;; content-hash from loader
```

**Rationale:**
- Exact reproducibility — known which rules were in each match
- Can update game without breaking ongoing tournaments
- Version = content-hash from `parlameme.v3.loader`

---

## Decision 3: Match as Flow Instance (Variant C.1.a)

**Question:** Where to store match state?

**Decision:** In-memory with archive persistence

```clojure
;; Tournament in memory
{:tournament-id :tournament-001
 :flow/type :tournament
 :flow/id :world-cup
 ...}

;; Match (separate flow)
{:session-id :match-001
 :flow/type :game
 :flow/parent {:tournament-id :tournament-001
               :stage :group-a
               :round 1}
 :flow/instance-of {:flow-id :parliament-arena
                    :version "abc123"}
 ...}
```

**Rationale:**
- Matches have full history as regular games
- Archive replay enables verification
- Parent link allows finding all tournament matches
- Scales to thousands of matches

---

## Decision 4: Archive-First Persistence

**Question:** Where is the source of truth?

**Decision:** Archives (seed + decisions) are the source of truth

```
┌─────────────────────┐
│      Atom           │  ← Fast operations, current state
│  (in-memory state)  │
└─────────┬───────────┘
          │ archive on completion
          ▼
┌─────────────────────┐
│   Archive (EDN)     │  ← Durable storage, replay, verification
│  (seed + decisions) │
└─────────────────────┘
```

**Pattern:**
1. Runtime works with atom (fast)
2. Decisions recorded in state for replay
3. Archive created on game completion
4. Recovery via deterministic replay

**Previously considered:** XTDB for bitemporality, but removed in favor of simpler archive-first approach.

---

## Decision 5: Deterministic Replay (Not Event Sourcing)

**Question:** How to store history for replay/recovery?

**Decision:** Minimal archives with deterministic replay

```clojure
;; Archive (stored)
{:version 1
 :rules-hash "sha256..."
 :seed 12345
 :players [:alice :bob]
 :decisions [[:deal :alice :bribe :bob {:amount 10}]
             [:respond :bob "deal-0" :accept]]}

;; Replay
(archive/replay archive compiled-game)
;; => identical final state
```

**Benefits:**
- Much smaller than event logs (~1-2KB per game)
- Blockchain-friendly storage
- Verifiable — replay produces identical state
- Simpler implementation

**Previously considered:** Event sourcing with snapshots, but archives are more efficient.

---

## Decision 6: Hierarchical Flow Membership (Variant C)

**Question:** Can an entity participate in multiple flows?

**Decision:** Entity can be in parent flow + its children, but not in unrelated flows

```
Tournament A
├── Match A1 — Alice vs Bob      ✓ Alice in Tournament A and Match A1
├── Match A2 — Carol vs Dave     ✓ Carol in Tournament A and Match A2
└── ...

Tournament B
└── Match B1 — Eve vs Frank      ✗ Alice cannot be here
```

**Constraint:**
- One "root" flow per entity at a time
- Children flows are "diving into" parent context

**Implementation:**
```clojure
(defn can-join-flow? [entity-id flow-id state]
  (let [current-root (get-root-flow entity-id state)]
    (or (nil? current-root)
        (ancestor-of? current-root flow-id))))
```

---

## Decision 7: Unified action! API

**Question:** How to unify API for actions in different flow types?

**Decision:** Single `action!` with dispatch by type

```clojure
(flow/action! flow-id entity-id :bribe {:responder :bob :amount 50})
(flow/action! tournament-id system-id :report-result {:match-id m1 :winner :alice})
```

**Dispatch:**
```clojure
(defn action! [flow-id entity-id action-type params]
  (let [flow (get-flow flow-id)
        handler (get-action-handler (:flow/type flow) action-type)]
    (handler flow entity-id params)))
```

**Convenience helpers:**
```clojure
(defn bribe! [game-id proposer responder amount]
  (action! game-id proposer :bribe {:responder responder :amount amount}))

(defn report-match! [tournament-id match-id winner]
  (action! tournament-id :system :report-result {:match-id match-id :winner winner}))
```

---

## Decision 8: Child → Parent Lifecycle

**Question:** How does child flow (match) notify parent (tournament)?

**Decision:** Child executes action `:child/completed` on parent

```clojure
;; When match ends (in flow/runtime)
(defn on-flow-complete [runtime]
  (when-let [parent (:flow/parent (:compiled runtime))]
    (action! (:tournament-id parent) 
             :system 
             :child/completed
             {:child-id (:flow/id runtime)
              :result (get-victory-result runtime)})))
```

**Tournament handles:**
```clojure
(defmethod handle-action [:tournament :child/completed]
  [runtime entity-id {:keys [child-id result]}]
  (-> runtime
      (update-match-status child-id :completed)
      (apply-match-result result)
      (maybe-advance-bracket)))
```

---

## Decision 9: DSL Expressiveness

**Question:** Which tournament formats to support?

**Decision:** Composable stage types

| Stage Type | Description |
|------------|-------------|
| `:round-robin` | Each plays each |
| `:single-elimination` | Bracket, lose once = out |
| `:double-elimination` | Upper/Lower brackets |
| `:swiss` | N rounds Swiss system |
| `:groups-and-knockout` | Groups → playoffs |
| `:best-of-n` | Up to N wins per match |

**Extensibility:**
```clojure
(defmethod stage-type->phases :double-elimination
  [stage-def participants]
  ;; Returns upper bracket + lower bracket + grand final
  [{:phase/id :upper-round-1 :spawns [...]}
   {:phase/id :lower-round-1 :spawns [...]}
   ...
   {:phase/id :grand-final :spawns [...]}])
```

**Best-of-N:**
```clojure
(match-template :parliament-arena
  {:best-of 3  ;; Up to 2 wins
   :tie-breaker :sudden-death})
```

---

## Subsystem Integration

### Flow v3 Runtime
- Unified runtime for games and tournaments
- Polymorphic dispatch by `:flow/type`

### MCP Integration
- Single `action!` translates to MCP tools
- Tournament tools: `tournament/report_result`, `tournament/get_bracket`
- Dynamic tools based on current flow context

### WebSocket (Sente)
- Real-time updates for games and tournaments
- Topic structure: `[:flow/<flow-id>]`

### Archive Persistence
- Games archived on completion with seed + decisions
- Tournaments archive each match separately
- Replay for verification at any time

### Ledger & Escrow
- Tournament entry fees via standard ledger
- Prize distribution on tournament completion
- Merkle anchoring of results (deferred)

### Monitoring
- Flow metrics aggregated by type
- Unified event stream for Riemann/frontend

### Frontend
- Components work with Flow abstraction
- Type-specific rendering via multimethods

---

## File Structure (Current)

```
src/cljc/parlameme/
├── flow/
│   ├── state.cljc           # Foundation: entities, resources, groups
│   ├── effects.cljc         # Foundation: effect multimethod
│   ├── expr.cljc            # Expression evaluation
│   ├── events.cljc          # Event sourcing (in-memory)
│   ├── runtime.cljc         # Flow execution engine
│   ├── schema.cljc          # Malli schemas for Flow
│   └── action.clj           # Unified action API (multimethod)
├── v3/
│   ├── dsl.cljc             # Game DSL (extends flow/dsl)
│   ├── compiler/core.cljc   # Game compiler
│   ├── runtime/core.cljc    # Game runtime (wraps flow/runtime)
│   └── games/               # Game definitions
├── tournament/
│   ├── dsl.cljc             # Tournament DSL (extends flow/dsl)
│   ├── compiler.cljc        # Tournament compiler
│   ├── runtime.cljc         # Tournament runtime
│   └── effects.cljc         # Tournament effects
├── archive.cljc             # Deterministic replay
├── rng.cljc                 # Deterministic RNG
└── persistence/
    └── schema.cljc          # Malli schemas for persistence
```

---

## Implementation Status

### Completed
- ✅ Extract Flow Core
- ✅ Unified State & Persistence (archive-first)
- ✅ Child Flow Support (tournament matches)
- ✅ Tournament DSL (round-robin, single-elimination)
- ✅ MCP tools for tournaments
- ✅ WebSocket subscriptions

### Deferred
- 🔻 Merkle anchoring to Base L2
- 🔻 Archive storage on Arweave/IPFS

---

## Related Documents

- `implementation_status.md` — Current implementation status
- `tournament_integration.md` — Integration specifics
- `../flow_v3_specification.md` — Flow v3 DSL reference
