"""Reptiloid Exchange — market simulator with emergent prices and social dynamics.

4-8 cryptid-themed trading firms on a resource exchange. Circular production
chain (A→B→C→D→A) guarantees mandatory trade (Sidereal Confluence insight).
Multiple commodities with emergent prices, information asymmetry, OTC deals,
market manipulation, and reputation as enforcement.

Game design foundations:
- Circular production chain: every role MUST trade (no autarky)
- Continuous double auction: price-time priority, emergent equilibrium
- Information asymmetry: research, insider knowledge, published analysis
- Social enforcement: reputation gates deals, suspicion attracts regulation
- Multiple victory paths: trading profit, market domination, or reputation

Key mechanics:
- Order book with limit/market orders matched in settlement
- OTC bilateral trades with negotiated prices
- Corporate actions: hostile takeovers, syndicates, cornering markets
- Regulation: violations, audits, whistleblowing
- Speech acts: price predictions, accusations, promises
"""

from dataclasses import dataclass

import attrs

from engine.dsl.builder import Game
from engine.expr import Ref, actor, alive, game, params
from engine.expr.core import Expr, Lit
from engine.expr.functions import get_var, resource_of, some
from engine.runtime.effects import (
    AssignRoles,
    Boost,
    Broadcast,
    BurnStakes,
    Damage,
    Each,
    Notify,
    ResolveSpeechActs,
    ReturnStakes,
    Reveal,
    SetVar,
    Transfer,
    TransferStakes,
    When,
    register_effect,
)
from engine.runtime.state import (
    ChannelHint,
    OutcomeDef,
    ParamDef,
    PhaseHint,
    RoleHint,
    VarHint,
)

# ---------------------------------------------------------------------------
# Custom effect: MatchOrders — continuous double auction
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MatchOrders:
    """Match buy/sell orders for an asset using price-time priority.

    Reads order_book.{asset} from vars_, matches bids (descending) against
    asks (ascending), executes trades at midpoint price, updates price history.
    """

    asset: str | Expr


@register_effect(MatchOrders)
def _match_orders(effect, state, ctx):
    """Continuous double auction: price-time priority matching.

    Escrow model: deal effects already locked resources via Damage.
    Settlement credits counterparties, refunds excess, returns unmatched.
    Prevents self-trading (same player on both sides).
    """
    import copy

    from engine.expr.evaluator import evaluate

    asset = (
        evaluate(effect.asset, ctx) if isinstance(effect.asset, Expr) else effect.asset
    )

    order_book = state.get_game_var("order_book") or {}
    asset_orders = order_book.get(asset, [])
    if not asset_orders:
        return state

    # Separate and sort
    bids = sorted(
        [o for o in asset_orders if o["side"] == "buy"],
        key=lambda o: (-o["price"], o["seq"]),
    )
    asks = sorted(
        [o for o in asset_orders if o["side"] == "sell"],
        key=lambda o: (o["price"], o["seq"]),
    )

    trades = []
    matched_bid_ids = set()
    matched_ask_ids = set()
    bi, ai = 0, 0

    while bi < len(bids) and ai < len(asks):
        bid = bids[bi]
        ask = asks[ai]

        if bid["price"] < ask["price"]:
            break

        # Prevent self-trading
        if bid["player"] == ask["player"]:
            ai += 1
            continue

        trade_price = (bid["price"] + ask["price"]) / 2
        trade_qty = min(bid.get("qty", 1), ask.get("qty", 1))

        trades.append(
            {
                "buyer": bid["player"],
                "seller": ask["player"],
                "asset": asset,
                "price": trade_price,
                "qty": trade_qty,
                "bid_price": bid["price"],
                "bid_id": bid["id"],
                "ask_id": ask["id"],
            }
        )
        matched_bid_ids.add(bid["id"])
        matched_ask_ids.add(ask["id"])
        bi += 1
        ai += 1

    # Execute trades — escrow settlement
    for trade in trades:
        trade_cost = trade["price"] * trade["qty"]
        # Give buyer the asset
        state = state.adjust_resource(trade["buyer"], asset, trade["qty"], ctx.compiled)
        # Give seller credits at trade price
        state = state.adjust_resource(
            trade["seller"], "credits", trade_cost, ctx.compiled
        )
        # Refund buyer excess (locked at bid_price, traded at midpoint)
        locked = trade["bid_price"] * trade["qty"]
        refund = locked - trade_cost
        if refund > 0:
            state = state.adjust_resource(
                trade["buyer"], "credits", refund, ctx.compiled
            )

    # GTC semantics: unmatched orders stay on book with funds locked.
    # Players must use cancel_order to reclaim locked funds.
    matched_ids = matched_bid_ids | matched_ask_ids

    # Update vars: remove matched orders, keep unmatched on book
    new_vars = copy.deepcopy(dict(state.vars_))
    book = new_vars.get("order_book", {})
    book[asset] = [o for o in asset_orders if o["id"] not in matched_ids]
    new_vars["order_book"] = book

    if trades:
        last_price = trades[-1]["price"]
        prices = new_vars.get("prices", {})
        prices[asset] = last_price
        new_vars["prices"] = prices

        history = new_vars.get("price_history", {})
        asset_hist = history.get(asset, [])
        asset_hist.append(last_price)
        if len(asset_hist) > 20:
            asset_hist = asset_hist[-20:]
        history[asset] = asset_hist
        new_vars["price_history"] = history

        new_vars["trade_count"] = new_vars.get("trade_count", 0) + len(trades)

    state = attrs.evolve(state, vars_=new_vars)

    for trade in trades:
        state = state.add_history(
            "trade",
            buyer=trade["buyer"],
            seller=trade["seller"],
            asset=trade["asset"],
            price=trade["price"],
            qty=trade["qty"],
        )

    return state


# ---------------------------------------------------------------------------
# Custom effect: PlaceOrder — add order to book with auto-incrementing seq
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlaceOrder:
    """Place an order on the order book from deal params.

    Reads asset/side/price/qty from ctx bindings, assigns sequence number,
    and appends to vars_.order_book. Self-contained — no need for preceding
    SetVar to manage order_seq.
    """

    pass


@register_effect(PlaceOrder)
def _place_order(effect, state, ctx):
    """Add order to the order book using deal params and actor."""
    import copy

    params = ctx.bindings.get("params") or {}
    asset = params.get("asset")
    side = params.get("side")
    price = params.get("price")
    qty = params.get("qty", 1)
    player = ctx.bindings.get("actor")

    if not all([asset, side, player]):
        return state

    # Market orders: no explicit price → use max_price (buy) or 0 (sell)
    if price is None:
        price = params.get("max_price", 9999) if side == "buy" else 0

    new_vars = copy.deepcopy(dict(state.vars_))
    order_seq = new_vars.get("order_seq", 0)
    order_seq += 1
    new_vars["order_seq"] = order_seq

    order = {
        "id": f"ord-{order_seq}",
        "player": player,
        "asset": asset,
        "side": side,
        "price": price,
        "qty": qty,
        "seq": order_seq,
    }

    book = new_vars.get("order_book", {})
    asset_orders = book.get(asset, [])
    asset_orders.append(order)
    book[asset] = asset_orders
    new_vars["order_book"] = book
    return attrs.evolve(state, vars_=new_vars)


# ---------------------------------------------------------------------------
# Custom effect: CancelOrder — remove order from book, refund locked resources
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CancelOrder:
    """Cancel an order from the order book and refund locked resources.

    Reads order_id from ctx params. Only cancels orders belonging to the actor.
    """

    pass


@register_effect(CancelOrder)
def _cancel_order(effect, state, ctx):
    """Remove order from book and refund locked resources."""
    import copy

    params = ctx.bindings.get("params") or {}
    order_id = params.get("order_id")
    player = ctx.bindings.get("actor")

    if not order_id or not player:
        return state

    new_vars = copy.deepcopy(dict(state.vars_))
    book = new_vars.get("order_book", {})

    # Find and remove the order across all assets
    cancelled = None
    for asset_key, orders in book.items():
        for i, order in enumerate(orders):
            if order["id"] == order_id and order["player"] == player:
                cancelled = order
                orders.pop(i)
                break
        if cancelled:
            break

    if not cancelled:
        # Order not found or doesn't belong to player — notify only
        state = state.add_history(
            "notify",
            entity=player,
            message=f"Cancel failed: order {order_id} not found or not yours.",
        )
        return state

    new_vars["order_book"] = book
    state = attrs.evolve(state, vars_=new_vars)

    # Refund locked resources
    asset = cancelled["asset"]
    qty = cancelled.get("qty", 1)
    if cancelled["side"] == "buy":
        state = state.adjust_resource(
            player,
            "credits",
            cancelled["price"] * qty,
            ctx.compiled,
        )
    else:
        state = state.adjust_resource(player, asset, qty, ctx.compiled)

    state = state.add_history(
        "notify",
        entity=player,
        message=f"Order {order_id} cancelled. Funds returned.",
    )
    return state


# ---------------------------------------------------------------------------
# Game definition
# ---------------------------------------------------------------------------

exchange = (
    Game("exchange", "Reptiloid Exchange", players=(4, 8))
    # ── Resources ─────────────────────────────────────
    .resource("credits", initial=500, visibility="private", bounds=(0, None))
    .resource("alpha", initial=0, visibility="private", bounds=(0, None))
    .resource("beta", initial=0, visibility="private", bounds=(0, None))
    .resource("gamma", initial=0, visibility="private", bounds=(0, None))
    .resource("delta", initial=0, visibility="private", bounds=(0, None))
    .resource("research", initial=2, visibility="private", bounds=(0, 10))
    .resource("reputation", initial=50, visibility="public", bounds=(0, 100))
    .resource("suspicion", initial=0, visibility="public", bounds=(0, 100))
    # ── Attributes ────────────────────────────────────
    .attr("role", visibility="private")
    # ── Roles: circular production chain ──────────────
    .role("harvester", team="neutral", count=1)
    .role("refiner", team="neutral", count=1)
    .role("synthesizer", team="neutral", count=1)
    .role("excavator", team="neutral", count=1)
    .role("freelancer", team="neutral", filler=True)
    # ── Channels ──────────────────────────────────────
    .channel(
        "trading_floor",
        type="public",
        description="Public trading floor — signals, announcements, negotiations",
    )
    .channel(
        "dark_pool",
        type="private",
        description="Private OTC negotiation channel",
        effects=[Boost("actor", "suspicion", 2)],
    )
    .channel(
        "research_wire",
        type="broadcast",
        description="Published research and analysis — read-only announcements",
    )
    # ── Trading Deals ─────────────────────────────────
    # Limit order — post to order book, matched in settlement
    .deal(
        "limit_order",
        actor=alive(),
        params={
            "asset": ParamDef(
                type="keyword",
                options=("alpha", "beta", "gamma", "delta"),
                label="Asset to trade",
            ),
            "side": ParamDef(
                type="keyword",
                options=("buy", "sell"),
                label="Buy or sell",
            ),
            "price": ParamDef(type="number", min=1, max=9999, label="Limit price"),
            "qty": ParamDef(type="number", min=1, max=100, default=1, label="Quantity"),
        },
        effects=[
            # Lock credits for buy orders, assets for sell orders
            When(
                params.side == Lit("buy"),
                (
                    Damage(
                        "actor",
                        "credits",
                        params.price * params.qty,
                    ),
                ),
            ),
            When(
                params.side == Lit("sell"),
                (Damage("actor", params.asset, params.qty),),
            ),
            PlaceOrder(),
            Notify(
                "actor",
                "Limit {params.side} order placed: {params.qty}x {params.asset} @ {params.price}",
            ),
        ],
        doc="Post a limit order to the exchange. Locked funds released on fill or cancel.",
    )
    # Market order — executes at best available price in settlement
    .deal(
        "market_order",
        actor=alive(),
        params={
            "asset": ParamDef(
                type="keyword",
                options=("alpha", "beta", "gamma", "delta"),
                label="Asset to trade",
            ),
            "side": ParamDef(
                type="keyword",
                options=("buy", "sell"),
                label="Buy or sell",
            ),
            "qty": ParamDef(type="number", min=1, max=10, default=1, label="Quantity"),
            "max_price": ParamDef(
                type="number",
                min=1,
                max=9999,
                default=200,
                label="Max price per unit (for buy orders)",
            ),
        },
        effects=[
            # Buy: lock max_price × qty credits (excess refunded in settlement)
            When(
                params.side == Lit("buy"),
                (
                    Damage(
                        "actor",
                        "credits",
                        params.max_price * params.qty,
                    ),
                ),
            ),
            When(
                params.side == Lit("sell"),
                (Damage("actor", params.asset, params.qty),),
            ),
            PlaceOrder(),
            Notify("actor", "Market {params.side} order: {params.qty}x {params.asset}"),
        ],
        doc="Market order — executes at best available price in settlement. Buy locks max_price × qty credits.",
    )
    # Cancel order — remove own order from book, refund locked resources
    .deal(
        "cancel_order",
        actor=alive(),
        params={
            "order_id": ParamDef(type="string", label="Order ID to cancel"),
        },
        effects=[
            CancelOrder(),
        ],
        doc="Cancel a pending limit order and reclaim locked funds.",
    )
    # OTC trade — bilateral negotiation
    .deal(
        "otc_trade",
        proposer=alive(),
        responder=alive(),
        params={
            "asset": ParamDef(
                type="keyword",
                options=("alpha", "beta", "gamma", "delta"),
                label="Asset to trade",
            ),
            "qty": ParamDef(type="number", min=1, max=100, default=1, label="Quantity"),
            "price": ParamDef(type="number", min=1, max=9999, label="Total price"),
        },
        stakes={"proposer": [("credits", "price")]},
        responses=["accept", "counter", "reject"],
        outcomes={
            "accept": OutcomeDef(
                guard=resource_of(Ref("responder"), params.asset)
                >= params.qty,
                effects=(
                    # Staked credits go to responder (seller)
                    TransferStakes(to="responder"),
                    # Transfer asset from responder to proposer (buyer)
                    Transfer(
                        "responder",
                        "proposer",
                        params.asset,
                        params.qty,
                    ),
                    Broadcast(
                        "OTC: {proposer} buys {params.qty}x {params.asset} from {responder} @ {params.price}"
                    ),
                ),
                doc="Accept the OTC trade — responder must have enough of the asset",
            ),
            "counter": OutcomeDef(
                effects=(
                    ReturnStakes(),
                    Notify("proposer", "{responder} wants to counter-offer."),
                ),
                doc="Counter-propose different terms (start new deal)",
            ),
            "reject": OutcomeDef(
                effects=(ReturnStakes(),),
                doc="Reject the trade proposal",
            ),
        },
        doc="Bilateral OTC trade. Proposer buys asset from responder at total price.",
    )
    # ── Information Deals ─────────────────────────────
    # Buy research — spend credits for information tokens
    .deal(
        "buy_research",
        actor=alive(),
        per_phase=1,
        stakes={"actor": [("credits", 30)]},
        effects=[
            Boost("actor", "research", 1),
            Notify("actor", "Research acquired. You can now investigate or publish."),
        ],
        doc="Purchase research tokens (30 credits). Enables investigations and publications.",
    )
    # Investigate position — spend research to see someone's holdings
    .deal(
        "investigate_position",
        actor=alive(),
        params={
            "subject": ParamDef(type="player", label="Player to investigate"),
        },
        per_phase=1,
        effects=[
            Damage("actor", "research", 1),
            Boost("actor", "suspicion", 3),
            Reveal(params.subject, "alpha", "actor"),
            Reveal(params.subject, "beta", "actor"),
            Reveal(params.subject, "gamma", "actor"),
            Reveal(params.subject, "delta", "actor"),
            Notify(
                "actor",
                "Investigation complete. You can see {params.subject}'s commodity holdings.",
            ),
        ],
        doc="Spend 1 research to reveal target's commodity positions. Generates +3 suspicion.",
    )
    # Publish analysis — broadcast research, gain reputation
    .deal(
        "publish_analysis",
        actor=alive(),
        params={
            "asset": ParamDef(
                type="keyword",
                options=("alpha", "beta", "gamma", "delta"),
                label="Asset to analyze",
            ),
        },
        per_round=1,
        effects=[
            Damage("actor", "research", 1),
            Boost("actor", "reputation", 5),
            Broadcast("{actor} publishes analysis on {params.asset}."),
        ],
        doc="Publish market analysis. Costs 1 research, gains +5 reputation. Can influence prices.",
    )
    # Insider trade — use research for unfair advantage
    .deal(
        "insider_trade",
        actor=alive(),
        params={
            "asset": ParamDef(
                type="keyword",
                options=("alpha", "beta", "gamma", "delta"),
                label="Asset",
            ),
            "qty": ParamDef(type="number", min=1, max=10, default=1, label="Quantity"),
        },
        effects=[
            Damage("actor", "research", 2),
            Boost("actor", "suspicion", 10),
            Boost("actor", params.asset, params.qty),
            Notify("actor", "Insider trade complete. +10 suspicion."),
        ],
        doc="Acquire assets through insider channels. Costs 2 research, +10 suspicion. High risk, high reward.",
    )
    # ── Corporate Deals ───────────────────────────────
    # Hostile takeover — attempt to seize another player's assets
    .deal(
        "hostile_takeover",
        proposer=alive(),
        responder=alive(),
        stakes={"proposer": [("credits", 200), ("reputation", 20)]},
        responses=["surrender", "defend", "poison_pill"],
        outcomes={
            "surrender": OutcomeDef(
                effects=(
                    BurnStakes(),
                    Transfer("responder", "proposer", "alpha", Lit(5)),
                    Transfer("responder", "proposer", "beta", Lit(5)),
                    Transfer("responder", "proposer", "gamma", Lit(5)),
                    Transfer("responder", "proposer", "delta", Lit(5)),
                    Broadcast(
                        "{proposer} successfully takes over {responder}'s position!"
                    ),
                ),
                doc="Surrender — proposer seizes 5 of each commodity",
            ),
            "defend": OutcomeDef(
                effects=(
                    ReturnStakes(),
                    Damage("proposer", "reputation", 10),
                    Boost("responder", "reputation", 10),
                    Broadcast(
                        "{responder} successfully defends against {proposer}'s takeover!"
                    ),
                ),
                doc="Defend — takeover fails, proposer loses reputation",
            ),
            "poison_pill": OutcomeDef(
                effects=(
                    BurnStakes(),
                    Damage("responder", "credits", 100),
                    Damage("proposer", "credits", 100),
                    Broadcast(
                        "{responder} deploys poison pill! Both firms suffer losses."
                    ),
                ),
                doc="Poison pill — mutual destruction, both lose resources",
            ),
        },
        doc="Hostile takeover attempt. Stake 200 credits + 20 reputation. Target chooses defense.",
    )
    # Pay dividend — distribute credits to boost reputation
    .deal(
        "pay_dividend",
        actor=alive(),
        per_round=1,
        params={
            "amount": ParamDef(type="number", min=10, max=100, label="Dividend amount"),
        },
        stakes={"actor": [("credits", "amount")]},
        effects=[
            BurnStakes(),
            Boost("actor", "reputation", params.amount / Lit(10)),
            Broadcast("{actor} pays dividend of {params.amount} credits."),
        ],
        doc="Pay dividend to market. Burns credits, gains reputation (amount/10).",
    )
    # ── Regulation Deals ──────────────────────────────
    # Report violation — accuse another player of market manipulation
    .deal(
        "report_violation",
        proposer=alive(),
        responder=alive(),
        stakes={"proposer": [("reputation", 10)]},
        responses=["accept_fine", "deny"],
        outcomes={
            "accept_fine": OutcomeDef(
                effects=(
                    ReturnStakes(),
                    Damage("responder", "credits", 50),
                    Damage("responder", "suspicion", 20),
                    Boost("proposer", "reputation", 5),
                    Broadcast(
                        "{responder} pays fine of 50 credits for market violation."
                    ),
                ),
                doc="Accept the fine — clear suspicion, pay credits",
            ),
            "deny": OutcomeDef(
                effects=(
                    ReturnStakes(),
                    Boost("responder", "suspicion", 10),
                    Broadcast("{responder} denies {proposer}'s violation report."),
                ),
                doc="Deny the accusation — risk more suspicion",
            ),
        },
        doc="Report market violation. Stake 10 reputation. Target can pay fine or deny.",
    )
    # Audit defense — spend credits to reduce suspicion
    .deal(
        "audit_defense",
        actor=alive(),
        per_round=1,
        params={
            "amount": ParamDef(
                type="number", min=20, max=200, label="Defense spending"
            ),
        },
        stakes={"actor": [("credits", "amount")]},
        effects=[
            BurnStakes(),
            Damage("actor", "suspicion", params.amount / Lit(5)),
            Notify("actor", "Audit defense deployed. Suspicion reduced."),
        ],
        doc="Spend credits to reduce suspicion. Reduction = amount/5.",
    )
    # ── Votes ─────────────────────────────────────────
    # Market halt — emergency trading freeze
    .vote(
        "market_halt",
        voters=alive(),
        options=("halt", "continue"),
        threshold="supermajority",
        outcomes={
            "halt": OutcomeDef(
                effects=(
                    Each("p", alive(), [Damage("p", "suspicion", 15)]),
                    Broadcast("MARKET HALT! Trading suspended. All suspicion reduced."),
                ),
                doc="Halt trading, reduce everyone's suspicion by 15",
            ),
            "continue": OutcomeDef(
                effects=(Broadcast("Market continues operating normally."),),
                doc="No halt — business as usual",
            ),
        },
        doc="Vote to halt market. Supermajority needed. Reduces all suspicion.",
    )
    # Elect regulator — give investigation powers
    .vote(
        "elect_regulator",
        voters=alive(),
        options=("elect", "oppose"),
        threshold="majority",
        outcomes={
            "elect": OutcomeDef(
                effects=(
                    Broadcast(
                        "Market regulator elected! Enhanced investigation powers active."
                    ),
                ),
                doc="Regulator elected — enhanced oversight",
            ),
            "oppose": OutcomeDef(
                effects=(Broadcast("Regulator election fails."),),
                doc="No regulator appointed",
            ),
        },
        doc="Elect a market regulator with investigation powers. Majority needed.",
    )
    # Bailout vote — rescue bankrupt player
    .vote(
        "bailout_vote",
        voters=alive(),
        options=("bailout", "refuse"),
        threshold="majority",
        outcomes={
            "bailout": OutcomeDef(
                effects=(
                    Each("p", alive(), [Damage("p", "credits", 25)]),
                    Broadcast("Bailout approved! Each firm contributes 25 credits."),
                ),
                doc="Bailout — everyone pays 25 credits",
            ),
            "refuse": OutcomeDef(
                effects=(Broadcast("Bailout refused. No rescue."),),
                doc="No bailout",
            ),
        },
        doc="Vote to bail out struggling firm. Majority needed. Each firm pays 25 credits.",
    )
    # ── Speech Acts ───────────────────────────────────
    .speech_act(
        "predict_price",
        act_type="predict",
        actor_filter=alive(),
        params={
            "asset": ParamDef(
                type="keyword",
                options=("alpha", "beta", "gamma", "delta"),
                label="Asset",
            ),
            "direction": ParamDef(
                type="keyword",
                options=("up", "down", "stable"),
                label="Predicted direction",
            ),
        },
        cost={"reputation": 3},
        verify_triggers=("settlement",),
        # Verify by comparing current price vs previous price in history.
        # get_var("price_history", asset, -2) = price before last settlement.
        # If history has < 2 entries, some?() fails → verified_false (fair: no trades yet).
        verify_condition=(
            some(get_var("price_history", params.asset, Lit(-2)))
            & (
                (
                    (params.direction == Lit("up"))
                    & (
                        get_var("prices", params.asset)
                        > get_var("price_history", params.asset, Lit(-2))
                    )
                )
                | (
                    (params.direction == Lit("down"))
                    & (
                        get_var("prices", params.asset)
                        < get_var("price_history", params.asset, Lit(-2))
                    )
                )
                | (
                    (params.direction == Lit("stable"))
                    & (
                        get_var("prices", params.asset)
                        == get_var("price_history", params.asset, Lit(-2))
                    )
                )
            )
        ),
        verify_true_effects=[
            Boost("actor", "reputation", 10),
            Broadcast("{actor}'s price prediction was CORRECT!"),
        ],
        verify_false_effects=[
            Damage("actor", "reputation", 5),
            Broadcast("{actor}'s price prediction was WRONG."),
        ],
        per_round=1,
        doc="Predict asset price direction. Verified at phase end by comparing current vs previous price.",
    )
    .speech_act(
        "accuse_manipulation",
        act_type="accuse",
        actor_filter=alive(),
        target_filter=alive(),
        params={
            "claim": ParamDef(type="string", label="Accusation details"),
        },
        cost={"reputation": 5},
        verify_triggers=("phase_change",),
        verify_condition=Ref("target", "suspicion") >= Lit(30),
        verify_true_effects=[
            Boost("actor", "reputation", 15),
            Damage("target", "reputation", 10),
            Broadcast("{actor}'s accusation against {target} CONFIRMED!"),
        ],
        verify_false_effects=[
            Damage("actor", "reputation", 10),
            Boost("target", "reputation", 5),
            Broadcast("{actor}'s accusation against {target} was BASELESS."),
        ],
        per_round=1,
        doc="Accuse target of manipulation. Verified by suspicion >= 30.",
    )
    .speech_act(
        "promise_delivery",
        act_type="promise",
        actor_filter=alive(),
        target_filter=alive(),
        params={
            "asset": ParamDef(
                type="keyword",
                options=("alpha", "beta", "gamma", "delta"),
                label="Asset promised",
            ),
            "qty": ParamDef(type="number", min=1, max=50, default=1, label="Quantity"),
        },
        cost={"reputation": 2},
        promise_deadline=2,
        verify_triggers=("phase_change",),
        verify_true_effects=[
            Boost("actor", "reputation", 8),
            Broadcast("{actor} fulfilled delivery promise to {target}!"),
        ],
        verify_false_effects=[
            Damage("actor", "reputation", 15),
            Broadcast("{actor} BROKE delivery promise to {target}!"),
        ],
        per_round=1,
        doc="Promise to deliver assets within 2 rounds. Verified automatically.",
    )
    # ── Commitments ───────────────────────────────────
    # Margin call — emergency loan when credits hit 0 (checked each phase)
    .commitment(
        "margin_call",
        trigger="phase_change",
        guard=actor.credits <= Lit(0),
        effects=[
            Boost("actor", "credits", 100),
            Damage("actor", "reputation", 15),
            Boost("actor", "suspicion", 10),
            Broadcast(
                "MARGIN CALL on {actor}! Emergency loan: +100 credits, -15 reputation, +10 suspicion."
            ),
        ],
        once=False,
        doc="Emergency loan when credits reach 0. Fires each phase transition. Recurring.",
    )
    # Reputation collapse — high suspicion causes extra reputation loss on elimination
    .commitment(
        "reputation_collapse",
        trigger="eliminate",
        guard=actor.suspicion >= Lit(80),
        effects=[
            Damage("actor", "reputation", 25),
            Broadcast(
                "{actor}'s firm is under regulatory investigation! -25 reputation."
            ),
        ],
        doc="High suspicion causes reputation collapse on elimination.",
    )
    # ── Phases ────────────────────────────────────────
    .phase(
        "setup",
        category="setup",
        automatic=True,
        once=True,
        effects=[
            AssignRoles(),
            SetVar("trading_day", 1),
            SetVar("total_days", 10),
            SetVar("order_seq", 0),
            SetVar("trade_count", 0),
            SetVar("order_book", {"alpha": [], "beta": [], "gamma": [], "delta": []}),
            SetVar("prices", {"alpha": 100, "beta": 100, "gamma": 100, "delta": 100}),
            SetVar(
                "price_history",
                {"alpha": [100], "beta": [100], "gamma": [100], "delta": [100]},
            ),
        ],
    )
    # Morning briefing — production income, research
    # starts_round=True: each trading day starts a new round
    .phase(
        "morning_briefing",
        allows=["buy_research", "publish_analysis", "predict_price"],
        channels=["trading_floor", "research_wire"],
        duration=30,
        starts_round=True,
        effects=[
            # Role-based production: circular chain
            Each(
                "p",
                alive(),
                [
                    # Harvester: +2 alpha
                    When(
                        Ref("p", "role") == Lit("harvester"),
                        (
                            Boost("p", "alpha", 2),
                            Notify("p", "Production: +2 alpha (harvester)"),
                        ),
                    ),
                    # Refiner: +2 beta
                    When(
                        Ref("p", "role") == Lit("refiner"),
                        (
                            Boost("p", "beta", 2),
                            Notify("p", "Production: +2 beta (refiner)"),
                        ),
                    ),
                    # Synthesizer: +2 gamma
                    When(
                        Ref("p", "role") == Lit("synthesizer"),
                        (
                            Boost("p", "gamma", 2),
                            Notify("p", "Production: +2 gamma (synthesizer)"),
                        ),
                    ),
                    # Excavator: +2 delta
                    When(
                        Ref("p", "role") == Lit("excavator"),
                        (
                            Boost("p", "delta", 2),
                            Notify("p", "Production: +2 delta (excavator)"),
                        ),
                    ),
                    # Freelancer: +50 credits
                    When(
                        Ref("p", "role") == Lit("freelancer"),
                        (
                            Boost("p", "credits", 50),
                            Notify("p", "Income: +50 credits (freelancer)"),
                        ),
                    ),
                ],
            ),
            # Suspicion decay (-5 per day, min 0)
            Each(
                "p",
                alive(),
                [
                    When(
                        Ref("p", "suspicion") > Lit(0), (Damage("p", "suspicion", 5),)
                    ),
                ],
            ),
            Broadcast(
                "Trading Day {game.trading_day} of {game.total_days} — markets opening."
            ),
        ],
    )
    # Open market — main trading session
    .phase(
        "open_market",
        allows=[
            "limit_order",
            "market_order",
            "cancel_order",
            "otc_trade",
            "insider_trade",
            "predict_price",
            "accuse_manipulation",
            "promise_delivery",
            "market_halt",
            "elect_regulator",
        ],
        channels=["trading_floor", "dark_pool"],
        duration=60,
    )
    # Research phase — investigations and analysis
    .phase(
        "research_phase",
        allows=[
            "buy_research",
            "investigate_position",
            "publish_analysis",
            "accuse_manipulation",
            "elect_regulator",
        ],
        channels=["trading_floor", "research_wire"],
        duration=30,
    )
    # Afternoon market — final trading + corporate actions
    .phase(
        "afternoon_market",
        allows=[
            "limit_order",
            "market_order",
            "cancel_order",
            "otc_trade",
            "hostile_takeover",
            "pay_dividend",
            "report_violation",
            "audit_defense",
            "predict_price",
            "accuse_manipulation",
            "promise_delivery",
            "market_halt",
            "bailout_vote",
        ],
        channels=["trading_floor", "dark_pool"],
        duration=60,
    )
    # Settlement — match orders, update prices (automatic)
    .phase(
        "settlement",
        category="resolution",
        automatic=True,
        effects=[
            # Match orders for each commodity
            MatchOrders("alpha"),
            MatchOrders("beta"),
            MatchOrders("gamma"),
            MatchOrders("delta"),
            Broadcast("Settlement complete. Prices updated."),
            # Verify price predictions after prices are updated
            ResolveSpeechActs(trigger="settlement"),
        ],
    )
    # Close — end of trading day (automatic)
    # next="morning_briefing": explicit trading day cycle
    .phase(
        "close",
        category="resolution",
        automatic=True,
        next="morning_briefing",
        effects=[
            # Advance trading day
            SetVar("trading_day", game.trading_day + Lit(1)),
            # Reputation recovery (+2 per day for low-suspicion players)
            Each(
                "p",
                alive(),
                [
                    When(
                        (Ref("p", "suspicion") < Lit(30))
                        & (Ref("p", "reputation") < Lit(100)),
                        (Boost("p", "reputation", 2),),
                    ),
                ],
            ),
        ],
    )
    # ── Victory Conditions ────────────────────────────
    # Primary: portfolio score = credits + (holdings × prices) + reputation bonus - suspicion penalty
    .victory(
        "trading_champion",
        when=game.trading_day > game.total_days,
        type="distribution",
        score=(
            actor.credits * Lit(10)
            + (actor.alpha + actor.beta + actor.gamma + actor.delta) * Lit(15)
            + actor.reputation * Lit(20)
            - actor.suspicion * Lit(10)
        ),
        priority=1,
        message="Trading Champion — highest portfolio score!",
    )
    # Early end: market domination
    .victory(
        "market_domination",
        when=actor.credits >= Lit(2000),
        type="single",
        priority=2,
        message="Market Domination — credits >= 2000!",
    )
    # ── AI Agent Context ──────────────────────────────
    .context(
        game_summary=(
            "Resource exchange with 4 commodities in a circular production chain. "
            "Trade via order book or OTC deals. Win by maximizing portfolio: "
            "credits + commodity holdings + reputation - suspicion."
        ),
        score_explanation=(
            "Score = credits×10 + (total_commodities)×15 + reputation×20 - suspicion×10. "
            "Early win: credits >= 2000."
        ),
        var_hints=[
            VarHint(
                "trading_day",
                "Day",
                format="progress",
                max_var="total_days",
                priority=100,
            ),
            VarHint("prices", "Prices", format="table", priority=90),
            VarHint("trade_count", "Trades", priority=50),
            VarHint(
                "order_seq",
                "Orders",
                priority=40,
                phases=("open_market", "afternoon_market"),
            ),
        ],
        phase_hints=[
            PhaseHint(
                "morning_briefing",
                "Start of trading day. Production income arrives. Buy research or publish analysis.",
                tips=(
                    "Your role determines production: check what you produce",
                    "Research tokens enable investigations and publications",
                ),
            ),
            PhaseHint(
                "open_market",
                "Main trading session. Place limit/market orders or negotiate OTC trades.",
                urgency="critical",
                tips=(
                    "Limit orders sit on the book until settlement",
                    "OTC trades execute immediately but need counterparty agreement",
                    "Insider trades are fast but generate high suspicion",
                ),
            ),
            PhaseHint(
                "research_phase",
                "Intelligence gathering. Investigate competitors or publish analysis.",
                tips=(
                    "Investigations reveal commodity holdings but add +3 suspicion",
                    "Publications boost reputation by +5",
                ),
            ),
            PhaseHint(
                "afternoon_market",
                "Final trading + corporate actions. Last chance before settlement.",
                urgency="critical",
                tips=(
                    "Hostile takeovers cost 200 credits + 20 reputation",
                    "Dividends convert credits to reputation",
                    "Report violations to damage competitors",
                ),
            ),
            PhaseHint(
                "settlement",
                "Automatic order matching. Prices update based on trades.",
            ),
        ],
        role_hints=[
            RoleHint(
                "harvester",
                strategy="You produce alpha. Sell alpha, buy beta (your production input). "
                "Control alpha supply to influence prices.",
                allies=("freelancer", "excavator"),
                threats=("other harvesters",),
                phase_tips={
                    "open_market": "Sell alpha when price is high, stockpile when low"
                },
            ),
            RoleHint(
                "refiner",
                strategy="You produce beta. Sell beta, buy gamma. "
                "Position between harvester and synthesizer in the chain.",
                allies=("harvester", "freelancer"),
                threats=("other refiners",),
            ),
            RoleHint(
                "synthesizer",
                strategy="You produce gamma. Sell gamma, buy delta. "
                "Critical link in the production chain.",
                allies=("refiner", "excavator"),
                threats=("delta supply disruptions",),
            ),
            RoleHint(
                "excavator",
                strategy="You produce delta. Sell delta, buy alpha. "
                "Complete the circular chain back to harvesters.",
                allies=("synthesizer", "harvester"),
                threats=("alpha price spikes",),
            ),
            RoleHint(
                "freelancer",
                strategy="No production but steady income (+50 credits/day). "
                "Trade information, arbitrage price differences, broker deals.",
                allies=("everyone",),
                threats=("no natural commodity supply",),
                phase_tips={
                    "open_market": "Arbitrage: buy low, sell high across commodities",
                    "research_phase": "Your credits fund investigations — information is power",
                },
            ),
        ],
        channel_hints=[
            ChannelHint(
                "trading_floor",
                when_to_use="Signal trades, negotiate publicly, build reputation",
                strategy="Public signals influence market sentiment",
            ),
            ChannelHint(
                "dark_pool",
                when_to_use="Private OTC negotiation — discreet but suspicious",
                risk="+2 suspicion per message",
                strategy="Use sparingly for high-value deals you want to hide",
            ),
            ChannelHint(
                "research_wire",
                when_to_use="Publish analysis — builds reputation, influences market",
                strategy="Published research creates information asymmetry in your favor",
            ),
        ],
        deal_priorities={
            "limit_order": 100,
            "market_order": 95,
            "otc_trade": 90,
            "cancel_order": 85,
            "hostile_takeover": 80,
            "insider_trade": 75,
            "buy_research": 70,
            "investigate_position": 65,
            "publish_analysis": 60,
            "report_violation": 55,
            "pay_dividend": 50,
            "audit_defense": 45,
        },
    )
    .build()
)
