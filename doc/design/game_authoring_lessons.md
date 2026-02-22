# Game Authoring Lessons Learned

**Based on:** Election Race game development (2026-02-03)

This document captures non-bug issues encountered when authoring a new game with the Flow v3 DSL. These are DX (Developer Experience) problems that should inform future improvements.

## 1. DSL Expression Gotchas

### Nested Quotes Don't Work

**Problem:** Clojure symbols in quoted expressions don't resolve - they remain as literal symbols.

```clojure
;; WRONG - MAX-ROUNDS stays as symbol, causes NPE at runtime
(def ^:const MAX-ROUNDS 4)
(victory :winner {:when '(>= :round MAX-ROUNDS) ...})

;; CORRECT - use literal values
(victory :winner {:when '(>= :round 4) ...})
```

**Why it happens:** The DSL expression evaluator has a whitelist of operators. `quote` is not in it, and Clojure's `def` bindings are not available at expression evaluation time.

**Recommendation:** Add compile-time validation that warns when expressions contain unrecognized symbols that look like constants (UPPER_CASE).

### Sub-expression Quoting Also Fails

```clojure
;; WRONG - nested quote causes "Unknown operator: quote"
:when '(= 0 (count-where '(= :team :mafia)))

;; CORRECT - use property access syntax without inner quote
:when '(= 0 (count-where (= [:actor :team] :mafia)))
```

**Recommendation:** Document this clearly in DSL spec. Consider compile-time detection.

## 2. Validation Error Messages

### Missing Available Values

**Problem:** When a keyword param has invalid value, error doesn't show valid options.

```
;; Error received:
{:error {:code :invalid-param, :message "Invalid value for :intensity"}}

;; Would be better:
{:error {:code :invalid-param, 
         :message "Invalid value for :intensity. Valid: [:mild :moderate :aggressive]"}}
```

**Recommendation:** Include `:values` from param schema in error messages.

### No Icon/Style Discovery

**Problem:** No way to know valid icons or styles without grepping source code.

```clojure
;; What icons exist? What styles are valid?
(deal :hold-rally {:ui {:icon :announce :style :primary} ...})
```

Had to grep through existing games and UI code to find valid values.

**Recommendation:** 
1. Add `(loader/list-icons)` and `(loader/list-styles)` REPL helpers
2. Document all valid values in DSL spec
3. Add compile-time validation with suggestions

## 3. Channel Configuration

### Group Channels Require Existing Group Types

**Problem:** Defining a channel for a group that's created dynamically at runtime is tricky.

```clojure
;; This channel is for :party-caucus groups created in :setup phase
(channel :party-chat {:type :group :group-type :party-caucus ...})

;; But :party-caucus doesn't exist at compile time!
;; Had to understand the compile-time vs runtime distinction
```

**Recommendation:** Document the channel→group relationship more clearly. Consider allowing channels to reference group patterns.

## 4. Compiler Warnings

### Conflict Warnings Too Verbose

**Problem:** Conflict analysis produces many warnings that are often expected/intentional.

```
;; Every deal that writes to :funds conflicts with every other deal writing to :funds
{:phase :campaign, :deal1 :opposition-research, :deal2 :fundraise, :writes #{[:actor :funds]}}
{:phase :campaign, :deal1 :opposition-research, :deal2 :hold-rally, :writes #{[:actor :funds]}}
{:phase :campaign, :deal1 :opposition-research, :deal2 :attack-ad, :writes #{[:actor :funds]}}
... (20+ more warnings)
```

**Recommendation:** 
1. Add severity levels (info/warn/error)
2. Allow suppressing expected conflicts: `{:suppress-conflicts #{[:actor :funds]}}`
3. Only show same-entity conflicts by default

## 5. Testing and Debugging

### No Easy Way to Test Single Actions

**Problem:** Testing a single action requires setting up full game state.

```clojure
;; Current: need full session setup
(sessions/create-session! ...)
(sessions/join-session! ...)
(sessions/start-game! ...)
(sessions/start-deal! ...)

;; Would like: quick action testing
(test-action :hold-rally {:actor :alice :investment 100}
             :with-state {:alice {:funds 1000}})
```

**Recommendation:** Add `v3/simulator` helpers for quick action testing.

### Expression Debugging is Hard

**Problem:** When an expression fails, it's hard to see intermediate values.

```clojure
;; This fails but why?
:guard '(and (>= [:actor :funds] :investment) 
             (= [:actor :status] :candidate))

;; Would like:
;; Guard failed: (and true false)
;;   (>= [:actor :funds] :investment) => true (1000 >= 100)
;;   (= [:actor :status] :candidate) => false (:nominee != :candidate)
```

**Recommendation:** Add `:debug? true` option to expression evaluation that traces sub-expressions.

## 6. Documentation Gaps

### Missing Examples for Complex Patterns

**Problem:** DSL spec covers syntax but lacks examples for common patterns:
- Multi-party deals (more than 2 players)
- Conditional effects based on game state
- Group creation and management
- Channel access control

**Recommendation:** Add "Cookbook" section with patterns.

### No Schema Explorer

**Problem:** Hard to discover what fields are available/required for each DSL form.

```clojure
;; What can go in a deal definition?
(deal :my-deal {???})
```

**Recommendation:** Add `(dsl/describe :deal)` that prints schema with examples.

## Summary: Priority Improvements

1. **High:** Better error messages with valid values
2. **High:** Document nested quote restriction prominently
3. **Medium:** Add REPL discovery helpers (icons, styles, schemas)
4. **Medium:** Reduce conflict warning verbosity
5. **Low:** Add expression debugging mode
6. **Low:** Expand DSL cookbook/examples
