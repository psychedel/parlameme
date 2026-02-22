# DSL & Expression Layer

The expression system and DSL builder form the foundation of the Parlameme game engine. Every game mechanic — guards, effects, victory conditions, deal logic — is built from a typed AST that composes at definition time and evaluates at runtime.

## Architecture

```
Game Author (Python)
    │
    ▼
┌─────────────────────────────────────────────────┐
│  DSL Builder (engine/dsl/builder.py)            │
│  Game("id").resource(...).deal(...).build()      │
└────────────────────┬────────────────────────────┘
                     │ produces
                     ▼
┌─────────────────────────────────────────────────┐
│  CompiledGame (frozen attrs class)              │
│  All definitions stored as frozen dataclasses    │
└────────────────────┬────────────────────────────┘
                     │ consumed by
          ┌──────────┼──────────┐
          ▼          ▼          ▼
      Runtime    Archive     MCP Schema
      (effects)  (replay)   (tool generation)
```

## File Layout

| File | Purpose | Lines |
|------|---------|-------|
| `engine/expr/core.py` | AST node types + operator overloading | ~250 |
| `engine/expr/evaluator.py` | Tree-walking interpreter + built-in functions | ~750 |
| `engine/expr/functions.py` | DSL convenience functions (AST constructors) | ~175 |
| `engine/expr/registry.py` | Open function registry | ~80 |
| `engine/expr/__init__.py` | Public API re-exports | ~30 |
| `engine/dsl/builder.py` | Fluent `Game(...)` builder | ~650 |

---

## Expression AST

Every expression is a **frozen dataclass** that builds an AST at definition time. No evaluation happens until runtime. This means expressions are data — they can be stored, serialized, and introspected.

### Node Types

| Node | Purpose | Example |
|------|---------|---------|
| `Ref(parts)` | Path reference to state | `Ref("actor", "role")` |
| `Lit(value)` | Literal value | `Lit(100)`, `Lit("seer")` |
| `Cmp(op, lhs, rhs)` | Comparison (==, !=, >, >=, <, <=) | `actor.role == "seer"` |
| `Arith(op, lhs, rhs)` | Arithmetic (+, -, *, /) | `actor.gold + 50` |
| `And(exprs)` | Logical AND (n-ary) | `alive() & (actor.role == "wolf")` |
| `Or(exprs)` | Logical OR (n-ary) | `dead(target) \| (target.team == "evil")` |
| `Not(inner)` | Logical NOT | `~alive(target)` |
| `Call(fn, args)` | Function call (open registry) | `count_where(alive())` |
| `If(cond, then_, else_)` | Conditional expression | `If(cond, Lit(10), Lit(0))` |

### Operator Overloading

Python operators build AST nodes instead of computing values:

```python
from engine.expr import actor, target, alive, count_where

# Comparison → Cmp node
actor.role == "seer"           # Cmp("==", Ref("actor","role"), Lit("seer"))

# Arithmetic → Arith node
actor.gold + 50                # Arith("+", Ref("actor","gold"), Lit(50))

# Logic → And/Or/Not (use &, |, ~ because Python can't override and/or/not)
alive() & (actor.role == "wolf")  # And(Call("alive?"), Cmp(...))

# Attribute access on Ref → extends path
actor.team                     # Ref("actor", "team")
game.prices.alpha              # Ref("game", "prices", "alpha")
params.amount                  # Ref("params", "amount")
```

### Critical Gotchas

**1. `__eq__` returns Expr, not bool.**
```python
expr = actor.role == "seer"    # This is a Cmp node, NOT a boolean
# Do NOT use in `if expr:` — always use `if expr is not None:`
```

**2. `__bool__` raises TypeError.**
```python
if actor.role == "seer":       # TypeError!
if (actor.role == "seer") is not None:  # Correct way to check existence
```

**3. Use `&` / `|` / `~` for logic, not `and` / `or` / `not`.**
```python
alive() and (actor.role == "wolf")    # WRONG — Python short-circuits
alive() & (actor.role == "wolf")      # Correct — builds And node
```

**4. N-ary flattening.** `And`/`Or` automatically flatten nested instances:
```python
a & b & c      # And((a, b, c)) — flat tuple, not nested And(And(a,b), c)
```

**5. `__hash__` uses `id()`** because `__eq__` is overridden. Expr objects cannot be used as dict keys or in sets by value — they compare by identity.

---

## Predefined Context Variables

The module exports pre-built `Ref` singletons for common binding roots:

```python
from engine.expr import actor, target, game, proposer, responder, subject, self_, params
```

| Variable | Binding | Used in |
|----------|---------|---------|
| `actor` | `Ref("actor")` | Deal initiator, vote actor, filter context |
| `target` | `Ref("target")` | Deal target party |
| `proposer` | `Ref("proposer")` | Bilateral deal proposer |
| `responder` | `Ref("responder")` | Bilateral deal responder |
| `subject` | `Ref("subject")` | Vote subject, speech act subject |
| `game` | `Ref("game")` | Game-level variables |
| `self_` | `Ref("self")` | Self-referential binding (Python `self` is reserved) |
| `params` | `Ref("params")` | Deal/vote parameters |

The `params` singleton enables clean parameter access:
```python
# Before:
Transfer(Ref("params", "asset"), Ref("actor"), Ref("target"), Ref("params", "qty"))

# After:
Transfer(params.asset, Ref("actor"), Ref("target"), params.qty)
```

---

## Path Resolution

When the evaluator encounters a `Ref`, it resolves the path through a priority chain:

```
Ref("X", "Y", "Z")
    │
    ▼
1. Bindings (actor, target, proposer, responder, etc.)
   → If X is a bound entity ID: look up Y.Z on that entity
   → If X is a bound dict: navigate X[Y][Z]
    │
    ▼
2. Game vars (if X == "game")
   → state.get_game_var(Y), then navigate into Z
    │
    ▼
3. Params (if X == "params")
   → bindings["params"][Y], then navigate into Z
    │
    ▼
4. Keyword fallback (bare Ref with no path segments)
   → Check params[X], then game vars[X], then actor's property X
```

Deep paths work for nested data structures:
```python
game.prices.alpha              # state.vars_["prices"]["alpha"]
params.subjects.first          # bindings["params"]["subjects"]["first"]
```

List indexing is supported through the evaluator's path resolution (dict/list navigation), but `Ref` paths are strings — use `get_var()` for dynamic or numeric keys:
```python
get_var("order_book", Lit(0))  # state.vars_["order_book"][0]
```

---

## Functions

Functions are registered in an open registry and dispatched by name at evaluation time.

### Using Functions in Game Definitions

Functions in `engine/expr/functions.py` are factory functions that return `Call` AST nodes:

```python
from engine.expr import alive, count_where, resource_of, find_by_role

# Predicates
alive()                        # Call("alive?", ())         — is actor alive?
alive(target)                  # Call("alive?", (target,))  — is target alive?

# Aggregations
count_where(alive())           # Count all alive entities
count_where(actor.team == "mafia")  # Count mafia members

# Lookups
resource_of(actor, "gold")     # Get actor's gold
find_by_role("seer")           # Find entity with role "seer"
```

### Built-in Function Reference

#### Predicates (return bool)

| Function | Signature | Description |
|----------|-----------|-------------|
| `alive(entity?)` | 0-1 args | Entity is active (default: actor) |
| `dead(entity?)` | 0-1 args | Entity is inactive |
| `active(entity?)` | 0-1 args | Alias for alive |
| `inactive(entity?)` | 0-1 args | Alias for dead |
| `in_group(group)` | 1 arg | Actor is in named group |
| `in_group_type(type)` | 1 arg | Actor is in any group of type |
| `same_group(a, b)` | 2 args | Two entities share a group |
| `has_relation(entity, rel)` | 2 args | Actor has relation to entity |
| `phase_is(phase_id)` | 1 arg | Current phase matches |
| `some(expr)` | 1 arg | Value is not None |
| `contains(coll, val)` | 2 args | Collection contains value |
| `not_contains(coll, val)` | 2 args | Collection doesn't contain value |
| `every_in_group(group, pred)` | 2 args | All group members match predicate |

#### Aggregations (return number)

| Function | Signature | Description |
|----------|-----------|-------------|
| `count_where(pred)` | 1 arg | Count entities matching predicate |
| `count_alive()` | 0 args | Count active entities |
| `count_active()` | 0 args | Alias for count_alive |
| `count_group(group)` | 1 arg | Count members in group |
| `count_in_group(group)` | 1 arg | Alias for count_group |
| `count_not_in_group(group)` | 1 arg | Active entities not in group |
| `sum_resource(res)` | 1 arg | Sum resource across active entities |
| `avg_resource(res)` | 1 arg | Average resource across active entities |
| `max_resource(res)` | 1 arg | Max resource across active entities |
| `min_resource(res)` | 1 arg | Min resource across active entities |

#### Lookups (return entity ID, list, or value)

| Function | Signature | Description |
|----------|-----------|-------------|
| `find_by_role(role)` | 1 arg | First entity with role |
| `find_by_attr(attr, val)` | 2 args | First entity with attr=val |
| `filter_by_team(team)` | 1 arg | All entities with team |
| `filter_by_attr(attr, val)` | 2 args | All entities with attr=val |
| `resource_of(entity, res)` | 2 args | Get entity's resource value |
| `length(coll)` | 1 arg | Length of collection |

#### Collection Operations (for nested var data)

| Function | Signature | Description |
|----------|-----------|-------------|
| `get_var(key, ...)` | 1-10 args | Get nested game var by key path |
| `list_length(coll)` | 1 arg | Alias for length |
| `sort_by(list, field, dir?)` | 2-3 args | Sort list of dicts by field |
| `filter_where(list, field, val)` | 3 args | Filter list by field=value |
| `map_field(list, field)` | 2 args | Extract field from list of dicts |
| `aggregate(list, field, op)` | 3 args | Aggregate field (sum/max/min/avg/count) |
| `best(list, field, mode?)` | 2-3 args | Dict with max/min field value |
| `index_of(list, field, val)` | 3 args | Find index where field=value (-1 if not found) |

#### Speech Act Functions

| Function | Signature | Description |
|----------|-----------|-------------|
| `has_pending_claim(entity, type?)` | 1-2 args | Entity has unverified speech act |
| `count_endorsements(instance_id)` | 1 arg | Count endorsers of speech act |
| `claim_verified(entity, result?)` | 1-2 args | Entity has verified claims |
| `has_pending_inquire(target)` | 1 arg | Target has unanswered inquire |

### Registering Custom Functions

Games can extend the expression language without editing the evaluator:

```python
from engine.expr.registry import fn_registry

@fn_registry.register("my_func", doc="Description for AI agents", min_args=1, max_args=2)
def _my_func(args, ctx):
    """Handler receives (args: tuple[Expr], ctx: Context) — must evaluate args manually."""
    entity_id = evaluate(args[0], ctx)
    # ... custom logic ...
    return result
```

The `doc` parameter is used by `mcp/mechanics.py` to generate human-readable descriptions for AI agents.

---

## Nil-Safe Arithmetic

The evaluator coerces `None` to `0.0` in all arithmetic and comparison operations:

```python
_num(None)   # → 0.0
_num(True)   # → 1.0
_num(False)  # → 0.0
_num("abc")  # → 0.0  (can't convert)
```

Division by zero returns `0.0` instead of raising.

This means expressions never crash on missing data — a missing resource or unset variable simply evaluates as zero.

---

## DSL Builder

The `Game` class provides a fluent builder for game definitions:

```python
from engine.dsl.builder import Game
from engine.expr import actor, target, params, alive, count_alive
from engine.runtime.effects import Transfer, Boost, Eliminate, Cond
from engine.runtime.state import OutcomeDef, ParamDef

game = (
    Game("duel", "Duel Arena", players=(2, 4))
    .resource("health", initial=100, visibility="public")
    .resource("gold", initial=50, visibility="private")
    .attr("role", visibility="private", values=("warrior", "mage"), distribute=True)
    .deal(
        "attack",
        actor=alive(),
        target=alive(),
        params={"damage": ParamDef(type="int", min=1, max=20)},
        effects=[
            Transfer("gold", actor, target, Lit(5)),
            Cond([
                (params.damage > 10, (Boost("health", target, params.damage * -2),)),
                (None,               (Boost("health", target, params.damage * -1),)),
            ]),
        ],
    )
    .phase("combat", allows=["attack"])
    .victory("last_standing", when=count_alive() <= 1, type="elimination")
    .build()
)
```

### Builder Methods

| Method | Purpose |
|--------|---------|
| `.resource(id, initial, visibility, bounds, transferable)` | Define a numeric resource |
| `.attr(id, initial, visibility, values, distribute)` | Define an attribute |
| `.role(id, team, ...)` / `.roles({...})` | Define roles |
| `.group(id, visible, exclusive, ...)` | Define a group type |
| `.deal(id, actor, target, proposer, responder, ...)` | Define a deal (action/interaction) |
| `.vote(id, voters, proposer, subject, options, ...)` | Define a vote |
| `.phase(id, allows, effects, automatic, once, ...)` | Define a phase |
| `.victory(id, when, type, score, ...)` | Define a victory condition |
| `.channel(id, type, group, ...)` | Define a communication channel |
| `.commitment(id, trigger, effect, ...)` | Define a commitment |
| `.speech_act(id, actor, ...)` | Define a speech act |
| `.context()` | Define AI context hints |
| `.build()` | Compile into frozen `CompiledGame` |

### Deal Shorthands

The builder infers deal structure from which keyword arguments are provided:

```python
# Single-party action (actor only)
.deal("attack", actor=alive(), effects=[...])

# Bilateral deal (proposer + responder)
.deal("trade", proposer=alive(), responder=alive(), outcomes={
    "accept": OutcomeDef(effects=(...)),
    "reject": OutcomeDef(effects=()),
})

# Bilateral with target (proposer + responder + target parameter)
.deal("bribe", proposer=alive(), responder=alive(),
      target=alive(),  # target becomes a party
      outcomes={...})

# With immediate effects (no response needed)
.deal("cast_spell", actor=alive(), effects=[Boost("mana", actor, Lit(-10))])
```

### Build-Time Validation

`build()` performs best-effort validation of expression bindings:

- Collects all `Ref` roots used in effects, guards, and outcomes
- Compares against available bindings for each deal/vote/speech_act
- Emits `warnings.warn()` for unknown bindings (does not break the build)
- Known builtins (`game`, `params`, `claim`, `self`) are always allowed
- Single-character refs (from `Each` bindings like `"p"`) are skipped

This catches common mistakes like referencing `responder` in a single-party deal.

---

## Evaluation Context

The `Context` dataclass carries all state needed for expression evaluation:

```python
@dataclass
class Context:
    state: GameState          # Current immutable game state
    compiled: CompiledGame    # Game definition (for metadata)
    bindings: dict[str, Any]  # Named bindings (actor, target, params, etc.)

    def with_binding(key, value) -> Context     # Create new context with extra binding
    def with_bindings(**kwargs) -> Context       # Create new context with multiple bindings
    def resolve_path(parts) -> Any              # Resolve Ref path against state
    def get_active_entities() -> list[str]      # All active entity IDs
    def get_all_entities() -> list[str]         # All entity IDs (including inactive)
```

### Tracing

For debugging, use `evaluate_with_trace()` to get intermediate results:

```python
from engine.expr import evaluate_with_trace

result, trace = evaluate_with_trace(expr, ctx)
for entry in trace:
    print(f"{entry.expr!r} → {entry.result!r}")
```

---

## Patterns for Game Authors

### Pattern 1: Conditional Effects with Cond

```python
from engine.runtime.effects import Cond

Cond([
    (actor.role == "warrior", (Boost("health", actor, Lit(20)),)),
    (actor.role == "mage",    (Boost("mana", actor, Lit(20)),)),
    # Else branch is optional — omit the (None, (...)) if nothing should happen
])
```

### Pattern 2: Dynamic Resources via Expressions

Effect targets can be Expr values resolved at runtime:

```python
# Resource name from params
Transfer(params.asset, actor, target, params.qty)

# Entity from lookup
Boost("gold", find_by_role("king"), Lit(100))
```

### Pattern 3: Response Outcome Guards

Bilateral deals can validate responder state before applying effects:

```python
.deal("trade",
    proposer=alive(), responder=alive(),
    params={
        "asset": ParamDef(type="choice", values=("gold", "silver")),
        "qty": ParamDef(type="int", min=1, max=100),
    },
    outcomes={
        "accept": OutcomeDef(
            effects=(...),
            guard=resource_of(responder, params.asset) >= params.qty,
        ),
        "reject": OutcomeDef(effects=()),
    },
)
```

If the guard fails, the deal stays pending (not consumed), and the responder gets a `GUARD_FAILED` error.

### Pattern 4: Param Access Sugar

Use the `params` singleton for clean parameter references:

```python
from engine.expr import params

# These are equivalent:
Ref("params", "amount")        # Verbose
params.amount                  # Clean — builds Ref("params", "amount")

# Chain for nested params:
params.target.name             # Ref("params", "target", "name")
```

### Pattern 5: Game Variables via Deep Paths

Access nested game state through `game` references:

```python
from engine.expr import game
from engine.expr.functions import get_var

# Direct path reference
game.round                     # Ref("game", "round")
game.prices.alpha              # Ref("game", "prices", "alpha")

# Function for dynamic keys
get_var("order_book")          # Call("get_var", (Lit("order_book"),))
```

### Pattern 6: Composite Guards

Build complex guards with operator composition:

```python
guard = (
    alive()
    & (actor.role == "detective")
    & (actor.investigations > 0)
    & ~has_relation(target, "investigated")
)
```

---

## How Other Layers Consume Expressions

### Runtime (effects.py)

Effects receive expressions and evaluate them against a Context:

```python
# In an effect handler:
amount = evaluate(effect.amount, ctx)  # Expr → Python value
resource = evaluate(effect.resource, ctx) if isinstance(effect.resource, Expr) else effect.resource
```

### Archive (replay)

Expressions are part of the CompiledGame definition, which is frozen. Archives store only the seed + decisions — on replay, the same expressions evaluate against the same state to produce identical results.

### MCP (mechanics.py)

The `describe_expr()` function converts AST back to human-readable text for AI agents:

```python
describe_expr(actor.gold >= 100)   # → "actor's gold >= 100"
describe_expr(count_where(alive())) # → "count of entities where Check if entity is active"
```

For registered functions, it uses the `doc` field from the function registry as a fallback description.

---

## Testing Expressions

```python
from engine.expr import evaluate, Context, Ref, Lit, actor, alive
from engine.runtime.state import GameState, CompiledGame

# Build a minimal context
state = GameState(...)  # or get from GameRuntime
ctx = Context(state=state, compiled=compiled, bindings={"actor": "alice"})

# Evaluate
result = evaluate(actor.role == "seer", ctx)  # → True/False
result = evaluate(alive(), ctx)                # → True/False
result = evaluate(actor.gold + 50, ctx)        # → float
```

For game-level tests, prefer using `GameRuntime` which builds contexts automatically:

```python
rt = GameRuntime(compiled_game)
state = rt.start_game(["alice", "bob", "charlie"])
# Runtime methods internally create Context and evaluate expressions
```
