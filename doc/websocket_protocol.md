# WebSocket Protocol v3

## Design Principles

1. **Single namespace**: All WebSocket messages use `:v3/*`
2. **Symmetric messages**: Same event structure for all recipients
3. **Explicit types**: Server always sends `:execution-type`, client never infers
4. **Consistent field names**: `:deal-id` everywhere (not `:deal` or `:id`)
5. **Schema-validated**: All messages have Malli schemas

## Message Flow

```
┌─────────────────────────────────────────────────────────────┐
│                      CLIENT                                  │
├─────────────────────────────────────────────────────────────┤
│  UI Components                                               │
│       │                                                      │
│       ▼ dispatch                                             │
│  re-frame events (:game/*)                                   │
│       │                                                      │
│       ▼ effect                                               │
│  sente.cljs API  ────────── WebSocket ──────────►           │
│                              :v3/*                           │
│       ◄────────── WebSocket ──────────                       │
│                    :v3/*                                     │
│       │                                                      │
│       ▼ dispatch                                             │
│  re-frame events (:game/*)                                   │
│       │                                                      │
│       ▼ update                                               │
│  app-db [:game]                                              │
└─────────────────────────────────────────────────────────────┘
                              │
                              │ WebSocket
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                      SERVER                                  │
├─────────────────────────────────────────────────────────────┤
│  sente.clj handlers (:v3/*)                                  │
│       │                                                      │
│       ▼                                                      │
│  sessions.clj (state management)                             │
│       │                                                      │
│       ▼                                                      │
│  runtime/core.cljc (game logic)                              │
└─────────────────────────────────────────────────────────────┘
```

## Client → Server Messages

### Session Management

```clojure
;; Join game
[:v3/join {:game-id     keyword?
           :player-name string?
           :game-type   keyword?}]

;; Start game (host only)
[:v3/start {}]

;; Add bots
[:v3/add-bots {:count pos-int?}]  ; optional

;; Leave game
[:v3/leave {}]
```

### Game Actions

```clojure
;; Start a bilateral deal (single responder)
[:v3/start-deal {:deal-id   keyword?
                 :responder keyword?  ; single responder for bilateral deals
                 :params    map?}]

;; Start a multilateral deal (multiple responders)
[:v3/start-deal {:deal-id    keyword?
                 :responders vector?  ; vector of responder IDs for multilateral
                 :params     map?}]

;; Respond to pending deal (bilateral or multilateral)
[:v3/respond-deal {:instance-id string?
                   :response    keyword?}]  ; :accept, :reject, etc.

;; Make commitment
[:v3/make-commitment {:commitment-id keyword?
                      :params        map?}]

;; Advance phase (manual transition)
[:v3/advance-phase {}]
```

### Voting

```clojure
;; Start vote (with optional params for outcome effect resolution)
[:v3/start-vote {:vote-id keyword?
                 :subject any?
                 :params  map?}]    ; optional — merged into outcome context

;; Cast vote
[:v3/cast-vote {:instance-id string?
                :option      keyword?}]
```

### State Requests

```clojure
;; Request full state refresh
[:v3/get-state {}]

;; Request available games
[:v3/get-games {}]
```

### Communication Channels

Players can communicate through in-game channels defined in the game DSL.
Channel availability depends on game phase and player group membership.

```clojure
;; Send message to channel
[:v3/send-message {:channel-id keyword?
                   :content    string?}]

;; Request messages from channel
[:v3/get-messages {:channel-id keyword?
                   :limit      int?      ; optional, default 100
                   :since      string?}] ; optional, timestamp

;; Request available channels
[:v3/list-channels {}]
```

## Server → Client Messages

### State Updates

```clojure
;; Full state update (sent after any action)
[:v3/state {:phase           keyword?
            :round           int?
            :entities        map?
            :available-deals vector?
            :pending-deals   vector?
            :history         vector?
            :game-definition map?}]  ; only on first sync

;; available-deals item:
{:deal-id        keyword?
 :execution-type #{:immediate :bilateral :multilateral}
 :label          string?
 :params         map?}

;; pending-deals item (bilateral):
{:instance-id  string?
 :deal-id      keyword?
 :proposer     keyword?
 :responder    keyword?
 :params       map?
 :awaiting     keyword?}  ; who must respond

;; pending-deals item (multilateral):
{:instance-id     string?
 :deal-id         keyword?
 :proposer        keyword?
 :responders      vector?           ; all responders
 :responses       map?              ; {responder-id -> :pending/:accept/:reject}
 :completion-rule keyword?          ; :all | :majority | :threshold | :any-reject
 :threshold       int?              ; for :threshold rule
 :multilateral?   true}
```

### Deal Events

```clojure
;; Bilateral deal started (to proposer and responder)
[:v3/deal-started {:instance-id string?
                   :deal-id     keyword?
                   :proposer    keyword?
                   :responder   keyword?
                   :your-role   #{:proposer :responder :observer}}]

;; Deal resolved (broadcast to session - works for both bilateral and multilateral)
[:v3/deal-resolved {:instance-id string?
                    :deal-id     keyword?
                    :outcome     keyword?
                    :effects     vector?}]
```

### Multilateral Deal Events

Multilateral deals involve multiple responders who must accept/reject based on a completion rule.

```clojure
;; Multilateral deal started (to proposer and all responders)
[:v3/multilateral-deal-started {:instance-id     string?
                                :deal-id         keyword?
                                :proposer        keyword?
                                :responders      vector?    ; all responder IDs
                                :completion-rule keyword?   ; :all | :majority | :threshold | :any-reject
                                :threshold       int?       ; only for :threshold rule
                                :params          map?
                                :your-role       #{:proposer :responder :observer}}]

;; Responder responded (broadcast to all involved parties)
;; Sent each time a responder accepts/rejects, until deal resolves
[:v3/multilateral-response {:instance-id string?
                            :deal-id     keyword?
                            :responder   keyword?   ; who just responded
                            :response    keyword?   ; :accept or :reject
                            :responses   map?}]     ; updated {responder-id -> status}

;; Completion rules:
;; - :all        - All responders must accept. Any rejection fails immediately.
;; - :any-reject - Same as :all (first rejection fails the deal)
;; - :threshold  - Need at least N accepts (uses :threshold field)
;; - :majority   - More than 50% must accept
```

### Vote Events

```clojure
;; Vote started
[:v3/vote-started {:instance-id string?
                   :vote-id     keyword?
                   :proposer    keyword?
                   :options     vector?}]

;; Vote resolved
[:v3/vote-resolved {:instance-id string?
                    :vote-id     keyword?
                    :result      keyword?
                    :tally       map?}]
```

### Game Events

```clojure
;; Player joined/left
[:v3/event {:type       #{:player-joined :player-left}
            :player-id  keyword?
            :player-name string?}]

;; Phase changed
[:v3/event {:type      :phase-changed
            :old-phase keyword?
            :new-phase keyword?}]

;; Game over
[:v3/win {:winner     keyword?  ; or vector for team/multiple
          :condition  keyword?
          :message    string?
          :scores     map?}]
```

### Errors

```clojure
[:v3/error {:code    keyword?
            :message string?
            :details map?}]
```

### Channel Events

```clojure
;; Message sent confirmation (to sender)
[:v3/message-sent {:message-id string?
                   :channel-id keyword?
                   :timestamp  string?}]

;; Messages list response
[:v3/channel-messages {:channel-id keyword?
                       :messages   vector?}]

;; message item:
{:message-id   string?
 :channel-id   keyword?
 :sender       keyword?       ; nil for system messages
 :content      string?
 :timestamp    string?
 :message-type #{:player :system :narration}}

;; Available channels list
[:v3/channels-list {:channels vector?}]

;; channel item:
{:channel-id keyword?
 :type       #{:public :group :private :broadcast}
 :name       string?
 :can-read   boolean?
 :can-write  boolean?}

;; New message push (to all readers)
[:v3/new-message {:channel-id keyword?
                  :message    map?}]  ; same structure as message item
```

## Re-frame Event Naming Convention

Client uses `:game/*` and `:channel/*` namespaces for re-frame events:

| Category | Events |
|----------|--------|
| Session | `:game/join`, `:game/leave`, `:game/start` |
| Deals | `:game/propose-deal`, `:game/respond-deal` |
| Votes | `:game/start-vote`, `:game/cast-vote` |
| Phase | `:game/advance-phase` |
| State | `:game/state-update`, `:game/error` |
| UI | `:game/select-target`, `:game/set-param` |
| Channels | `:channel/send-message`, `:channel/get-messages`, `:channel/list-channels` |
| Channel Events | `:channel/message-sent`, `:channel/messages-received`, `:channel/channels-list`, `:channel/new-message` |

**No `:v3/*` or `:session/*` re-frame events.** These are WebSocket protocol only.

## Field Naming Standards

| Field | Standard Name | Never Use |
|-------|---------------|-----------|
| Deal identifier | `:deal-id` | `:deal`, `:id`, `:flow-id` |
| Vote identifier | `:vote-id` | `:vote`, `:id` |
| Deal instance | `:instance-id` | `:deal-instance-id` |
| Player reference | `:player-id` | `:player`, `:entity-id` |
| Execution mode | `:execution-type` | `:type`, `:mode` |
| Channel identifier | `:channel-id` | `:channel`, `:id` |
| Message identifier | `:message-id` | `:message`, `:id` |
