# Flow v3 DSL: Evaluation and Development Guide

This document captures my experience designing and implementing a complex political-economic game ("Election Race") using the Flow v3 DSL. It provides an honest evaluation of what works well, what could be improved, and practical guidance for game developers.

## Executive Summary

**Overall Assessment: 8/10**

The DSL is powerful and well-designed for its purpose. It successfully abstracts complex game mechanics into declarative data structures. However, some patterns require significant exploration to discover, and error messages could be more helpful.

## What Works Exceptionally Well

### 1. Declarative Threading Composition

The `->` macro pattern for building games is elegant and readable:

```clojure
(-> (game :my-game "My Game" {...})
    (resource :gold {...})
    (attr :role {...})
    (deal :trade {...})
    (phase :day {...}))
```

**Verdict: Excellent.** Building games feels like describing them rather than programming them.

### 2. Deal System Expressiveness

The deal system handles a wide variety of interactions:
- Single-party actions (night powers, solo decisions)
- Two-party negotiations (bribes, alliances, trades)
- Multi-party agreements (coalitions)

The `parties`, `stakes`, `response`, and `outcomes` structure covers most game theory scenarios.

**Verdict: Excellent.** Complex prisoner's dilemma, signaling, and coalition dynamics are naturally expressible.

### 3. Expression Language

The whitelisted expression evaluator provides safety while remaining expressive:

```clojure
'(and (alive?) (= [:actor :role] :detective))
'(> [:actor :funds] 100)
'(count-where (= [:actor :team] :mafia))
```

**Verdict: Good.** Powerful enough for complex guards and conditions.

### 4. Effect System

Effects compose well and cover most needs:

```clojure
[:transfer :from :to :resource :amount]
[:boost :entity :resource :amount]
[:set-attr :entity :attr :value]
[:when 'condition [:effect1] [:effect2]]
[:each :binding 'filter [:effects]]
```

**Verdict: Excellent.** The conditional and looping effects are particularly useful.

### 5. Compilation Validation

The 10-phase compilation pipeline catches many errors early:
- Schema validation
- Reference resolution
- Conflict detection

**Verdict: Good.** Finding errors at compile-time rather than runtime saves significant debugging time.

## What Could Be Improved

### 1. Error Messages Could Be Clearer

When I used invalid icon names or styles, the error was:

```
{:ui {:icon ["should be a keyword"], :style ["should be either :primary, :danger, ..."]}}
```

**Problem:** The error says "should be a keyword" when the actual issue was that emoji strings aren't supported. The available icons aren't documented in error messages.

**Suggestion:** Include examples of valid values in error messages.

### 2. No Obvious Way to Discover Valid Values

I had to grep existing games to find:
- Valid icon keywords (`:coin`, `:star`, `:eye`, etc.)
- Valid style keywords (`:primary`, `:danger`, `:mystery`, etc.)
- Valid format keywords (`:number`, `:percentage`, `:currency`)

**Problem:** These constraints aren't obvious from the DSL function signatures or documentation.

**Suggestion:** Add helper functions or better documentation:
```clojure
(dsl/valid-icons)     ;; => [:coin :star :eye ...]
(dsl/valid-styles)    ;; => [:primary :danger ...]
```

### 3. Group Channels Require Existing Group-Types

I tried to create channels for dynamically-created groups:

```clojure
(channel :democrat-caucus
         {:type :group
          :group :party-caucus-democrat})  ;; ERROR: doesn't exist
```

**Problem:** The group is created at runtime via `[:create-group ...]`, but the channel validation happens at compile-time.

**Current Workaround:** Use a generic group-type and create multiple instances at runtime.

**Suggestion:** Allow channels to reference group-types (not instances), or defer validation.

### 4. Nested Quotes in Expressions

The "no nested quotes" rule is critical but not obvious:

```clojure
;; WRONG - runtime error "Unknown operator: quote"
:when '(count-where '(= :team :mafia))

;; CORRECT - use property vector syntax
:when '(count-where (= [:actor :team] :mafia))
```

**Problem:** This is documented but easy to forget. The error message doesn't immediately suggest the fix.

**Suggestion:** Better error message: "Nested quotes not allowed. Use `[:actor :field]` syntax instead of `'(= :field value)`"

### 5. Vote Subject Types Are Limited

I wanted a vote where the subject was an endorsement group (keyword), but this wasn't easily expressible:

```clojure
:subject {:type :keyword
          :values [:labor :business :youth]}  ;; Didn't work as expected
```

**Workaround:** Changed to player-based voting.

### 6. Conflict Warnings Are Noisy

In a parallel phase, many deals write to the same resources (e.g., `:funds`). This generates many warnings:

```
Unhandled conflict in phase :campaign: :fundraise vs :attack-ad both write #{[:actor :funds]}
```

**Problem:** These are expected in economic games where multiple actions spend money.

**Suggestion:** Allow marking certain conflicts as "expected" to suppress warnings:
```clojure
(phase :campaign
       {:parallel? true
        :expected-conflicts #{[:actor :funds]}  ;; Suppress these warnings
        ...})
```

## Patterns Discovered Through Experience

### Pattern 1: Resource Visibility Tiers

Use visibility strategically:
- `:public` - For social feedback (popularity, credibility)
- `:private` - For hidden advantages (funds, influence)
- `:hidden` - For system state (scandal-exposed, marked-for-death)

### Pattern 2: Stakes as Credibility Signals

Stakes aren't just economic - they signal commitment:

```clojure
:stakes {:proposer [[:credibility 10]]}  ;; "I'm serious about this"
```

Burning stakes on betrayal creates game-theoretic deterrence.

### Pattern 3: Conditional Effects for Branching Outcomes

```clojure
:outcomes
{:accept {:effects [[:when '(> [:proposer :credibility] [:responder :credibility])
                     [:boost :proposer :popularity 10]
                     [:damage :responder :popularity 5]]
                    [:when '(<= [:proposer :credibility] [:responder :credibility])
                     [:boost :responder :popularity 10]
                     [:damage :proposer :popularity 5]]]}}
```

### Pattern 4: Phase Guards for Conditional Game Flow

```clojure
(phase :primary
       {:when '(= :round 2)  ;; Only appears in round 2
        ...})

(phase :election
       {:when '(>= :round 4)  ;; End-game phase
        :once? true           ;; Can only happen once
        ...})
```

### Pattern 5: Multi-Outcome Deals Based on Parameters

```clojure
:outcomes
{:mild {:guard '(= :intensity :mild)
        :effects [...]}
 :moderate {:guard '(= :intensity :moderate)
            :effects [...]}
 :aggressive {:guard '(= :intensity :aggressive)
              :effects [...]}}
```

### Pattern 6: Custom Party Names for Thematic Deals

Use domain-specific party names instead of generic `proposer`/`responder`:

```clojure
;; Instead of generic parties:
(deal :accuse
  {:parties {:accuser {:filter '(alive?)}
             :seconded-by {:filter '(alive?) :excludes [:accuser]}}
   :params {:target {:type :player :filter '(alive?)}}
   ...})

;; Multilateral with custom names:
(deal :propose-team
  {:parties {:leader {:filter '(= :actor.role :leader)}
             :partners {:filter '(alive?) :count [2 3] :excludes [:leader]}}
   ...})
```

The engine classifies parties by structure (`:count` → multilateral, `:excludes` → respondent), not by name. MCP maps custom names to canonical parameters automatically.

### Pattern 7: Vote Parameters for Dynamic Outcomes

Pass parameters when starting votes to make outcomes configurable:

```clojure
(vote :boost
  {:proposer {:filter '(alive?)}
   :subject {:type :player :filter '(alive?)}
   :voters {:filter '(alive?)}
   :options [:approve :deny]
   :outcomes {:approve {:effects [[:boost :subject :resource :amount]]}}})

;; At runtime, :resource and :amount come from vote params:
(runtime/start-vote rt :boost
  {:proposer :alice :subject :bob
   :params {:resource :influence :amount 10}})
```

### Pattern 8: One-Time Setup Phases

Use `:once? true` for phases that should only run on the first round:

```clojure
(phase :first-night
  {:once? true  ;; CRITICAL — without this, phase repeats every round
   :allows [:seer-peek :wolf-kill]
   :effects [[:broadcast "The first night begins..."]]})
```

**Warning:** The compiler flags `first-*` phases missing `:once?` to prevent this common mistake.

## Practical Development Workflow

### 1. Start Simple, Iterate

Begin with minimal game:
```clojure
(-> (game :test "Test" {:players {:min 2 :max 4}})
    (resource :gold {:initial 100})
    (deal :give {:parties {:actor {:filter '(alive?)}} ...})
    (phase :main {:allows [:give]}))
```

Compile frequently to catch errors early.

### 2. Use REPL for Testing

```clojure
;; Compile
(def result (compiler/compile-game my-game-source))
(println (:errors result))

;; Test runtime
(def rt (-> (runtime/load-game {:compiled (:compiled result)})
            (runtime/start-game [:alice :bob])))

;; Test deals
(runtime/start-deal rt :my-deal {:actor :alice :params {...}})
```

### 3. Check Existing Games for Patterns

The games in `src/cljc/parlameme/v3/games/` are excellent references:
- `mafia.cljc` - Social deduction, night/day cycles
- `werewolf.cljc` - Complex roles, commitments
- `auction.cljc` - Economic mechanics, bidding
- `resistance.cljc` - Team deduction, mechanism design
- `parliament_arena.cljc` - Political economy, coalitions

### 4. Icons and Styles Reference

**Valid Icons:**
`:coin`, `:star`, `:eye`, `:crown`, `:heart`, `:shield`, `:sword`, `:dagger`, `:target`, `:warning`, `:skull`, `:potion`, `:scroll`, `:ballot`, `:users`, `:handshake`, `:thumbs-up`, `:thumbs-down`, `:whisper`, `:announce`, `:gift`, `:fire`, `:lightning`, `:hand`, `:gavel`, `:magnifier`, `:arrow-up`, `:rocket`, `:skip`, `:gun`, `:broken-heart`, `:curse`, `:transform`, `:balance`, `:radiation`, `:can`, `:bottle-cap`, `:painting`

**Valid Styles:**
`:primary`, `:danger`, `:warning`, `:mystery`, `:formal`, `:commerce`, `:social`

**Valid Formats:**
`:number`, `:percentage`, `:currency`

## Game Theory Implementation Checklist

When implementing game-theoretic mechanics:

- [ ] **Prisoner's Dilemma**: Attack deals that damage attacker's credibility
- [ ] **Signaling**: Public promises/stakes that create accountability
- [ ] **Screening**: Deals where acceptance reveals information
- [ ] **Commitment Devices**: Stakes that burn on betrayal
- [ ] **Coalition Formation**: Multi-party deals with unanimous acceptance (custom party names supported)
- [ ] **Reputation Systems**: Public resources that track trustworthiness
- [ ] **Information Asymmetry**: Private/hidden attributes revealed through actions
- [ ] **Parameterized Votes**: Vote outcomes that depend on runtime parameters (use `:params`)
- [ ] **Setup Phases**: One-time phases with `:once? true` for initial setup (e.g., role reveal)

## Conclusion

The Flow v3 DSL is a well-designed tool for expressing complex social and economic games declaratively. Its main strengths are composability, expressiveness, and safety through validation. The main areas for improvement are discoverability of valid values and clearer error messages.

For game developers: invest time upfront understanding the patterns from existing games, use the REPL heavily for testing, and build incrementally. The learning curve is moderate but the payoff is significant.

---

*Document created during development of Election Race game, February 2026*
