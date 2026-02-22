# Unified Flow Architecture

## Принцип

**Log IS Archive.** Один источник правды, разные стадии жизненного цикла.

```
┌─────────────────────────────────────────────────────────────┐
│                     Flow Lifecycle                          │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│   CREATE              ACTIVE                 COMPLETE       │
│      │                   │                      │           │
│      ▼                   ▼                      ▼           │
│  ┌────────┐         ┌────────┐            ┌─────────┐       │
│  │ :meta  │────────►│:events │───────────►│:complete│       │
│  └────────┘         │:messages             └────┬────┘       │
│                     └────────┘                  │           │
│                          │                      │           │
│                    active/{id}.transit    complete/{id}.transit
│                          │                      │           │
│                          │                      ▼           │
│                          │               ┌───────────┐      │
│                          │               │   Index   │      │
│                          │               │  (query)  │      │
│                          └──────────────►└───────────┘      │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## Формат: Transit

Transit сохраняет Clojure типы (keywords, sets, namespaced keys) и уже используется в проекте для API.

```clojure
;; Entry structure (append-only, hash-chained)
{:v 3                           ; schema version
 :seq 0                         ; sequence number
 :type :meta|:event|:message|:complete
 :ts 1737718523456              ; timestamp ms
 :data {...}                    ; type-specific payload
 :prev "0000...64chars"         ; previous entry hash (SHA-256)
 :hash "abcd...64chars"}        ; hash of this entry
```

## Файловая структура

```
data/flows/
├── active/                     ; Незавершённые flows
│   ├── {flow-id}.transit
│   └── ...
│
├── complete/                   ; Завершённые flows (= archives)
│   ├── {flow-id}.transit
│   └── ...
│
└── index.transit               ; Индекс для быстрых queries
```

## Entry Types

### :meta — Начало flow

```clojure
;; Game
{:flow-type :game
 :game-type :mafia
 :game-version "abc123"         ; compiled/source-hash
 :players [:alice :bob :carol]
 :seed 12345
 :space-id :public
 :entry-fee 0
 :created-at 1737718523456}

;; Tournament
{:flow-type :tournament
 :tournament-type :round-robin
 :game-type :mafia
 :host :alice
 :space-id :public
 :config {:name "Cup 2024" :max-participants 8}
 :entry-fee 0
 :created-at 1737718523456}
```

### :event — Действия

```clojure
;; Game events
{:event/type :deal-started
 :deal-id :bribe
 :instance-id "deal-0"
 :proposer :alice
 :responder :bob
 :params {:amount 25}}

{:event/type :deal-response
 :instance-id "deal-0"
 :responder :bob
 :response :accept}

{:event/type :vote-started ...}
{:event/type :vote-cast ...}
{:event/type :phase-changed ...}

;; Tournament events
{:event/type :participant-registered
 :participant :bob}

{:event/type :tournament-started
 :participants [:alice :bob :carol]
 :matches [...]}

{:event/type :match-result
 :match-id :round-robin-match-0
 :winner :alice
 :scores {:alice 3 :bob 1}}
```

### :message — Сообщения (для replay контекста)

```clojure
{:channel-id :lobby
 :sender :alice
 :content "Let's make a deal"
 :round 1
 :phase :floor}
```

### :complete — Завершение

```clojure
;; Game
{:winner :alice
 :condition :elimination
 :final-standings [...]}

;; Tournament
{:winner :alice
 :final-standings [{:participant :alice :points 6 :wins 2}
                   {:participant :bob :points 3 :wins 1}]}
```

## API Design

### Namespace: `parlameme.flow.log`

Core append-only operations:

```clojure
;; === Lifecycle ===
(create-flow! flow-id metadata)     ; → entry (creates active/{id}.transit)
(append-event! flow-id event)       ; → entry
(append-message! flow-id message)   ; → entry
(finalize-flow! flow-id outcome)    ; → {:entry :merkle-root :archive-id}
                                    ;   (moves to complete/, updates index)

;; === Reading ===
(read-flow flow-id)                 ; → [entries...] (from active/ or complete/)
(flow-exists? flow-id)              ; → boolean
(flow-active? flow-id)              ; → boolean
(flow-complete? flow-id)            ; → boolean

;; === Metadata ===
(get-metadata flow-id)              ; → metadata map
(get-flow-type flow-id)             ; → :game | :tournament

;; === Events ===
(get-events flow-id)                ; → [event-data...]
(get-messages flow-id)              ; → [message-data...]

;; === Integrity ===
(verify-flow flow-id)               ; → {:valid? bool :merkle-root hash}

;; === Recovery ===
(list-incomplete-flows)             ; → [flow-ids...]
(partition-by-staleness)            ; → {:fresh [...] :stale [...]}

;; === Cleanup ===
(delete-flow! flow-id)              ; removes active log
(close-flow! flow-id)               ; closes writer without finalizing
```

### Namespace: `parlameme.flow.archive`

Query & replay operations (работает с complete/ flows):

```clojure
;; === Index Queries ===
(list-archives opts)                ; → [{:id :flow-type :winner :ts ...}]
                                    ;   opts: :flow-type :game-type :player :limit :offset

(get-archive flow-id)               ; → full archive data (reads complete/{id}.transit)
(get-archive-meta flow-id)          ; → index entry (fast, no file read)
(archive-exists? flow-id)           ; → boolean

;; === Derived Views ===
(archive-summary flow-id)           ; → {:player-count :event-count :duration ...}

;; === Player Queries ===
(player-archives player-id opts)    ; → archives where player participated
(player-stats player-id)            ; → {:games-played :wins :tournaments ...}

;; === Statistics ===
(stats)                             ; → {:total :games :tournaments :by-type ...}

;; === Index Management ===
(rebuild-index!)                    ; rebuild from complete/ files
(init!)                             ; load index into memory
```

### Namespace: `parlameme.flow.replay`

Deterministic replay (для verification, debugging, AI training):

```clojure
(replay flow-id compiled-game)      ; → runtime at final state
(replay flow-id compiled-game       ; → runtime at entry N
        {:stop-at N})

(replay-state flow-id compiled-game); → state only (no runtime)

(verify-replay flow-id              ; → {:valid? bool :details ...}
               compiled-game
               expected-state)

;; Timeline navigation
(get-timeline flow-id)              ; → [{:seq :ts :kind :event/:message :data}...]
(replay-to-timestamp flow-id ts)    ; → runtime at timestamp
```

### Namespace: `parlameme.flow.recovery`

Crash recovery (работает с active/ flows):

```clojure
(recover-game flow-id compiled-game)    ; → {:ok? true :runtime :events-applied}
(recover-tournament flow-id)            ; → {:ok? true :tournament-state :events-applied}
(recover-flow flow-id opts)             ; → dispatches by flow-type

(recover-all! opts)                     ; → {:recovered N :failed M :stale K}
                                        ;   opts: :compiled-games {type -> compiled}
```

## Transitions

### finalize-flow!

```clojure
(defn finalize-flow!
  "Complete flow: append :complete entry, move to complete/, update index."
  [flow-id outcome]
  ;; 1. Append :complete entry with hash chain
  (let [complete-entry (append-entry! flow-id :complete outcome)
        merkle-root (:hash complete-entry)]

    ;; 2. Close writer
    (close-writer! flow-id)

    ;; 3. Move file: active/{id}.transit → complete/{id}.transit
    (move-to-complete! flow-id)

    ;; 4. Update index (atomic)
    (let [metadata (get-metadata flow-id)
          index-entry (build-index-entry flow-id metadata outcome)]
      (add-to-index! index-entry))

    ;; 5. Return result
    {:entry complete-entry
     :merkle-root merkle-root
     :archive-id flow-id}))
```

## Integration Points

### v3/sessions.clj

```clojure
;; On create-session!
(flow-log/create-flow! session-id
                       {:flow-type :game
                        :game-type game-type
                        :players players
                        :seed seed
                        ...})

;; On deal/vote/action
(flow-log/append-event! session-id
                        {:event/type :deal-started ...})

;; On message
(flow-log/append-message! session-id message)

;; On complete-game!
(flow-log/finalize-flow! session-id {:winner winner ...})
;; No separate archive-store call needed!
```

### tournament/sessions.clj

```clojure
;; On create-tournament!
(flow-log/create-flow! tournament-id
                       {:flow-type :tournament
                        :tournament-type type
                        ...})

;; On register/unregister/start/result
(flow-log/append-event! tournament-id event)

;; On complete
(flow-log/finalize-flow! tournament-id outcome)
```

### api/history.clj

```clojure
;; list-flows, get-flow, etc. now use:
(require '[parlameme.flow.archive :as archive])

(defn list-flows [opts]
  (ok {:flows (archive/list-archives opts)}))

(defn get-flow [flow-id]
  (if-let [arch (archive/get-archive flow-id)]
    (ok {:archive arch})
    (errors/fail :archive-not-found ...)))
```

## Removed Modules

After migration:
- `parlameme.archive` → merged into `parlameme.flow.replay`
- `parlameme.archive.store` → replaced by `parlameme.flow.archive`
- `parlameme.session.log` → replaced by `parlameme.flow.log`

## Migration Path

1. **Create new modules:**
   - `flow/log.cljc` (Transit-based, replaces session/log)
   - `flow/archive.clj` (index + queries)
   - `flow/replay.cljc` (deterministic replay)

2. **Update sessions:**
   - `v3/sessions.clj` → use flow/log
   - `tournament/sessions.clj` → use flow/log

3. **Update API:**
   - `api/history.clj` → use flow/archive

4. **Remove old modules:**
   - `session/log.clj`
   - `archive.cljc`
   - `archive/store.clj`

5. **Migrate data:**
   - Existing `data/archives/*.edn` → `data/flows/complete/*.transit`
   - Existing `data/sessions/logs/*.jsonl` → `data/flows/active/*.transit`

## Benefits

1. **Single source of truth** — no data duplication
2. **Type preservation** — Transit keeps Clojure types
3. **Simpler mental model** — Log IS Archive
4. **Extensible** — easy to add blockchain anchoring later
5. **Idiomatic Clojure** — namespaced keywords, immutable data
