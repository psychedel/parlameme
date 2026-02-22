# Developer Experience Improvements Plan

**Created:** 2026-02-03  
**Based on:** Election Race game authoring experience and codebase analysis

This document contains a comprehensive plan to improve the game authoring experience in the Flow v3 DSL system.

---

## Executive Summary

Six areas require improvement:

| Area | Priority | Effort | Impact |
|------|----------|--------|--------|
| 1. Better error messages | High | Medium | High |
| 2. Icon/style discovery | Medium | Low | Medium |
| 3. Keyword param dropdowns in UI | High | Low | High |
| 4. WebSocket error feedback | High | Low | High |
| 5. Conflict warning verbosity | Medium | Medium | Medium |
| 6. DSL expression debugging | Low | High | Medium |

---

## 1. Better Validation Error Messages

### Problem
When a keyword param has an invalid value, the error doesn't show valid options:
```
{:error {:code :invalid-param, :message "Invalid value for :intensity"}}
```

### Root Cause
`/src/cljc/parlameme/v3/validation.cljc` doesn't check `:values` field for keyword params.

### Solution

**File:** `/src/cljc/parlameme/v3/validation.cljc`

Add keyword validation with available values in error:

```clojure
(defn validate-param
  [param-id param-def value ctx]
  (cond
    ;; ... existing checks ...

    ;; NEW: Invalid keyword option
    (and value 
         (= :keyword (:type param-def))
         (:values param-def)
         (not (contains? (set (:values param-def)) value)))
    {:param param-id 
     :error (str "Invalid value '" value "'. Valid options: " 
                 (str/join ", " (map name (:values param-def))))
     :value value 
     :valid-values (:values param-def)}

    :else nil))
```

**File:** `/src/cljc/parlameme/v3/schema.cljc`

Add `:values` to DealParam schema (line ~220):

```clojure
(def DealParam
  [:map {:closed false}
   [:type [:enum :number :string :keyword :player :resource]]
   [:values {:optional true} [:vector :keyword]]  ;; ADD THIS
   [:min {:optional true} [:or :int Expr]]
   [:max {:optional true} [:or :int Expr]]
   [:default {:optional true} :any]])
```

### Testing
```clojure
;; Should return error with valid options
(v/validate-param :intensity 
                  {:type :keyword :values [:mild :moderate :aggressive]} 
                  :invalid 
                  {})
;; => {:param :intensity 
;;     :error "Invalid value 'invalid'. Valid options: mild, moderate, aggressive"
;;     :valid-values [:mild :moderate :aggressive]}
```

---

## 2. Icon and Style Discovery

### Problem
No way to discover valid icons/styles without grepping source code.

### Solution

**File:** `/src/clj/parlameme/v3/loader.clj`

Add discovery helpers:

```clojure
(def icon-map
  "All valid icon keywords with their emoji representations."
  {:coin "💰" :money "💰" :gold "🪙" :star "⭐" :crown "👑"
   :heart "❤️" :lightning "⚡" :chart "📊" :ballot "🗳️"
   :handshake "🤝" :lock "🔒" :unlock "🔓" :eye "👁️" :spy "🕵️"
   :shield "🛡️" :ok "🟢" :warning "⚠️" :alert "⚠️"
   :user "👤" :users "👥" :game "🎮" :skull "💀"
   :announce "📢" :whisper "🤫" :sword "⚔️" :target "🎯"
   :scroll "📜" :thumbs-up "👍" :default "📋"})

(def style-keywords
  "All valid style keywords for deal/action UI."
  #{:primary :danger :success :warning :commerce :mysterious :noble
    :neutral :dramatic :corrupt :innocent :evil :special :mystery
    :formal :social})

(defn list-icons
  "List all valid icon keywords.
   Usage: (loader/list-icons)
   Returns: map of keyword -> emoji"
  []
  (println "Valid icon keywords:")
  (doseq [[k v] (sort-by key icon-map)]
    (println (format "  %-15s %s" (name k) v)))
  icon-map)

(defn list-styles
  "List all valid style keywords.
   Usage: (loader/list-styles)"
  []
  (println "Valid style keywords:")
  (doseq [s (sort style-keywords)]
    (println (format "  :%s" (name s))))
  style-keywords)

(defn describe
  "Describe a DSL form with its schema and examples.
   Usage: (loader/describe :deal)
          (loader/describe :phase)
          (loader/describe :resource)"
  [form-type]
  (case form-type
    :deal (println "Deal schema:\n"
                   "  :parties     - {:actor|:proposer|:responder {:filter expr}}\n"
                   "  :params      - {:name {:type :keyword|:number|:player :values [...]}}\n"
                   "  :stakes      - {:proposer [[resource amount]]}\n"
                   "  :outcomes    - {:accept {:effects [...]} :reject {...}}\n"
                   "  :guard       - expr (pre-condition)\n"
                   "  :ui          - {:icon :keyword :style :keyword :display \"string\"}\n"
                   "  :per-round   - number (limit per round)\n"
                   "  :per-game    - number (limit per game)")
    :phase (println "Phase schema:\n"
                    "  :allows      - [:deal-id ...]\n"
                    "  :parallel?   - boolean\n"
                    "  :resolution  - :priority|:first-wins|:merge\n"
                    "  :duration    - {:seconds n}\n"
                    "  :automatic?  - boolean\n"
                    "  :when        - expr (phase entry condition)\n"
                    "  :effects     - [[:effect ...]] (on phase start)")
    :resource (println "Resource schema:\n"
                       "  :initial     - number\n"
                       "  :visibility  - :public|:private|:hidden\n"
                       "  :bounds      - [min max]\n"
                       "  :transferable - boolean\n"
                       "  :ui          - {:icon :keyword :color :keyword :format :number|:currency|:percentage}")
    (println "Unknown form type. Try: :deal :phase :resource :attr :channel :victory :vote")))
```

### REPL Usage
```clojure
(loader/list-icons)
;; Valid icon keywords:
;;   announce        📢
;;   ballot          🗳️
;;   ...

(loader/list-styles)
;; Valid style keywords:
;;   :commerce
;;   :danger
;;   ...

(loader/describe :deal)
;; Deal schema:
;;   :parties     - {:actor|:proposer|:responder {:filter expr}}
;;   ...
```

---

## 3. Keyword Param Dropdowns in UI

### Problem
Parameters with `:type :keyword` and `:values` render as textbox instead of dropdown.

### Root Cause
`/src/cljs/parlameme/ui/spec/deals.cljs` `render-param-input` function missing `:keyword` case.

### Solution

**File:** `/src/cljs/parlameme/ui/spec/deals.cljs`

Add `:keyword` case to `render-param-input` (around line 115):

```clojure
(defn- render-param-input
  [{:keys [name type min max default values]} current-val on-change targets]
  ;; ... existing code ...
  (case type
    :number-slider [number-slider ...]
    :text-input [:div ...]
    (:player :player-select) [:div ...]
    
    ;; ADD THIS CASE:
    :keyword
    (if (seq values)
      ;; Dropdown for keyword with predefined values
      [:div
       [:label {:class "block text-sm font-medium text-slate-400 mb-1"}
        (spec/keyword->label name)]
       [:select {:class "w-full px-4 py-3 rounded-xl bg-slate-900/80 border border-slate-700/50 text-white"
                 :value (or (some-> current-val cljs.core/name) "")
                 :on-change #(on-change name (keyword (.. % -target -value)))}
        [:option {:value ""} (str "Select " (spec/keyword->label name) "...")]
        (for [opt values]
          ^{:key opt}
          [:option {:value (cljs.core/name opt)} (cljs.core/name opt)])]]
      ;; Fallback to text input for keyword without values
      [:div
       [:label {:class "block text-sm font-medium text-slate-400 mb-1"}
        (spec/keyword->label name)]
       [:input {:type "text"
                :class "w-full px-4 py-3 rounded-xl bg-slate-900/80 border border-slate-700/50 text-white"
                :placeholder (str "Enter " (spec/keyword->label name))
                :value (or (some-> current-val cljs.core/name) "")
                :on-change #(on-change name (keyword (.. % -target -value)))}]])

    ;; ADD: select type alias
    :select
    [:div
     [:label {:class "block text-sm font-medium text-slate-400 mb-1"}
      (spec/keyword->label name)]
     [:select {:class "w-full px-4 py-3 rounded-xl bg-slate-900/80 border border-slate-700/50 text-white"
               :value (or (some-> current-val cljs.core/name) "")
               :on-change #(on-change name (keyword (.. % -target -value)))}
      [:option {:value ""} (str "Select " (spec/keyword->label name) "...")]
      (for [opt (or values [])]
        ^{:key opt}
        [:option {:value (cljs.core/name opt)} (cljs.core/name opt)])]]

    ;; Default fallback
    [:div {:class "text-sm text-slate-400"}
     (str (spec/keyword->label name) ": " current-val)]))
```

**Also update:** `/src/cljc/parlameme/v3/compiler/phases/generate.cljc`

Ensure `:values` is propagated to UI spec (around line 55):

```clojure
(defn param->ui-input [{:keys [type min max values default] :as param} param-id]
  {:name param-id
   :type (case type
           :number :number-slider
           :keyword (if values :select :text-input)
           :player :player-select
           :string :text-input
           :text-input)
   :min min
   :max max
   :values values  ;; ADD THIS - pass through values for select
   :default default
   :coerce-to (case type
                :number :number
                :keyword :keyword
                :player :keyword
                nil)})
```

### Testing
1. Create deal with `:type :keyword :values [:a :b :c]`
2. Verify UI shows dropdown with options a, b, c
3. Select option, verify correct keyword sent to server

---

## 4. WebSocket Error Feedback to Client

### Problem
`/src/clj/parlameme/game/sente.clj` catch block logs errors but doesn't send to client.

### Root Cause
Lines 193-200 catch exceptions but don't call `chsk-send!` with error.

### Solution

**File:** `/src/clj/parlameme/game/sente.clj`

Update the catch block (lines 193-200):

```clojure
(defn- event-msg-handler
  [{:keys [id uid ?data ring-req] :as ev-msg}]
  (when (and id (not= id :chsk/ws-ping))
    (try
      (handle-event id uid ?data ring-req)
      (catch Exception e
        (log/error e "Event" id "failed")
        (riemann/error! {:layer :sente
                         :error-type :event-handler-failed
                         :message (str "Event " id " failed: " (ex-message e))
                         :exception e})
        ;; ADD: Send error back to client
        (try
          (let [error-msg {:code :handler-error
                           :message "Request processing failed"
                           :event-id (name id)}]
            ;; Try to send via player-id first (for registered players)
            (if-let [player-id (uid->player uid)]
              (v3-sente/send-v3-error! player-id error-msg)
              ;; Fallback to uid for unregistered connections
              (chsk-send! uid [:v3/error error-msg])))
          (catch Exception send-err
            (log/warn send-err "Failed to send error to client")))))))
```

**Import required:** Add to ns requires:
```clojure
[parlameme.v3.sente :as v3-sente]
```

### Testing
1. Trigger an error (e.g., call non-existent deal)
2. Verify client receives `:v3/error` event
3. Verify UI shows error message instead of "Loading..."

---

## 5. Reduce Conflict Warning Verbosity

### Problem
Every deal writing to same resource generates conflict warning, even when expected.

### Root Cause
`/src/cljc/parlameme/v3/compiler/phases/conflicts.cljc` has no suppression mechanism.

### Solution

**Step 1:** Add suppression to phase DSL

**File:** `/src/cljc/parlameme/v3/dsl.cljc`

Add `:suppress-conflicts` option to phase (around line 300):

```clojure
(defn phase
  "Define a game phase."
  [game phase-id opts]
  (let [phase-def (merge
                    {:phase/id phase-id
                     :allows []
                     :parallel? false
                     :automatic? false}
                    opts
                    ;; NEW: extract suppression config
                    {:suppress-conflicts (set (:suppress-conflicts opts))})]
    (update game :game/phases conj phase-def)))
```

**Step 2:** Filter suppressed conflicts

**File:** `/src/cljc/parlameme/v3/compiler/phases/conflicts.cljc`

Update `detect-conflicts` function:

```clojure
(defn- should-suppress-conflict?
  "Check if conflict should be suppressed based on phase config."
  [conflict phase]
  (let [suppressions (or (:suppress-conflicts phase) #{})
        writes (:writes conflict)]
    ;; Suppress if any conflicting write path is in suppression set
    ;; e.g., #{[:actor :funds]} suppresses all conflicts on :funds
    (some (fn [write-path]
            (or (suppressions write-path)
                ;; Also check resource-only suppression: #{:funds}
                (suppressions (second write-path))))
          writes)))

(defn detect-conflicts [ctx]
  (let [phases (get-in ctx [:game :game/phases])
        deals (get-in ctx [:game :game/deals])
        all-conflicts (mapcat #(find-phase-conflicts % deals) 
                              (filter :parallel? phases))]
    (-> ctx
        (assoc-in [:game :meta/conflicts] all-conflicts)
        (update :warnings into
                (for [conflict all-conflicts
                      :let [phase (find-phase phases (:phase conflict))]
                      :when (and (not (conflict-handled? conflict phase))
                                 (not (should-suppress-conflict? conflict phase)))]
                  {:phase :conflicts
                   :type :unhandled-conflict
                   :conflict conflict
                   :message (format "Unhandled conflict in %s: %s vs %s write %s"
                                   (:phase conflict)
                                   (:deal1 conflict)
                                   (:deal2 conflict)
                                   (:writes conflict))})))))
```

**Step 3:** Add severity levels

```clojure
(defn add-warning [ctx warning]
  (let [severity (case (:type warning)
                   :unhandled-conflict :warn
                   :missing-priorities :error
                   :noop-outcome :info
                   :equivalent-outcomes :info
                   :info)]
    (update ctx :warnings conj (assoc warning :severity severity))))
```

### DSL Usage
```clojure
(phase game :campaign
  {:parallel? true
   :allows [:hold-rally :fundraise :attack-ad]
   ;; Suppress conflicts on :funds - we expect multiple actions to modify it
   :suppress-conflicts #{:funds [:actor :funds]}})
```

---

## 6. DSL Expression Debugging (Lower Priority)

### Problem
When expressions fail, no visibility into intermediate values.

### Solution Sketch

**File:** `/src/cljc/parlameme/v3/runtime/expr.cljc`

Add trace mode:

```clojure
(defn evaluate
  "Evaluate DSL expression with optional tracing."
  ([expr ctx] (evaluate expr ctx nil))
  ([expr ctx opts]
   (if (:trace? opts)
     (evaluate-with-trace expr ctx)
     (evaluate-impl expr ctx))))

(defn evaluate-with-trace
  "Evaluate expression and collect trace of sub-expressions."
  [expr ctx]
  (let [trace (atom [])
        result (evaluate-traced expr ctx trace)]
    {:result result
     :trace @trace}))

(defn- evaluate-traced [expr ctx trace]
  (let [result (evaluate-impl expr ctx)]
    (swap! trace conj {:expr expr :result result})
    result))
```

### Usage
```clojure
(expr/evaluate '(and (>= [:actor :funds] 100) (= [:actor :status] :candidate))
               ctx
               {:trace? true})
;; => {:result false
;;     :trace [{:expr [:actor :funds] :result 50}
;;             {:expr (>= [:actor :funds] 100) :result false}
;;             {:expr [:actor :status] :result :nominee}
;;             {:expr (= [:actor :status] :candidate) :result false}
;;             {:expr (and ...) :result false}]}
```

---

## Implementation Order

### Phase 1: Quick Wins (1-2 days)
1. **Keyword param dropdowns** - Simple UI fix, high impact
2. **WebSocket error feedback** - Simple backend fix, high impact
3. **Icon/style helpers** - Add to loader.clj, immediate value

### Phase 2: Validation (2-3 days)
4. **Better error messages** - Validation + schema changes
5. **Compile-time icon/style validation** - Optional warnings

### Phase 3: Compiler (3-4 days)
6. **Conflict warning suppression** - DSL + compiler changes
7. **Warning severity levels** - Filter by level in output

### Phase 4: Advanced (future)
8. **Expression debugging** - Major expr.cljc changes
9. **DSL cookbook/examples** - Documentation effort

---

## Files Changed Summary

| File | Changes |
|------|---------|
| `src/cljc/parlameme/v3/validation.cljc` | Add keyword :values validation |
| `src/cljc/parlameme/v3/schema.cljc` | Add :values to DealParam schema |
| `src/clj/parlameme/v3/loader.clj` | Add list-icons, list-styles, describe |
| `src/cljs/parlameme/ui/spec/deals.cljs` | Add :keyword/:select case |
| `src/cljc/parlameme/v3/compiler/phases/generate.cljc` | Pass :values to UI spec |
| `src/clj/parlameme/game/sente.clj` | Send error to client in catch |
| `src/cljc/parlameme/v3/dsl.cljc` | Add :suppress-conflicts to phase |
| `src/cljc/parlameme/v3/compiler/phases/conflicts.cljc` | Filter suppressed conflicts |

---

## Verification Checklist

- [x] Error messages include valid values for keyword params ✅ (2026-02-03)
- [x] `(loader/list-icons)` works in REPL ✅ (2026-02-03)
- [x] `(loader/list-styles)` works in REPL ✅ (2026-02-03)
- [x] Keyword params with :values render as dropdown in UI ✅ (2026-02-03)
- [x] WebSocket errors show in UI instead of "Loading..." ✅ (2026-02-03)
- [x] Deal `:suppress-conflicts` reduces warning noise ✅ (2026-02-03)
- [x] All existing tests still pass ✅ (2026-02-03)

## Implementation Notes (2026-02-03)

All Phase 1, 2, and 3 improvements have been implemented:

### Phase 1: Quick Wins
1. **Keyword param dropdowns** - Modified `generate.cljc` to pass `:values`, `subs.cljs` to rename to `:options`, `deals.cljs` to add `:select` case
2. **WebSocket error feedback** - Modified `game/sente.clj` to send errors back to client via `v3-sente/send-v3-error!`
3. **Icon/style REPL helpers** - Added `list-icons`, `list-styles`, `describe` to `loader.clj`

### Phase 2: Validation
4. **Better error messages** - Modified `validation.cljc` to check `:values` for keyword params and include valid options in error
5. **Compile-time validation** - Added validation for `:values` field in `compiler/phases/validate.cljc`

### Phase 3: Conflict Suppression
6. **`:suppress-conflicts` on deals** - Added to schema and conflict detection. Warnings now include hint on how to silence.

### Note on Implementation
Conflict suppression was implemented at the deal level (`:suppress-conflicts [:other-deal]`) rather than phase level. This provides more granular control - you can acknowledge specific known conflicts without blanket suppression.
