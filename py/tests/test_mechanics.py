"""Tests for mcp/mechanics.py — effect/expr description utilities."""

from __future__ import annotations

import pytest

from engine.expr.core import And, Arith, Call, Cmp, If, Lit, Not, Or, Ref
from engine.runtime.effects import (
    Boost,
    Broadcast,
    BurnStakes,
    Cond,
    CreateGroup,
    Damage,
    DissolveGroup,
    Each,
    Eliminate,
    Emit,
    JoinGroup,
    LeaveGroup,
    Maybe,
    Notify,
    Relate,
    ReturnStakes,
    Reveal,
    SendMessage,
    SetAttr,
    SetVar,
    Transfer,
    TransferStakes,
    Unrelate,
    When,
)
from engine.runtime.state import DealDef, OutcomeDef, PartyDef, SpeechActDef, VoteDef
from mcp.mechanics import (
    _short_effects,
    describe_deal_mechanics,
    describe_effect,
    describe_effects,
    describe_expr,
    describe_outcome,
    describe_outcome_detail,
    describe_speech_act_mechanics,
    describe_stakes,
    describe_vote_mechanics,
    outcome_summary,
    speech_act_verification_summary,
)

# ---------------------------------------------------------------------------
# describe_expr
# ---------------------------------------------------------------------------


class TestDescribeExpr:
    def test_literal_string(self):
        assert describe_expr(Lit("hello")) == '"hello"'

    def test_literal_number(self):
        assert describe_expr(Lit(42)) == "42"

    def test_literal_none(self):
        assert describe_expr(Lit(None)) == "none"

    def test_literal_bool(self):
        assert describe_expr(Lit(True)) == "true"
        assert describe_expr(Lit(False)) == "false"

    def test_ref_simple(self):
        assert describe_expr(Ref("actor")) == "actor"

    def test_ref_path(self):
        assert describe_expr(Ref("actor", "role")) == "actor.role"

    def test_comparison(self):
        expr = Cmp("==", Ref("actor", "role"), Lit("seer"))
        assert describe_expr(expr) == 'actor.role is "seer"'

    def test_comparison_not_equal(self):
        expr = Cmp("!=", Ref("target", "team"), Lit("wolves"))
        assert describe_expr(expr) == 'target.team is not "wolves"'

    def test_comparison_gt(self):
        expr = Cmp(">", Ref("actor", "intel"), Lit(0))
        assert describe_expr(expr) == "actor.intel > 0"

    def test_arithmetic(self):
        expr = Arith("+", Ref("actor", "caps"), Lit(10))
        assert describe_expr(expr) == "actor.caps + 10"

    def test_and_expr(self):
        expr = And(
            (
                Cmp("==", Ref("actor", "role"), Lit("seer")),
                Cmp(">", Ref("actor", "intel"), Lit(0)),
            )
        )
        assert "and" in describe_expr(expr)

    def test_or_expr(self):
        expr = Or((Cmp("==", Ref("a"), Lit(1)), Cmp("==", Ref("b"), Lit(2))))
        assert "or" in describe_expr(expr)

    def test_not_expr(self):
        expr = Not(Call("alive?", ()))
        assert describe_expr(expr) == "not (alive)"

    def test_call_alive(self):
        expr = Call("alive?", ())
        assert describe_expr(expr) == "alive"

    def test_call_count_where(self):
        pred = Cmp("==", Ref("actor", "team"), Lit("wolves"))
        expr = Call("count_where", (pred,))
        assert "count(" in describe_expr(expr)

    def test_none_input(self):
        assert describe_expr(None) == "always"

    def test_non_expr_input(self):
        assert describe_expr(42) == "42"


# ---------------------------------------------------------------------------
# describe_effect — simple effects
# ---------------------------------------------------------------------------


class TestDescribeSimpleEffects:
    def test_boost(self):
        result = describe_effect(Boost("actor", "caps", 10))
        assert result == "+10 caps to actor"

    def test_damage(self):
        result = describe_effect(Damage("target", "reputation", 20))
        assert result == "-20 reputation to target"

    def test_transfer(self):
        result = describe_effect(Transfer("a", "b", "gold", 50))
        assert result == "Transfer 50 gold from a to b"

    def test_eliminate(self):
        result = describe_effect(Eliminate("target"))
        assert result == "Eliminate target"

    def test_eliminate_expr(self):
        result = describe_effect(Eliminate(Ref("actor", "lover")))
        assert "Eliminate" in result
        assert "actor.lover" in result

    def test_reveal(self):
        result = describe_effect(Reveal("target", "role", to="actor"))
        assert result == "Reveal target's role to actor"

    def test_reveal_fake(self):
        result = describe_effect(
            Reveal("target", "hidden_type", to="actor", fake="loyalist")
        )
        assert "FAKE" in result
        assert "loyalist" in result

    def test_relate(self):
        result = describe_effect(Relate("a", "b", "bribed"))
        assert result == "Create relation: a --bribed--> b"

    def test_unrelate(self):
        result = describe_effect(Unrelate("a", "b", "bribed"))
        assert result == "Remove relation: a --bribed--> b"

    def test_set_attr(self):
        result = describe_effect(SetAttr("target", "role", "minister"))
        assert "Set target.role" in result
        assert "minister" in result

    def test_set_var(self):
        result = describe_effect(SetVar("current_lot", 3))
        assert "Set current_lot" in result

    def test_create_group(self):
        result = describe_effect(CreateGroup("wolf_pack"))
        assert "Create wolf_pack group" in result

    def test_join_group(self):
        result = describe_effect(JoinGroup("target", "cabinet"))
        assert result == "target joins cabinet"

    def test_leave_group(self):
        result = describe_effect(LeaveGroup("target", "cabinet"))
        assert result == "target leaves cabinet"

    def test_dissolve_group(self):
        result = describe_effect(DissolveGroup("cabinet"))
        assert result == "Dissolve cabinet"

    def test_return_stakes(self):
        result = describe_effect(ReturnStakes())
        assert result == "Return locked stakes"

    def test_transfer_stakes(self):
        result = describe_effect(TransferStakes("responder"))
        assert result == "Transfer locked stakes to responder"

    def test_burn_stakes(self):
        result = describe_effect(BurnStakes())
        assert result == "Locked stakes are burned"

    def test_broadcast(self):
        result = describe_effect(Broadcast("{actor} betrays!"))
        assert 'Announce: "{actor} betrays!"' == result

    def test_send_message(self):
        result = describe_effect(SendMessage("assembly", "actor", "Hello parliament"))
        assert "Send to assembly" in result

    def test_skip_emit(self):
        result = describe_effect(Emit("test_event", {}))
        assert result is None

    def test_skip_notify(self):
        result = describe_effect(Notify("actor", "You did something."))
        assert result is None


# ---------------------------------------------------------------------------
# describe_effect — control flow
# ---------------------------------------------------------------------------


class TestDescribeControlFlow:
    def test_when(self):
        eff = When(
            condition=Cmp("==", Ref("responder", "hidden_type"), Lit("opportunist")),
            effects=(Boost("responder", "caps", 10),),
        )
        result = describe_effect(eff)
        assert "If" in result
        assert 'responder.hidden_type is "opportunist"' in result
        assert "+10 caps to responder" in result

    def test_when_multiline(self):
        eff = When(
            condition=Cmp("==", Ref("p", "faction"), Lit("iron_guard")),
            effects=(Boost("p", "influence", 5), Boost("p", "caps", 10)),
        )
        result = describe_effect(eff)
        assert "If" in result
        assert "+5 influence" in result
        assert "+10 caps" in result

    def test_cond(self):
        eff = Cond(
            branches=(
                (Cmp("==", Ref("x"), Lit(1)), (Boost("a", "gold", 10),)),
                (None, (Boost("a", "gold", 5),)),
            )
        )
        result = describe_effect(eff)
        assert "If" in result
        assert "Otherwise" in result

    def test_maybe(self):
        eff = Maybe(
            probability=0.25,
            effects=(Broadcast("Leaked!"),),
        )
        result = describe_effect(eff)
        assert "25% chance" in result
        assert "Leaked!" in result

    def test_each(self):
        eff = Each(
            binding="p",
            filter=Call("alive?", ()),
            effects=(Boost("p", "caps", 10),),
        )
        result = describe_effect(eff)
        assert "For each p" in result
        assert "alive" in result
        assert "+10 caps" in result


# ---------------------------------------------------------------------------
# describe_effects — batch
# ---------------------------------------------------------------------------


class TestDescribeEffects:
    def test_filters_skipped(self):
        effects = (
            Boost("a", "gold", 10),
            Emit("test", {}),
            Notify("a", "hey"),
            Damage("b", "rep", 5),
        )
        result = describe_effects(effects)
        assert len(result) == 2
        assert "+10 gold to a" in result[0]
        assert "-5 rep to b" in result[1]


# ---------------------------------------------------------------------------
# describe_stakes
# ---------------------------------------------------------------------------


class TestDescribeStakes:
    def test_simple(self):
        result = describe_stakes({"proposer": [("caps", "amount")]})
        assert "proposer locks" in result
        assert "caps (amount)" in result

    def test_multi_party(self):
        result = describe_stakes(
            {
                "proposer": [("reputation", 10)],
                "responder": [("reputation", 10)],
            }
        )
        assert "proposer locks" in result
        assert "responder locks" in result


# ---------------------------------------------------------------------------
# describe_outcome
# ---------------------------------------------------------------------------


class TestDescribeOutcome:
    def test_uses_doc(self):
        outcome = OutcomeDef(
            effects=(Boost("a", "caps", 10),),
            doc="Bribe accepted — caps transferred",
        )
        result = describe_outcome("accept", outcome)
        assert result == "Bribe accepted — caps transferred"

    def test_fallback_to_effects(self):
        outcome = OutcomeDef(
            effects=(Boost("a", "caps", 10), Damage("b", "rep", 5)),
            doc="",
        )
        result = describe_outcome("accept", outcome)
        assert "+10 caps to a" in result
        assert "-5 rep to b" in result

    def test_empty(self):
        outcome = OutcomeDef(effects=(), doc="")
        result = describe_outcome("ok", outcome)
        assert result == "ok"


# ---------------------------------------------------------------------------
# describe_outcome_detail
# ---------------------------------------------------------------------------


class TestDescribeOutcomeDetail:
    def test_with_effects(self):
        outcome = OutcomeDef(
            effects=(
                TransferStakes("responder"),
                Relate("proposer", "responder", "bribed"),
            ),
            doc="Bribe accepted",
        )
        lines = describe_outcome_detail("accept", outcome)
        assert any("accept" in l and "Bribe accepted" in l for l in lines)
        assert any("Transfer locked stakes" in l for l in lines)
        assert any("bribed" in l for l in lines)


# ---------------------------------------------------------------------------
# describe_deal_mechanics
# ---------------------------------------------------------------------------


class TestDescribeDealMechanics:
    def test_bilateral_deal(self):
        deal = DealDef(
            id="bribe",
            parties={
                "proposer": PartyDef(filter=Call("alive?", ())),
                "responder": PartyDef(filter=Call("alive?", ())),
            },
            params={},
            stakes={"proposer": [("caps", "amount")]},
            response_options=("accept", "reject"),
            outcomes={
                "accept": OutcomeDef(
                    effects=(TransferStakes("responder"),),
                    doc="Caps transferred",
                ),
                "reject": OutcomeDef(
                    effects=(ReturnStakes(),),
                    doc="Caps returned",
                ),
            },
            doc="Offer caps for support.",
        )
        result = describe_deal_mechanics("bribe", deal)
        assert "## Mechanics: bribe (deal)" in result
        assert "Offer caps for support." in result
        assert "Stakes:" in result
        assert "### Outcomes" in result
        assert "accept" in result
        assert "reject" in result

    def test_immediate_deal(self):
        deal = DealDef(
            id="seer_vision",
            parties={"actor": PartyDef(filter=Call("alive?", ()))},
            outcomes={
                "ok": OutcomeDef(
                    effects=(Reveal("target", "role", to="actor"),),
                    doc="",
                ),
            },
            per_round=1,
            doc="See one player's true role.",
        )
        result = describe_deal_mechanics("seer_vision", deal)
        assert "## Mechanics: seer_vision" in result
        assert "1/round" in result

    def test_with_guard(self):
        deal = DealDef(
            id="dutch_claim",
            parties={"actor": PartyDef()},
            guard=Cmp("==", Ref("game", "auction_type"), Lit("dutch")),
            outcomes={},
            doc="Claim at descending price.",
        )
        result = describe_deal_mechanics("dutch_claim", deal)
        assert "Guard:" in result
        assert "dutch" in result


# ---------------------------------------------------------------------------
# describe_vote_mechanics
# ---------------------------------------------------------------------------


class TestDescribeVoteMechanics:
    def test_vote(self):
        vote = VoteDef(
            id="lynch",
            options=("lynch", "spare"),
            threshold="majority",
            outcomes={
                "lynch": OutcomeDef(
                    effects=(Eliminate("subject"),),
                    doc="Lynch the accused — role revealed on death",
                ),
                "spare": OutcomeDef(
                    effects=(),
                    doc="Target is spared",
                ),
            },
            doc="Village votes to lynch or spare.",
        )
        result = describe_vote_mechanics("lynch", vote)
        assert "## Mechanics: lynch (vote)" in result
        assert "majority" in result
        assert "lynch" in result.lower()
        assert "spare" in result.lower()


# ---------------------------------------------------------------------------
# describe_speech_act_mechanics
# ---------------------------------------------------------------------------


class TestDescribeSpeechActMechanics:
    def test_claim_act(self):
        sa = SpeechActDef(
            id="claim_type",
            act_type="claim",
            verify_condition=Cmp(
                "==", Ref("actor", "hidden_type"), Ref("claim", "value")
            ),
            verify_triggers=("eliminate", "game_end"),
            verify_true_effects=(Boost("actor", "reputation", 15),),
            verify_false_effects=(Damage("actor", "reputation", 20),),
            per_game=1,
            phase_filter=("caucus", "floor"),
            doc="Claim your hidden type.",
        )
        result = describe_speech_act_mechanics("claim_type", sa)
        assert "## Mechanics: claim_type (claim)" in result
        assert "Claim your hidden type." in result
        assert "eliminate" in result
        assert "game_end" in result
        assert "+15 reputation" in result
        assert "-20 reputation" in result
        assert "1/game" in result
        assert "caucus" in result

    def test_inquire_act(self):
        sa = SpeechActDef(
            id="interrogate",
            act_type="inquire",
            cost={"influence": 8},
            inquire_response_options=("answer_truthfully", "deflect", "refuse"),
            inquire_deadline=1,
            inquire_silence_effects=(
                Damage("target", "influence", 10),
                Boost("target", "suspicion", 5),
            ),
            per_round=1,
            doc="Interrogate target.",
        )
        result = describe_speech_act_mechanics("interrogate", sa)
        assert "8 influence" in result
        assert "answer_truthfully" in result
        assert "Silence penalty:" in result
        assert "-10 influence" in result


# ---------------------------------------------------------------------------
# outcome_summary
# ---------------------------------------------------------------------------


class TestOutcomeSummary:
    def test_with_docs(self):
        outcomes = {
            "accept": OutcomeDef(doc="Caps transferred"),
            "reject": OutcomeDef(doc="Caps returned"),
        }
        result = outcome_summary(outcomes)
        assert "accept (Caps transferred)" in result
        assert "reject (Caps returned)" in result

    def test_truncation(self):
        outcomes = {
            "ok": OutcomeDef(doc="A" * 100),
        }
        result = outcome_summary(outcomes)
        assert "..." in result

    def test_no_doc(self):
        outcomes = {"accept": OutcomeDef(doc="")}
        result = outcome_summary(outcomes)
        assert result == "accept"


# ---------------------------------------------------------------------------
# speech_act_verification_summary
# ---------------------------------------------------------------------------


class TestSpeechActVerificationSummary:
    def test_full(self):
        sa = SpeechActDef(
            id="test",
            act_type="claim",
            verify_triggers=("eliminate",),
            verify_true_effects=(Boost("actor", "trust", 20),),
            verify_false_effects=(Damage("actor", "trust", 25),),
        )
        result = speech_act_verification_summary(sa)
        assert "Verified on: eliminate" in result
        assert "+20 trust" in result
        assert "-25 trust" in result

    def test_no_effects(self):
        sa = SpeechActDef(id="test", act_type="promise", verify_triggers=())
        result = speech_act_verification_summary(sa)
        assert result == ""


# ---------------------------------------------------------------------------
# Unknown effect graceful fallback
# ---------------------------------------------------------------------------


class TestUnknownEffect:
    def test_unknown_type(self):
        class CustomEffect:
            pass

        result = describe_effect(CustomEffect())
        assert "CustomEffect" in result

    def test_no_crash(self):
        """describe_effect never crashes — returns type name for unknown effects."""

        class WeirdEffect:
            def __init__(self):
                self.data = {"nested": [1, 2, 3]}

        result = describe_effect(WeirdEffect())
        assert result is not None
        assert "WeirdEffect" in result


# ---------------------------------------------------------------------------
# Integration: actual game definitions
# ---------------------------------------------------------------------------


class TestGameIntegration:
    def test_auction_deal_mechanics(self):
        """Test describe_deal_mechanics on actual auction game deal."""
        from games.auction import auction

        deal = auction.deals["bidding_ring"]
        result = describe_deal_mechanics("bidding_ring", deal)
        assert "## Mechanics: bidding_ring" in result
        assert "Outcomes" in result
        assert "join" in result
        assert "expose" in result

    def test_werewolf_vote_mechanics(self):
        """Test describe_vote_mechanics on actual werewolf game vote."""
        from games.werewolf import werewolf

        vote = werewolf.votes["lynch"]
        result = describe_vote_mechanics("lynch", vote)
        assert "lynch" in result.lower()
        assert "majority" in result

    def test_parliament_speech_act_mechanics(self):
        """Test describe_speech_act_mechanics on actual PA speech act."""
        from games.parliament_arena import parliament_arena

        sa = parliament_arena.speech_acts["claim_type"]
        result = describe_speech_act_mechanics("claim_type", sa)
        assert "claim" in result
        assert "eliminate" in result
        assert "reputation" in result

    def test_parliament_bribe_outcomes(self):
        """Test that bribe deal shows all 3 outcomes with Cond effects."""
        from games.parliament_arena import parliament_arena

        deal = parliament_arena.deals["bribe"]
        result = describe_deal_mechanics("bribe", deal)
        assert "accept" in result
        assert "reject" in result
        assert "expose" in result
        assert "opportunist" in result
        assert "ideologue" in result


# ---------------------------------------------------------------------------
# _short_effects — filters Broadcast/SendMessage for compact summaries
# ---------------------------------------------------------------------------


class TestShortEffects:
    def test_filters_broadcast(self):
        effects = (
            Boost("actor", "caps", 10),
            Broadcast(template="{actor} did something!"),
            Damage("target", "reputation", 5),
        )
        result = _short_effects(effects)
        assert len(result) == 2
        assert "+10 caps" in result[0]
        assert "-5 reputation" in result[1]

    def test_filters_send_message(self):
        effects = (
            Boost("actor", "gold", 20),
            SendMessage(channel="lobby", sender="system", content="Hello"),
        )
        result = _short_effects(effects)
        assert len(result) == 1
        assert "+20 gold" in result[0]

    def test_filters_emit_and_notify(self):
        effects = (
            Emit(event="test"),
            Notify(entity="actor", template="notified"),
            Boost("actor", "rep", 5),
        )
        result = _short_effects(effects)
        assert len(result) == 1
        assert "+5 rep" in result[0]

    def test_empty_effects(self):
        assert _short_effects(()) == []

    def test_all_filtered(self):
        effects = (
            Broadcast(template="hello"),
            Emit(event="test"),
        )
        assert _short_effects(effects) == []


# ---------------------------------------------------------------------------
# Cond empty branch skipping
# ---------------------------------------------------------------------------


class TestCondEmptyBranch:
    def test_skip_empty_otherwise(self):
        """Cond with empty Otherwise branch should not output 'Otherwise:' line."""
        effect = Cond(
            branches=(
                (
                    Cmp("==", Ref("actor", "role"), Lit("spy")),
                    (Boost("actor", "intel", 10),),
                ),
                (None, ()),  # empty Otherwise
            )
        )
        result = describe_effect(effect)
        assert "Otherwise" not in result
        assert "+10 intel" in result

    def test_skip_empty_conditional_branch(self):
        """Cond where one conditional branch has no effects."""
        effect = Cond(
            branches=(
                (Cmp("==", Ref("actor", "type"), Lit("a")), ()),  # empty
                (
                    Cmp("==", Ref("actor", "type"), Lit("b")),
                    (Boost("actor", "caps", 5),),
                ),
            )
        )
        result = describe_effect(effect)
        assert "type_a" not in result  # type "a" branch skipped
        assert "+5 caps" in result

    def test_all_branches_empty(self):
        """Cond where all branches are empty produces empty string."""
        effect = Cond(branches=((None, ()),))
        result = describe_effect(effect)
        assert result == ""

    def test_nonempty_otherwise_preserved(self):
        """Cond with non-empty Otherwise still shows it."""
        effect = Cond(
            branches=(
                (Cmp("==", Ref("x", "y"), Lit("a")), (Boost("x", "gold", 1),)),
                (None, (Damage("x", "gold", 2),)),
            )
        )
        result = describe_effect(effect)
        assert "Otherwise:" in result
        assert "-2 gold" in result


# ---------------------------------------------------------------------------
# describe_outcome_detail — edge cases
# ---------------------------------------------------------------------------


class TestDescribeOutcomeDetail:
    def test_with_doc(self):
        od = OutcomeDef(doc="Caps transferred", effects=(Boost("a", "caps", 10),))
        lines = describe_outcome_detail("accept", od)
        assert lines[0] == "**accept** — Caps transferred"
        assert len(lines) == 2  # header + 1 effect

    def test_no_doc_with_effects(self):
        """No doc but has effects — show ID only (no duplication)."""
        od = OutcomeDef(doc="", effects=(Boost("a", "caps", 10),))
        lines = describe_outcome_detail("accept", od)
        assert lines[0] == "**accept**"
        assert len(lines) == 2

    def test_no_doc_no_effects(self):
        """No doc and no effects — show '(no effect)' marker."""
        od = OutcomeDef(doc="", effects=())
        lines = describe_outcome_detail("abstain", od)
        assert lines == ["**abstain** — (no effect)"]

    def test_doc_no_effects(self):
        """Has doc but no effects — still shows doc."""
        od = OutcomeDef(doc="Nothing happens", effects=())
        lines = describe_outcome_detail("pass", od)
        assert lines[0] == "**pass** — Nothing happens"
        assert len(lines) == 1  # no effect lines


# ---------------------------------------------------------------------------
# Immediate deal: single "ok" outcome → "### Effects" not "### Outcomes"
# ---------------------------------------------------------------------------


class TestImmediateDealOutput:
    def test_single_ok_shows_effects_section(self):
        """Deal with single 'ok' outcome shows ### Effects, not ### Outcomes."""
        deal = DealDef(
            id="quick",
            parties={"actor": PartyDef()},
            outcomes={"ok": OutcomeDef(effects=(Boost("actor", "caps", 10),))},
            doc="Quick action.",
        )
        result = describe_deal_mechanics("quick", deal)
        assert "### Effects" in result
        assert "### Outcomes" not in result
        assert "+10 caps" in result
        assert "**ok**" not in result  # no outcome header

    def test_multiple_outcomes_shows_outcomes_section(self):
        """Deal with multiple outcomes shows ### Outcomes normally."""
        deal = DealDef(
            id="normal",
            parties={"actor": PartyDef(), "target": PartyDef()},
            outcomes={
                "accept": OutcomeDef(
                    doc="Deal accepted", effects=(Boost("actor", "caps", 10),)
                ),
                "reject": OutcomeDef(doc="Deal rejected", effects=(ReturnStakes(),)),
            },
            doc="Normal deal.",
        )
        result = describe_deal_mechanics("normal", deal)
        assert "### Outcomes" in result
        assert "### Effects" not in result
        assert "**accept** — Deal accepted" in result
        assert "**reject** — Deal rejected" in result
