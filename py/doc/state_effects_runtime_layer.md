# State, Effects & Runtime Layer

The runtime layer is the engine's execution core. It takes a frozen `CompiledGame` from the DSL layer and drives all game mechanics: phase advancement, deals, votes, messaging, speech acts, victory detection, and archive replay. The entire layer is pure and stateless — all state flows through immutable `GameState` instances.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  CompiledGame (frozen, from DSL layer)                          │
│  Resources, attrs, roles, phases, deals, votes, speech acts...  │
└────────────────────────────┬────────────────────────────────────┘
                             │ consumed by
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  GameRuntime (stateless — stores only compiled)                  │
│  THE single source of truth for all game logic                   │
│                                                                  │
│  ┌─────────┐  ┌───────┐  ┌───────┐  ┌──────────┐  ┌─────────┐ │
│  │ Phases   │  │ Deals │  │ Votes │  │ Speech   │  │ Victory │ │
│  │ advance  │  │ start │  │ start │  │ Acts     │  │ detect  │ │
│  │ cascade  │  │ respond│  │ cast  │  │ verify   │  │ score   │ │
│  └────┬─────┘  └───┬───┘  └───┬───┘  └────┬─────┘  └────┬────┘ │
│       └────────────┴──────────┴────────────┴─────────────┘      │
│                            │                                     │
│                            ▼                                     │
│              ┌──────────────────────────┐                        │
│              │  Effect System (open)    │                        │
│              │  @register_effect(Type)  │                        │
│              │  apply_effects(list,s,c) │                        │
│              └────────────┬─────────────┘                        │
│                           ▼                                      │
│              ┌──────────────────────────┐                        │
│              │  GameState (immutable)   │                        │
│              │  @attrs.frozen           │                        │
│              │  attrs.evolve() → new    │                        │
│              └──────────────────────────┘                        │
└─────────────────────────────────────────────────────────────────┘
```

## File Layout

| File | Purpose | Lines |
|------|---------|-------|
| `engine/runtime/state.py` | GameState, Entity, all Def types, view_for() | ~920 |
| `engine/runtime/effects.py` | 37 effect types + open registry + handlers | ~1220 |
| `engine/runtime/core.py` | GameRuntime — the single execution authority | ~1510 |
| `engine/runtime/rng.py` | Deterministic LCG for replay compatibility | ~50 |
| `engine/errors.py` | Error registry (E enum), Result types (Ok/Err) | ~200 |
| `engine/archive/__init__.py` | Archive create, replay, verify | ~410 |

---

## GameState — Immutable State Container

`GameState` is a `@attrs.frozen` dataclass — every mutation returns a new instance via `attrs.evolve()`. This guarantees:

- **Replay safety**: any state can be compared by value.
- **No hidden mutation**: effects can't corrupt shared state.
- **Time-travel debugging**: keep any intermediate state alive.

### Fields

| Field | Type | Purpose |
|-------|------|---------|
| `phase` / `phase_index` / `round` | str / int / int | Current position in phase cycle |
| `entities` | `dict[str, Entity]` | Players/NPCs with resources + attrs |
| `groups` | `dict[str, Group]` | Dynamic groups (wolf_pack, lovers) |
| `relations` | `dict[tuple, frozenset]` | Pairwise entity relations |
| `reveals` | `dict[tuple[str,str,str], Any]` | Visibility grants `(observer, entity, attr)` |
| `vars_` | `dict[str, Any]` | Game-level variables (prices, order book, etc.) |
| `pending_deals` | `dict[str, PendingDeal]` | Deals awaiting response |
| `pending_votes` | `dict[str, PendingVote]` | Votes in progress |
| `messages` | `tuple[Message, ...]` | Channel messages (first-class) |
| `pending_speech_acts` | `dict[str, PendingSpeechAct]` | Speech acts awaiting verification |
| `resolved_speech_acts` | `tuple[PendingSpeechAct, ...]` | Verified/expired speech acts |
| `history` | `tuple[HistoryEntry, ...]` | Game event log |
| `decisions` | `tuple[dict, ...]` | Replay-critical decisions |
| `usage` | `dict[str, dict[str, int]]` | Per-round/phase/game action limits |
| `seed` / `rng_state` | int / int | Deterministic RNG tracking |
| `status` / `victory_result` | str / dict | Game completion state |

### Functional Update Pattern

```python
# Every mutation returns a new state
new_state = state.set_resource("alice", "gold", 50)
new_state = state.adjust_resource("alice", "gold", -10, compiled)  # with bounds
new_state = state.set_attr("alice", "role", "seer")
new_state = state.set_game_var("current_lot", 3)
new_state = state.deactivate("bob")
new_state = state.add_reveal("alice", "bob", "role", True)
new_state, msg_id = state.next_id("msg")  # deterministic IDs
```

### Resource Bounds

Resources can declare bounds in `ResourceDef`:
```python
.resource("gold", initial=100, bounds=(0, None))  # min=0, no max
```
`set_resource()` and `adjust_resource()` clamp to bounds when a `CompiledGame` is provided.

### Reveal System

Visibility is controlled by three mechanisms:

1. **ResourceDef/AttrDef visibility**: `PUBLIC`, `PRIVATE`, `HIDDEN`
2. **Reveal grants**: `(observer, entity, attr) → value` in state.reveals
3. **Public reveals**: observer = `"public"` → visible to everyone

Helper functions:
```python
from engine.runtime.state import reveal_key, public_reveal_key, PUBLIC_OBSERVER

rk = reveal_key("alice", "bob", "role")       # ("alice", "bob", "role")
pk = public_reveal_key("bob", "role")           # ("public", "bob", "role")
```

`view_for(state, observer_id, compiled)` generates the filtered view:
- PUBLIC: always visible
- PRIVATE: visible only to owner
- HIDDEN: never visible (runtime-only)
- Reveals override visibility; `True` → real value, other → fake value
- Spectator mode (`SPECTATOR_ID`): PUBLIC only, no reveals, public channels

---

## Effect System — Tagged Union with Open Registry

Effects are **frozen dataclasses** (zero allocation overhead, pattern-matchable). Handlers are registered at import time via `@register_effect(Type)`.

### Effect Categories

| Category | Effects | Purpose |
|----------|---------|---------|
| **Resource** | `Transfer`, `Boost`, `Damage`, `SetResource` | Move/add/remove/set resources |
| **Entity** | `Eliminate`, `Reactivate`, `SetAttr`, `SetAdd`, `SetRemove` | Modify entity state |
| **Relation** | `Relate`, `Unrelate` | Pairwise entity relationships |
| **Group** | `CreateGroup`, `JoinGroup`, `LeaveGroup`, `DissolveGroup` | Dynamic group management |
| **Variable** | `SetVar`, `UpdateVar` | Game-level state mutations |
| **Communication** | `Broadcast`, `Notify`, `Emit`, `SendMessage`, `Reveal` | Messaging and visibility |
| **Stakes** | `ReturnStakes`, `TransferStakes`, `TransferStakesSplit`, `BurnStakes` | Deal escrow management |
| **Control flow** | `When`, `Each`, `Let`, `Cond`, `Maybe`, `Repeat` | Conditional/iterative effects |
| **Speech act** | `VerifySpeechAct`, `ResolveSpeechActs` | Force-verify pending acts |
| **Setup** | `AssignRoles`, `SetupVisibility`, `ResolveMarked` | Game initialization |

### Dynamic Resource Resolution

`Transfer`, `Boost`, `Damage`, and `SetResource` accept `str | Expr` for the resource field:
```python
Transfer("proposer", "responder", Ref("params", "asset"), Ref("params", "qty"))
```
The `_resolve_resource()` helper evaluates Expr resources at runtime.

### Entity Resolution

Effect handlers resolve entity references through a chain:
1. **Direct binding**: `ctx.bindings["actor"]` → entity ID
2. **Alias table**: `"self"` → `"actor"`, `"buyer"` → `"proposer"`, etc.
3. **Dynamic aliases**: custom party names from CompiledGame deals (cached per game)
4. **Literal fallback**: if nothing matches, treat ref as literal entity ID

Static aliases:
```python
"self" → "actor",  "giver" → "proposer",  "receiver" → "responder",
"sender" → "proposer",  "buyer" → "proposer",  "seller" → "responder"
```

Dynamic aliases (from deal party definitions):
- Party with `excludes` → maps to `"responder"`
- Party with `count` → maps to `"responders"`
- Cached per `CompiledGame` instance via `id(compiled)`

### Core Dispatch

```python
def apply_effect(effect, state, ctx: Context):
    handler = _effect_handlers.get(type(effect))
    return handler(effect, state, ctx)

def apply_effects(effects, state, ctx: Context):
    for effect in effects:
        ctx = Context(state=state, compiled=ctx.compiled, bindings=ctx.bindings)
        state = apply_effect(effect, state, ctx)
    return state
```

Key design: `apply_effects` rebuilds `Context` after each effect so that expressions evaluate against the **current** state, not the state before the first effect.

### Registering Custom Effects

Games can define custom effects without editing `effects.py`:
```python
from engine.runtime.effects import register_effect

@dataclass(frozen=True, slots=True)
class PlaceOrder:
    player: str
    asset: str
    side: str
    price: float
    qty: int

@register_effect(PlaceOrder)
def _apply_place_order(effect, state, ctx):
    # Custom logic here
    return new_state
```

### Control Flow Effects

| Effect | Behavior |
|--------|----------|
| `When(condition, effects)` | Execute effects if condition is truthy |
| `Each(binding, filter, effects)` | For each active entity matching filter |
| `Let(bindings, effects)` | Evaluate Expr bindings, add to context |
| `Cond(branches)` | Multi-branch conditional: first matching guard wins |
| `Maybe(probability, effects)` | Probabilistic via deterministic RNG |
| `Repeat(times, effects)` | Execute effects N times (times can be Expr) |

### UpdateVar — Deep Nested Mutations

`UpdateVar` navigates into `vars_` via a path and applies an operation:

| Operation | Behavior |
|-----------|----------|
| `set` | Set value at path |
| `append` / `prepend` | Add to list start/end |
| `append_max` | Append with max length (key = max size) |
| `remove` | Remove value from list |
| `remove_where` | Remove dict from list where field matches |
| `increment` / `decrement` | Numeric add/subtract |
| `sort_by` | Sort list of dicts by field |
| `clear` | Reset to empty list/dict |

Path elements can be Expr (evaluated at runtime for dynamic navigation).

---

## GameRuntime — Execution Authority

`GameRuntime` is the **single source of truth** for all game logic. It is stateless — stores only `self.compiled`. All state flows through parameters and return values.

### Lifecycle

```python
rt = GameRuntime(compiled)
state = rt.start_game(["alice", "bob", "charlie"])  # create entities
state = rt.run_setup(state)                          # setup effects + auto-advance
```

### Phase System

Phases form a cycle. `advance_phase()` cascades through automatic phases:

```python
def advance_phase(state) -> GameState:
    for _ in range(max_cascades=20):
        state = _advance_one(state)       # move to next non-skipped phase
        state = _run_entry_effects(state)  # execute phase.effects
        state = _cleanup_phase(state)      # reset usage + check speech acts
        if not phase.automatic:
            return state
    raise RuntimeError("cascade limit")
```

Phase skip rules:
- `category="setup"` and `round > 1` → skip
- `once=True` and already executed → skip
- `when` guard evaluates to falsy → skip

Phase cleanup (decoupled for testability):
- `_reset_phase_usage()`: removes `phase:*` keys from usage counters
- `_process_speech_acts_on_phase_change()`: triggers speech act checks, inquire deadlines, promise fulfillment

### Deal System

Two deal types flow through a single entry point:

```
start_deal()
    ├── Immediate (no responder party or no response_options)
    │   └── _execute_immediate_deal() → apply effects → done
    └── Bilateral/Multilateral (has responder + responses)
        └── _create_pending_deal() → PendingDeal in state
            └── respond_to_deal() → resolve → apply effects → done
```

**Outcome resolution** (`_resolve_outcome`): outcomes are sorted by priority (highest first). First outcome with a passing guard wins. Guardless outcomes serve as default fallback.

**Outcome guards**: checked both in `_execute_immediate_deal()` and `respond_to_deal()`. If guard fails, deal returns `GUARD_FAILED` error (pending deal stays pending, not consumed).

**Stakes**: resources locked on deal creation, returned/transferred/burned on resolution via `ReturnStakes`/`TransferStakes`/`BurnStakes` effects.

**PendingDeal** stores: `instance_id`, `deal_id`, `proposer`, `responders` (eid → response), `params`, `stakes`, and `target` (for 3-party deals).

**Multilateral completion rules**:
- `"all"` — unanimous, majority fallback
- `"majority"` — first to reach > 50% threshold
- `"any"` — first response resolves

### Vote System

```python
result = rt.start_vote(state, "expel", proposer_id="alice", subject_id="bob")
state = result["state"]
iid = result["instance_id"]

result = rt.cast_vote(state, iid, "alice", "yes")
# Auto-completes when all eligible voters have cast
```

Vote tallying:
- Weighted votes (float weights, not truncated to int)
- Thresholds: `plurality`, `majority`, `supermajority`, `unanimous`
- Tie detection: if top two options have equal count, `tie=True`, no winner

### Speech Act System

Six act types: `claim`, `accuse`, `promise`, `predict`, `endorse`, `inquire`.

Lifecycle:
```
execute_speech_act() → PendingSpeechAct in state
    ├── Endorsement chain: endorse_speech_act() adds endorsers
    ├── Inquire response: respond_to_inquire()
    └── Verification triggers (phase_change, eliminate, game_end, custom)
        └── _verify_speech_act() → verify_true/false effects + shared fate
```

Verification is delegated to `_verify_pending_speech_act()` in effects.py — the single canonical implementation shared between runtime triggers and effect-based triggers (`ResolveSpeechActs`).

### Commitment System

Commitments are declarative hooks that fire on game events:
```
_fire_commitments(trigger, entity_id, state)
    └── For each CommitmentDef matching trigger:
        ├── Check guard (Expr)
        ├── Apply effects
        └── Depth guard (max 5) prevents infinite recursion
```

Example: lover heartbreak — when one lover dies, the other dies too (via Eliminate effect in commitment, which can trigger more commitments).

### Victory Detection

Two victory types:
- **`single`**: first active entity matching `when` condition wins
- **`distribution`**: when `when` is true, compute `score` for all active entities. Highest score wins. Deterministic tie-breaking: sorted entity ID (alphabetical).

Victory conditions are checked in priority order (lowest priority number first).

### Usage Limits

Actions track usage at three granularities:
```
{actor_id}:{action_id} → {
    "round:{N}": count,      # reset on new round
    "phase:{phase_id}": count, # reset on phase change
    "game": count             # never reset
}
```

Phase usage keys (`phase:*`) are cleaned up in `_reset_phase_usage()` on phase transition.

---

## Result Type

All runtime methods return `Result = Ok | Err`:

```python
from engine.errors import Ok, Err, ok, fail, E

# Success
return ok(state, outcome="accept", instance_id="deal-0")

# Error
return fail(E.GUARD_FAILED, "sealed_bid/accept")
return fail(E.DEAL_NOT_ALLOWED, "sealed_bid", "preview")
```

Error codes are in `E` enum (str mixin for JSON serialization). Templates in `_TEMPLATES` dict provide human-readable messages with positional formatting.

---

## Deterministic RNG

Linear Congruential Generator with same parameters as the Clojure version:

```python
class DeterministicRNG:
    # a=1103515245, c=12345, m=2^31
    def next_int(self) -> tuple[int, DeterministicRNG]     # raw value + new rng
    def next_range(self, n) -> tuple[int, DeterministicRNG] # 0..n-1 + new rng
    def next_float(self) -> tuple[float, DeterministicRNG]  # 0.0-1.0 + new rng
    def shuffle(self, items) -> tuple[list, DeterministicRNG]
    def choice(self, items) -> tuple[Any, DeterministicRNG]
```

**Critical**: RNG is immutable. Every call returns `(value, new_rng)`. State tracks `rng_state` separately from `seed` — seed is for archive replay, rng_state tracks current position.

---

## Archive System

Archives capture the minimal representation for deterministic replay:

```python
Archive(game_id, rules_hash, seed, players, decisions)
```

The replay guarantee: given the same `CompiledGame` and `Archive`, `replay()` produces an identical `GameState` every time.

### Decision Types

| Type | Recorded When | Replayed Via |
|------|---------------|--------------|
| `deal` | start_deal (immediate or pending) | runtime.start_deal() |
| `respond` | respond_to_deal | runtime.respond_to_deal() |
| `start_vote` | start_vote | runtime.start_vote() |
| `vote` | cast_vote | runtime.cast_vote() |
| `advance_phase` | advance_phase | runtime.advance_phase() |
| `message` | send_message | runtime.send_message() |
| `speech_act` | execute_speech_act | runtime.execute_speech_act() |
| `endorse` | endorse_speech_act | runtime.endorse_speech_act() |
| `inquire_response` | respond_to_inquire | runtime.respond_to_inquire() |
| `timeout_advance` | phase timeout | runtime.advance_phase() |
| `timeout_expire_deal` | deal timeout | runtime.respond_to_deal("reject") |
| `timeout_auto_vote` | vote timeout | runtime.cast_vote() |

### Verification

```python
result = verify(archive, compiled)
# result: {valid, state, decisions_expected, decisions_replayed, failed, fingerprint}
```

Fingerprint is SHA-256 of game-relevant state (phase, round, entities, vars) — not the full state (excludes history, messages, etc.).

---

## view_for() — Presentation Layer

Pure function that generates a filtered state view for a specific observer:

```python
view = view_for(state, "alice", compiled)
# Returns: {round, phase, status, entities, messages, speech_acts, vars}
```

Each entity in the view contains only resources/attrs visible to the observer. Messages filtered by channel read permissions. Speech acts filtered by visibility (`public` / `private`).

### Channel Permissions

Two predicate functions for MCP/UI:
- `can_read_channel(state, observer, channel, compiled)` — used by view_for
- `can_write_channel(state, sender, channel, compiled)` — used by MCP tools

Both handle: public, group, private, broadcast, ad-hoc private (`private:a:b`), and Expr-based write filters.

---

## Key Design Decisions

### Why immutable state?

Immutability is load-bearing — it enables:
1. **Archive replay**: compare any two states by value
2. **Branch exploration**: keep multiple state versions alive (e.g., "what if" analysis)
3. **Effect composition**: effects can't corrupt each other's intermediate state
4. **Thread safety**: no locks needed for concurrent reads

### Why a single GameRuntime class?

Previous iterations used layered delegation (separate deal/vote/phase managers). This created:
- Circular dependencies between managers
- Inconsistent state threading
- Unclear source of truth

Single class = every method has the same `(self, state, ...) → Result` signature, same commitment callback injection, same usage tracking.

### Why open effect registry?

Games define custom effects (PlaceOrder, MatchOrders, CancelOrder) without editing engine code. The registry pattern means:
- Engine effects and game effects use the same dispatch
- Custom effects get archive replay for free
- `describe_effect()` in mechanics.py handles unknown types via docstring fallback

### Why tuple returns for RNG?

Mutable RNG would break replay. Every RNG call returns `(value, new_rng)` — the caller must thread the new RNG through subsequent calls. `GameState.rng_state` persists the position across turns.

---

## Common Patterns

### Building a Context

```python
ctx = self._ctx(state, actor=player_id, target=target_id, params={...})
# Equivalent to:
ctx = Context(state=state, compiled=self.compiled,
              bindings={"actor": player_id, "target": target_id, "params": {...}})
```

### Commitment Callback Injection

Effects that may trigger commitments (Eliminate) need the callback:
```python
ctx = self._ctx(state,
    _on_eliminate=lambda eid, s: self._fire_commitments("eliminate", eid, s)
)
```
Without this, raw `apply_effects([Eliminate("bob")])` won't trigger commitment chains.

### Record Decision for Replay

Every player action must call `state.record_decision(...)` for archive compatibility:
```python
state = state.record_decision({
    "type": "deal", "deal": deal_id, "proposer": actor_id,
    "target": target_id, "params": params,
})
```

### Deterministic ID Generation

```python
state, instance_id = state.next_id("deal")  # "deal-0", "deal-1", ...
state, msg_id = state.next_id("msg")         # "msg-0", "msg-1", ...
```
Counters are per-prefix, stored in `state.counters`.

---

## Gotchas

1. **`start_game()` doesn't run setup** — must call `run_setup()` explicitly. `run_setup()` applies setup phase effects AND advances to first interactive phase.

2. **`check_victory()` returns dict or None** — NOT a modified state. The caller (session layer) is responsible for calling `end_game(state, victory)`.

3. **Expr objects can't be used in `if expr:`** — `Expr.__bool__()` raises TypeError. Always use `if expr is not None:`.

4. **`DeterministicRNG.next_int()` returns raw int** — use `next_range(n)` for bounded values.

5. **Effect argument order**: `Boost(entity, resource, amount)`, `Transfer(source, target, resource, amount)`. Entity comes first.

6. **`view_for` is standalone** — `view_for(state, observer, compiled)`, NOT a method on GameState or GameRuntime.

7. **Phase usage keys use `phase:` prefix** — cleaned by `_reset_phase_usage()`. Round keys (`round:`) persist across phases.

8. **`_on_eliminate` must be injected** — commitment chains only fire when the callback is in context bindings. Phase entry effects, deal effects, and vote outcome effects all inject it.

9. **`PendingDeal.target` preserves target_id** — bilateral deals store target in PendingDeal so it's available when `respond_to_deal()` builds the resolution context.

10. **Speech act verification is canonical in effects.py** — `_verify_pending_speech_act()` is the single implementation. Both `GameRuntime._verify_speech_act()` and `ResolveSpeechActs` effect delegate to it.
