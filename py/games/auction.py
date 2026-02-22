"""Art Auction: Mechanism Design — economic game with multiple auction formats.

Explores auction theory, information economics, and strategic bidding.
5 auction formats × 6 painting lots × collusion/exposure dynamics.

Game theory foundations:
- First-price sealed bid: strategic underbidding (bid shading)
- Vickrey second-price: truthful bidding is dominant strategy
- English ascending: price discovery, no winner's curse
- Dutch descending: optimal stopping problem
- All-pay: war of attrition, sunk cost commitment

Communication is strategic:
- Auction floor for signaling and misdirection
- Bidding rings for collusion (can be exposed!)
- Information purchasing creates asymmetry

Ported from: src/cljc/parlameme/v3/games/auction.cljc
"""

from engine.dsl.builder import Game
from engine.expr import Ref, actor, alive, game, params
from engine.runtime.effects import (
    Boost,
    Broadcast,
    BurnStakes,
    Cond,
    Damage,
    Each,
    Notify,
    ReturnStakes,
    SetAttr,
    SetVar,
    When,
)
from engine.runtime.state import ChannelHint, OutcomeDef, PhaseHint, VarHint

auction = (
    Game("auction", "Art Auction: Mechanism Design", players=(3, 16))
    # ── Game Parameters ────────────────────────────────
    .param("total_lots", default=6, min=3, max=12, label="Number of lots")
    .param("starting_gold", default=1000, min=500, max=2000, label="Starting gold")
    # ── Resources ─────────────────────────────────────
    # Gold: primary currency (private — budget is secret)
    .resource("gold", initial=1000, visibility="private", bounds=(0, None))
    # Credit: borrowing capacity
    .resource("credit", initial=500, visibility="private", bounds=(0, 1000))
    # Reputation: auction house standing (public signal)
    .resource("reputation", initial=50, visibility="public", bounds=(0, 100))
    # Collection value: portfolio worth (public)
    .resource("collection_value", initial=0, visibility="public", bounds=(0, None))
    # Insider info: private information tokens
    .resource("insider_info", initial=0, visibility="private", bounds=(0, 5))
    # ── Attributes ────────────────────────────────────
    # Per-player auction state (hidden from others)
    .attr("current_bid", visibility="hidden", initial=0)
    .attr("taste_bonus", visibility="hidden", initial=0)
    .attr("credit_taken", visibility="hidden", initial=0)
    # ── Channels ──────────────────────────────────────
    # Auction floor: public discussion and signaling
    .channel(
        "auction_floor",
        type="public",
        description="Public auction floor — discuss lots, signal intentions, negotiate",
    )
    # Auctioneer: official announcements (broadcast)
    .channel(
        "auctioneer",
        type="broadcast",
        description="Official auction announcements: lot details, results, settlements",
    )
    # ── Sealed Bid Deals ──────────────────────────────
    # First-price sealed bid — strategic underbidding optimal
    .deal(
        "sealed_bid",
        actor=alive(),
        guard=game.auction_type == "first_price",
        per_phase=1,
        params={"amount": {"type": "number", "min": 1, "label": "Bid amount"}},
        stakes={"actor": [("gold", "amount")]},
        effects=[
            SetAttr("actor", "current_bid", params.amount),
            Notify("actor", "Your sealed bid of {amount} is locked."),
        ],
        doc="First-price sealed bid. Winner pays their bid. Strategic underbidding optimal.",
    )
    # Pass on auction — opt out of current lot
    .deal(
        "pass_bid",
        actor=alive(),
        per_phase=1,
        effects=[
            SetAttr("actor", "current_bid", 0),
            Notify("actor", "You pass on this lot."),
        ],
        doc="Pass on current auction. May signal weakness or wisdom.",
    )
    # Vickrey second-price — truthful bidding is dominant strategy
    .deal(
        "vickrey_bid",
        actor=alive(),
        guard=game.auction_type == "vickrey",
        per_phase=1,
        params={"amount": {"type": "number", "min": 1, "label": "True value bid"}},
        stakes={"actor": [("gold", "amount")]},
        effects=[
            SetAttr("actor", "current_bid", params.amount),
            Notify(
                "actor", "Your Vickrey bid is recorded. Truthful bidding is optimal."
            ),
        ],
        doc="Vickrey auction: highest wins, pays second-highest price. Truthful bidding dominant.",
    )
    # All-pay bid — war of attrition, everyone pays
    .deal(
        "all_pay_bid",
        actor=alive(),
        guard=game.auction_type == "all_pay",
        per_phase=1,
        params={"amount": {"type": "number", "min": 1, "label": "Commitment amount"}},
        stakes={"actor": [("gold", "amount")]},
        effects=[
            BurnStakes(),  # Key: bid is spent regardless of outcome
            SetAttr("actor", "current_bid", params.amount),
            Notify(
                "actor", "Your all-pay bid of {amount} is committed (non-refundable)."
            ),
        ],
        doc="All-pay auction: everyone pays their bid, highest wins. War of attrition.",
    )
    # ── English Ascending Deals ───────────────────────
    # English bid — ascending price discovery
    # Uses When effects instead of outcome guards so params.amount is available
    .deal(
        "english_bid",
        actor=alive(),
        guard=game.auction_type == "english",
        params={"amount": {"type": "number", "min": 1, "label": "Bid amount"}},
        stakes={"actor": [("gold", "amount")]},
        outcomes={
            "raise": OutcomeDef(
                effects=(
                    SetAttr("actor", "current_bid", params.amount),
                    SetVar("highest_bid", params.amount),
                    SetVar("highest_bidder", actor.id),
                    Broadcast("{actor} bids {amount}!"),
                ),
                guard=params.amount > game.highest_bid,
                priority=10,
                doc="Valid raise — exceeds current high bid",
            ),
            "invalid": OutcomeDef(
                effects=(
                    ReturnStakes(),
                    Notify(
                        "actor",
                        "Bid must exceed current high bid of {game.highest_bid}.",
                    ),
                ),
                doc="Bid too low — returned",
            ),
        },
        doc="English ascending bid. Must exceed current high. Reveals valuation.",
    )
    # Jump bid — costly signal of high valuation (50%+ above current)
    .deal(
        "jump_bid",
        actor=alive(),
        guard=game.auction_type == "english",
        params={"amount": {"type": "number", "min": 1, "label": "Jump bid amount"}},
        stakes={"actor": [("gold", "amount")]},
        outcomes={
            "jump": OutcomeDef(
                effects=(
                    SetAttr("actor", "current_bid", params.amount),
                    SetVar("highest_bid", params.amount),
                    SetVar("highest_bidder", actor.id),
                    Boost("actor", "reputation", 5),
                    Broadcast("{actor} JUMPS to {amount}!"),
                ),
                guard=params.amount > game.highest_bid * 1.5,
                priority=10,
                doc="Valid jump bid — 50%+ above current, signals strength",
            ),
            "invalid": OutcomeDef(
                effects=(
                    ReturnStakes(),
                    Notify("actor", "Jump bid must be 50%+ above current high bid."),
                ),
                doc="Jump bid too low",
            ),
        },
        doc="Jump bid significantly above current. Signals strength, discourages competition.",
    )
    # ── Dutch Descending Deal ─────────────────────────
    # Dutch claim — first to claim at descending price wins
    .deal(
        "dutch_claim",
        actor=alive(),
        guard=(game.auction_type == "dutch") & (game.highest_bidder == ""),
        effects=[
            SetVar("highest_bidder", actor.id),
            SetVar("highest_bid", game.dutch_price),
            Damage("actor", "gold", game.dutch_price),
            Boost("actor", "collection_value", game.lot_value),
            Broadcast("{actor} claims the lot at {game.dutch_price} gold!"),
        ],
        doc="Claim item at current descending price. Timing game — wait for lower price or lose it.",
    )
    # ── Information & Strategy Deals ──────────────────
    # Buy insider info — information market (reveals taste bonus for this lot)
    .deal(
        "buy_info",
        actor=alive(),
        per_phase=1,
        stakes={"actor": [("gold", 50)]},
        effects=[
            Boost("actor", "insider_info", 1),
            # Taste bonus: insider info lets you extract more value from lots
            Boost("actor", "taste_bonus", 10),
            Notify(
                "actor",
                "You learn the lot is worth {game.lot_value}. Your taste bonus: +10.",
            ),
        ],
        doc="Purchase insider info about painting's true value. 50 gold. Adds +10 taste bonus to collection.",
    )
    # Bidding ring — collusion with exposure risk
    .deal(
        "bidding_ring",
        proposer=alive(),
        responder=alive(),
        stakes={"proposer": [("reputation", 15)]},
        responses=["join", "reject", "expose"],
        outcomes={
            "join": OutcomeDef(
                effects=(
                    ReturnStakes(),
                    Damage("proposer", "reputation", 5),
                    Damage("responder", "reputation", 5),
                    Notify(
                        "proposer", "Ring formed with {responder}. Bid low together."
                    ),
                    Notify("responder", "You join {proposer}'s bidding ring."),
                ),
                doc="Join the ring — both lose some reputation but can collude",
            ),
            "reject": OutcomeDef(
                effects=(ReturnStakes(),),
                doc="Decline the ring proposal",
            ),
            "expose": OutcomeDef(
                effects=(
                    BurnStakes(),
                    Damage("proposer", "reputation", 30),
                    Boost("responder", "reputation", 20),
                    Broadcast("{responder} exposes {proposer}'s collusion attempt!"),
                ),
                doc="Expose the collusion — devastating reputation damage",
            ),
        },
        doc="Propose collusion to suppress bids. High risk if exposed.",
    )
    # Take credit — leverage affects risk behavior
    .deal(
        "take_credit",
        actor=alive(),
        per_round=1,
        params={
            "amount": {
                "type": "number",
                "min": 50,
                "max": 300,
                "label": "Credit amount",
            }
        },
        effects=[
            Damage("actor", "credit", "amount"),
            Boost("actor", "gold", "amount"),
            # Track total credit taken for interest calculation at game end
            Boost("actor", "collection_value", params.amount * -0.2),
            Notify(
                "actor",
                "Borrowed {amount} gold. 20% interest deducted from collection value.",
            ),
        ],
        doc="Borrow against future winnings. 20% interest deducted from collection value immediately.",
    )
    # Appraise collection — public portfolio evaluation
    .deal(
        "appraise",
        actor=alive(),
        stakes={"actor": [("gold", 25)]},
        effects=[
            Broadcast("{actor}'s collection is worth {actor.collection_value} gold."),
        ],
        doc="Public appraisal of your collection. Costs 25 gold.",
    )
    # Gift art — strategic giving for reputation
    .deal(
        "gift_art",
        proposer=alive(),
        responder=alive(),
        responses=["accept", "decline"],
        outcomes={
            "accept": OutcomeDef(
                effects=(
                    Damage("proposer", "collection_value", 100),
                    Boost("responder", "collection_value", 100),
                    Boost("proposer", "reputation", 10),
                    Boost("responder", "reputation", 5),
                    Broadcast("{proposer} gifts art to {responder}!"),
                ),
                doc="Accept the gift — both gain reputation",
            ),
            "decline": OutcomeDef(
                effects=(
                    Boost("proposer", "reputation", 3),
                    Broadcast("{responder} declines {proposer}'s gift."),
                ),
                doc="Politely decline",
            ),
        },
        doc="Gift a painting. Builds relationship, signals wealth.",
    )
    # ── Votes ─────────────────────────────────────────
    # Choose auction format for next lot
    .vote(
        "choose_format",
        voters=alive(),
        options=("first_price", "english", "dutch", "vickrey", "all_pay"),
        threshold="plurality",
        outcomes={
            "first_price": OutcomeDef(
                doc="First-price sealed bid — winner pays own bid",
                effects=(
                    SetVar("auction_type", "first_price"),
                    Broadcast("Next auction: First-price sealed bid"),
                ),
            ),
            "english": OutcomeDef(
                doc="English ascending — open outcry, highest wins",
                effects=(
                    SetVar("auction_type", "english"),
                    Broadcast("Next auction: English ascending"),
                ),
            ),
            "dutch": OutcomeDef(
                doc="Dutch descending — price drops, first claim wins",
                effects=(
                    SetVar("auction_type", "dutch"),
                    Broadcast("Next auction: Dutch descending"),
                ),
            ),
            "vickrey": OutcomeDef(
                doc="Vickrey second-price — highest wins, pays second-highest",
                effects=(
                    SetVar("auction_type", "vickrey"),
                    Broadcast("Next auction: Vickrey second-price"),
                ),
            ),
            "all_pay": OutcomeDef(
                doc="All-pay — everyone pays their bid, highest wins",
                effects=(
                    SetVar("auction_type", "all_pay"),
                    Broadcast("Next auction: All-pay (everyone pays!)"),
                ),
            ),
        },
        doc="Vote on auction format for next lot.",
    )
    # ── Phases ────────────────────────────────────────
    .phase(
        "setup",
        category="setup",
        automatic=True,
        once=True,
        effects=[
            SetVar("current_lot", 1),
            # total_lots injected via game param (default 6)
            SetVar("highest_bid", 0),
            SetVar("highest_bidder", ""),
            SetVar("auction_type", "first_price"),
            SetVar("lot_value", 200),
            SetVar("dutch_price", 500),
            SetVar("second_highest_bid", 0),
        ],
    )
    # Preview — examine lot, buy info, reset bids
    # Lot values escalate: 150, 200, 250, 300, 400, 500
    # starts_round=True: each lot = one round
    .phase(
        "preview",
        allows=["buy_info", "appraise"],
        channels=["auction_floor", "auctioneer"],
        duration=30,
        starts_round=True,
        effects=[
            SetVar("highest_bid", 0),
            SetVar("highest_bidder", ""),
            SetVar("second_highest_bid", 0),
            # Lot value increases each round (base 100 + lot_number * 60)
            SetVar("lot_value", 100 + game.current_lot * 60),
            # Dutch starting price = 2.5x lot value
            SetVar("dutch_price", (100 + game.current_lot * 60) * 2.5),
            Each("p", alive(), [SetAttr("p", "current_bid", 0)]),
            Broadcast(
                "Lot {game.current_lot} of {game.total_lots} — estimated value: {game.lot_value} gold!"
            ),
        ],
    )
    # Format vote — choose auction type
    .phase(
        "format_vote",
        allows=["choose_format"],
        channels=["auction_floor"],
        duration=20,
    )
    # Bidding — place bids in chosen format
    .phase(
        "bidding",
        allows=[
            "sealed_bid",
            "pass_bid",
            "english_bid",
            "jump_bid",
            "dutch_claim",
            "all_pay_bid",
            "vickrey_bid",
        ],
        channels=["auction_floor", "auctioneer"],
        duration=60,
    )
    # Reveal — determine winner from sealed bids (automatic)
    # For sealed/vickrey/all_pay: scan all bids, find highest bidder
    # For english: highest_bidder already set during bidding
    # For dutch: winner already set on claim
    .phase(
        "reveal",
        category="resolution",
        automatic=True,
        effects=[
            # For sealed formats: find max bidder via iterative comparison
            # Each player: if my bid > current highest, I become highest bidder
            Each(
                "p",
                alive(),
                [
                    When(
                        Ref("p", "current_bid") > game.highest_bid,
                        (
                            SetVar("second_highest_bid", game.highest_bid),
                            SetVar("highest_bid", Ref("p", "current_bid")),
                            SetVar("highest_bidder", Ref("p", "id")),
                        ),
                    ),
                    When(
                        (Ref("p", "current_bid") > game.second_highest_bid)
                        & (Ref("p", "current_bid") <= game.highest_bid)
                        & (Ref("p", "id") != game.highest_bidder),
                        (SetVar("second_highest_bid", Ref("p", "current_bid")),),
                    ),
                ],
            ),
            When(
                game.highest_bidder != "",
                (
                    Broadcast(
                        "Winner: {game.highest_bidder} with bid of {game.highest_bid}!"
                    ),
                ),
            ),
            When(
                game.highest_bidder == "",
                (Broadcast("No bids placed — lot goes unsold."),),
            ),
        ],
    )
    # Settlement — award lot to winner, handle payments (automatic)
    .phase(
        "settlement",
        category="resolution",
        automatic=True,
        effects=[
            # Award collection value to winner (if any)
            When(
                game.highest_bidder != "",
                (
                    # First-price / english: winner pays their bid (stakes locked)
                    # Vickrey: winner pays second-highest price
                    Cond(
                        branches=(
                            # Vickrey: return difference (bid - second_price) to winner
                            (
                                game.auction_type == "vickrey",
                                (
                                    Broadcast(
                                        "Vickrey settlement: winner pays {game.second_highest_bid} (second price)."
                                    ),
                                ),
                            ),
                            # All-pay: stakes already burned, just award
                            (
                                game.auction_type == "all_pay",
                                (Broadcast("All-pay settlement: all bids consumed."),),
                            ),
                            # First-price / english / dutch: standard settlement
                            (
                                None,
                                (Broadcast("Settlement complete."),),
                            ),
                        )
                    ),
                    # Award the lot (taste_bonus already applied via buy_info)
                    Boost(game.highest_bidder, "collection_value", game.lot_value),
                    Boost(game.highest_bidder, "reputation", 5),
                ),
            ),
            # Advance to next lot
            SetVar("current_lot", game.current_lot + 1),
        ],
    )
    # Intermission — form rings, take credit, gift art
    # next="preview": explicit lot cycle (intermission → preview → format_vote → ...)
    .phase(
        "intermission",
        allows=["bidding_ring", "take_credit", "gift_art"],
        channels=["auction_floor"],
        duration=45,
        next="preview",
    )
    # ── Victory Conditions ────────────────────────────
    # Primary: highest total wealth (gold + collection)
    .victory(
        "wealth_leader",
        when=game.current_lot > game.total_lots,
        type="distribution",
        score=actor.gold + actor.collection_value + actor.taste_bonus,
        priority=1,
        message="Wealthiest collector wins!",
    )
    # Secondary: most valuable collection
    .victory(
        "collection_master",
        when=game.current_lot > game.total_lots,
        type="distribution",
        score=actor.collection_value,
        priority=2,
        message="Greatest art collection wins!",
    )
    # Tertiary: highest reputation
    .victory(
        "reputation_king",
        when=game.current_lot > game.total_lots,
        type="distribution",
        score=actor.reputation,
        priority=3,
        message="Most respected collector wins!",
    )
    # ── AI Agent Context ──────────────────────────────
    .context(
        game_summary=(
            "Art auction with 5 format types across 6 lots. "
            "Win by maximizing gold + collection value + taste bonus."
        ),
        score_explanation=(
            "Score = gold + collection_value + taste_bonus. "
            "Credit taken deducts 20% from collection."
        ),
        var_hints=[
            VarHint(
                "current_lot",
                "Lot",
                format="progress",
                max_var="total_lots",
                priority=100,
            ),
            VarHint("auction_type", "Format", priority=90),
            VarHint(
                "highest_bid",
                "High bid",
                format="currency",
                phases=("bidding",),
                priority=80,
            ),
            VarHint(
                "highest_bidder",
                "High bidder",
                format="player",
                phases=("bidding",),
                priority=70,
            ),
            VarHint(
                "lot_value",
                "Lot value",
                format="currency",
                phases=("preview", "bidding"),
                priority=60,
            ),
            VarHint(
                "dutch_price",
                "Dutch price",
                format="currency",
                phases=("bidding",),
                priority=50,
            ),
        ],
        phase_hints=[
            PhaseHint(
                "preview",
                "Examine the lot. Buy insider info for +10 taste bonus, or save gold.",
                tips=("Insider info costs 50g but adds permanent value",),
            ),
            PhaseHint(
                "format_vote",
                "Choose auction format. Format affects optimal bidding strategy.",
                tips=(
                    "Vickrey: bid true value",
                    "English: reveals valuations",
                    "All-pay: dangerous war of attrition",
                ),
            ),
            PhaseHint(
                "bidding",
                "Place your bid. Only format-matching bid type works.",
                urgency="critical",
            ),
            PhaseHint(
                "intermission",
                "Between lots. Form bidding rings, take credit, gift art.",
                tips=(
                    "Bidding rings risk exposure (-30 reputation)",
                    "Credit costs 20% of collection value",
                ),
            ),
        ],
        channel_hints=[
            ChannelHint(
                "auction_floor",
                when_to_use="Signal intentions, bluff, or coordinate",
                strategy="Misdirection can manipulate other bidders",
            ),
            ChannelHint("auctioneer", when_to_use="Read-only official announcements"),
        ],
        deal_priorities={
            "sealed_bid": 100,
            "english_bid": 100,
            "vickrey_bid": 100,
            "all_pay_bid": 100,
            "dutch_claim": 100,
            "jump_bid": 90,
            "pass_bid": 80,
            "buy_info": 70,
            "bidding_ring": 60,
            "take_credit": 50,
            "gift_art": 40,
            "appraise": 30,
        },
    )
    .build()
)
