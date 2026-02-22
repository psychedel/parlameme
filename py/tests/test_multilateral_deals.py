"""Tests for multilateral deal support.

Multilateral deals have multiple responders with count constraints
and completion rules (all, majority, any).
"""

import pytest

from engine.dsl.builder import Game
from engine.expr import alive
from engine.runtime.core import GameRuntime
from engine.runtime.effects import Boost, Broadcast, ReturnStakes, TransferStakes
from engine.runtime.state import PartyDef


def _build_coalition_game(completion_rule: str = "all") -> Game:
    """Build a minimal game with a multilateral deal."""
    return (
        Game("coalition_test", "Coalition Test", players=(4, 6))
        .resource("influence", initial=50, visibility="public")
        .deal(
            "form_coalition",
            proposer=alive(),
            responders={
                "filter": alive(),
                "count": (2, 3),
            },
            stakes={"proposer": [("influence", 5)]},
            responses=["join", "decline"],
            outcomes={
                "join": {
                    "effects": [
                        ReturnStakes(),
                        Boost("proposer", "influence", 10),
                        Broadcast("{proposer} forms a coalition!"),
                    ],
                },
                "decline": {
                    "effects": [
                        TransferStakes("responder"),
                    ],
                },
            },
            completion_rule=completion_rule,
            doc="Form a coalition with 2-3 other players",
        )
        .phase("negotiate", allows=["form_coalition"])
        .victory("none", type="manual", when="(= 1 0)")
        .build()
    )


class TestMultilateralDSL:
    def test_responders_party_created(self):
        compiled = _build_coalition_game()
        deal = compiled.deals["form_coalition"]
        assert "proposer" in deal.parties
        assert "responders" in deal.parties
        rp = deal.parties["responders"]
        assert rp.count == (2, 3)
        assert rp.excludes == ("proposer",)

    def test_completion_rule_stored(self):
        compiled = _build_coalition_game("majority")
        deal = compiled.deals["form_coalition"]
        assert deal.completion_rule == "majority"


class TestMultilateralRuntime:
    def _start(self, rule="all"):
        compiled = _build_coalition_game(rule)
        runtime = GameRuntime(compiled)
        state = runtime.start_game(["alice", "bob", "carol", "dave"])
        state = runtime.run_setup(state)
        return compiled, runtime, state

    def test_start_deal_with_responder_ids(self):
        _, rt, state = self._start()
        result = rt.start_deal(
            state,
            "form_coalition",
            actor_id="alice",
            responder_ids=["bob", "carol"],
        )
        assert result["ok"]
        pending = result["state"].pending_deals
        assert len(pending) == 1
        deal = next(iter(pending.values()))
        assert deal.proposer == "alice"
        assert set(deal.responders.keys()) == {"bob", "carol"}
        assert all(v is None for v in deal.responders.values())

    def test_count_validation_too_few(self):
        _, rt, state = self._start()
        result = rt.start_deal(
            state,
            "form_coalition",
            actor_id="alice",
            responder_ids=["bob"],  # need 2-3
        )
        assert not result["ok"]
        assert "2-3" in result["error"]["message"]

    def test_count_validation_too_many(self):
        _, rt, state = self._start()
        result = rt.start_deal(
            state,
            "form_coalition",
            actor_id="alice",
            responder_ids=["bob", "carol", "dave", "alice"],  # need 2-3
        )
        assert not result["ok"]

    def test_bilateral_still_works(self):
        """Existing bilateral deals unaffected."""
        compiled = (
            Game("bilateral", "Bilateral", players=(2, 4))
            .resource("gold", initial=10)
            .deal(
                "trade",
                proposer=alive(),
                responder=alive(),
                responses=["accept", "reject"],
                outcomes={
                    "accept": {"effects": [Boost("proposer", "gold", 5)]},
                    "reject": {"effects": []},
                },
            )
            .phase("main", allows=["trade"])
            .victory("none", type="manual", when="(= 1 0)")
            .build()
        )
        rt = GameRuntime(compiled)
        state = rt.start_game(["alice", "bob"])
        state = rt.run_setup(state)

        r = rt.start_deal(state, "trade", actor_id="alice", responder_id="bob")
        assert r["ok"]
        iid = r["instance_id"]

        r2 = rt.respond_to_deal(r["state"], iid, "bob", "accept")
        assert r2["ok"]
        assert r2["outcome"] == "accept"


class TestMultilateralCompletion:
    def _propose(self, rule="all"):
        compiled = _build_coalition_game(rule)
        rt = GameRuntime(compiled)
        state = rt.start_game(["alice", "bob", "carol", "dave"])
        state = rt.run_setup(state)
        r = rt.start_deal(
            state,
            "form_coalition",
            actor_id="alice",
            responder_ids=["bob", "carol"],
        )
        assert r["ok"]
        iid = next(iter(r["state"].pending_deals))
        return rt, r["state"], iid

    def test_all_rule_waits_for_everyone(self):
        rt, state, iid = self._propose("all")

        # First response — not yet complete
        r1 = rt.respond_to_deal(state, iid, "bob", "join")
        assert r1["ok"]
        assert r1["outcome"] == "pending"
        assert r1["waiting"] == ["carol"]

        # Second response — now complete
        r2 = rt.respond_to_deal(r1["state"], iid, "carol", "join")
        assert r2["ok"]
        assert r2["outcome"] == "join"
        # Pending deal removed
        assert iid not in r2["state"].pending_deals

    def test_all_rule_mixed_responses(self):
        """When all respond but not unanimously, majority wins."""
        rt, state, iid = self._propose("all")

        r1 = rt.respond_to_deal(state, iid, "bob", "join")
        assert r1["outcome"] == "pending"

        r2 = rt.respond_to_deal(r1["state"], iid, "carol", "decline")
        assert r2["ok"]
        # Tie — each option has 1 vote, most_common picks first
        assert r2["outcome"] in ("join", "decline")

    def test_majority_rule(self):
        """Majority resolves when threshold reached."""
        compiled = _build_coalition_game("majority")
        rt = GameRuntime(compiled)
        state = rt.start_game(["alice", "bob", "carol", "dave"])
        state = rt.run_setup(state)

        r = rt.start_deal(
            state,
            "form_coalition",
            actor_id="alice",
            responder_ids=["bob", "carol", "dave"],
        )
        assert r["ok"]
        iid = next(iter(r["state"].pending_deals))

        # 1 of 3 — not majority yet
        r1 = rt.respond_to_deal(r["state"], iid, "bob", "join")
        assert r1["outcome"] == "pending"

        # 2 of 3 — majority!
        r2 = rt.respond_to_deal(r1["state"], iid, "carol", "join")
        assert r2["ok"]
        assert r2["outcome"] == "join"

    def test_any_rule(self):
        """Any resolves on first response."""
        rt, state, iid = self._propose("any")
        r = rt.respond_to_deal(state, iid, "bob", "decline")
        assert r["ok"]
        assert r["outcome"] == "decline"

    def test_double_response_rejected(self):
        rt, state, iid = self._propose("all")
        r1 = rt.respond_to_deal(state, iid, "bob", "join")
        assert r1["ok"]
        # Bob tries again
        r2 = rt.respond_to_deal(r1["state"], iid, "bob", "decline")
        assert not r2["ok"]

    def test_non_responder_rejected(self):
        rt, state, iid = self._propose("all")
        # Dave is not a responder
        r = rt.respond_to_deal(state, iid, "dave", "join")
        assert not r["ok"]


class TestMultilateralReplay:
    def test_archive_roundtrip(self):
        """Multilateral deals can be replayed from archive."""
        from engine.archive import Archive, replay

        compiled = _build_coalition_game()
        rt = GameRuntime(compiled)
        state = rt.start_game(["alice", "bob", "carol", "dave"], seed=42)
        state = rt.run_setup(state)

        r = rt.start_deal(
            state,
            "form_coalition",
            actor_id="alice",
            responder_ids=["bob", "carol"],
        )
        state = r["state"]
        iid = next(iter(state.pending_deals))

        r1 = rt.respond_to_deal(state, iid, "bob", "join")
        state = r1["state"]
        r2 = rt.respond_to_deal(state, iid, "carol", "join")
        state = r2["state"]

        archive = Archive(
            seed=42,
            players=tuple(["alice", "bob", "carol", "dave"]),
            decisions=tuple(state.decisions),
            rules_hash=compiled.source_hash,
        )

        replayed = replay(archive, compiled)
        # Verify state matches
        assert replayed.pending_deals == state.pending_deals
        assert len(replayed.decisions) == len(state.decisions)
