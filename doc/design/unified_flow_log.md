# Unified Flow Log Design

## Overview

Single persistence layer for both games and tournaments, based on the existing session log with hash chain.

## Current State

```
parlameme.session.log (games only)
├── Entry types: :meta, :decision, :message, :complete
├── Directory: data/sessions/logs/
├── Recovery: recover-runtime replays decisions
└── Used by: v3/sessions.clj
```

## Proposed Design

### Unified Flow Log

```
parlameme.flow.log (games + tournaments)
├── Entry types:
│   ├── :meta          - flow metadata (game or tournament)
│   ├── :event         - any flow event (replaces :decision)
│   ├── :message       - chat messages (games only)
│   └── :complete      - flow completed
├── Directory: data/flows/logs/
├── Flow types in metadata:
│   ├── :flow/type :game
│   └── :flow/type :tournament
└── Recovery: type-specific replay
```

### Entry Format (unchanged structure)

```clojure
{:v 3                           ; bump version for unified format
 :seq 0                         ; sequence number
 :type :meta|:event|:message|:complete
 :ts 1737718523456              ; timestamp ms
 :data {...}                    ; type-specific payload
 :prev "0000...64chars"         ; previous entry hash
 :hash "abcd...64chars"}        ; SHA-256 of canonical entry
```

### Metadata Entry (:meta)

```clojure
;; Game
{:flow/type :game
 :flow/id "session-123"
 :game-type :mafia
 :game-version "abc123"
 :players [:alice :bob :charlie]
 :seed 12345
 :escrow-enabled? true
 :entry-fee 100
 :started-at 1737718523456}

;; Tournament
{:flow/type :tournament
 :flow/id "cup-2024"
 :tournament-type :round-robin
 :space-id :public
 :host :alice
 :game-type :mafia
 :config {:name "Cup 2024" :max-participants 8}
 :entry-fee 0
 :created-at 1737718523456}
```

### Event Entry (:event)

```clojure
;; Game events (existing)
{:event/type :deal
 :deal-id :bribe
 :proposer :alice
 :responder :bob
 :params {:amount 25}}

{:event/type :response
 :deal-instance-id "deal-0"
 :responder :bob
 :response :accept}

{:event/type :vote-start ...}
{:event/type :vote-cast ...}

;; Tournament events (new)
{:event/type :participant-registered
 :participant :bob}

{:event/type :participant-unregistered
 :participant :bob}

{:event/type :tournament-started
 :matches [...]}

{:event/type :match-result
 :match-id :round-robin-match-0
 :reporter :alice
 :scores {:alice 3 :bob 1}}
```

### Complete Entry (:complete)

```clojure
;; Game
{:winner :alice
 :condition :elimination}

;; Tournament
{:winner :alice
 :standings [{:participant :alice :wins 3 :losses 0}
             {:participant :bob :wins 2 :losses 1}
             ...]}
```

## Implementation Plan

### Phase 1: Refactor session/log.clj → flow/log.cljc

1. **Move file**: `src/clj/parlameme/session/log.clj` → `src/cljc/parlameme/flow/log.cljc`
2. **Generalize terminology**: session → flow
3. **Add `:flow/type`** to metadata
4. **Bump schema version** to 3
5. **Keep backward compatibility**: existing logs still readable

Key changes:
```clojure
;; Old
(create-log! session-id metadata)
(append-decision! session-id decision)

;; New
(create-log! flow-id {:flow/type :game ...})
(append-event! flow-id {:event/type :deal ...})
```

### Phase 2: Update v3/sessions.clj

1. Change require from `session.log` to `flow.log`
2. Update calls to use new API
3. Add `:flow/type :game` to metadata

### Phase 3: Integrate tournament/sessions.clj

1. Add `flow.log` require
2. Create log on `create-tournament!`
3. Append events on state changes
4. Finalize log on tournament completion
5. Add `recover-tournament` function

### Phase 4: Unified Recovery

```clojure
(defn recover-all!
  "Recover all incomplete flows on startup."
  []
  (let [{:keys [fresh stale]} (partition-by-staleness)]
    ;; Recover fresh flows
    (doseq [flow-id fresh]
      (let [metadata (get-log-metadata flow-id)]
        (case (:flow/type metadata)
          :game (recover-game! flow-id)
          :tournament (recover-tournament! flow-id))))
    ;; Cleanup stale flows
    (cleanup-stale! stale)))
```

## Directory Structure

```
data/flows/logs/
  {flow-id}.jsonl       ; active flow log
  {flow-id}.complete    ; completion marker

;; Examples:
  game-abc123.jsonl
  tournament-cup2024.jsonl
```

## Migration

1. **New installs**: use `data/flows/logs/`
2. **Existing installs**: 
   - Check for `data/sessions/logs/` on startup
   - If exists, migrate to `data/flows/logs/`
   - Or just support both directories during transition

## Benefits

1. **Single codebase** for persistence
2. **Consistent hash chain** for all flows
3. **Unified recovery** on startup
4. **Blockchain-ready** for both games and tournaments
5. **Simpler mental model**

## API Summary

```clojure
;; Core operations
(create-log! flow-id metadata)      ; Start new flow log
(append-event! flow-id event)       ; Record event
(append-message! flow-id message)   ; Record message (games only)
(finalize-log! flow-id outcome)     ; Complete flow

;; Reading
(read-log flow-id)                  ; All entries
(get-log-metadata flow-id)          ; First entry data
(get-log-events flow-id)            ; All events
(verify-log flow-id)                ; Hash chain verification

;; Recovery
(list-incomplete-flows)             ; Flows needing recovery
(partition-by-staleness)            ; {:fresh [...] :stale [...]}
(recover-flow flow-id)              ; Type-dispatched recovery

;; Lifecycle
(init!)                             ; Initialize, return recovery info
(shutdown!)                         ; Close all active logs
```
