# Flow v3 DSL Specification

Complete specification of the Flow v3 Domain-Specific Language for defining social games with economic mechanics.

## Table of Contents

1. [Overview](#overview)
2. [Core Concepts](#core-concepts)
3. [DSL Constructs](#dsl-constructs)
4. [Expression Language](#expression-language)
5. [Effects System](#effects-system)
6. [Compilation Pipeline](#compilation-pipeline)
7. [Runtime Model](#runtime-model)
8. [Schemas](#schemas)
9. [Examples](#examples)

---

## Overview

Flow v3 is a declarative DSL for defining turn-based social games with:

- **Resources** — transferable quantities (money, influence, reputation)
- **Attributes** — non-transferable properties (role, team, status)
- **Deals** — bilateral or unilateral interactions between players
- **Votes** — collective decision-making mechanisms
- **Commitments** — binding promises with trigger conditions
- **Phases** — sequential game stages with allowed actions
- **Victory** — terminal conditions and scoring
- **Channels** — communication channels between players

### Design Principles

1. **Data-first** — Games are pure data structures, not code
2. **Declarative** — Describe *what*, not *how*
3. **Composable** — Build complex games from simple primitives
4. **Safe** — Whitelisted expressions, validated schemas
5. **Extensible** — New mechanics via composition, not modification

### Architecture

```
DSL Definition → Compilation (10 phases) → Compiled Game → Runtime Execution
```

---

## Core Concepts

### Entity Model

```
┌─────────────────────────────────────────────────────────┐
│                        GAME                             │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐     │
│  │   ENTITY    │  │   ENTITY    │  │   ENTITY    │     │
│  │   (player)  │  │   (player)  │  │   (player)  │     │
│  ├─────────────┤  ├─────────────┤  ├─────────────┤     │
│  │ resources:  │  │ resources:  │  │ resources:  │     │
│  │  - wealth   │  │  - wealth   │  │  - wealth   │     │
│  │  - influence│  │  - influence│  │  - influence│     │
│  │ attrs:      │  │ attrs:      │  │ attrs:      │     │
│  │  - role     │  │  - role     │  │  - role     │     │
│  │  - team     │  │  - team     │  │  - team     │     │
│  │ groups: #{} │  │ groups: #{} │  │ groups: #{} │     │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘     │
│         │                │                │             │
│         └────── RELATIONS ───────────────┘             │
│                  (allied, enemy, bribed)               │
│                                                         │
│  ┌─────────────────────────────────────────────────┐   │
│  │                    GROUPS                        │   │
│  │  coalition-1: {members: #{alice bob}}           │   │
│  │  faction-a:   {members: #{charlie}}             │   │
│  └─────────────────────────────────────────────────┘   │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### Visibility Model

| Level | Description | Example |
|-------|-------------|---------|
| `:public` | Visible to all players | Public resources, vote results |
| `:private` | Visible only to owner | Hand cards, secret role |
| `:hidden` | Visible to no one (system only) | Internal counters |

### Phase Model

Games progress through phases with optional branching:

```
setup → phase-1 → phase-2 → ... → phase-n → [victory check] → next round
           │                                        ↓
           └──(transition)──► phase-X          [game end]
```

Each phase:
- Has allowed actions (deals, votes)
- May have automatic effects on entry
- May be skipped via `:when` condition
- May run in parallel or sequential mode
- May branch via `:transitions` (conditional) or `:next` (unconditional)
- May mark round boundaries via `:starts-round?`

---

## DSL Constructs

### game

Creates the root game structure.

```clojure
(game id name opts)

;; Example
(game :mafia "Mafia"
  {:players {:min 6 :max 12}
   :description "Classic social deduction"
   :icon "🔫"})
```

**Options:**
| Key | Type | Description |
|-----|------|-------------|
| `:players` | `{:min N :max M}` | Player count constraints |
| `:description` | `string` | Game description |
| `:icon` | `string` | Emoji or icon |

---

### resource

Defines a transferable numeric quantity.

```clojure
(resource id opts)

;; Examples
(resource :wealth {:initial 100 :visibility :private :bounds [0 1000]})
(resource :influence {:initial 0 :visibility :public :transferable false})
(resource :score {:derived '(+ [:actor :wealth] [:actor :reputation])})
```

**Options:**
| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `:initial` | `number` | `0` | Starting value |
| `:visibility` | `:public\|:private\|:hidden` | `:public` | Who can see |
| `:bounds` | `[min max]` | `nil` | Value constraints |
| `:transferable` | `boolean` | `true` | Can be transferred |
| `:derived` | `expr` | `nil` | Computed from other values |
| `:ledger` | `{:sync ...}` | `nil` | Escrow integration |

---

### attr

Defines a non-transferable property.

```clojure
(attr id opts)

;; Examples
(attr :role {:visibility :private :values [:detective :civilian :mafia]})
(attr :team {:visibility :hidden :values [:town :mafia]})
(attr :protected {:visibility :hidden :initial false})
```

**Options:**
| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `:visibility` | `:public\|:private\|:hidden` | `:public` | Who can see |
| `:values` | `[...]` | `nil` | Enum of allowed values |
| `:initial` | `any` | `nil` | Starting value |
| `:revealable?` | `boolean` | `false` | Can be revealed |
| `:random-init?` | `boolean` | `false` | Random initial value |
| `:distribute?` | `boolean` | `false` | Distribute unique values |

---

### roles

Defines role distribution with teams.

```clojure
(roles role-configs)

;; Example
(roles {:civilian {:team :town :filler true}
        :detective {:team :town :unique true}
        :doctor {:team :town :unique true}
        :goon {:team :mafia :count 2}
        :godfather {:team :mafia :unique true :appears-as :town}})
```

**Role Options:**
| Key | Type | Description |
|-----|------|-------------|
| `:team` | `keyword` | Team membership |
| `:unique` | `boolean` | Only one player can have |
| `:count` | `number` | Exact count of this role |
| `:filler` | `boolean` | Fill remaining slots |
| `:appears-as` | `keyword` | Appears as different team to investigators |
| `:doc` | `string` | Human-readable description (used by AI guidance) |
| `:abilities` | `vector<string>` | List of ability descriptions (used by AI guidance) |
| `:strategy` | `vector<string>` | Strategy hints for this role (used by AI guidance) |

**AI Guidance Example:**
```clojure
(roles {:detective {:team :town
                    :doc "Town investigator who can reveal alignments."
                    :abilities ["Investigate one player per night"
                                "Learn if target appears INNOCENT or GUILTY"]
                    :strategy ["Investigate suspicious players first"
                               "Share findings carefully to avoid being killed"]}
        :civilian {:team :town :filler true
                   :doc "Regular townsperson with no special abilities."
                   :abilities ["Vote in day lynches"]
                   :strategy ["Observe player behavior carefully"
                              "Vote based on evidence and logic"]}})
```

---

### group-type

Defines a type of player group (coalition, faction, etc.).

```clojure
(group-type id opts)

;; Example
(group-type :coalition
  {:visible? false
   :exclusive? false
   :knows-members? true
   :shared-resources [:treasury]})
```

**Options:**
| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `:visible?` | `boolean` | `true` | Visible to non-members |
| `:exclusive?` | `boolean` | `false` | One group per player |
| `:knows-members?` | `boolean` | `true` | Members see each other |
| `:shared-resources` | `[keywords]` | `[]` | Group-level resources |

---

### deal

Defines an interaction between players. The core mechanic for player actions.

```clojure
(deal id opts)
```

#### Single-Party Deal (immediate execution)

```clojure
(deal :investigate
  {:parties {:actor {:filter '(= [:actor :role] :detective)}}
   :params {:target {:type :player :filter '(alive?)}}
   :guard '(not= :actor :target)
   :phase :night
   :limit {:per-round 1}
   :outcomes {:ok {:effects [[:reveal :target :team :actor]]}}})
```

#### Two-Party Deal (requires response)

```clojure
(deal :bribe
  {:parties {:proposer {:filter '(alive?)}
             :responder {:filter '(alive?)}}
   :params {:amount {:type :integer :min 1 :max '[:proposer :wealth]}}
   :stakes {:proposer [[:wealth :amount]]}
   :guard '(not= :proposer :responder)
   :phase :floor
   :response {:timeout 60
              :options [:accept :reject :expose]}
   :outcomes {:accept {:effects [[:transfer-stakes :responder]
                                 [:relate :proposer :responder :bribed]]}
              :reject {:effects [[:return-stakes]]}
              :expose {:effects [[:return-stakes]
                                 [:damage :proposer :reputation 10]]}}})
```

**Options:**
| Key | Type | Description |
|-----|------|-------------|
| `:parties` | `{role {:filter expr}}` | Who can participate |
| `:params` | `{name {:type ...}}` | Parameters with validation |
| `:stakes` | `{role [[resource amount]]}` | Resources locked on start |
| `:guard` | `expr` | Condition checked on deal start and in available-deals query |
| `:phase` | `keyword \| [keywords]` | When available |
| `:limit` | `{:per-round N ...}` | Usage limits |
| `:response` | `{:timeout N :options [...]}` | For two-party deals |
| `:option-guards` | `{option expr}` | Per-option guard restricting who can choose each outcome |
| `:outcomes` | `{outcome {:effects [...]}}` | Effects per outcome |

**Party Roles:**
- Single-party: `:actor`
- Two-party: `:proposer`, `:responder`
- Multi-party: `:proposer`, `:responders` (with `:count` constraint)
- Custom party names are fully supported (e.g., `:leader`/`:partners`)

#### Custom Party Names

Deals can use any party names. The engine classifies parties by structural features, not by name:

```clojure
;; Standard bilateral — classified by :proposer + :responder keys
(deal :bribe
  {:parties {:proposer {:filter '(alive?)}
             :responder {:filter '(alive?)}}
   ...})

;; Custom bilateral — classified by :excludes (dependency between parties)
(deal :accuse
  {:parties {:accuser {:filter '(alive?)}
             :seconded-by {:filter '(alive?) :excludes [:accuser]}}
   ...})

;; Custom multilateral — classified by :count (multi-responder)
(deal :propose-team
  {:parties {:leader {:filter '(= :actor.role :leader)}
             :partners {:filter '(alive?) :count [2 3]
                        :excludes [:leader]}}
   ...})
```

**Classification rules:**
- Party with `:count` → multilateral responders
- Party with `:excludes` → dependent on initiator (i.e., respondent)
- Remaining party → initiator (proposer)
- Only `:actor` with no other parties → immediate (single-party)

MCP automatically maps custom party names to canonical parameters (`responder`, `responders`, `target`) for AI agents.

---

### vote

Defines a collective decision mechanism.

```clojure
(vote id opts)

;; Example
(vote :lynch
  {:proposer {:filter '(alive?)}
   :subject {:type :player :filter '(alive?)}
   :voters {:filter '(alive?)}
   :options [:guilty :innocent]
   :weights :equal
   :threshold :majority
   :visibility :public
   :phase :trial
   :outcomes {:guilty {:effects [[:eliminate :subject]]}
              :innocent {:effects []}}})
```

**Options:**
| Key | Type | Description |
|-----|------|-------------|
| `:proposer` | `{:filter expr}` | Who can start vote |
| `:subject` | `{:type :filter}` | What/who is voted on |
| `:voters` | `{:filter expr}` | Who can vote |
| `:options` | `[keywords]` or `[:from expr]` | Vote choices |
| `:weights` | `:equal` or `[:by resource]` | Vote weight |
| `:threshold` | `:majority\|:supermajority\|:unanimous\|:plurality\|{:percent N}` | Win condition |
| `:visibility` | `:public\|:private\|:commit-reveal` | Vote visibility |
| `:phase` | `keyword` | When available |
| `:outcomes` | `{outcome {:effects [...]}}` | Effects per result |

#### Vote Parameters

Votes can carry parameters that are available in outcome effect resolution:

```clojure
(vote :boost
  {:proposer {:filter '(alive?)}
   :subject {:type :player :filter '(alive?)}
   :voters {:filter '(alive?)}
   :options [:approve :deny]
   :threshold :majority
   :phase :council
   ;; Parameters passed when vote is started
   ;; Available in outcome effects via :params context
   :outcomes {:approve {:effects [[:boost :subject :resource :amount]]}
              :deny {:effects []}}})
```

When starting a vote via runtime:
```clojure
(runtime/start-vote rt :boost
  {:proposer :alice
   :subject :bob
   :params {:resource :influence :amount 10}})
```

The `:params` map is stored with the vote instance and merged into the execution context when the vote completes. This allows outcome effects to reference vote parameters (e.g., `:amount`, `:resource`) just like deal parameters.

**Commitment Auto-Triggering:** Commitments with `:triggers [:on-death]` are automatically
executed when the committed entity is eliminated via `:eliminate` effect. This enables
mechanics like Hunter's revenge and Lover's heartbreak without manual phase effects.

---

### commitment

Defines a binding promise with triggers.

```clojure
(commitment id opts)

;; Example
(commitment :dead-hand
  {:actor {:filter '(alive?)}
   :stake [[:wealth 50]]
   :triggers [:eliminated]
   :params {:target {:type :player}}
   :effect [[:damage :target :reputation 30]]
   :cancellable? false})
```

**Options:**
| Key | Type | Description |
|-----|------|-------------|
| `:actor` | `{:filter expr}` | Who can make commitment |
| `:stake` | `[[resource amount]]` | Locked resources |
| `:triggers` | `[event-types]` | When commitment fires |
| `:params` | `{...}` | Commitment parameters |
| `:effect` | `[effects]` | What happens on trigger |
| `:cancellable?` | `boolean` | Can be cancelled |

---

### phase

Defines a game phase.

```clojure
(phase id opts)

;; Example
(phase :night
  {:when '(> (count-alive) 2)
   :category :action
   :duration {:seconds 120}
   :parallel? true
   :allows [:mafia-kill :doctor-protect :investigate]
   :effects [[:broadcast "Night falls..."]]})
```

**Options:**
| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `:when` | `expr` | `true` | Skip if false |
| `:category` | `keyword` | `:action` | Phase type |
| `:duration` | `{:seconds N}` | `nil` | Time limit |
| `:parallel?` | `boolean` | `false` | Simultaneous actions |
| `:automatic?` | `boolean` | `false` | System-controlled |
| `:once?` | `boolean` | `false` | Execute only on first occurrence (skipped on subsequent rounds) |
| `:allows` | `[deal-ids]` | `[]` | Available deals |
| `:effects` | `[effects]` | `[]` | On-entry effects |
| `:next` | `keyword` | `nil` | Unconditional jump target (phase id). If set, skips linear advancement. |
| `:transitions` | `[TransitionDef]` | `[]` | Conditional transitions — evaluated in order, first truthy guard wins |
| `:starts-round?` | `boolean` | `false` | Entering this phase increments round counter |

**Phase Transition Resolution (non-linear):**

When advancing from a phase, the engine resolves the next phase in this order:
1. **Conditional transitions**: evaluate `:transitions` guards in order — first truthy match jumps to target
2. **Unconditional next**: if no transition matched and `:next` is set — jump to target
3. **Linear fallback**: original sequential `(idx + 1) % n` scan

If the target phase should be skipped (`:when` returns false, `:once?` already fired), the engine falls through to linear advancement from that position.

**TransitionDef:**
```clojure
{:guard expr    ;; Condition evaluated at transition time
 :target keyword}  ;; Phase ID to jump to
```

**Round counting modes (auto-detected):**
- **Explicit**: any phase has `:starts-round? true` → round increments only when entering a `:starts-round?` phase
- **Legacy**: no phase uses `:starts-round?` → round increments when phase index wraps past start (original behavior)

**Example — branching phases:**
```clojure
;; Day leads to trial only if accusations exist, otherwise to night
(phase :day {:allows [:accuse :discuss]
             :transitions [{:guard '(> (count-where (= :actor.accused true)) 0)
                            :target :trial}]
             :next :night})  ;; fallback if no accusations

;; Trial always goes to night after voting
(phase :trial {:allows [:lynch]
               :next :night})

;; Night is the round boundary
(phase :night {:allows [:wolf-mark]
               :starts-round? true})
```

**Categories:**
- `:setup` — Game initialization
- `:action` — Player actions
- `:resolution` — Resolve pending actions
- `:transition` — Between main phases

---

### victory

Defines win conditions.

```clojure
(victory id opts)
```

#### Single Winner

```clojure
(victory :town-wins
  {:type :single
   :when '(= 0 (count-where (= [:actor :team] :mafia)))
   :for :town
   :message "Town has eliminated all mafia!"})
```

#### Distribution (Prize Pool)

```clojure
(victory :session-complete
  {:type :distribution
   :when '(>= :round 10)
   :score '(+ (* 0.30 [:actor :wealth])
              (* 0.25 [:actor :influence])
              (* 0.20 [:actor :reputation]))
   :message "Session complete. Calculating final scores..."})
```

**Options:**
| Key | Type | Description |
|-----|------|-------------|
| `:type` | `:single\|:distribution` | Victory type |
| `:when` | `expr` | Trigger condition |
| `:for` | `keyword` | Winning team (for :single) |
| `:score` | `expr` | Score calculation (for :distribution) |
| `:message` | `string` | Victory message |

---

### channel

Defines a communication channel for player interaction.

```clojure
(channel id opts)
```

#### Channel Types

| Type | Description |
|------|-------------|
| `:public` | All active players can read and write |
| `:group` | Only members of specified group (requires `:group` option) |
| `:private` | Dynamic channel between specific players (requires `:max-participants`) |
| `:broadcast` | All can read, only specified writers can write (requires `:writers`) |

#### Examples

```clojure
;; Public discussion for all living players
(channel :town-square
  {:type :public
   :description "Public discussion for all living players"})

;; Private mafia coordination
(channel :mafia-chat
  {:type :group
   :group :mafia-family
   :description "Private mafia coordination"})

;; Whisper channel with limits
(channel :whisper
  {:type :private
   :max-participants 2
   :limits {:max-messages-per-phase 3}})

;; System announcements
(channel :announcements
  {:type :broadcast
   :writers [:host :system]})
```

**Options:**
| Key | Type | Description |
|-----|------|-------------|
| `:type` | `:public\|:group\|:private\|:broadcast` | Channel type (required) |
| `:description` | `string` | Human-readable description |
| `:group` | `keyword` | Group-type id (for `:group` type) |
| `:max-participants` | `number` | Max players in private channel (for `:private` type) |
| `:writers` | `[keywords]` | Who can write (for `:broadcast` type) |
| `:limits` | `map` | Rate limiting options |

**Limits Options:**
| Key | Type | Description |
|-----|------|-------------|
| `:max-messages-per-phase` | `number` | Max messages per player per phase |
| `:max-message-length` | `number` | Max characters per message |
| `:cooldown-seconds` | `number` | Minimum seconds between messages |

---

### communication-limits

Sets global and per-channel communication limits.

```clojure
(communication-limits opts)

;; Example
(communication-limits
  {:global {:max-message-length 1000
            :max-messages-per-phase 20}
   :per-channel {:whisper {:max-messages-per-phase 3
                           :cooldown-seconds 10}}})
```

**Options:**
| Key | Type | Description |
|-----|------|-------------|
| `:global` | `map` | Global limits for all channels |
| `:per-channel` | `map` | Override limits for specific channels |

---

## Expression Language

Expressions are used in `:when`, `:guard`, `:filter`, `:score`, and effect parameters.

### Syntax

Expressions are quoted Clojure forms:

```clojure
'(operator arg1 arg2 ...)
```

### Operators (Whitelisted)

#### Comparison
```clojure
= != > >= < <=
```

#### Logic
```clojure
and or not if cond
```

#### Arithmetic
```clojure
+ - * /
```

#### Predicates

```clojure
(alive?)                      ; Current actor is alive
(alive? :target)              ; Specific entity is alive
(dead?)                       ; Current actor is dead
(dead? :entity)               ; Specific entity is dead
(in-group? :entity :group)    ; Entity in specific group
(in-group-type? :entity :type); Entity in group of type
(same-group? :a :b)           ; Two entities share a group
(has-relation? :from :to :type) ; Relation exists
(all-dead? :group)            ; All group members dead
(phase? :phase-id)            ; Current phase check
(contains? :actor.set :val)   ; Value is in set-typed attribute
(not-contains? :actor.set :v) ; Value is NOT in set-typed attribute
(every-in-group? :group pred) ; All group members satisfy predicate
(attr? :entity :attr-name)    ; Entity has non-nil attribute
(some? expr)                  ; Expression result is non-nil
```

#### Aggregations

```clojure
(count-alive)                 ; Count of living players
(count-in-group :group-id)    ; Count in specific group
(count-in-group-type :type)   ; Count in groups of type
(count-where expr)            ; Count matching condition
(alive-players)               ; Vector of living players
(sum-resource :resource)      ; Sum across all players
(avg-resource :resource)      ; Average
(max-resource :resource)      ; Maximum
(min-resource :resource)      ; Minimum
(resource-of :entity :res)    ; Specific entity's resource value
(group-resource :group :res)  ; Group's resource value
(find-by-attr :attr :value)   ; First entity with matching attr
(find-by-role :role)          ; First entity with matching role
(filter-by-attr :attr :value) ; All entities with matching attr
(filter-by-team :team)        ; All entities with matching team
(length :entity :attr)        ; Count of collection-valued attr
```

### Variable Resolution

1. **Keywords** resolve from context, then game vars, then actor properties:
   ```clojure
   :amount      ; → (:amount params) or (:amount context) or [:actor :amount]
   ```

2. **Dot notation** (PREFERRED) for property access:
   ```clojure
   :actor.wealth         ; Actor's wealth
   :proposer.team        ; Proposer's team
   :game.round           ; Game round
   :target.alive         ; Target's alive status
   :p.faction            ; Loop variable property
   :last-lynched.role    ; Game var → entity → property (var indirection)
   ```

3. **Property vectors** (legacy syntax, still supported):
   ```clojure
   [:actor :wealth]      ; Actor's wealth (equivalent to :actor.wealth)
   [:proposer :team]     ; Proposer's team (equivalent to :proposer.team)
   ```

4. **Expressions** are evaluated:
   ```clojure
   '(+ :actor.wealth 100)
   ```

### Dot Notation

Dot notation is the recommended way to access entity properties. It's more concise and readable:

```clojure
;; PREFERRED - dot notation
:filter '(and (alive?) (= :actor.role :detective))
:when '(>= :game.round 5)
:score ':actor.health
:guard '(> :target.funds 100)

;; LEGACY - vector syntax (still works)
:filter '(and (alive?) (= [:actor :role] :detective))
:when '(>= [:game :round] 5)
```

Dot notation works with any entity reference: `:actor`, `:target`, `:proposer`, `:responder`, `:p` (loop variable), `:game`, etc.

### Context Variables

Available in expressions based on situation:

| Variable | Available In | Description |
|----------|--------------|-------------|
| `:actor` | Single-party deals | Acting player |
| `:proposer` | Two-party deals | Deal initiator |
| `:responder` | Two-party deals | Deal recipient |
| `:target` | Deals with target param | Target of action |
| `:round` | Always | Current round number |
| `:phase` | Always | Current phase id |
| `:params` | Deals/votes | Action parameters |

### CRITICAL: No Nested Quotes

```clojure
;; WRONG — causes "Unknown operator: quote" error
:when '(= 0 (count-where '(= :team :mafia)))

;; CORRECT — use dot notation (PREFERRED)
:when '(= 0 (count-where (= :actor.team :mafia)))

;; CORRECT — use property vector syntax (legacy)
:when '(= 0 (count-where (= [:actor :team] :mafia)))
```

### Expression Tracing (Debugging)

For debugging complex expressions, use `evaluate-with-trace` in the REPL:

```clojure
(require '[parlameme.v3.runtime.expr :as expr])

;; Evaluate with full trace
(expr/evaluate-with-trace
  '(and (alive?) (>= :actor.funds 100))
  {:actor :alice}
  game-state
  compiled)

;; Returns:
{:result true
 :trace [{:expr '(alive?) :result true}
         {:expr :actor.funds :result 150}
         {:expr '(>= :actor.funds 100) :result true}
         {:expr '(and (alive?) (>= :actor.funds 100)) :result true}]}
```

This is useful for debugging why guards or filters don't match expected players.

---

## Effects System

Effects are vectors that transform game state:

```clojure
[:effect-type arg1 arg2 ...]
```

### Resource Effects

```clojure
[:transfer :from :to :resource :amount]     ; Move resource
[:boost :entity :resource :amount]          ; Increase
[:damage :entity :resource :amount]         ; Decrease
[:set-resource :entity :resource :value]    ; Set exactly
```

### Entity Effects

```clojure
[:eliminate :entity]                        ; Remove from game (triggers on-death commitments)
[:assign-role :entity :role]               ; Set role
[:set-attr :entity :attr :value]           ; Set attribute
[:set-add :entity :attr :value]            ; Add value to set-typed attribute
[:set-remove :entity :attr :value]         ; Remove value from set-typed attribute
[:reveal :entity :attr :to]                ; Reveal hidden attr (persistent)
[:reveal-as :entity :attr :fake-value :to] ; Show fake value to observer (deception)
```

**Persistent Reveals:** The `:reveal` effect now persistently records that the observer
can see the attribute. The `:reveal-as` effect records a fake value that the observer
sees instead of the real one (e.g., Godfather appearing as `:town` to detective).
Both are tracked in `:reveals` state map and respected by `get-entity-view`.

### Relation Effects

```clojure
[:relate :from :to :type]                  ; Create relation
[:unrelate :from :to :type]                ; Remove relation
```

### Group Effects

```clojure
[:create-group :type :opts]                ; Create new group
[:join-group :entity :group]               ; Add to group
[:leave-group :entity :group]              ; Remove from group
[:dissolve-group :group]                   ; Delete group
```

### Stake Effects

```clojure
[:return-stakes]                           ; Return locked resources
[:transfer-stakes :to]                     ; Give stakes to recipient
[:burn-stakes]                             ; Destroy stakes
[:pool-stakes :into]                       ; Merge into group resource
```

### Control Flow Effects

```clojure
[:when condition & effects]                ; Conditional
[:each :binding filter & effects]          ; Loop
[:maybe probability & effects]             ; Random
[:let {:var expr} & effects]               ; Binding
[:cond c1 e1 c2 e2 ...]                    ; Multi-branch
```

### Ledger Effects (Escrow)

```clojure
[:ledger/stake :resource :entity :amount]
[:ledger/transfer :resource :from :to :amount]
[:ledger/distribute :pool :by-score :resource]
```

### Parameter Resolution in Effects

- **Literal**: `100` → 100
- **Keyword**: `:amount` → from params
- **Expression**: `'(* 2 :amount)` → evaluated
- **Property**: `[:actor :wealth]` → entity property

---

## Compilation Pipeline

### 10-Phase Pipeline

```
Source → collect → validate → resolve → expand → analyze
                                                    ↓
       emit ← generate ← indices ← conflicts ← optimize
```

| Phase | Purpose |
|-------|---------|
| **collect** | Normalize structure, gather components |
| **validate** | Malli schema validation |
| **resolve** | Resolve cross-references |
| **expand** | Expand templates, add defaults |
| **analyze** | Static analysis (read/write patterns) |
| **optimize** | Cache filters, constant folding |
| **conflicts** | Detect resource conflicts |
| **indices** | Build lookup indices |
| **generate** | Generate UI specs |
| **emit** | Prepare for runtime |

### Compilation Context

```clojure
{:source    game-definition
 :game      transformed-game     ; Updated each phase
 :errors    []                   ; Accumulated errors
 :warnings  []                   ; Accumulated warnings
 :compiled  nil}                 ; Final result
```

### Compiled Output

```clojure
{:compiled/version 1
 :compiled/timestamp 1234567890
 :compiled/source-hash "abc123..."
 
 :game/id :my-game
 :game/name "My Game"
 :game/players {:min 3 :max 8}
 
 :game/resources {:wealth {...} :influence {...}}
 :game/attrs {:role {...} :team {...}}
 :game/deals {:bribe {...} :trade {...}}
 :game/votes {:lynch {...}}
 :game/commitments {:dead-hand {...}}
 :game/phases [{:phase/id :day ...} {:phase/id :night ...}]
 :game/victory [{:victory/id :town-wins ...}]
 
 ;; Computed indices
 :indices/by-phase {:day #{:bribe :trade} :night #{:kill}}
 :indices/writes {:wealth #{:bribe :trade}}
 :indices/reads {:reputation #{:blackmail}}
 :indices/conflicts [...]
 
 :meta/warnings [...]}
```

### Compiler Warnings

The `analyze` compilation phase generates warnings for common issues:

**`first-*` phases without `:once?`**: Phases whose IDs start with `first-` (e.g., `:first-night`, `:first-day`) are typically intended to run only on the first round. The compiler warns if such phases lack `:once? true`, as they would incorrectly re-execute on every round:

```
WARNING: Phase :first-night starts with 'first-' but doesn't have :once? true.
  This phase will execute every round. Add :once? true to execute only on round 1.
```

**Guard circular dependencies**: Phases with `:when` guards that reference attributes only modified by deals exclusively allowed in that same phase. This creates a chicken-and-egg scenario where the phase never activates because its guard depends on state changes that can only happen inside it:

```
WARNING: Phase :trial has guard referencing attrs #{:accused} which are only written
  by deals #{:accuse} exclusively in this phase. The guard may never become true.
  Consider: move :accuse to a prior phase, or use a different guard condition.
```

---

## Runtime Model

### Loading and Starting

```clojure
(require '[parlameme.v3.runtime.core :as runtime])

;; Load compiled game
(def rt (runtime/load-game compiled-game))

;; Start with players
(def rt2 (runtime/start-game rt [:alice :bob :charlie]))
```

### Runtime State

```clojure
{:game-id :my-game
 :round 1
 :phase :day
 :phase-index 2
 
 :entities
 {:alice {:id :alice
          :alive true
          :resources {:wealth 100 :influence 20}
          :attrs {:role :detective :team :town}
          :groups #{}}
  :bob {...}
  :charlie {...}}
 
 :groups
 {:coalition-1 {:id :coalition-1
                :type :coalition
                :members #{:alice :bob}
                :resources {:treasury 50}}}
 
 :relations
 {[:alice :bob] #{:allied}
  [:bob :charlie] #{:enemy}}
 
 :pending-deals
 {"deal-123" {:deal-id :bribe
              :proposer :alice
              :responder :bob
              :params {:amount 30}
              :instance-id "deal-123"}}
 
 :pending-votes
 {"vote-456" {:vote :lynch
              :subject :charlie
              :votes {:alice :guilty :bob :innocent}
              :eligible #{:alice :bob :charlie}}}
 
 :commitments
 [{:commitment :dead-hand
   :actor :alice
   :params {:target :bob}
   :instance-id "commit-789"}]
 
 :history
 [{:type :deal-started :deal :bribe :proposer :alice ...}
  {:type :vote-cast :vote :lynch :voter :alice :option :guilty}
  ...]
 
 :vars
 {:current-lot 5}
 
 :game/ledger-queue
 [{:op :ledger/transfer :from :alice :to :bob :amount 50}]}
```

### Core Operations

#### Deals

```clojure
;; Start a deal
(runtime/start-deal rt :bribe 
  {:proposer :alice :responder :bob :params {:amount 50}})
;; → {:ok? true :runtime updated-rt :instance-id "deal-123"}

;; Respond to pending deal
(runtime/respond-to-deal rt "deal-123" :accept)
;; → {:ok? true :runtime updated-rt}
```

#### Votes

```clojure
;; Start a vote
(runtime/start-vote rt :lynch {:proposer :alice :subject :bob})
;; → {:ok? true :runtime updated-rt :instance-id "vote-456"}

;; Start a vote with parameters (available in outcome effects)
(runtime/start-vote rt :boost {:proposer :alice :subject :bob
                               :params {:resource :influence :amount 10}})
;; → {:ok? true :runtime updated-rt :instance-id "vote-789"}

;; Cast vote
(runtime/cast-vote rt "vote-456" :alice :guilty)
;; → {:ok? true :runtime updated-rt}

;; Complete voting (when all voted or timeout)
(runtime/complete-vote rt "vote-456")
;; → {:ok? true :runtime updated-rt :outcome :guilty}
```

#### Phase Management

```clojure
;; Advance to next phase
(runtime/advance-phase rt)
;; → {:ok? true :runtime updated-rt}

;; Run automatic phase (system-controlled)
(runtime/run-automatic-phase rt)
;; → {:ok? true :runtime updated-rt}
```

#### Victory

```clojure
;; Check for victory condition
(runtime/check-victory rt)
;; → nil (no victory)
;; → {:type :single :winner :alice :victory-id :town-wins}
;; → {:type :distribution :scores {:alice 150 :bob 120} :victory-id :session-end}

;; End game
(runtime/end-game rt victory-result)
;; → {:ok? true :runtime final-rt}
```

### Result Type

All operations return uniform results:

```clojure
;; Success
{:ok? true :runtime updated-runtime}
{:ok? true :runtime updated-runtime :instance-id "..."}

;; Failure
{:ok? false :error {:code :insufficient-funds
                    :message "Not enough wealth"
                    :details {:required 50 :available 30}}}
```

### Queries

```clojure
;; Get entity view (respects visibility)
(runtime/get-entity-view rt :alice :bob)  ; What bob sees of alice

;; Get full game view for observer
(runtime/get-game-view rt :alice)

;; Get available deals for entity
(runtime/get-available-deals rt :alice)
;; → [{:deal-id :bribe :can-propose? true :can-respond? false} ...]
```

---

## Schemas

All components are validated against Malli schemas.

### Core Schemas (src/cljc/parlameme/v3/schema.cljc)

```clojure
;; Resource
[:map
 [:resource/id :keyword]
 [:resource/initial {:optional true} :int]
 [:resource/visibility {:optional true} [:enum :public :private :hidden]]
 [:resource/bounds {:optional true} [:tuple :int :int]]
 [:resource/transferable {:optional true} :boolean]]

;; Deal
[:map
 [:deal/id :keyword]
 [:deal/parties [:map-of :keyword [:map [:filter {:optional true} :any]]]]
 [:deal/params {:optional true} [:map-of :keyword :any]]
 [:deal/stakes {:optional true} [:map-of :keyword [:vector [:tuple :keyword :any]]]]
 [:deal/guard {:optional true} :any]
 [:deal/phase {:optional true} [:or :keyword [:vector :keyword]]]
 [:deal/outcomes [:map-of :keyword [:map [:effects [:vector :any]]]]]]

;; Phase
[:map
 [:phase/id :keyword]
 [:phase/when {:optional true} :any]
 [:phase/allows {:optional true} [:vector :keyword]]
 [:phase/effects {:optional true} [:vector :any]]
 [:phase/duration {:optional true} [:map [:seconds :int]]]
 [:phase/next {:optional true} :keyword]
 [:phase/transitions {:optional true} [:vector [:map [:guard :any] [:target :keyword]]]]
 [:phase/starts-round {:optional true} :boolean]]

;; Victory
[:map
 [:victory/id :keyword]
 [:victory/type [:enum :single :distribution]]
 [:victory/when :any]
 [:victory/score {:optional true} :any]
 [:victory/for {:optional true} :keyword]]
```

---

## Examples

### Minimal Game

```clojure
(-> (game :simple "Simple Game" {:players {:min 2 :max 4}})
    
    (resource :points {:initial 0 :visibility :public})
    
    (deal :give-point
      {:parties {:actor {:filter '(alive?)}}
       :params {:target {:type :player}}
       :outcomes {:ok {:effects [[:boost :target :points 1]]}}})
    
    (phase :main {:allows [:give-point]})
    
    (victory :winner
      {:type :single
       :when '(>= [:actor :points] 10)
       :message "Winner!"}))
```

### Social Deduction (Mafia-style)

```clojure
(-> (game :mafia "Mafia" {:players {:min 6 :max 12}})
    
    (attr :role {:visibility :private 
                 :values [:civilian :detective :doctor :mafioso]})
    (attr :team {:visibility :hidden 
                 :values [:town :mafia]})
    (attr :protected {:visibility :hidden :initial false})
    
    (roles {:civilian {:team :town :filler true}
            :detective {:team :town :unique true}
            :doctor {:team :town :unique true}
            :mafioso {:team :mafia :count 2}})
    
    ;; Night actions
    (deal :mafia-kill
      {:parties {:actor {:filter '(and (alive?) (= :actor.team :mafia))}}
       :params {:target {:type :player :filter '(and (alive?) 
                                                     (not= :target.team :mafia))}}
       :phase :night
       :limit {:per-round 1}
       :outcomes {:ok {:effects [[:when '(not :target.protected)
                                   [:eliminate :target]]]}}})
    
    (deal :doctor-protect
      {:parties {:actor {:filter '(and (alive?) (= :actor.role :doctor))}}
       :params {:target {:type :player :filter '(alive?)}}
       :phase :night
       :limit {:per-round 1}
       :outcomes {:ok {:effects [[:set-attr :target :protected true]]}}})
    
    ;; Day voting
    (vote :lynch
      {:proposer {:filter '(alive?)}
       :subject {:type :player :filter '(alive?)}
       :voters {:filter '(alive?)}
       :options [:guilty :innocent]
       :threshold :majority
       :phase :day
       :outcomes {:guilty {:effects [[:eliminate :subject]]}}})
    
    ;; Phases
    (phase :night {:allows [:mafia-kill :doctor-protect :investigate]
                   :parallel? true})
    (phase :dawn {:automatic? true
                  :effects [[:each :p '(alive?) 
                             [:set-attr :p :protected false]]]})
    (phase :day {:allows [:accuse :discuss]})
    (phase :trial {:when '(> (count :accused) 0)
                   :allows [:lynch]})
    
    ;; Victory
    (victory :town-wins
      {:type :single
       :when '(= 0 (count-where (= :actor.team :mafia)))
       :for :town})
    
    (victory :mafia-wins
      {:type :single
       :when '(>= (count-where (= :actor.team :mafia))
                  (count-where (= :actor.team :town)))
       :for :mafia}))
```

### Economic Game (Parliament-style)

```clojure
(-> (game :parliament "Parliament" {:players {:min 3 :max 12}})
    
    ;; Resources
    (resource :caps {:initial 100 :visibility :private :transferable true})
    (resource :influence {:initial 10 :visibility :public})
    (resource :reputation {:initial 50 :visibility :public})
    
    ;; Factions
    (attr :faction {:visibility :public 
                    :values [:vault :scrap :green :iron]
                    :distribute? true})
    
    ;; Deals
    (deal :bribe
      {:parties {:proposer {:filter '(alive?)}
                 :responder {:filter '(alive?)}}
       :params {:amount {:type :integer :min 1 :max ':proposer.caps}}
       :stakes {:proposer [[:caps :amount]]}
       :guard '(not= :proposer :responder)
       :phase :floor
       :response {:timeout 60 :options [:accept :reject]}
       :outcomes {:accept {:effects [[:transfer-stakes :responder]
                                     [:relate :proposer :responder :bribed]]}
                  :reject {:effects [[:return-stakes]]}}})
    
    (deal :form-coalition
      {:parties {:proposer {:filter '(alive?)}
                 :responder {:filter '(alive?)}}
       :guard '(not (same-group? :proposer :responder))
       :phase :caucus
       :response {:timeout 30 :options [:join :decline]}
       :outcomes {:join {:effects [[:create-group :coalition {:founder :proposer}]
                                   [:join-group :proposer :new-group]
                                   [:join-group :responder :new-group]]}
                  :decline {:effects []}}})
    
    ;; Voting
    (vote :bill
      {:proposer {:filter '(alive?)}
       :voters {:filter '(alive?)}
       :options [:aye :nay :abstain]
       :weights [:by :influence]
       :threshold :majority
       :phase :vote
       :outcomes {:aye {:effects [[:boost :proposer :reputation 5]]}
                  :nay {:effects [[:damage :proposer :reputation 3]]}}})
    
    ;; Phases
    (phase :caucus {:allows [:form-coalition :discuss]})
    (phase :floor {:allows [:bribe :trade :promise]})
    (phase :vote {:allows [:bill :allocation]})
    
    ;; Victory - distribution after 10 rounds
    (victory :session-end
      {:type :distribution
       :when '(>= :round 10)
       :score '(+ (* 0.30 :actor.caps)
                  (* 0.25 :actor.influence)
                  (* 0.25 :actor.reputation)
                  (* 0.20 (count-in-relation :actor :allied)))}))
```

---

## See Also

- `doc/flow_v3_architecture.md` — High-level architecture
- `src/cljc/parlameme/v3/dsl.cljc` — DSL implementation
- `src/cljc/parlameme/v3/compiler/core.cljc` — Compilation pipeline
- `src/cljc/parlameme/v3/runtime/core.cljc` — Runtime engine
- `src/cljc/parlameme/v3/games/` — Example games
