"""Built-in expression functions — each returns an Expr AST node."""

from engine.expr.core import Call, Expr, Lit, _wrap

# ---------------------------------------------------------------------------
# Predicates
# ---------------------------------------------------------------------------


def alive(entity: Expr | None = None) -> Call:
    """Check if entity is active. No arg = check actor."""
    if entity is None:
        return Call("alive?", ())
    return Call("alive?", (_wrap(entity),))


def dead(entity: Expr | None = None) -> Call:
    if entity is None:
        return Call("dead?", ())
    return Call("dead?", (_wrap(entity),))


def active(entity: Expr | None = None) -> Call:
    if entity is None:
        return Call("active?", ())
    return Call("active?", (_wrap(entity),))


def inactive(entity: Expr | None = None) -> Call:
    if entity is None:
        return Call("inactive?", ())
    return Call("inactive?", (_wrap(entity),))


def in_group(group: str) -> Call:
    return Call("in_group?", (Lit(group),))


def in_group_type(group_type: str) -> Call:
    return Call("in_group_type?", (Lit(group_type),))


def same_group(entity_a: Expr, entity_b: Expr) -> Call:
    return Call("same_group?", (_wrap(entity_a), _wrap(entity_b)))


def has_relation(entity: Expr, relation: str) -> Call:
    return Call("has_relation?", (_wrap(entity), Lit(relation)))


def phase_is(phase_id: str) -> Call:
    return Call("phase?", (Lit(phase_id),))


def some(expr: Expr) -> Call:
    return Call("some?", (_wrap(expr),))


def contains(collection: Expr, value: Expr | str) -> Call:
    return Call("contains?", (_wrap(collection), _wrap(value)))


def not_contains(collection: Expr, value: Expr | str) -> Call:
    return Call("not_contains?", (_wrap(collection), _wrap(value)))


def every_in_group(group: str, pred: Expr) -> Call:
    return Call("every_in_group?", (Lit(group), pred))


# ---------------------------------------------------------------------------
# Aggregations
# ---------------------------------------------------------------------------


def count_where(pred: Expr) -> Call:
    return Call("count_where", (pred,))


def count_alive() -> Call:
    return Call("count_alive", ())


def count_active() -> Call:
    return Call("count_active", ())


def count_group(group: str) -> Call:
    return Call("count_group", (Lit(group),))


def count_in_group(group: str) -> Call:
    return Call("count_in_group", (Lit(group),))


def count_not_in_group(group: str) -> Call:
    return Call("count_not_in_group", (Lit(group),))


def sum_resource(resource: str) -> Call:
    return Call("sum_resource", (Lit(resource),))


def avg_resource(resource: str) -> Call:
    return Call("avg_resource", (Lit(resource),))


def max_resource(resource: str) -> Call:
    return Call("max_resource", (Lit(resource),))


def min_resource(resource: str) -> Call:
    return Call("min_resource", (Lit(resource),))


def find_by_role(role: str) -> Call:
    return Call("find_by_role", (Lit(role),))


def find_by_attr(attr: str, value: str) -> Call:
    return Call("find_by_attr", (Lit(attr), Lit(value)))


def filter_by_team(team: str) -> Call:
    return Call("filter_by_team", (Lit(team),))


def filter_by_attr(attr: str, value: str) -> Call:
    return Call("filter_by_attr", (Lit(attr), Lit(value)))


def length(collection: Expr) -> Call:
    return Call("length", (_wrap(collection),))


# ---------------------------------------------------------------------------
# Collection operations
# ---------------------------------------------------------------------------


def resource_of(entity: str | Expr, resource: str | Expr) -> Call:
    return Call("resource_of", (_wrap(entity), _wrap(resource)))


def get_var(*keys: str | Expr) -> Call:
    return Call("get_var", tuple(_wrap(k) for k in keys))


def list_length(collection: Expr) -> Call:
    return Call("list_length", (_wrap(collection),))


def sort_by(lst: Expr, field: str | Expr, direction: str = "asc") -> Call:
    return Call("sort_by", (_wrap(lst), _wrap(field), Lit(direction)))


def filter_where(lst: Expr, field: str | Expr, value: str | Expr) -> Call:
    return Call("filter_where", (_wrap(lst), _wrap(field), _wrap(value)))


def map_field(lst: Expr, field: str | Expr) -> Call:
    return Call("map_field", (_wrap(lst), _wrap(field)))


def aggregate(lst: Expr, field: str | Expr, op: str) -> Call:
    return Call("aggregate", (_wrap(lst), _wrap(field), Lit(op)))


def best(lst: Expr, field: str | Expr, mode: str = "max") -> Call:
    return Call("best", (_wrap(lst), _wrap(field), Lit(mode)))


def index_of(lst: Expr, field: str | Expr, value: str | Expr) -> Call:
    return Call("index_of", (_wrap(lst), _wrap(field), _wrap(value)))
