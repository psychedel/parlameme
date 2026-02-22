# Code Style Guide

Краткие правила для Clojure/Reagent/Re-frame в проекте.

---

## Clojure Idioms

**Data > Functions > Macros**

```clojure
;; Prefer threading
(-> state
    (update :x inc)
    (assoc :y 0))

;; Prefer for over nested map/filter
(for [[k v] items
      :when (pos? v)]
  [k (* 2 v)])

;; Prefer transducers for efficiency
(into [] (comp (filter pos?) (map inc)) coll)

;; Use pre-conditions for invariants
(defn transfer [state from to amount]
  {:pre [(pos? amount) (>= (get-resource state from) amount)]}
  ...)
```

---

## Re-frame Subscriptions

**Layer 2 (extractors)** — прямой доступ к db:
```clojure
(rf/reg-sub :game/phase (fn [db _] (get-in db [:game :phase])))
```

**Layer 3 (derived)** — композиция через `:<-`:
```clojure
(rf/reg-sub
 :game/alive-players
 :<- [:game/players]
 (fn [players _] (filter :alive players)))
```

**Indexed lookups** для O(1) доступа:
```clojure
(rf/reg-sub :deals-by-id :<- [:deals] 
  (fn [deals _] (into {} (map (juxt :id identity)) deals)))

(rf/reg-sub :deal-by-id :<- [:deals-by-id]
  (fn [index [_ id]] (get index id)))
```

---

## Reagent Components

**Container/Presentation pattern:**

```clojure
;; Container — подписывается, передаёт данные
(defn player-list []
  (let [players @(rf/subscribe [:players])
        selected @(rf/subscribe [:selected])]
    [:div
     (for [p players]
       ^{:key (:id p)}
       [player-card p {:selected? (= (:id p) selected)}])]))

;; Presentation — чистый, без подписок
(defn player-card [{:keys [id name alive]} {:keys [selected?]}]
  [:div {:class (when selected? "selected")}
   [:span name]
   (when-not alive [:span "dead"])])
```

---

## Effects & Events

**Pure handlers:**
```clojure
(rf/reg-event-fx
 :start-deal
 (fn [{:keys [db]} [_ deal-id target]]
   {:db (assoc db :pending-deal deal-id)
    :sente/send [:v3/start-deal {:deal-id deal-id :target target}]}))
```

**Custom effects:**
```clojure
(rf/reg-fx :sente/send (fn [msg] (sente/send! msg)))
```

---

## Multimethod Extension

```clojure
;; Define extensible dispatch
(defmulti execute-effect (fn [effect _ctx _state] (first effect)))

;; Add handlers
(defmethod execute-effect :transfer [effect ctx state] ...)
(defmethod execute-effect :boost [effect ctx state] ...)
(defmethod execute-effect :default [effect _ state]
  (log/warn "Unknown effect" effect)
  state)
```

---

## Naming

| Type | Convention | Example |
|------|------------|---------|
| Namespace | lowercase, dots | `parlameme.v3.runtime.core` |
| Function | kebab-case | `make-initial-state` |
| Predicate | ends with `?` | `alive?`, `ok?` |
| Private | prefix `-` | `(defn- helper ...)` |
| Constant | kebab-case | `default-timeout` |
| Multimethod | kebab-case | `execute-effect` |
