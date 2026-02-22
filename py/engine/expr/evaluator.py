"""Expression evaluator — tree-walking interpreter with pattern matching.

Evaluates Expr AST nodes against a Context that provides entity state,
game variables, and resource lookups.

Functions are dispatched via the open registry (engine.expr.registry).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from engine.expr.core import (
    And,
    Arith,
    Call,
    Cmp,
    Expr,
    If,
    Lit,
    Not,
    Or,
    Ref,
)
from engine.expr.registry import fn_registry

if TYPE_CHECKING:
    from engine.runtime.state import CompiledGame, GameState


# ---------------------------------------------------------------------------
# Evaluation context
# ---------------------------------------------------------------------------


@dataclass
class Context:
    """Runtime context for expression evaluation."""

    state: GameState
    compiled: CompiledGame
    bindings: dict[str, Any] = field(default_factory=dict)

    def with_binding(self, key: str, value: Any) -> Context:
        new_bindings = {**self.bindings, key: value}
        return Context(state=self.state, compiled=self.compiled, bindings=new_bindings)

    def with_bindings(self, **kwargs: Any) -> Context:
        new_bindings = {**self.bindings, **kwargs}
        return Context(state=self.state, compiled=self.compiled, bindings=new_bindings)

    def resolve_path(self, parts: tuple[str, ...]) -> Any:
        """Resolve a dotted path like ("actor", "role") against state."""
        if not parts:
            return None

        root = parts[0]
        rest = parts[1:]

        # Check bindings first (actor, target, proposer, etc.)
        if root in self.bindings:
            val = self.bindings[root]
            if isinstance(val, str) and not rest:
                return val
            if isinstance(val, str) and rest:
                return self.state.get_entity_property(val, rest, self.compiled)
            if isinstance(val, dict):
                result = val
                for key in rest:
                    if isinstance(result, dict):
                        result = result.get(key)
                    else:
                        return None
                return result
            if not rest:
                return val
            return None

        # Game-level references
        if root == "game":
            if not rest:
                return None
            val = self.state.get_game_var(rest[0])
            for key in rest[1:]:
                if isinstance(val, dict):
                    val = val.get(key)
                elif isinstance(val, (list, tuple)):
                    try:
                        val = val[int(key)]
                    except (ValueError, IndexError):
                        val = None
                else:
                    val = None
                if val is None:
                    break
            return val

        # Params
        if root == "params":
            if not rest:
                return self.bindings.get("params")
            params = self.bindings.get("params", {})
            val = params.get(rest[0])
            for key in rest[1:]:
                if isinstance(val, dict):
                    val = val.get(key)
                elif isinstance(val, (list, tuple)):
                    try:
                        val = val[int(key)]
                    except (ValueError, IndexError):
                        val = None
                else:
                    val = None
                if val is None:
                    break
            return val

        # Plain keyword — check params, then game vars
        if not rest:
            params = self.bindings.get("params", {})
            if root in params:
                return params[root]
            game_val = self.state.get_game_var(root)
            if game_val is not None:
                return game_val
            actor_id = self.bindings.get("actor")
            if actor_id and isinstance(actor_id, str):
                return self.state.get_entity_property(actor_id, (root,), self.compiled)
            return None

        return None

    def get_active_entities(self) -> list[str]:
        return self.state.get_active_entity_ids()

    def get_all_entities(self) -> list[str]:
        return list(self.state.entities.keys())


# ---------------------------------------------------------------------------
# Core evaluator
# ---------------------------------------------------------------------------


def _num(x: Any) -> float:
    """Nil-safe numeric coercion: None -> 0."""
    if x is None:
        return 0.0
    if isinstance(x, bool):
        return 1.0 if x else 0.0
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def evaluate(expr: Expr | Any, ctx: Context) -> Any:
    """Evaluate an expression against context. Returns Python value."""
    if not isinstance(expr, Expr):
        return expr

    match expr:
        case Lit(value):
            return value

        case Ref(parts):
            return ctx.resolve_path(parts)

        case Cmp(op, lhs, rhs):
            l = evaluate(lhs, ctx)
            r = evaluate(rhs, ctx)
            match op:
                case "==":
                    return l == r
                case "!=":
                    return l != r
                case ">":
                    return _num(l) > _num(r)
                case ">=":
                    return _num(l) >= _num(r)
                case "<":
                    return _num(l) < _num(r)
                case "<=":
                    return _num(l) <= _num(r)
                case _:
                    raise ValueError(f"Unknown comparison: {op}")

        case Arith(op, lhs, rhs):
            l = _num(evaluate(lhs, ctx))
            r = _num(evaluate(rhs, ctx))
            match op:
                case "+":
                    return l + r
                case "-":
                    return l - r
                case "*":
                    return l * r
                case "/":
                    return l / r if r != 0 else 0.0
                case _:
                    raise ValueError(f"Unknown arithmetic: {op}")

        case And(exprs):
            return all(evaluate(e, ctx) for e in exprs)

        case Or(exprs):
            return any(evaluate(e, ctx) for e in exprs)

        case Not(inner):
            return not evaluate(inner, ctx)

        case If(condition, then_, else_):
            if evaluate(condition, ctx):
                return evaluate(then_, ctx)
            return evaluate(else_, ctx)

        case Call(fn, args):
            return fn_registry.call(fn, args, ctx)

        case _:
            raise ValueError(f"Unknown expression type: {type(expr).__name__}")


# ---------------------------------------------------------------------------
# Built-in function registrations
# ---------------------------------------------------------------------------

# -- Predicates --


@fn_registry.register("alive?", doc="Check if entity is active", max_args=1)
def _alive(args, ctx):
    if args:
        entity_id = evaluate(args[0], ctx)
        return ctx.state.is_active(entity_id) if isinstance(entity_id, str) else False
    actor_id = ctx.bindings.get("actor")
    return ctx.state.is_active(actor_id) if actor_id else False


@fn_registry.register("active?", doc="Alias for alive?", max_args=1)
def _active(args, ctx):
    return _alive(args, ctx)


@fn_registry.register("dead?", doc="Check if entity is inactive", max_args=1)
def _dead(args, ctx):
    if args:
        entity_id = evaluate(args[0], ctx)
        return (
            not ctx.state.is_active(entity_id) if isinstance(entity_id, str) else True
        )
    actor_id = ctx.bindings.get("actor")
    return not ctx.state.is_active(actor_id) if actor_id else True


@fn_registry.register("inactive?", doc="Alias for dead?", max_args=1)
def _inactive(args, ctx):
    return _dead(args, ctx)


@fn_registry.register(
    "in_group?", doc="Check if actor is in group", min_args=1, max_args=1
)
def _in_group(args, ctx):
    group = evaluate(args[0], ctx)
    actor_id = ctx.bindings.get("actor")
    return ctx.state.entity_in_group(actor_id, group) if actor_id else False


@fn_registry.register(
    "in_group_type?", doc="Check if actor is in group type", min_args=1, max_args=1
)
def _in_group_type(args, ctx):
    group_type = evaluate(args[0], ctx)
    actor_id = ctx.bindings.get("actor")
    return ctx.state.entity_in_group_type(actor_id, group_type) if actor_id else False


@fn_registry.register(
    "same_group?", doc="Check if two entities share a group", min_args=2, max_args=2
)
def _same_group(args, ctx):
    a = evaluate(args[0], ctx)
    b = evaluate(args[1], ctx)
    return ctx.state.in_same_group(a, b)


@fn_registry.register(
    "has_relation?", doc="Check if actor has relation to entity", min_args=2, max_args=2
)
def _has_relation(args, ctx):
    entity = evaluate(args[0], ctx)
    relation = evaluate(args[1], ctx)
    actor_id = ctx.bindings.get("actor")
    return ctx.state.has_relation(actor_id, entity, relation) if actor_id else False


@fn_registry.register("phase?", doc="Check current phase", min_args=1, max_args=1)
def _phase(args, ctx):
    phase_id = evaluate(args[0], ctx)
    return ctx.state.phase == phase_id


@fn_registry.register("some?", doc="Check if value is not None", min_args=1, max_args=1)
def _some(args, ctx):
    return evaluate(args[0], ctx) is not None


@fn_registry.register(
    "contains?", doc="Check if collection contains value", min_args=2, max_args=2
)
def _contains(args, ctx):
    coll = evaluate(args[0], ctx)
    val = evaluate(args[1], ctx)
    if isinstance(coll, (set, frozenset, list, tuple)):
        return val in coll
    return False


@fn_registry.register(
    "not_contains?",
    doc="Check if collection does not contain value",
    min_args=2,
    max_args=2,
)
def _not_contains(args, ctx):
    coll = evaluate(args[0], ctx)
    val = evaluate(args[1], ctx)
    if isinstance(coll, (set, frozenset, list, tuple)):
        return val not in coll
    return True


@fn_registry.register(
    "every_in_group?",
    doc="Check if all group members match predicate",
    min_args=2,
    max_args=2,
)
def _every_in_group(args, ctx):
    group = evaluate(args[0], ctx)
    pred = args[1]  # unevaluated — used as filter
    members = ctx.state.get_group_members(group)
    return all(evaluate(pred, ctx.with_binding("actor", m)) for m in members)


# -- Aggregations --


@fn_registry.register(
    "count_where", doc="Count entities matching predicate", min_args=1, max_args=1
)
def _count_where(args, ctx):
    pred = args[0]
    return sum(
        1
        for eid in ctx.get_all_entities()
        if evaluate(pred, ctx.with_binding("actor", eid))
    )


@fn_registry.register("count_alive", doc="Count active entities", max_args=0)
def _count_alive(args, ctx):
    return len(ctx.get_active_entities())


@fn_registry.register("count_active", doc="Alias for count_alive", max_args=0)
def _count_active(args, ctx):
    return len(ctx.get_active_entities())


@fn_registry.register(
    "count_group", doc="Count members in group", min_args=1, max_args=1
)
def _count_group(args, ctx):
    group = evaluate(args[0], ctx)
    return len(ctx.state.get_group_members(group))


@fn_registry.register(
    "count_in_group", doc="Count members in group", min_args=1, max_args=1
)
def _count_in_group(args, ctx):
    group = evaluate(args[0], ctx)
    return len(ctx.state.get_group_members(group))


@fn_registry.register(
    "count_not_in_group",
    doc="Count active entities not in group",
    min_args=1,
    max_args=1,
)
def _count_not_in_group(args, ctx):
    group = evaluate(args[0], ctx)
    members = ctx.state.get_group_members(group)
    return sum(1 for eid in ctx.get_active_entities() if eid not in members)


@fn_registry.register(
    "sum_resource", doc="Sum resource across active entities", min_args=1, max_args=1
)
def _sum_resource(args, ctx):
    resource = evaluate(args[0], ctx)
    return sum(
        ctx.state.get_resource(eid, resource) for eid in ctx.get_active_entities()
    )


@fn_registry.register(
    "avg_resource",
    doc="Average resource across active entities",
    min_args=1,
    max_args=1,
)
def _avg_resource(args, ctx):
    resource = evaluate(args[0], ctx)
    entities = ctx.get_active_entities()
    if not entities:
        return 0.0
    return sum(ctx.state.get_resource(eid, resource) for eid in entities) / len(
        entities
    )


@fn_registry.register(
    "max_resource", doc="Max resource across active entities", min_args=1, max_args=1
)
def _max_resource(args, ctx):
    resource = evaluate(args[0], ctx)
    entities = ctx.get_active_entities()
    return max((ctx.state.get_resource(eid, resource) for eid in entities), default=0)


@fn_registry.register(
    "min_resource", doc="Min resource across active entities", min_args=1, max_args=1
)
def _min_resource(args, ctx):
    resource = evaluate(args[0], ctx)
    entities = ctx.get_active_entities()
    return min((ctx.state.get_resource(eid, resource) for eid in entities), default=0)


@fn_registry.register(
    "resource_of",
    doc="Get resource value for a specific entity",
    min_args=2,
    max_args=2,
)
def _resource_of(args, ctx):
    entity = evaluate(args[0], ctx)
    resource = evaluate(args[1], ctx)
    return ctx.state.get_resource(str(entity), str(resource))


@fn_registry.register(
    "find_by_role", doc="Find first entity with role", min_args=1, max_args=1
)
def _find_by_role(args, ctx):
    role = evaluate(args[0], ctx)
    for eid in ctx.get_all_entities():
        if ctx.state.get_attr(eid, "role") == role:
            return eid
    return None


@fn_registry.register(
    "find_by_attr", doc="Find first entity with attr=value", min_args=2, max_args=2
)
def _find_by_attr(args, ctx):
    attr = evaluate(args[0], ctx)
    value = evaluate(args[1], ctx)
    for eid in ctx.get_all_entities():
        if ctx.state.get_attr(eid, attr) == value:
            return eid
    return None


@fn_registry.register(
    "filter_by_team", doc="List entities with team", min_args=1, max_args=1
)
def _filter_by_team(args, ctx):
    team = evaluate(args[0], ctx)
    return [
        eid for eid in ctx.get_all_entities() if ctx.state.get_attr(eid, "team") == team
    ]


@fn_registry.register(
    "filter_by_attr", doc="List entities with attr=value", min_args=2, max_args=2
)
def _filter_by_attr(args, ctx):
    attr = evaluate(args[0], ctx)
    value = evaluate(args[1], ctx)
    return [
        eid for eid in ctx.get_all_entities() if ctx.state.get_attr(eid, attr) == value
    ]


@fn_registry.register("length", doc="Length of collection", min_args=1, max_args=1)
def _length(args, ctx):
    val = evaluate(args[0], ctx)
    if isinstance(val, (list, tuple, set, frozenset, dict)):
        return len(val)
    return 0


# -- Collection operations --


@fn_registry.register(
    "get_var", doc="Get nested var value by key path", min_args=1, max_args=10
)
def _get_var(args, ctx):
    keys = [evaluate(a, ctx) for a in args]
    if not keys:
        return None
    val = ctx.state.get_game_var(keys[0])
    for key in keys[1:]:
        if isinstance(val, dict):
            val = val.get(key) if isinstance(key, str) else val.get(str(key))
        elif isinstance(val, (list, tuple)):
            try:
                val = val[int(key)]
            except (ValueError, IndexError, TypeError):
                val = None
        else:
            val = None
        if val is None:
            break
    return val


@fn_registry.register(
    "list_length",
    doc="Length of a collection (alias for length)",
    min_args=1,
    max_args=1,
)
def _list_length(args, ctx):
    return _length(args, ctx)


@fn_registry.register(
    "sort_by", doc="Sort list of dicts by field", min_args=2, max_args=3
)
def _sort_by(args, ctx):
    lst = evaluate(args[0], ctx)
    field = evaluate(args[1], ctx)
    direction = evaluate(args[2], ctx) if len(args) > 2 else "asc"
    if not isinstance(lst, (list, tuple)):
        return []
    reverse = direction == "desc"
    return sorted(
        lst,
        key=lambda item: item.get(field, 0) if isinstance(item, dict) else 0,
        reverse=reverse,
    )


@fn_registry.register(
    "filter_where", doc="Filter list of dicts by field=value", min_args=3, max_args=3
)
def _filter_where(args, ctx):
    lst = evaluate(args[0], ctx)
    field = evaluate(args[1], ctx)
    value = evaluate(args[2], ctx)
    if not isinstance(lst, (list, tuple)):
        return []
    return [item for item in lst if isinstance(item, dict) and item.get(field) == value]


@fn_registry.register(
    "map_field", doc="Extract field from list of dicts", min_args=2, max_args=2
)
def _map_field(args, ctx):
    lst = evaluate(args[0], ctx)
    field = evaluate(args[1], ctx)
    if not isinstance(lst, (list, tuple)):
        return []
    return [item.get(field) if isinstance(item, dict) else None for item in lst]


@fn_registry.register(
    "aggregate",
    doc="Aggregate field: sum/max/min/avg/count",
    min_args=3,
    max_args=3,
)
def _aggregate(args, ctx):
    lst = evaluate(args[0], ctx)
    field = evaluate(args[1], ctx)
    op = evaluate(args[2], ctx)
    if not isinstance(lst, (list, tuple)):
        return 0
    values = [item.get(field, 0) if isinstance(item, dict) else 0 for item in lst]
    if not values:
        return 0
    if op == "sum":
        return sum(values)
    if op == "max":
        return max(values)
    if op == "min":
        return min(values)
    if op == "avg":
        return sum(values) / len(values)
    if op == "count":
        return len(values)
    return 0


@fn_registry.register(
    "best", doc="Find dict with max/min field value", min_args=2, max_args=3
)
def _best(args, ctx):
    lst = evaluate(args[0], ctx)
    field = evaluate(args[1], ctx)
    mode = evaluate(args[2], ctx) if len(args) > 2 else "max"
    if not isinstance(lst, (list, tuple)) or not lst:
        return None
    fn = max if mode == "max" else min
    return fn(
        lst,
        key=lambda item: item.get(field, 0) if isinstance(item, dict) else 0,
    )


@fn_registry.register(
    "index_of",
    doc="Find index of dict where field=value (-1 if not found)",
    min_args=3,
    max_args=3,
)
def _index_of(args, ctx):
    lst = evaluate(args[0], ctx)
    field = evaluate(args[1], ctx)
    value = evaluate(args[2], ctx)
    if not isinstance(lst, (list, tuple)):
        return -1
    for i, item in enumerate(lst):
        if isinstance(item, dict) and item.get(field) == value:
            return i
    return -1


# ---------------------------------------------------------------------------
# Speech act functions
# ---------------------------------------------------------------------------


@fn_registry.register(
    "has_pending_claim",
    doc="Check if entity has a pending (unverified) speech act",
    min_args=1,
    max_args=2,
)
def _has_pending_claim(args, ctx):
    entity_id = evaluate(args[0], ctx)
    act_type = evaluate(args[1], ctx) if len(args) > 1 else None
    for sa in ctx.state.pending_speech_acts.values():
        if sa.actor == entity_id and sa.status == "pending":
            if act_type is None or sa.act_type == act_type:
                return True
    return False


@fn_registry.register(
    "count_endorsements",
    doc="Count endorsers of a speech act by instance_id",
    min_args=1,
    max_args=1,
)
def _count_endorsements(args, ctx):
    instance_id = evaluate(args[0], ctx)
    sa = ctx.state.pending_speech_acts.get(instance_id)
    if sa:
        return len(sa.endorsers)
    # Check resolved too
    for rsa in ctx.state.resolved_speech_acts:
        if rsa.instance_id == instance_id:
            return len(rsa.endorsers)
    return 0


@fn_registry.register(
    "claim_verified",
    doc="Check if entity has verified claims (optionally filter by true/false)",
    min_args=1,
    max_args=2,
)
def _claim_verified(args, ctx):
    entity_id = evaluate(args[0], ctx)
    result_filter = evaluate(args[1], ctx) if len(args) > 1 else None
    for rsa in ctx.state.resolved_speech_acts:
        if rsa.actor == entity_id:
            if result_filter is None:
                return True
            if result_filter == "true" and rsa.status == "verified_true":
                return True
            if result_filter == "false" and rsa.status == "verified_false":
                return True
    return False


@fn_registry.register(
    "has_pending_inquire",
    doc="Check if target has an unanswered inquire",
    min_args=1,
    max_args=1,
)
def _has_pending_inquire(args, ctx):
    target_id = evaluate(args[0], ctx)
    for sa in ctx.state.pending_speech_acts.values():
        if (
            sa.act_type == "inquire"
            and sa.target == target_id
            and sa.inquire_response is None
        ):
            return True
    return False


# ---------------------------------------------------------------------------
# Tracing evaluator (for debugging)
# ---------------------------------------------------------------------------


@dataclass
class TraceEntry:
    expr: Expr
    result: Any


def evaluate_with_trace(expr: Expr, ctx: Context) -> tuple[Any, list[TraceEntry]]:
    """Evaluate with full trace of intermediate results."""
    trace: list[TraceEntry] = []
    result = _eval_traced(expr, ctx, trace)
    return result, trace


def _eval_traced(expr: Expr, ctx: Context, trace: list[TraceEntry]) -> Any:
    result = evaluate(expr, ctx)
    trace.append(TraceEntry(expr=expr, result=result))
    return result
