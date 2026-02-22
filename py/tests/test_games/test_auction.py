"""Art Auction: Mechanism Design — game-specific integration tests.

Tests auction setup, bid mechanics, auction format guards, vote lifecycle,
settlement, victory conditions, and deterministic replay.
"""

from __future__ import annotations

from engine.archive import create_archive, verify
from engine.runtime.core import GameRuntime
from engine.runtime.state import GameState, view_for
from games.auction import auction
from mcp.formatters import _can_player_use_deal, format_available_actions

# =========================================================================
# Helpers
# =========================================================================

PLAYERS = ["alice", "bob", "charlie"]

rt = GameRuntime(auction)


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


# =========================================================================
# Setup
# =========================================================================


class TestSetup:
    def test_initial_phase_is_preview(self):
        """After setup, game is in preview phase."""
        state = _setup()
        assert state.phase == "preview"

    def test_initial_resources(self):
        """Players start with correct resources."""
        state = _setup()
        for pid in PLAYERS:
            assert state.get_resource(pid, "gold") == 1000
            assert state.get_resource(pid, "credit") == 500
            assert state.get_resource(pid, "reputation") == 50
            assert state.get_resource(pid, "collection_value") == 0
            assert state.get_resource(pid, "insider_info") == 0

    def test_game_vars_initialized(self):
        """Game variables set in setup."""
        state = _setup()
        assert state.get_game_var("current_lot") == 1
        assert state.get_game_var("total_lots") == 6
        assert state.get_game_var("auction_type") == "first_price"
        assert state.get_game_var("highest_bid") == 0
        assert state.get_game_var("highest_bidder") == ""

    def test_preview_resets_bids(self):
        """Preview phase resets bids and sets lot value."""
        state = _setup()
        # Preview effects should have set lot_value = 100 + 1*60 = 160
        assert state.get_game_var("lot_value") == 160
        for pid in PLAYERS:
            assert state.get_attr(pid, "current_bid") == 0


# =========================================================================
# Auction Format Guards
# =========================================================================


class TestFormatGuards:
    def test_sealed_bid_only_in_first_price(self):
        """sealed_bid requires auction_type == first_price."""
        state = _setup()
        state = _advance_to(state, "bidding")

        # Default is first_price
        can = _can_player_use_deal(state, auction, "sealed_bid", "alice")
        assert can, "sealed_bid should be available in first_price"

        can = _can_player_use_deal(state, auction, "english_bid", "alice")
        assert not can, "english_bid should NOT be available in first_price"

        can = _can_player_use_deal(state, auction, "vickrey_bid", "alice")
        assert not can, "vickrey_bid should NOT be available in first_price"

    def test_pass_bid_always_available(self):
        """pass_bid has no guard — always available."""
        state = _setup()
        state = _advance_to(state, "bidding")

        can = _can_player_use_deal(state, auction, "pass_bid", "alice")
        assert can, "pass_bid should always be available"


# =========================================================================
# Sealed Bid
# =========================================================================


class TestSealedBid:
    def test_sealed_bid_locks_amount(self):
        """Sealed bid sets current_bid and locks gold as stakes."""
        state = _setup()
        state = _advance_to(state, "bidding")

        result = rt.start_deal(
            state, "sealed_bid", actor_id="alice", params={"amount": 200}
        )
        assert result["ok"], f"sealed_bid failed: {result}"
        state = result["state"]
        assert state.get_attr("alice", "current_bid") == 200

    def test_pass_bid_sets_zero(self):
        """Pass bid sets current_bid to 0."""
        state = _setup()
        state = _advance_to(state, "bidding")

        result = rt.start_deal(state, "pass_bid", actor_id="alice")
        assert result["ok"]
        state = result["state"]
        assert state.get_attr("alice", "current_bid") == 0


# =========================================================================
# Format Vote
# =========================================================================


class TestFormatVote:
    def test_choose_english_format(self):
        """Voting for english changes auction_type."""
        state = _setup()
        state = _advance_to(state, "format_vote")

        result = rt.start_vote(state, "choose_format")
        assert result["ok"], f"start choose_format failed: {result}"
        state = result["state"]
        iid = result["instance_id"]

        for pid in PLAYERS:
            result = rt.cast_vote(state, iid, pid, "english")
            if result["ok"]:
                state = result["state"]

        assert state.get_game_var("auction_type") == "english"

    def test_choose_vickrey_format(self):
        """Voting for vickrey changes auction_type."""
        state = _setup()
        state = _advance_to(state, "format_vote")

        result = rt.start_vote(state, "choose_format")
        assert result["ok"]
        state = result["state"]
        iid = result["instance_id"]

        for pid in PLAYERS:
            result = rt.cast_vote(state, iid, pid, "vickrey")
            if result["ok"]:
                state = result["state"]

        assert state.get_game_var("auction_type") == "vickrey"


# =========================================================================
# English Auction
# =========================================================================


class TestEnglishAuction:
    def test_english_bid_raises(self):
        """Valid english bid above highest_bid is accepted."""
        state = _setup()
        state = _advance_to(state, "format_vote")

        # Vote for english
        result = rt.start_vote(state, "choose_format")
        state = result["state"]
        iid = result["instance_id"]
        for pid in PLAYERS:
            result = rt.cast_vote(state, iid, pid, "english")
            if result["ok"]:
                state = result["state"]

        state = _advance_to(state, "bidding")
        assert state.get_game_var("auction_type") == "english"

        result = rt.start_deal(
            state, "english_bid", actor_id="alice", params={"amount": 100}
        )
        assert result["ok"], f"english_bid failed: {result}"
        state = result["state"]
        assert state.get_game_var("highest_bid") == 100.0
        assert state.get_game_var("highest_bidder") == "alice"


# =========================================================================
# Information & Strategy
# =========================================================================


class TestInformation:
    def test_buy_info_gives_insider_and_bonus(self):
        """Buying info costs gold and gives insider_info + taste_bonus."""
        state = _setup()
        initial_gold = state.get_resource("alice", "gold")
        result = rt.start_deal(state, "buy_info", actor_id="alice")
        assert result["ok"], f"buy_info failed: {result}"
        state = result["state"]
        assert state.get_resource("alice", "insider_info") == 1
        # taste_bonus is boosted as a resource by the effect
        assert state.get_resource("alice", "taste_bonus") == 10
        # Gold reduced by 50 (stakes)
        assert state.get_resource("alice", "gold") < initial_gold

    def test_bidding_ring_expose(self):
        """Exposing a bidding ring damages proposer reputation."""
        state = _setup()
        state = _advance_to(state, "intermission")

        initial_rep = state.get_resource("alice", "reputation")
        result = rt.start_deal(
            state, "bidding_ring", actor_id="alice", responder_id="bob"
        )
        assert result["ok"], f"bidding_ring failed: {result}"
        state = result["state"]
        iid = result["instance_id"]

        result = rt.respond_to_deal(state, iid, "bob", "expose")
        assert result["ok"]
        state = result["state"]
        # Alice's reputation should be devastated
        assert state.get_resource("alice", "reputation") < initial_rep

    def test_take_credit_adds_gold(self):
        """Taking credit gives gold but costs collection_value."""
        state = _setup()
        state = _advance_to(state, "intermission")

        initial_gold = state.get_resource("alice", "gold")
        result = rt.start_deal(
            state, "take_credit", actor_id="alice", params={"amount": 100}
        )
        assert result["ok"], f"take_credit failed: {result}"
        state = result["state"]
        assert state.get_resource("alice", "gold") >= initial_gold + 100
        # Credit reduced
        assert state.get_resource("alice", "credit") < 500


# =========================================================================
# Settlement & Lot Progression
# =========================================================================


class TestSettlement:
    def test_winner_gets_collection_value(self):
        """Winning bidder gets collection_value boost in settlement."""
        state = _setup()
        state = _advance_to(state, "bidding")

        # Alice bids
        result = rt.start_deal(
            state, "sealed_bid", actor_id="alice", params={"amount": 150}
        )
        assert result["ok"]
        state = result["state"]

        # Bob passes
        result = rt.start_deal(state, "pass_bid", actor_id="bob")
        assert result["ok"]
        state = result["state"]

        # Advance through reveal + settlement (both automatic)
        state = _advance(state)
        # Should be at intermission (after reveal + settlement cascade)
        assert state.phase == "intermission"
        # Alice should be highest bidder and receive lot value
        assert state.get_resource("alice", "collection_value") > 0

    def test_lot_number_advances(self):
        """After settlement, current_lot increases."""
        state = _setup()
        assert state.get_game_var("current_lot") == 1

        # Play through one full round
        state = _advance_to(state, "intermission")

        assert state.get_game_var("current_lot") == 2


# =========================================================================
# Phase Cycle
# =========================================================================


class TestPhaseCycle:
    def test_full_round_phases(self):
        """A full round cycles: preview -> format_vote -> bidding -> (auto) -> intermission."""
        state = _setup()
        assert state.phase == "preview"

        state = _advance(state)
        assert state.phase == "format_vote"

        state = _advance(state)
        assert state.phase == "bidding"

        state = _advance(state)
        # reveal + settlement are automatic, cascade to intermission
        assert state.phase == "intermission"

        state = _advance(state)
        # Next round: preview
        assert state.phase == "preview"


# =========================================================================
# Visibility
# =========================================================================


class TestVisibility:
    def test_gold_is_private(self):
        """Gold is private — not visible to others."""
        state = _setup()
        view = view_for(state, "alice", auction)
        assert "gold" not in view["entities"]["bob"]["resources"]

    def test_reputation_is_public(self):
        """Reputation is public."""
        state = _setup()
        view = view_for(state, "alice", auction)
        assert "reputation" in view["entities"]["bob"]["resources"]

    def test_collection_value_is_public(self):
        """Collection value is public."""
        state = _setup()
        view = view_for(state, "alice", auction)
        assert "collection_value" in view["entities"]["bob"]["resources"]


# =========================================================================
# Victory Conditions
# =========================================================================


class TestVictory:
    def test_no_victory_before_lot_6(self):
        """No victory until all lots are played."""
        state = _setup()
        victory = rt.check_victory(state)
        assert victory is None


# =========================================================================
# Replay
# =========================================================================


class TestReplay:
    def test_bids_and_votes_replay(self):
        """Bids + format votes replay deterministically."""
        state = _setup(seed=42)

        # Buy info
        result = rt.start_deal(state, "buy_info", actor_id="alice")
        if result["ok"]:
            state = result["state"]

        # Advance to format_vote
        state = _advance_to(state, "format_vote")

        # Vote for english
        result = rt.start_vote(state, "choose_format")
        assert result["ok"]
        state = result["state"]
        iid = result["instance_id"]
        for pid in PLAYERS:
            result = rt.cast_vote(state, iid, pid, "english")
            if result["ok"]:
                state = result["state"]

        # Advance to bidding
        state = _advance_to(state, "bidding")

        # English bid
        result = rt.start_deal(
            state, "english_bid", actor_id="alice", params={"amount": 100}
        )
        if result["ok"]:
            state = result["state"]

        # Advance through rest of round
        state = _advance_to(state, "intermission")

        archive = create_archive(auction, state)
        result = verify(archive, auction)
        assert result["valid"], (
            f"Replay mismatch: expected {result['decisions_expected']}, "
            f"got {result['decisions_replayed']}"
        )
