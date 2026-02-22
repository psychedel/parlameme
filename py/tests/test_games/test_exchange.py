"""Reptiloid Exchange — game-specific integration tests.

Tests setup, role-based production, OTC trades, limit orders, order matching,
settlement, victory conditions, and deterministic replay.
"""

from __future__ import annotations

from engine.archive import create_archive, replay, verify
from engine.runtime.core import GameRuntime
from engine.runtime.state import GameState, view_for
from games.exchange import exchange

# =========================================================================
# Helpers
# =========================================================================

PLAYERS = ["p0", "p1", "p2", "p3"]

rt = GameRuntime(exchange)


def _setup(seed: int = 42) -> GameState:
    """Start game and run setup phase."""
    state = rt.start_game(PLAYERS, seed=seed)
    return rt.run_setup(state)


def _advance(state: GameState) -> GameState:
    """Record advance decision and advance phase."""
    state = state.record_decision({"type": "advance_phase"})
    return rt.advance_phase(state)


def _advance_to(state: GameState, target_phase: str, max_steps: int = 20) -> GameState:
    """Advance phases until reaching target_phase."""
    for _ in range(max_steps):
        if state.phase == target_phase:
            return state
        state = _advance(state)
    raise RuntimeError(f"Failed to reach phase '{target_phase}' after {max_steps} advances")


def _to_open_market(seed: int = 42) -> GameState:
    """Get to open_market phase (after morning production)."""
    state = _setup(seed)
    return _advance_to(state, "open_market")


def _to_afternoon(seed: int = 42) -> GameState:
    """Get to afternoon_market phase."""
    state = _to_open_market(seed)
    return _advance_to(state, "afternoon_market")


def _full_day_cycle(state: GameState) -> GameState:
    """Advance through a full trading day back to morning_briefing."""
    return _advance_to(state, "morning_briefing")


# =========================================================================
# Setup
# =========================================================================


class TestSetup:
    def test_initial_phase_is_morning(self):
        """After setup, game is in morning_briefing (setup auto-advances)."""
        state = _setup()
        assert state.phase == "morning_briefing"

    def test_roles_assigned(self):
        """All players have roles from the circular production chain."""
        state = _setup()
        roles = {state.get_attr(p, "role") for p in PLAYERS}
        # With 4 players: harvester, refiner, synthesizer, excavator (no freelancer)
        assert roles == {"harvester", "refiner", "synthesizer", "excavator"}

    def test_initial_resources(self):
        """Players start with correct resources."""
        state = _setup()
        for pid in PLAYERS:
            assert state.get_resource(pid, "credits") == 500
            assert state.get_resource(pid, "reputation") == 50
            assert state.get_resource(pid, "suspicion") == 0
            assert state.get_resource(pid, "research") == 2

    def test_game_vars_initialized(self):
        """Game variables set in setup."""
        state = _setup()
        assert state.get_game_var("trading_day") == 1
        assert state.get_game_var("total_days") == 10
        assert state.get_game_var("order_seq") == 0
        assert state.get_game_var("trade_count") == 0
        prices = state.get_game_var("prices")
        assert prices == {"alpha": 100, "beta": 100, "gamma": 100, "delta": 100}

    def test_order_book_initialized(self):
        """Order book starts empty for all assets."""
        state = _setup()
        book = state.get_game_var("order_book")
        for asset in ("alpha", "beta", "gamma", "delta"):
            assert book[asset] == []

    def test_five_players_gets_freelancer(self):
        """With 5 players, one gets freelancer role (filler)."""
        state = rt.start_game(["p0", "p1", "p2", "p3", "p4"], seed=42)
        state = rt.run_setup(state)
        roles = [state.get_attr(p, "role") for p in ["p0", "p1", "p2", "p3", "p4"]]
        assert "freelancer" in roles
        assert roles.count("freelancer") == 1


# =========================================================================
# Production
# =========================================================================


class TestProduction:
    def _find_role(self, state: GameState, role: str) -> str:
        """Find player with given role."""
        for pid in PLAYERS:
            if state.get_attr(pid, "role") == role:
                return pid
        raise ValueError(f"No player with role {role}")

    def test_harvester_produces_alpha(self):
        """Harvester gets +2 alpha in morning briefing."""
        state = _setup()
        pid = self._find_role(state, "harvester")
        assert state.get_resource(pid, "alpha") == 2.0

    def test_refiner_produces_beta(self):
        """Refiner gets +2 beta in morning briefing."""
        state = _setup()
        pid = self._find_role(state, "refiner")
        assert state.get_resource(pid, "beta") == 2.0

    def test_synthesizer_produces_gamma(self):
        """Synthesizer gets +2 gamma in morning briefing."""
        state = _setup()
        pid = self._find_role(state, "synthesizer")
        assert state.get_resource(pid, "gamma") == 2.0

    def test_excavator_produces_delta(self):
        """Excavator gets +2 delta in morning briefing."""
        state = _setup()
        pid = self._find_role(state, "excavator")
        assert state.get_resource(pid, "delta") == 2.0

    def test_freelancer_gets_credits(self):
        """Freelancer gets +50 credits in morning briefing."""
        state = rt.start_game(["p0", "p1", "p2", "p3", "p4"], seed=42)
        state = rt.run_setup(state)
        for pid in ["p0", "p1", "p2", "p3", "p4"]:
            if state.get_attr(pid, "role") == "freelancer":
                assert state.get_resource(pid, "credits") == 550
                return
        raise AssertionError("No freelancer found")

    def test_production_accumulates(self):
        """Production runs each morning — resources accumulate."""
        state = _setup()
        pid = self._find_role(state, "harvester")
        assert state.get_resource(pid, "alpha") == 2.0

        # Advance through full day cycle back to morning
        state = _advance_to(state, "open_market")
        state = _full_day_cycle(state)

        # Day 2 morning production adds another 2
        assert state.get_resource(pid, "alpha") == 4.0


# =========================================================================
# Phase Transitions
# =========================================================================


class TestPhaseTransitions:
    def test_day_cycle(self):
        """Full trading day cycle through all phases."""
        state = _setup()
        assert state.phase == "morning_briefing"

        state = _advance(state)
        assert state.phase == "open_market"

        state = _advance(state)
        assert state.phase == "research_phase"

        state = _advance(state)
        assert state.phase == "afternoon_market"

        # settlement + close are automatic → cascade to morning
        state = _advance(state)
        assert state.phase == "morning_briefing"

    def test_trading_day_advances(self):
        """Trading day counter increments each cycle."""
        state = _setup()
        assert state.get_game_var("trading_day") == 1

        state = _advance_to(state, "open_market")
        state = _full_day_cycle(state)
        assert state.get_game_var("trading_day") == 2


# =========================================================================
# OTC Trades
# =========================================================================


class TestOTCTrade:
    def test_otc_trade_accept(self):
        """Bilateral OTC trade: proposer buys, responder sells."""
        state = _to_open_market()

        # Find harvester (has alpha) and another player
        harvester = None
        buyer = None
        for pid in PLAYERS:
            if state.get_attr(pid, "role") == "harvester":
                harvester = pid
            elif buyer is None:
                buyer = pid

        h_alpha_before = state.get_resource(harvester, "alpha")
        b_credits_before = state.get_resource(buyer, "credits")

        # Buyer proposes OTC: buy 1 alpha for 150 total
        result = rt.start_deal(
            state,
            "otc_trade",
            actor_id=buyer,
            responder_id=harvester,
            params={"asset": "alpha", "qty": 1, "price": 150},
        )
        assert result["ok"]
        state = result["state"]
        iid = result["instance_id"]

        # Buyer's credits locked (150)
        assert state.get_resource(buyer, "credits") == b_credits_before - 150

        # Harvester accepts
        result2 = rt.respond_to_deal(state, iid, harvester, "accept")
        assert result2["ok"]
        state = result2["state"]

        # Buyer gets alpha, seller gets credits
        assert state.get_resource(buyer, "alpha") == 1
        assert state.get_resource(harvester, "alpha") == h_alpha_before - 1
        assert state.get_resource(harvester, "credits") == 500 + 150

    def test_otc_trade_reject(self):
        """Rejected OTC trade returns stakes."""
        state = _to_open_market()

        result = rt.start_deal(
            state,
            "otc_trade",
            actor_id="p0",
            responder_id="p1",
            params={"asset": "alpha", "qty": 1, "price": 100},
        )
        state = result["state"]
        iid = result["instance_id"]

        # Stakes locked
        assert state.get_resource("p0", "credits") == 400

        result2 = rt.respond_to_deal(state, iid, "p1", "reject")
        assert result2["ok"]
        state = result2["state"]

        # Stakes returned
        assert state.get_resource("p0", "credits") == 500

    def test_otc_trade_accept_requires_asset(self):
        """Responder cannot accept OTC if they don't have the asset."""
        state = _to_open_market()

        # Find a player who is NOT a harvester (has 0 alpha)
        non_harvester = None
        buyer = None
        for pid in PLAYERS:
            role = state.get_attr(pid, "role")
            if role != "harvester" and non_harvester is None:
                non_harvester = pid
            elif role != "harvester" and buyer is None:
                buyer = pid

        assert state.get_resource(non_harvester, "alpha") == 0

        # Buyer proposes OTC: buy 1 alpha from non_harvester
        result = rt.start_deal(
            state,
            "otc_trade",
            actor_id=buyer,
            responder_id=non_harvester,
            params={"asset": "alpha", "qty": 1, "price": 100},
        )
        assert result["ok"]
        state = result["state"]
        iid = result["instance_id"]

        # Non-harvester tries to accept (has 0 alpha) — should fail
        result2 = rt.respond_to_deal(state, iid, non_harvester, "accept")
        assert not result2["ok"]
        assert result2["error"]["code"] == "guard_failed"

        # Stakes still locked (deal still pending)
        assert state.get_resource(buyer, "credits") == 400


# =========================================================================
# Limit Orders & Settlement
# =========================================================================


class TestLimitOrders:
    def test_sell_order_locks_asset(self):
        """Sell limit order locks the asset via Damage."""
        state = _to_open_market()

        harvester = None
        for pid in PLAYERS:
            if state.get_attr(pid, "role") == "harvester":
                harvester = pid

        alpha_before = state.get_resource(harvester, "alpha")
        result = rt.start_deal(
            state,
            "limit_order",
            actor_id=harvester,
            params={"asset": "alpha", "side": "sell", "price": 110, "qty": 1},
        )
        assert result["ok"]
        state = result["state"]
        assert state.get_resource(harvester, "alpha") == alpha_before - 1

    def test_buy_order_locks_credits(self):
        """Buy limit order locks credits via Damage."""
        state = _to_open_market()

        result = rt.start_deal(
            state,
            "limit_order",
            actor_id="p3",
            params={"asset": "alpha", "side": "buy", "price": 115, "qty": 1},
        )
        assert result["ok"]
        state = result["state"]
        assert state.get_resource("p3", "credits") == 500 - 115

    def test_order_appears_in_book(self):
        """Placed order shows up in order book."""
        state = _to_open_market()

        result = rt.start_deal(
            state,
            "limit_order",
            actor_id="p0",
            params={"asset": "alpha", "side": "sell", "price": 110, "qty": 1},
        )
        state = result["state"]
        book = state.get_game_var("order_book")
        assert len(book["alpha"]) == 1
        order = book["alpha"][0]
        assert order["player"] == "p0"
        assert order["side"] == "sell"
        assert order["price"] == 110

    def test_matching_orders_trade(self):
        """Matching bid and ask execute in settlement at midpoint."""
        state = _to_open_market()

        # Find harvester (has alpha to sell)
        harvester = None
        for pid in PLAYERS:
            if state.get_attr(pid, "role") == "harvester":
                harvester = pid

        other = "p3" if harvester != "p3" else "p1"

        # Sell 1 alpha at 110
        result = rt.start_deal(
            state,
            "limit_order",
            actor_id=harvester,
            params={"asset": "alpha", "side": "sell", "price": 110, "qty": 1},
        )
        state = result["state"]

        # Buy 1 alpha at 120
        result = rt.start_deal(
            state,
            "limit_order",
            actor_id=other,
            params={"asset": "alpha", "side": "buy", "price": 120, "qty": 1},
        )
        state = result["state"]

        # Advance through settlement to next morning
        state = _full_day_cycle(state)

        # Trade at midpoint: (110 + 120) / 2 = 115
        assert state.get_game_var("prices")["alpha"] == 115
        assert state.get_game_var("trade_count") == 1

    def test_no_match_stays_on_book(self):
        """Unmatched orders stay on book with funds locked (GTC semantics)."""
        state = _to_open_market()

        # Sell at 200 (nobody will buy this high)
        harvester = None
        for pid in PLAYERS:
            if state.get_attr(pid, "role") == "harvester":
                harvester = pid

        alpha_before = state.get_resource(harvester, "alpha")
        result = rt.start_deal(
            state,
            "limit_order",
            actor_id=harvester,
            params={"asset": "alpha", "side": "sell", "price": 200, "qty": 1},
        )
        state = result["state"]
        assert state.get_resource(harvester, "alpha") == alpha_before - 1

        # Advance through settlement — no match, order stays locked
        state = _full_day_cycle(state)

        # Alpha still locked (not refunded), only day 2 production added
        assert (
            state.get_resource(harvester, "alpha") == alpha_before - 1 + 2
        )  # locked + production
        # Order still on book
        assert len(state.get_game_var("order_book")["alpha"]) == 1

    def test_no_match_when_bid_too_low(self):
        """Bid below ask price does not match."""
        state = _to_open_market()

        harvester = None
        for pid in PLAYERS:
            if state.get_attr(pid, "role") == "harvester":
                harvester = pid

        # Sell at 200
        result = rt.start_deal(
            state,
            "limit_order",
            actor_id=harvester,
            params={"asset": "alpha", "side": "sell", "price": 200, "qty": 1},
        )
        state = result["state"]

        # Buy at 100 (below ask)
        result = rt.start_deal(
            state,
            "limit_order",
            actor_id="p3",
            params={"asset": "alpha", "side": "buy", "price": 100, "qty": 1},
        )
        state = result["state"]

        # Advance through settlement
        state = _full_day_cycle(state)

        assert state.get_game_var("trade_count") == 0
        # Price unchanged
        assert state.get_game_var("prices")["alpha"] == 100


# =========================================================================
# Information Deals
# =========================================================================


class TestInformationDeals:
    def test_buy_research(self):
        """Buy research costs credits and adds research token."""
        state = _setup()
        research_before = state.get_resource("p0", "research")

        result = rt.start_deal(state, "buy_research", actor_id="p0")
        assert result["ok"]
        state = result["state"]
        assert state.get_resource("p0", "research") == research_before + 1
        assert state.get_resource("p0", "credits") == 500 - 30

    def test_investigate_position_reveals(self):
        """Investigation reveals target's commodity holdings."""
        state = _to_open_market()
        state = _advance_to(state, "research_phase")

        result = rt.start_deal(
            state,
            "investigate_position",
            actor_id="p0",
            params={"subject": "p1"},
        )
        assert result["ok"], f"investigate failed: {result.get('error')}"
        state = result["state"]
        assert state.get_resource("p0", "research") == 1  # 2 - 1
        assert state.get_resource("p0", "suspicion") == 3

        # Check reveals exist for p0 viewing p1's commodities
        view = view_for(state, "p0", exchange)
        p1_view = view["entities"]["p1"]
        # Alpha, beta, gamma, delta should be visible
        for asset in ("alpha", "beta", "gamma", "delta"):
            assert asset in p1_view["resources"]

    def test_publish_analysis(self):
        """Publishing analysis costs research and gains reputation."""
        state = _setup()

        result = rt.start_deal(
            state,
            "publish_analysis",
            actor_id="p0",
            params={"asset": "alpha"},
        )
        assert result["ok"]
        state = result["state"]
        assert state.get_resource("p0", "research") == 1  # 2 - 1
        assert state.get_resource("p0", "reputation") == 55  # 50 + 5

    def test_insider_trade(self):
        """Insider trade gives assets but adds high suspicion."""
        state = _to_open_market()

        result = rt.start_deal(
            state,
            "insider_trade",
            actor_id="p0",
            params={"asset": "beta", "qty": 3},
        )
        assert result["ok"]
        state = result["state"]
        assert state.get_resource("p0", "beta") == 3
        assert state.get_resource("p0", "research") == 0  # 2 - 2
        assert state.get_resource("p0", "suspicion") == 10


# =========================================================================
# Corporate & Regulation Deals
# =========================================================================


class TestCorporateDeals:
    def test_hostile_takeover_surrender(self):
        """Successful takeover seizes commodities."""
        state = _to_afternoon()

        # Give p1 some commodities to seize
        state = state.set_resource("p1", "alpha", 10)
        state = state.set_resource("p1", "beta", 10)
        state = state.set_resource("p1", "gamma", 10)
        state = state.set_resource("p1", "delta", 10)

        result = rt.start_deal(
            state,
            "hostile_takeover",
            actor_id="p0",
            responder_id="p1",
        )
        assert result["ok"], f"takeover failed: {result.get('error')}"
        state = result["state"]
        iid = result["instance_id"]

        result2 = rt.respond_to_deal(state, iid, "p1", "surrender")
        assert result2["ok"]
        state = result2["state"]

        # Proposer gains 5 of each commodity
        assert state.get_resource("p0", "alpha") >= 5
        assert state.get_resource("p1", "alpha") == 5  # 10 - 5

    def test_hostile_takeover_defend(self):
        """Defended takeover returns stakes, proposer loses reputation."""
        state = _to_afternoon()
        rep_before = state.get_resource("p0", "reputation")

        result = rt.start_deal(
            state,
            "hostile_takeover",
            actor_id="p0",
            responder_id="p1",
        )
        state = result["state"]
        iid = result["instance_id"]

        result2 = rt.respond_to_deal(state, iid, "p1", "defend")
        assert result2["ok"]
        state = result2["state"]

        # Stakes returned, reputation lost
        assert state.get_resource("p0", "credits") == 500  # stakes returned
        assert state.get_resource("p0", "reputation") == rep_before - 10

    def test_pay_dividend(self):
        """Dividend burns credits, gains reputation."""
        state = _to_open_market()
        state = _advance_to(state, "afternoon_market")

        result = rt.start_deal(
            state,
            "pay_dividend",
            actor_id="p0",
            params={"amount": 50},
        )
        assert result["ok"]
        state = result["state"]
        assert state.get_resource("p0", "credits") == 450  # 500 - 50
        assert state.get_resource("p0", "reputation") == 55  # 50 + 50/10

    def test_report_violation_accept_fine(self):
        """Accepting a violation report costs credits but clears suspicion."""
        state = _to_open_market()
        state = _advance_to(state, "afternoon_market")

        # Give target some suspicion
        state = state.set_resource("p1", "suspicion", 30)

        result = rt.start_deal(
            state,
            "report_violation",
            actor_id="p0",
            responder_id="p1",
        )
        assert result["ok"]
        state = result["state"]
        iid = result["instance_id"]

        result2 = rt.respond_to_deal(state, iid, "p1", "accept_fine")
        assert result2["ok"]
        state = result2["state"]

        assert state.get_resource("p1", "credits") == 450  # 500 - 50
        assert state.get_resource("p1", "suspicion") == 10  # 30 - 20

    def test_audit_defense(self):
        """Audit defense burns credits and reduces suspicion."""
        state = _to_open_market()
        state = _advance_to(state, "afternoon_market")

        state = state.set_resource("p0", "suspicion", 40)

        result = rt.start_deal(
            state,
            "audit_defense",
            actor_id="p0",
            params={"amount": 100},
        )
        assert result["ok"]
        state = result["state"]
        assert state.get_resource("p0", "credits") == 400  # 500 - 100
        assert state.get_resource("p0", "suspicion") == 20  # 40 - 100/5


# =========================================================================
# Visibility
# =========================================================================


class TestVisibility:
    def test_credits_private(self):
        """Credits are private — not visible to other players."""
        state = _setup()
        view = view_for(state, "p1", exchange)
        # p0's credits should not be in p1's view
        assert "credits" not in view["entities"]["p0"]["resources"]

    def test_reputation_public(self):
        """Reputation is public — visible to all."""
        state = _setup()
        view = view_for(state, "p1", exchange)
        assert "reputation" in view["entities"]["p0"]["resources"]
        assert view["entities"]["p0"]["resources"]["reputation"] == 50

    def test_suspicion_public(self):
        """Suspicion is public — visible to all."""
        state = _setup()
        view = view_for(state, "p1", exchange)
        assert "suspicion" in view["entities"]["p0"]["resources"]


# =========================================================================
# Victory
# =========================================================================


class TestVictory:
    def test_no_victory_early(self):
        """No victory triggered on day 1."""
        state = _setup()
        assert state.victory_result is None

    def test_market_domination(self):
        """Credits >= 2000 triggers early victory."""
        state = _to_open_market()
        state = state.set_resource("p0", "credits", 2000)

        result = rt.check_victory(state)
        assert result is not None
        assert result["condition"] == "market_domination"

    def test_trading_champion_after_10_days(self):
        """Score-based victory after 10 trading days."""
        state = _setup()
        # Fast-forward to day 11 (past total_days)
        import attrs

        new_vars = dict(state.vars_)
        new_vars["trading_day"] = 11
        state = attrs.evolve(state, vars_=new_vars)

        result = rt.check_victory(state)
        assert result is not None
        assert result["condition"] == "trading_champion"


# =========================================================================
# Suspicion Decay
# =========================================================================


class TestSuspicionDecay:
    def test_suspicion_decays_each_morning(self):
        """Suspicion decreases by 5 each morning briefing."""
        state = _setup()
        state = state.set_resource("p0", "suspicion", 20)

        # Advance through full day
        state = _advance_to(state, "open_market")
        state = _full_day_cycle(state)

        # Day 2 morning: -5 suspicion
        assert state.get_resource("p0", "suspicion") == 15

    def test_suspicion_cannot_go_below_zero(self):
        """Suspicion decay stops at 0 (bounded resource)."""
        state = _setup()
        state = state.set_resource("p0", "suspicion", 3)

        state = _advance_to(state, "open_market")
        state = _full_day_cycle(state)

        assert state.get_resource("p0", "suspicion") == 0


# =========================================================================
# Reputation Recovery
# =========================================================================


class TestReputationRecovery:
    def test_low_suspicion_gains_reputation(self):
        """Players with suspicion < 30 gain +2 reputation in close phase."""
        state = _setup()
        state = state.set_resource("p0", "suspicion", 0)
        state = state.set_resource("p0", "reputation", 40)

        state = _advance_to(state, "open_market")
        state = _full_day_cycle(state)

        # Close phase: +2 reputation, then morning again
        assert state.get_resource("p0", "reputation") == 42

    def test_high_suspicion_no_recovery(self):
        """Players with suspicion >= 30 don't get reputation recovery."""
        state = _setup()
        state = state.set_resource("p0", "suspicion", 50)
        state = state.set_resource("p0", "reputation", 40)

        state = _advance_to(state, "open_market")
        state = _full_day_cycle(state)

        # No recovery due to high suspicion. Suspicion decays by 5 (50→45)
        assert state.get_resource("p0", "reputation") == 40
        assert state.get_resource("p0", "suspicion") == 45


# =========================================================================
# Deal Phase Restrictions
# =========================================================================


class TestDealPhaseRestrictions:
    def test_limit_order_in_open_market(self):
        """Limit orders allowed in open_market."""
        state = _to_open_market()
        result = rt.start_deal(
            state,
            "limit_order",
            actor_id="p0",
            params={"asset": "alpha", "side": "sell", "price": 100, "qty": 1},
        )
        assert result["ok"]

    def test_limit_order_not_in_morning(self):
        """Limit orders not allowed in morning_briefing."""
        state = _setup()
        result = rt.start_deal(
            state,
            "limit_order",
            actor_id="p0",
            params={"asset": "alpha", "side": "sell", "price": 100, "qty": 1},
        )
        assert not result["ok"]
        assert result["error"]["code"] == "deal_not_allowed"

    def test_buy_research_in_morning(self):
        """Buy research allowed in morning_briefing."""
        state = _setup()
        result = rt.start_deal(state, "buy_research", actor_id="p0")
        assert result["ok"]

    def test_hostile_takeover_not_in_open_market(self):
        """Hostile takeover not allowed in open_market."""
        state = _to_open_market()
        result = rt.start_deal(
            state,
            "hostile_takeover",
            actor_id="p0",
            responder_id="p1",
        )
        assert not result["ok"]
        assert result["error"]["code"] == "deal_not_allowed"

    def test_hostile_takeover_in_afternoon(self):
        """Hostile takeover allowed in afternoon_market."""
        state = _to_afternoon()
        result = rt.start_deal(
            state,
            "hostile_takeover",
            actor_id="p0",
            responder_id="p1",
        )
        assert result["ok"]


# =========================================================================
# Cancel Order
# =========================================================================


class TestCancelOrder:
    def test_cancel_sell_order_refunds_asset(self):
        """Cancelling a sell order returns the locked asset."""
        state = _to_open_market()

        harvester = None
        for pid in PLAYERS:
            if state.get_attr(pid, "role") == "harvester":
                harvester = pid

        alpha_before = state.get_resource(harvester, "alpha")

        # Place sell order
        result = rt.start_deal(
            state,
            "limit_order",
            actor_id=harvester,
            params={"asset": "alpha", "side": "sell", "price": 110, "qty": 1},
        )
        state = result["state"]
        assert state.get_resource(harvester, "alpha") == alpha_before - 1

        # Get the order ID
        book = state.get_game_var("order_book")
        order_id = book["alpha"][0]["id"]

        # Cancel
        result = rt.start_deal(
            state,
            "cancel_order",
            actor_id=harvester,
            params={"order_id": order_id},
        )
        assert result["ok"]
        state = result["state"]

        # Asset refunded
        assert state.get_resource(harvester, "alpha") == alpha_before
        # Order removed from book
        assert len(state.get_game_var("order_book")["alpha"]) == 0

    def test_cancel_buy_order_refunds_credits(self):
        """Cancelling a buy order returns the locked credits."""
        state = _to_open_market()

        result = rt.start_deal(
            state,
            "limit_order",
            actor_id="p0",
            params={"asset": "beta", "side": "buy", "price": 120, "qty": 2},
        )
        state = result["state"]
        assert state.get_resource("p0", "credits") == 500 - 240  # 120 * 2

        order_id = state.get_game_var("order_book")["beta"][0]["id"]

        result = rt.start_deal(
            state,
            "cancel_order",
            actor_id="p0",
            params={"order_id": order_id},
        )
        assert result["ok"]
        state = result["state"]

        assert state.get_resource("p0", "credits") == 500

    def test_cancel_others_order_fails(self):
        """Cannot cancel another player's order."""
        state = _to_open_market()

        result = rt.start_deal(
            state,
            "limit_order",
            actor_id="p0",
            params={"asset": "alpha", "side": "buy", "price": 100, "qty": 1},
        )
        state = result["state"]
        order_id = state.get_game_var("order_book")["alpha"][0]["id"]

        # p1 tries to cancel p0's order
        result = rt.start_deal(
            state,
            "cancel_order",
            actor_id="p1",
            params={"order_id": order_id},
        )
        assert result["ok"]  # deal itself succeeds (effect handles validation)
        state = result["state"]

        # Order should still be in book (cancel failed silently)
        assert len(state.get_game_var("order_book")["alpha"]) == 1


# =========================================================================
# Market Orders
# =========================================================================


class TestMarketOrder:
    def test_market_buy_locks_proportional_credits(self):
        """Market buy order locks max_price × qty credits."""
        state = _to_open_market()

        result = rt.start_deal(
            state,
            "market_order",
            actor_id="p0",
            params={"asset": "alpha", "side": "buy", "qty": 2, "max_price": 150},
        )
        assert result["ok"]
        state = result["state"]
        assert state.get_resource("p0", "credits") == 500 - 300  # 150 * 2

    def test_market_sell_locks_asset(self):
        """Market sell order locks the asset."""
        state = _to_open_market()

        harvester = None
        for pid in PLAYERS:
            if state.get_attr(pid, "role") == "harvester":
                harvester = pid

        alpha_before = state.get_resource(harvester, "alpha")
        result = rt.start_deal(
            state,
            "market_order",
            actor_id=harvester,
            params={"asset": "alpha", "side": "sell", "qty": 1},
        )
        assert result["ok"]
        state = result["state"]
        assert state.get_resource(harvester, "alpha") == alpha_before - 1


# =========================================================================
# Self-Trade Prevention
# =========================================================================


class TestSelfTradePrevention:
    def test_self_trade_prevented(self):
        """Same player's buy and sell orders do not match."""
        state = _to_open_market()

        harvester = None
        for pid in PLAYERS:
            if state.get_attr(pid, "role") == "harvester":
                harvester = pid

        # Place both buy and sell for same player
        result = rt.start_deal(
            state,
            "limit_order",
            actor_id=harvester,
            params={"asset": "alpha", "side": "sell", "price": 100, "qty": 1},
        )
        state = result["state"]

        result = rt.start_deal(
            state,
            "limit_order",
            actor_id=harvester,
            params={"asset": "alpha", "side": "buy", "price": 110, "qty": 1},
        )
        state = result["state"]

        # Advance through settlement
        state = _full_day_cycle(state)

        # No trade should have occurred
        assert state.get_game_var("trade_count") == 0


# =========================================================================
# Speech Acts
# =========================================================================


class TestSpeechActs:
    def test_predict_price_correct(self):
        """Correct price prediction rewards reputation."""
        state = _to_open_market()

        # Make a prediction: alpha will go up
        result = rt.execute_speech_act(
            state,
            "predict_price",
            actor_id="p0",
            params={"asset": "alpha", "direction": "up"},
        )
        assert result["ok"], f"speech act failed: {result.get('error')}"
        state = result["state"]
        # Cost: -3 reputation
        assert state.get_resource("p0", "reputation") == 47

        # Now make alpha price go up: place orders that will trade above 100
        harvester = None
        for pid in PLAYERS:
            if state.get_attr(pid, "role") == "harvester":
                harvester = pid

        # Sell at 110
        result = rt.start_deal(
            state,
            "limit_order",
            actor_id=harvester,
            params={"asset": "alpha", "side": "sell", "price": 110, "qty": 1},
        )
        state = result["state"]

        # Buy at 120 (different player)
        buyer = "p3" if harvester != "p3" else "p1"
        result = rt.start_deal(
            state,
            "limit_order",
            actor_id=buyer,
            params={"asset": "alpha", "side": "buy", "price": 120, "qty": 1},
        )
        state = result["state"]

        # Advance through settlement (where price updates)
        state = _full_day_cycle(state)

        # Price is now 115 (midpoint of 110, 120). History: [100, 115].
        # Prediction "up": 115 > 100 ✓
        # Phase change triggered verification.
        # Check that prediction was verified as correct
        assert len(state.resolved_speech_acts) >= 1
        sa = [
            s for s in state.resolved_speech_acts if s.speech_act_id == "predict_price"
        ]
        assert len(sa) >= 1
        assert sa[0].status == "verified_true"

    def test_predict_price_wrong(self):
        """Wrong price prediction penalizes reputation."""
        state = _to_open_market()

        # Predict alpha will go DOWN
        result = rt.execute_speech_act(
            state,
            "predict_price",
            actor_id="p0",
            params={"asset": "alpha", "direction": "down"},
        )
        state = result["state"]

        # Make alpha price go UP
        harvester = None
        for pid in PLAYERS:
            if state.get_attr(pid, "role") == "harvester":
                harvester = pid

        result = rt.start_deal(
            state,
            "limit_order",
            actor_id=harvester,
            params={"asset": "alpha", "side": "sell", "price": 110, "qty": 1},
        )
        state = result["state"]

        buyer = "p3" if harvester != "p3" else "p1"
        result = rt.start_deal(
            state,
            "limit_order",
            actor_id=buyer,
            params={"asset": "alpha", "side": "buy", "price": 120, "qty": 1},
        )
        state = result["state"]

        state = _full_day_cycle(state)

        # Prediction wrong: price went up, predicted down
        sa = [
            s for s in state.resolved_speech_acts if s.speech_act_id == "predict_price"
        ]
        assert len(sa) >= 1
        assert sa[0].status == "verified_false"

    def test_predict_price_no_trades_fails(self):
        """Prediction with no trades (price unchanged) fails verification."""
        state = _to_open_market()

        # Predict alpha up — but no trades happen
        result = rt.execute_speech_act(
            state,
            "predict_price",
            actor_id="p0",
            params={"asset": "alpha", "direction": "up"},
        )
        state = result["state"]

        # Advance through day with no orders
        state = _full_day_cycle(state)

        # price_history still [100], get_var(-2) = None → some?() = False → verified_false
        sa = [
            s for s in state.resolved_speech_acts if s.speech_act_id == "predict_price"
        ]
        assert len(sa) >= 1
        assert sa[0].status == "verified_false"

    def test_accuse_manipulation_verified(self):
        """Accusation verified when target suspicion >= 30."""
        state = _to_open_market()
        state = state.set_resource("p1", "suspicion", 40)

        result = rt.execute_speech_act(
            state,
            "accuse_manipulation",
            actor_id="p0",
            target_id="p1",
            params={"claim": "insider trading"},
        )
        assert result["ok"]
        state = result["state"]

        # Advance to trigger phase_change verification
        state = _advance_to(state, "research_phase")

        sa = [
            s
            for s in state.resolved_speech_acts
            if s.speech_act_id == "accuse_manipulation"
        ]
        assert len(sa) >= 1
        assert sa[0].status == "verified_true"


# =========================================================================
# Votes
# =========================================================================


class TestVotes:
    def test_market_halt_vote(self):
        """Market halt vote with supermajority reduces suspicion."""
        state = _to_open_market()
        # Give everyone suspicion
        for pid in PLAYERS:
            state = state.set_resource(pid, "suspicion", 30)

        result = rt.start_vote(state, "market_halt", proposer_id="p0")
        assert result["ok"], f"vote failed: {result.get('error')}"
        state = result["state"]
        vid = result["instance_id"]

        # All vote halt (supermajority = all 4)
        for pid in PLAYERS:
            result = rt.cast_vote(state, vid, pid, "halt")
            assert result["ok"]
            state = result["state"]

        # Suspicion reduced by 15 for everyone
        for pid in PLAYERS:
            assert state.get_resource(pid, "suspicion") == 15

    def test_elect_regulator_majority(self):
        """Elect regulator vote with majority succeeds."""
        state = _to_open_market()

        result = rt.start_vote(state, "elect_regulator", proposer_id="p0")
        assert result["ok"], f"vote failed: {result.get('error')}"
        state = result["state"]
        vid = result["instance_id"]

        # 3 out of 4 vote elect (majority)
        for pid in ["p0", "p1", "p2"]:
            result = rt.cast_vote(state, vid, pid, "elect")
            assert result["ok"]
            state = result["state"]
        result = rt.cast_vote(state, vid, "p3", "oppose")
        assert result["ok"]
        state = result["state"]

        # Vote should have resolved with "elect" outcome
        assert result["outcome"] == "elect"

    def test_elect_regulator_fails(self):
        """Elect regulator vote fails without majority."""
        state = _to_open_market()

        result = rt.start_vote(state, "elect_regulator", proposer_id="p0")
        assert result["ok"]
        state = result["state"]
        vid = result["instance_id"]

        # Only 1 votes elect, 3 oppose
        result = rt.cast_vote(state, vid, "p0", "elect")
        assert result["ok"]
        state = result["state"]
        for pid in ["p1", "p2", "p3"]:
            result = rt.cast_vote(state, vid, pid, "oppose")
            assert result["ok"]
            state = result["state"]

        assert result["outcome"] == "oppose"

    def test_bailout_vote_majority(self):
        """Bailout vote with majority costs everyone 25 credits."""
        state = _to_open_market()

        result = rt.start_vote(state, "bailout_vote", proposer_id="p0")
        assert result["ok"]
        state = result["state"]
        vid = result["instance_id"]

        # 3 out of 4 vote bailout (majority)
        for pid in ["p0", "p1", "p2"]:
            result = rt.cast_vote(state, vid, pid, "bailout")
            assert result["ok"]
            state = result["state"]
        result = rt.cast_vote(state, vid, "p3", "refuse")
        assert result["ok"]
        state = result["state"]

        # Everyone pays 25 credits
        for pid in PLAYERS:
            assert state.get_resource(pid, "credits") == 475

    def test_bailout_vote_refused(self):
        """Bailout vote fails when majority refuses — no credits deducted."""
        state = _to_open_market()

        result = rt.start_vote(state, "bailout_vote", proposer_id="p0")
        assert result["ok"]
        state = result["state"]
        vid = result["instance_id"]

        # Only 1 votes bailout, 3 refuse
        result = rt.cast_vote(state, vid, "p0", "bailout")
        assert result["ok"]
        state = result["state"]
        for pid in ["p1", "p2", "p3"]:
            result = rt.cast_vote(state, vid, pid, "refuse")
            assert result["ok"]
            state = result["state"]

        assert result["outcome"] == "refuse"
        # No credits deducted
        for pid in PLAYERS:
            assert state.get_resource(pid, "credits") == 500


# =========================================================================
# Archive & Replay
# =========================================================================


class TestReplay:
    def test_archive_and_replay(self):
        """Full game can be archived and replayed deterministically."""
        state = _setup()
        state = _advance_to(state, "open_market")

        # Place some orders
        result = rt.start_deal(
            state,
            "limit_order",
            actor_id="p0",
            params={"asset": "alpha", "side": "sell", "price": 110, "qty": 1},
        )
        state = result["state"]

        result = rt.start_deal(
            state,
            "limit_order",
            actor_id="p3",
            params={"asset": "alpha", "side": "buy", "price": 120, "qty": 1},
        )
        state = result["state"]

        # Complete the day
        state = _full_day_cycle(state)

        # Create and verify archive
        archive = create_archive(exchange, state)
        assert verify(archive, exchange)

        # Replay produces same state
        replayed = replay(archive, exchange)
        assert replayed.phase == state.phase
        assert replayed.get_game_var("trade_count") == state.get_game_var("trade_count")
        assert replayed.get_game_var("prices") == state.get_game_var("prices")

        for pid in PLAYERS:
            assert replayed.get_resource(pid, "credits") == state.get_resource(
                pid, "credits"
            )
            assert replayed.get_resource(pid, "alpha") == state.get_resource(
                pid, "alpha"
            )


# =========================================================================
# Margin Call Commitment
# =========================================================================


class TestMarginCall:
    """Tests for margin_call commitment — emergency loan on bankrupt."""

    def test_margin_call_fires_on_phase_change_when_bankrupt(self):
        """Player with 0 credits gets emergency loan on phase transition."""
        state = _setup()
        # Drain p0's credits to 0
        credits = state.get_resource("p0", "credits")
        state = state.adjust_resource("p0", "credits", -credits, exchange)
        assert state.get_resource("p0", "credits") == 0

        initial_rep = state.get_resource("p0", "reputation")
        initial_sus = state.get_resource("p0", "suspicion")

        # Advance phase — should trigger margin_call
        state = _advance_to(state, "open_market")

        # Player should have received emergency loan
        assert state.get_resource("p0", "credits") >= 100, (
            f"Margin call should give +100 credits, got {state.get_resource('p0', 'credits')}"
        )
        # Reputation penalty
        assert state.get_resource("p0", "reputation") < initial_rep
        # Suspicion increase
        assert state.get_resource("p0", "suspicion") > initial_sus

    def test_no_margin_call_with_positive_credits(self):
        """Player with positive credits should NOT trigger margin_call."""
        state = _setup()
        initial_credits = state.get_resource("p0", "credits")
        assert initial_credits > 0

        initial_rep = state.get_resource("p0", "reputation")

        # Advance phase — should NOT trigger margin_call
        state = _advance_to(state, "open_market")

        # Credits should change only from production/morning effects, not from margin call
        # Reputation should not have the -15 margin call penalty
        # (morning briefing doesn't add reputation, so it should stay the same)
        assert state.get_resource("p0", "reputation") == initial_rep
