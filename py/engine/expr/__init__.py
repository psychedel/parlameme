"""Expression layer — typed AST with operator overloading."""

from engine.expr.core import (
    Expr, Ref, Lit, Cmp, Arith, And, Or, Not, Call, If,
    actor, target, game, proposer, responder, subject, self_, params,
)
from engine.expr.functions import (
    alive, dead, active, inactive,
    in_group, in_group_type, same_group, has_relation,
    count_where, count_alive, count_active, count_group,
    count_in_group, count_not_in_group,
    sum_resource, avg_resource, max_resource, min_resource,
    find_by_role, find_by_attr, filter_by_team, filter_by_attr,
    every_in_group, contains, not_contains, some, phase_is,
    length,
)
from engine.expr.evaluator import evaluate, evaluate_with_trace, Context

__all__ = [
    "Expr", "Ref", "Lit", "Cmp", "Arith", "And", "Or", "Not", "Call", "If",
    "actor", "target", "game", "proposer", "responder", "subject", "self_", "params",
    "alive", "dead", "active", "inactive",
    "in_group", "in_group_type", "same_group", "has_relation",
    "count_where", "count_alive", "count_active", "count_group",
    "count_in_group", "count_not_in_group",
    "sum_resource", "avg_resource", "max_resource", "min_resource",
    "find_by_role", "find_by_attr", "filter_by_team", "filter_by_attr",
    "every_in_group", "contains", "not_contains", "some", "phase_is",
    "length",
    "evaluate", "evaluate_with_trace", "Context",
]
