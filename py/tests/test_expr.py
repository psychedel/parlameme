"""Tests for the expression layer — AST construction and evaluation."""

import pytest
from engine.expr import (
    Expr, Ref, Lit, Cmp, And, Or, Not, Call,
    actor, target, game, proposer, responder,
    alive, dead, count_where, count_alive,
    sum_resource, max_resource, every_in_group,
    contains, not_contains, find_by_role, length,
    evaluate, Context,
)
from engine.runtime.state import (
    GameState, Entity, Group, CompiledGame, ResourceDef, AttrDef,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_state(entities=None, groups=None, vars_=None):
    compiled = CompiledGame(
        id="test", name="Test",
        resources={"health": ResourceDef(id="health", initial=100),
                    "energy": ResourceDef(id="energy", initial=10)},
        attrs_defs={"role": AttrDef(id="role"), "team": AttrDef(id="team")},
    )
    ents = entities or {
        "alice": Entity(id="alice", active=True,
                        resources={"health": 100, "energy": 10},
                        attrs_={"role": "seer", "team": "village"}),
        "bob": Entity(id="bob", active=True,
                      resources={"health": 50, "energy": 5},
                      attrs_={"role": "wolf", "team": "wolves"}),
        "carol": Entity(id="carol", active=False,
                        resources={"health": 0, "energy": 0},
                        attrs_={"role": "villager", "team": "village"}),
    }
    return GameState(
        entities=ents,
        groups=groups or {},
        vars_=vars_ or {},
    ), compiled


# ---------------------------------------------------------------------------
# AST Construction
# ---------------------------------------------------------------------------

class TestASTConstruction:
    def test_ref_single(self):
        assert actor.parts == ("actor",)

    def test_ref_dotted(self):
        r = actor.role
        assert isinstance(r, Ref)
        assert r.parts == ("actor", "role")

    def test_ref_deep_path(self):
        r = actor.some.deep.path
        assert r.parts == ("actor", "some", "deep", "path")

    def test_comparison_builds_cmp(self):
        expr = actor.health > 50
        assert isinstance(expr, Cmp)
        assert expr.op == ">"

    def test_equality_builds_cmp(self):
        expr = actor.role == "seer"
        assert isinstance(expr, Cmp)
        assert expr.op == "=="

    def test_and_builds_and(self):
        expr = alive() & (actor.role == "seer")
        assert isinstance(expr, And)
        assert len(expr.exprs) == 2

    def test_and_flattens(self):
        expr = alive() & (actor.role == "seer") & (actor.health > 50)
        assert isinstance(expr, And)
        assert len(expr.exprs) == 3  # Flattened, not nested

    def test_or_flattens(self):
        expr = (actor.role == "seer") | (actor.role == "witch") | (actor.role == "hunter")
        assert isinstance(expr, Or)
        assert len(expr.exprs) == 3

    def test_not_builds_not(self):
        expr = ~alive()
        assert isinstance(expr, Not)

    def test_arithmetic(self):
        from engine.expr.core import Arith
        expr = actor.health + actor.energy
        assert isinstance(expr, Arith)
        assert expr.op == "+"

    def test_bool_raises(self):
        with pytest.raises(TypeError, match="Cannot use Expr in boolean context"):
            if alive():
                pass

    def test_call_construction(self):
        expr = count_where(alive())
        assert isinstance(expr, Call)
        assert expr.fn == "count_where"
        assert len(expr.args) == 1


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

class TestEvaluation:
    def test_literal(self):
        state, compiled = make_state()
        ctx = Context(state=state, compiled=compiled)
        assert evaluate(Lit(42), ctx) == 42
        assert evaluate(Lit("hello"), ctx) == "hello"
        assert evaluate(Lit(True), ctx) is True

    def test_ref_actor_resource(self):
        state, compiled = make_state()
        ctx = Context(state=state, compiled=compiled, bindings={"actor": "alice"})
        assert evaluate(actor.health, ctx) == 100

    def test_ref_actor_attr(self):
        state, compiled = make_state()
        ctx = Context(state=state, compiled=compiled, bindings={"actor": "alice"})
        assert evaluate(actor.role, ctx) == "seer"

    def test_comparison_true(self):
        state, compiled = make_state()
        ctx = Context(state=state, compiled=compiled, bindings={"actor": "alice"})
        assert evaluate(actor.health > 50, ctx) is True

    def test_comparison_false(self):
        state, compiled = make_state()
        ctx = Context(state=state, compiled=compiled, bindings={"actor": "bob"})
        assert evaluate(actor.health > 80, ctx) is False

    def test_equality(self):
        state, compiled = make_state()
        ctx = Context(state=state, compiled=compiled, bindings={"actor": "alice"})
        assert evaluate(actor.role == "seer", ctx) is True
        assert evaluate(actor.role == "wolf", ctx) is False

    def test_and_both_true(self):
        state, compiled = make_state()
        ctx = Context(state=state, compiled=compiled, bindings={"actor": "alice"})
        assert evaluate(alive() & (actor.health >= 50), ctx) is True

    def test_and_one_false(self):
        state, compiled = make_state()
        ctx = Context(state=state, compiled=compiled, bindings={"actor": "carol"})
        assert evaluate(alive() & (actor.health >= 50), ctx) is False

    def test_or(self):
        state, compiled = make_state()
        ctx = Context(state=state, compiled=compiled, bindings={"actor": "alice"})
        assert evaluate((actor.role == "seer") | (actor.role == "wolf"), ctx) is True

    def test_not(self):
        state, compiled = make_state()
        ctx = Context(state=state, compiled=compiled, bindings={"actor": "carol"})
        assert evaluate(~alive(), ctx) is True

    def test_arithmetic(self):
        state, compiled = make_state()
        ctx = Context(state=state, compiled=compiled, bindings={"actor": "alice"})
        assert evaluate(actor.health + actor.energy, ctx) == 110.0

    def test_nil_safe_arithmetic(self):
        state, compiled = make_state()
        ctx = Context(state=state, compiled=compiled, bindings={"actor": "alice"})
        # Nonexistent resource → 0
        assert evaluate(Ref("actor", "nonexistent") + Lit(10), ctx) == 10.0

    def test_division_by_zero(self):
        state, compiled = make_state()
        ctx = Context(state=state, compiled=compiled)
        assert evaluate(Lit(10) / Lit(0), ctx) == 0.0

    def test_game_var(self):
        state, compiled = make_state(vars_={"round": 3, "custom_var": "hello"})
        state = GameState(**{**{f.name: getattr(state, f.name) for f in state.__attrs_attrs__},
                             "round": 3})
        ctx = Context(state=state, compiled=compiled)
        assert evaluate(game.round, ctx) == 3


class TestFunctions:
    def test_alive(self):
        state, compiled = make_state()
        ctx = Context(state=state, compiled=compiled, bindings={"actor": "alice"})
        assert evaluate(alive(), ctx) is True

    def test_dead(self):
        state, compiled = make_state()
        ctx = Context(state=state, compiled=compiled, bindings={"actor": "carol"})
        assert evaluate(dead(), ctx) is True

    def test_count_where(self):
        state, compiled = make_state()
        ctx = Context(state=state, compiled=compiled)
        # Count entities with health > 0
        assert evaluate(count_where(actor.health > 0), ctx) == 2

    def test_count_where_team(self):
        state, compiled = make_state()
        ctx = Context(state=state, compiled=compiled)
        assert evaluate(count_where(actor.team == "village"), ctx) == 2

    def test_count_alive(self):
        state, compiled = make_state()
        ctx = Context(state=state, compiled=compiled)
        assert evaluate(count_alive(), ctx) == 2

    def test_sum_resource(self):
        state, compiled = make_state()
        ctx = Context(state=state, compiled=compiled)
        # Only active: alice(100) + bob(50) = 150
        assert evaluate(sum_resource("health"), ctx) == 150

    def test_max_resource(self):
        state, compiled = make_state()
        ctx = Context(state=state, compiled=compiled)
        assert evaluate(max_resource("health"), ctx) == 100

    def test_find_by_role(self):
        state, compiled = make_state()
        ctx = Context(state=state, compiled=compiled)
        assert evaluate(find_by_role("seer"), ctx) == "alice"
        assert evaluate(find_by_role("wolf"), ctx) == "bob"
        assert evaluate(find_by_role("nonexistent"), ctx) is None

    def test_every_in_group(self):
        groups = {
            "team-0": Group(id="team-0", type="team",
                           members=frozenset(["alice", "bob"])),
        }
        ents = {
            "alice": Entity(id="alice", active=True, groups=frozenset(["team-0"])),
            "bob": Entity(id="bob", active=True, groups=frozenset(["team-0"])),
        }
        state, compiled = make_state(entities=ents, groups=groups)
        ctx = Context(state=state, compiled=compiled)
        assert evaluate(every_in_group("team-0", alive()), ctx) is True

    def test_contains(self):
        ents = {
            "alice": Entity(id="alice", active=True,
                           attrs_={"investigated": frozenset(["bob"])}),
        }
        state, compiled = make_state(entities=ents)
        ctx = Context(state=state, compiled=compiled, bindings={"actor": "alice"})
        assert evaluate(contains(actor.investigated, Lit("bob")), ctx) is True
        assert evaluate(not_contains(actor.investigated, Lit("carol")), ctx) is True
