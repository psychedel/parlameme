"""Tests for engine extensions: deep path resolution, UpdateVar, new Expr functions, dynamic ParamDef."""

import pytest

from engine.dsl.builder import Game
from engine.expr.core import Call, Lit, Ref
from engine.expr.evaluator import Context, evaluate
from engine.expr.functions import (
    aggregate,
    best,
    filter_where,
    get_var,
    index_of,
    list_length,
    map_field,
    sort_by,
)
from engine.runtime.core import GameRuntime
from engine.runtime.effects import (
    Boost,
    SetVar,
    UpdateVar,
    apply_effect,
    apply_effects,
)
from engine.runtime.state import ParamDef

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_game():
    """Minimal game for testing."""
    return (
        Game("test_ext", "Test Extensions", players=(2, 4))
        .resource("gold", initial=100, visibility="private")
        .resource("items", initial=0, visibility="private")
        .phase("main", name="Main")
        .victory("none", when=Lit(False))
        .build()
    )


def _ctx_with_vars(vars_: dict, **bindings):
    """Build a Context with pre-set game vars."""
    compiled = _make_game()
    rt = GameRuntime(compiled)
    state = rt.start_game(["p0", "p1", "p2"])
    for k, v in vars_.items():
        state = state.set_game_var(k, v)
    return Context(state=state, compiled=compiled, bindings=bindings), state, rt


# ===========================================================================
# Extension 2: Deep path resolution
# ===========================================================================


class TestDeepPathResolution:
    def test_simple_game_var(self):
        ctx, _, _ = _ctx_with_vars({"score": 3})
        assert evaluate(Ref("game", "score"), ctx) == 3

    def test_nested_dict(self):
        ctx, _, _ = _ctx_with_vars({"prices": {"alpha": 100, "beta": 200}})
        assert evaluate(Ref("game", "prices", "alpha"), ctx) == 100
        assert evaluate(Ref("game", "prices", "beta"), ctx) == 200

    def test_deeply_nested_dict(self):
        ctx, _, _ = _ctx_with_vars({"market": {"sectors": {"tech": {"price": 42}}}})
        assert evaluate(Ref("game", "market", "sectors", "tech", "price"), ctx) == 42

    def test_list_index(self):
        ctx, _, _ = _ctx_with_vars({"order_book": [{"price": 10}, {"price": 20}]})
        assert evaluate(Ref("game", "order_book", "0", "price"), ctx) == 10
        assert evaluate(Ref("game", "order_book", "1", "price"), ctx) == 20

    def test_missing_key_returns_none(self):
        ctx, _, _ = _ctx_with_vars({"prices": {"alpha": 100}})
        assert evaluate(Ref("game", "prices", "gamma"), ctx) is None

    def test_out_of_bounds_returns_none(self):
        ctx, _, _ = _ctx_with_vars({"items": [1, 2, 3]})
        assert evaluate(Ref("game", "items", "99"), ctx) is None

    def test_non_indexable_returns_none(self):
        ctx, _, _ = _ctx_with_vars({"count": 42})
        assert evaluate(Ref("game", "count", "sub"), ctx) is None

    def test_backward_compatible_single_level(self):
        ctx, _, _ = _ctx_with_vars({"score": 100})
        assert evaluate(Ref("game", "score"), ctx) == 100

    def test_params_deep_path(self):
        ctx, _, _ = _ctx_with_vars({})
        ctx = ctx.with_binding("params", {"order": {"type": "limit", "price": 50}})
        assert evaluate(Ref("params", "order", "type"), ctx) == "limit"
        assert evaluate(Ref("params", "order", "price"), ctx) == 50

    def test_params_missing_deep_key(self):
        ctx, _, _ = _ctx_with_vars({})
        ctx = ctx.with_binding("params", {"x": {"y": 1}})
        assert evaluate(Ref("params", "x", "z"), ctx) is None


# ===========================================================================
# Extension 1: UpdateVar effect
# ===========================================================================


class TestUpdateVar:
    def test_set_simple(self):
        ctx, state, rt = _ctx_with_vars({"counter": 0})
        state = apply_effect(
            UpdateVar(path=("counter",), operation="set", value=42), state, ctx
        )
        assert state.get_game_var("counter") == 42

    def test_set_nested(self):
        ctx, state, rt = _ctx_with_vars({"prices": {"alpha": 100}})
        state = apply_effect(
            UpdateVar(path=("prices", "alpha"), operation="set", value=150),
            state,
            ctx,
        )
        assert state.get_game_var("prices")["alpha"] == 150

    def test_set_creates_intermediate_dicts(self):
        ctx, state, rt = _ctx_with_vars({})
        state = state.set_game_var("market", {})
        ctx = Context(state=state, compiled=ctx.compiled, bindings=ctx.bindings)
        state = apply_effect(
            UpdateVar(path=("market", "status"), operation="set", value="open"),
            state,
            ctx,
        )
        assert state.get_game_var("market")["status"] == "open"

    def test_append(self):
        ctx, state, rt = _ctx_with_vars({"orders": []})
        state = apply_effect(
            UpdateVar(
                path=("orders",), operation="append", value={"id": 1, "price": 50}
            ),
            state,
            ctx,
        )
        assert len(state.get_game_var("orders")) == 1
        assert state.get_game_var("orders")[0]["price"] == 50

    def test_append_creates_list_if_none(self):
        ctx, state, rt = _ctx_with_vars({})
        state = state.set_game_var("log", {})
        ctx = Context(state=state, compiled=ctx.compiled, bindings=ctx.bindings)
        state = apply_effect(
            UpdateVar(path=("log", "entries"), operation="append", value="first"),
            state,
            ctx,
        )
        assert state.get_game_var("log")["entries"] == ["first"]

    def test_prepend(self):
        ctx, state, rt = _ctx_with_vars({"history": ["b", "c"]})
        state = apply_effect(
            UpdateVar(path=("history",), operation="prepend", value="a"),
            state,
            ctx,
        )
        assert state.get_game_var("history") == ["a", "b", "c"]

    def test_append_max(self):
        ctx, state, rt = _ctx_with_vars({"recent": [1, 2, 3]})
        state = apply_effect(
            UpdateVar(path=("recent",), operation="append_max", value=4, key="3"),
            state,
            ctx,
        )
        assert state.get_game_var("recent") == [2, 3, 4]

    def test_remove(self):
        ctx, state, rt = _ctx_with_vars({"tags": ["a", "b", "c"]})
        state = apply_effect(
            UpdateVar(path=("tags",), operation="remove", value="b"),
            state,
            ctx,
        )
        assert state.get_game_var("tags") == ["a", "c"]

    def test_remove_where(self):
        ctx, state, rt = _ctx_with_vars(
            {
                "orders": [
                    {"id": "o1", "player": "p0"},
                    {"id": "o2", "player": "p1"},
                    {"id": "o3", "player": "p0"},
                ]
            }
        )
        state = apply_effect(
            UpdateVar(
                path=("orders",), operation="remove_where", value="p0", key="player"
            ),
            state,
            ctx,
        )
        orders = state.get_game_var("orders")
        assert len(orders) == 1
        assert orders[0]["id"] == "o2"

    def test_increment(self):
        ctx, state, rt = _ctx_with_vars({"scores": {"p0": 10}})
        state = apply_effect(
            UpdateVar(path=("scores", "p0"), operation="increment", value=5),
            state,
            ctx,
        )
        assert state.get_game_var("scores")["p0"] == 15

    def test_decrement(self):
        ctx, state, rt = _ctx_with_vars({"scores": {"p0": 10}})
        state = apply_effect(
            UpdateVar(path=("scores", "p0"), operation="decrement", value=3),
            state,
            ctx,
        )
        assert state.get_game_var("scores")["p0"] == 7

    def test_increment_default(self):
        """Increment with no value defaults to 1."""
        ctx, state, rt = _ctx_with_vars({"counter": 0})
        state = state.set_game_var("stats", {})
        ctx = Context(state=state, compiled=ctx.compiled, bindings=ctx.bindings)
        state = apply_effect(
            UpdateVar(path=("stats", "visits"), operation="increment"),
            state,
            ctx,
        )
        assert state.get_game_var("stats")["visits"] == 1

    def test_sort_by(self):
        ctx, state, rt = _ctx_with_vars(
            {"bids": [{"price": 30}, {"price": 10}, {"price": 20}]}
        )
        state = apply_effect(
            UpdateVar(path=("bids",), operation="sort_by", key="price"),
            state,
            ctx,
        )
        prices = [b["price"] for b in state.get_game_var("bids")]
        assert prices == [10, 20, 30]

    def test_sort_by_desc(self):
        ctx, state, rt = _ctx_with_vars(
            {"bids": [{"price": 30}, {"price": 10}, {"price": 20}]}
        )
        state = apply_effect(
            UpdateVar(path=("bids",), operation="sort_by", key="price", value="desc"),
            state,
            ctx,
        )
        prices = [b["price"] for b in state.get_game_var("bids")]
        assert prices == [30, 20, 10]

    def test_clear_list(self):
        ctx, state, rt = _ctx_with_vars({"orders": [1, 2, 3]})
        state = apply_effect(
            UpdateVar(path=("orders",), operation="clear"),
            state,
            ctx,
        )
        assert state.get_game_var("orders") == []

    def test_clear_dict(self):
        ctx, state, rt = _ctx_with_vars({"cache": {"a": 1, "b": 2}})
        state = apply_effect(
            UpdateVar(path=("cache",), operation="clear"),
            state,
            ctx,
        )
        assert state.get_game_var("cache") == {}

    def test_immutability_preserved(self):
        """Original state must not be mutated."""
        ctx, state, rt = _ctx_with_vars({"prices": {"alpha": 100}})
        new_state = apply_effect(
            UpdateVar(path=("prices", "alpha"), operation="set", value=200),
            state,
            ctx,
        )
        assert state.get_game_var("prices")["alpha"] == 100
        assert new_state.get_game_var("prices")["alpha"] == 200

    def test_expr_value(self):
        """Value can be an Expr that gets evaluated."""
        ctx, state, rt = _ctx_with_vars({"base_price": 50, "prices": {"alpha": 0}})
        state = apply_effect(
            UpdateVar(
                path=("prices", "alpha"),
                operation="set",
                value=Ref("game", "base_price"),
            ),
            state,
            ctx,
        )
        assert state.get_game_var("prices")["alpha"] == 50

    def test_empty_path_noop(self):
        ctx, state, rt = _ctx_with_vars({"x": 1})
        new_state = apply_effect(
            UpdateVar(path=(), operation="set", value=99),
            state,
            ctx,
        )
        assert new_state.get_game_var("x") == 1


# ===========================================================================
# Extension 5: New Expr functions
# ===========================================================================


class TestNewExprFunctions:
    @pytest.fixture
    def order_book_ctx(self):
        orders = [
            {"id": "o1", "player": "p0", "price": 100, "side": "buy"},
            {"id": "o2", "player": "p1", "price": 95, "side": "buy"},
            {"id": "o3", "player": "p2", "price": 105, "side": "sell"},
            {"id": "o4", "player": "p0", "price": 110, "side": "sell"},
        ]
        ctx, state, rt = _ctx_with_vars({"order_book": orders, "counter": 42})
        return ctx

    # -- get_var --

    def test_get_var_simple(self, order_book_ctx):
        expr = get_var("counter")
        assert evaluate(expr, order_book_ctx) == 42

    def test_get_var_nested(self):
        ctx, _, _ = _ctx_with_vars({"market": {"prices": {"alpha": 99}}})
        expr = get_var("market", "prices", "alpha")
        assert evaluate(expr, ctx) == 99

    def test_get_var_list_index(self, order_book_ctx):
        expr = get_var("order_book", 0, "price")
        assert evaluate(expr, order_book_ctx) == 100

    def test_get_var_missing(self, order_book_ctx):
        expr = get_var("nonexistent")
        assert evaluate(expr, order_book_ctx) is None

    # -- resource_of --

    def test_resource_of(self, order_book_ctx):
        from engine.expr.functions import resource_of

        expr = resource_of(Lit("p0"), Lit("gold"))
        assert evaluate(expr, order_book_ctx) == 100

    def test_resource_of_dynamic(self, order_book_ctx):
        """resource_of with Ref-based entity and resource name."""
        from engine.expr.functions import resource_of

        ctx = order_book_ctx.with_bindings(responder="p0", params={"asset": "gold"})
        expr = resource_of(Ref("responder"), Ref("params", "asset"))
        assert evaluate(expr, ctx) == 100

    # -- list_length --

    def test_list_length(self, order_book_ctx):
        expr = list_length(Ref("game", "order_book"))
        assert evaluate(expr, order_book_ctx) == 4

    def test_list_length_empty(self):
        ctx, _, _ = _ctx_with_vars({"empty": []})
        assert evaluate(list_length(Ref("game", "empty")), ctx) == 0

    def test_list_length_non_list(self):
        ctx, _, _ = _ctx_with_vars({"num": 42})
        assert evaluate(list_length(Ref("game", "num")), ctx) == 0

    # -- sort_by --

    def test_sort_by_asc(self, order_book_ctx):
        expr = sort_by(Ref("game", "order_book"), "price")
        result = evaluate(expr, order_book_ctx)
        prices = [o["price"] for o in result]
        assert prices == [95, 100, 105, 110]

    def test_sort_by_desc(self, order_book_ctx):
        expr = sort_by(Ref("game", "order_book"), "price", "desc")
        result = evaluate(expr, order_book_ctx)
        prices = [o["price"] for o in result]
        assert prices == [110, 105, 100, 95]

    def test_sort_by_non_list(self, order_book_ctx):
        expr = sort_by(Ref("game", "counter"), "x")
        assert evaluate(expr, order_book_ctx) == []

    # -- filter_where --

    def test_filter_where(self, order_book_ctx):
        expr = filter_where(Ref("game", "order_book"), "side", "buy")
        result = evaluate(expr, order_book_ctx)
        assert len(result) == 2
        assert all(o["side"] == "buy" for o in result)

    def test_filter_where_no_match(self, order_book_ctx):
        expr = filter_where(Ref("game", "order_book"), "side", "cancel")
        assert evaluate(expr, order_book_ctx) == []

    def test_filter_where_by_player(self, order_book_ctx):
        expr = filter_where(Ref("game", "order_book"), "player", "p0")
        result = evaluate(expr, order_book_ctx)
        assert len(result) == 2

    # -- map_field --

    def test_map_field(self, order_book_ctx):
        expr = map_field(Ref("game", "order_book"), "price")
        assert evaluate(expr, order_book_ctx) == [100, 95, 105, 110]

    def test_map_field_ids(self, order_book_ctx):
        expr = map_field(Ref("game", "order_book"), "id")
        assert evaluate(expr, order_book_ctx) == ["o1", "o2", "o3", "o4"]

    # -- aggregate --

    def test_aggregate_sum(self, order_book_ctx):
        expr = aggregate(Ref("game", "order_book"), "price", "sum")
        assert evaluate(expr, order_book_ctx) == 410

    def test_aggregate_max(self, order_book_ctx):
        expr = aggregate(Ref("game", "order_book"), "price", "max")
        assert evaluate(expr, order_book_ctx) == 110

    def test_aggregate_min(self, order_book_ctx):
        expr = aggregate(Ref("game", "order_book"), "price", "min")
        assert evaluate(expr, order_book_ctx) == 95

    def test_aggregate_avg(self, order_book_ctx):
        expr = aggregate(Ref("game", "order_book"), "price", "avg")
        assert evaluate(expr, order_book_ctx) == 102.5

    def test_aggregate_count(self, order_book_ctx):
        expr = aggregate(Ref("game", "order_book"), "price", "count")
        assert evaluate(expr, order_book_ctx) == 4

    def test_aggregate_empty_list(self):
        ctx, _, _ = _ctx_with_vars({"empty": []})
        expr = aggregate(Ref("game", "empty"), "x", "sum")
        assert evaluate(expr, ctx) == 0

    # -- best --

    def test_best_max(self, order_book_ctx):
        expr = best(Ref("game", "order_book"), "price")
        result = evaluate(expr, order_book_ctx)
        assert result["price"] == 110

    def test_best_min(self, order_book_ctx):
        expr = best(Ref("game", "order_book"), "price", "min")
        result = evaluate(expr, order_book_ctx)
        assert result["price"] == 95

    def test_best_empty(self):
        ctx, _, _ = _ctx_with_vars({"empty": []})
        assert evaluate(best(Ref("game", "empty"), "x"), ctx) is None

    # -- index_of --

    def test_index_of_found(self, order_book_ctx):
        expr = index_of(Ref("game", "order_book"), "id", "o3")
        assert evaluate(expr, order_book_ctx) == 2

    def test_index_of_not_found(self, order_book_ctx):
        expr = index_of(Ref("game", "order_book"), "id", "o99")
        assert evaluate(expr, order_book_ctx) == -1

    def test_index_of_non_list(self, order_book_ctx):
        expr = index_of(Ref("game", "counter"), "x", "y")
        assert evaluate(expr, order_book_ctx) == -1


# ===========================================================================
# Extension 3: Dynamic ParamDef with Expr min/max
# ===========================================================================


class TestDynamicParamDef:
    def test_static_min_max_still_works(self):
        """Backward compat: static min/max unchanged."""
        game = (
            Game("test_param", "Test Params", players=(2, 4))
            .resource("gold", initial=100, visibility="private")
            .phase("main", name="Main", allows=["bid"])
            .deal(
                "bid",
                doc="Bid",
                params={
                    "amount": ParamDef(type="number", min=1, max=100, label="Amount")
                },
                outcomes={
                    "accept": (
                        Boost(
                            entity="actor",
                            resource="gold",
                            amount=Ref("params", "amount"),
                        ),
                    )
                },
            )
            .victory("none", when=Lit(False))
            .build()
        )
        rt = GameRuntime(game)
        state = rt.start_game(["p0", "p1"])
        state = rt.advance_phase(state)

        # Valid
        result = rt.start_deal(state, "bid", actor_id="p0", params={"amount": 50})
        assert result.get("ok"), result

        # Below min
        result = rt.start_deal(state, "bid", actor_id="p0", params={"amount": 0})
        assert not result.get("ok")

        # Above max
        result = rt.start_deal(state, "bid", actor_id="p0", params={"amount": 200})
        assert not result.get("ok")

    def test_expr_max_evaluates_against_state(self):
        """Expr max reads player's gold dynamically."""
        game = (
            Game("test_expr_param", "Test Expr Params", players=(2, 4))
            .resource("gold", initial=100, visibility="private")
            .phase("main", name="Main", allows=["bid"])
            .deal(
                "bid",
                doc="Bid",
                params={
                    "amount": ParamDef(
                        type="number",
                        min=1,
                        max=Ref("actor", "gold"),
                        label="Amount (up to your gold)",
                    )
                },
                outcomes={
                    "accept": (
                        Boost(
                            entity="actor",
                            resource="gold",
                            amount=Ref("params", "amount"),
                        ),
                    )
                },
            )
            .victory("none", when=Lit(False))
            .build()
        )
        rt = GameRuntime(game)
        state = rt.start_game(["p0", "p1"])
        state = rt.advance_phase(state)

        # p0 has 100 gold — bid 50 should work
        result = rt.start_deal(state, "bid", actor_id="p0", params={"amount": 50})
        assert result.get("ok"), result

        # p0 has 100 gold — bid 150 should fail
        result = rt.start_deal(state, "bid", actor_id="p0", params={"amount": 150})
        assert not result.get("ok")
        assert "must be <=" in result.get("error", {}).get("message", "")

    def test_expr_min_evaluates_against_state(self):
        """Expr min reads game var dynamically."""
        game = (
            Game("test_min_expr", "Test Min Expr", players=(2, 4))
            .resource("gold", initial=100, visibility="private")
            .phase("main", name="Main", allows=["bid"])
            .deal(
                "bid",
                doc="Bid",
                params={
                    "amount": ParamDef(
                        type="number",
                        min=Ref("game", "min_bid"),
                        max=1000,
                        label="Amount",
                    )
                },
                outcomes={
                    "accept": (
                        Boost(
                            entity="actor",
                            resource="gold",
                            amount=Ref("params", "amount"),
                        ),
                    )
                },
            )
            .victory("none", when=Lit(False))
            .build()
        )
        rt = GameRuntime(game)
        state = rt.start_game(["p0", "p1"])
        state = rt.advance_phase(state)
        state = state.set_game_var("min_bid", 10)

        # Below dynamic min
        result = rt.start_deal(state, "bid", actor_id="p0", params={"amount": 5})
        assert not result.get("ok")
        assert "must be >=" in result.get("error", {}).get("message", "")

        # At dynamic min
        result = rt.start_deal(state, "bid", actor_id="p0", params={"amount": 10})
        assert result.get("ok"), result


# ===========================================================================
# Extension 2+5 combined: deep path + functions
# ===========================================================================


class TestDeepPathWithFunctions:
    def test_get_var_with_deep_ref(self):
        """get_var and Ref deep paths produce same result."""
        ctx, _, _ = _ctx_with_vars({"market": {"prices": {"alpha": 42}}})
        via_ref = evaluate(Ref("game", "market", "prices", "alpha"), ctx)
        via_fn = evaluate(get_var("market", "prices", "alpha"), ctx)
        assert via_ref == via_fn == 42

    def test_filter_then_aggregate(self):
        """Chain filter_where + aggregate."""
        orders = [
            {"side": "buy", "price": 100},
            {"side": "buy", "price": 90},
            {"side": "sell", "price": 110},
        ]
        ctx, _, _ = _ctx_with_vars({"orders": orders})
        buy_orders = filter_where(Ref("game", "orders"), "side", "buy")
        total = aggregate(buy_orders, "price", "sum")
        assert evaluate(total, ctx) == 190


# ===========================================================================
# Mechanics describe_effect for UpdateVar
# ===========================================================================


class TestUpdateVarDescribe:
    def test_describe_update_var(self):
        from mcp.mechanics import describe_effect

        eff = UpdateVar(path=("prices", "alpha"), operation="set", value=100)
        desc = describe_effect(eff)
        assert "prices.alpha" in desc
        assert "set" in desc

    def test_describe_update_var_with_key(self):
        from mcp.mechanics import describe_effect

        eff = UpdateVar(
            path=("orders",), operation="remove_where", value="p0", key="player"
        )
        desc = describe_effect(eff)
        assert "orders" in desc
        assert "remove_where" in desc
        assert "player" in desc

    def test_describe_update_var_clear(self):
        from mcp.mechanics import describe_effect

        eff = UpdateVar(path=("cache",), operation="clear")
        desc = describe_effect(eff)
        assert "clear" in desc
