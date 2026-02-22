"""Tests for non-linear phase transitions.

Covers: unconditional next, conditional transitions, starts_round,
mixed linear/non-linear, cascade safety, archive replay,
builder validation, phase_transition_count, backward compatibility.
"""

import pytest

from engine.dsl.builder import Game
from engine.expr.core import Ref
from engine.runtime.core import GameRuntime
from engine.runtime.effects import Boost, SetVar
from engine.runtime.state import TransitionDef

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

game = Ref("game")
actor = Ref("actor")


def _build_and_start(g, players=None):
    compiled = g.build()
    rt = GameRuntime(compiled)
    state = rt.start_game(players or ["p1", "p2"])
    state = rt.run_setup(state)
    return rt, state


# ---------------------------------------------------------------------------
# Test games
# ---------------------------------------------------------------------------


def _branching_game():
    """Game with conditional phase transitions."""
    return (
        Game("branch", "Branch Test", players=(2, 4))
        .resource("gold", initial=100)
        .deal("choose_a", actor=Ref("actor", "active"), effects=[SetVar("path", "A")])
        .deal("choose_b", actor=Ref("actor", "active"), effects=[SetVar("path", "B")])
        .deal("noop", actor=Ref("actor", "active"), effects=[Boost("actor", "gold", 1)])
        .phase("setup", category="setup", automatic=True, once=True,
               effects=[SetVar("path", "A")])
        .phase(
            "start",
            allows=["choose_a", "choose_b"],
        )
        .phase(
            "branch",
            automatic=True,
            transitions=[
                Game.transition(game.path == "A", "path_a"),
                Game.transition(game.path == "B", "path_b"),
            ],
        )
        .phase("path_a", allows=["noop"], next="merge")
        .phase("path_b", allows=["noop"], next="merge")
        .phase("merge", allows=["noop"], starts_round=True)
        .victory("done", when=game.round > 3, type="distribution", score=actor.gold)
    )


def _unconditional_next_game():
    """Game with simple unconditional next jumps."""
    return (
        Game("uncond", "Unconditional Next", players=(2, 4))
        .resource("gold", initial=100)
        .deal("noop", actor=Ref("actor", "active"), effects=[Boost("actor", "gold", 1)])
        .phase("setup", category="setup", automatic=True, once=True)
        .phase("alpha", allows=["noop"], next="gamma")  # skip beta
        .phase("beta", allows=["noop"])  # should be skipped by alpha's next
        .phase("gamma", allows=["noop"])  # linear fallback from here
        .victory("done", when=game.round > 2, type="distribution", score=actor.gold)
    )


def _starts_round_game():
    """Game with explicit starts_round flag."""
    return (
        Game("rounds", "Round Test", players=(2, 4))
        .resource("gold", initial=100)
        .deal("noop", actor=Ref("actor", "active"), effects=[Boost("actor", "gold", 1)])
        .phase("setup", category="setup", automatic=True, once=True)
        .phase("morning", allows=["noop"], starts_round=True)
        .phase("afternoon", allows=["noop"])
        .phase("evening", allows=["noop"], next="morning")
        .victory("done", when=game.round > 3, type="distribution", score=actor.gold)
    )


# ===========================================================================
# Unconditional next
# ===========================================================================


class TestUnconditionalNext:
    def test_next_skips_intermediate_phase(self):
        """Phase alpha(next='gamma') should skip beta."""
        rt, state = _build_and_start(_unconditional_next_game())
        assert state.phase == "alpha"
        state = rt.advance_phase(state)
        assert state.phase == "gamma"  # jumped over beta

    def test_linear_fallback_after_next(self):
        """From gamma (no next), linear fallback wraps to alpha."""
        rt, state = _build_and_start(_unconditional_next_game())
        state = rt.advance_phase(state)  # alpha -> gamma
        assert state.phase == "gamma"
        state = rt.advance_phase(state)  # gamma -> linear -> alpha (wrap)
        assert state.phase == "alpha"

    def test_next_target_skippable_falls_through(self):
        """If next target should be skipped, fall through to linear."""
        g = (
            Game("skip", "Skip Target", players=(2, 2))
            .resource("gold", initial=10)
            .deal("noop", actor=Ref("actor", "active"), effects=[Boost("actor", "gold", 1)])
            .phase("setup", category="setup", automatic=True, once=True)
            .phase("a", allows=["noop"], next="b")
            .phase("b", allows=["noop"], once=True)  # will be skippable after first visit
            .phase("c", allows=["noop"])
            .victory("done", when=game.round > 5, type="distribution", score=actor.gold)
        )
        rt, state = _build_and_start(g)
        assert state.phase == "a"

        # First time: a -> b (next, b is not yet executed_once)
        state = rt.advance_phase(state)
        assert state.phase == "b"

        # b -> c (linear)
        state = rt.advance_phase(state)
        assert state.phase == "c"

        # c -> a (linear wrap)
        state = rt.advance_phase(state)
        assert state.phase == "a"

        # Second time: a -> next=b, but b is once-exhausted -> linear -> c
        state = rt.advance_phase(state)
        assert state.phase == "c"


# ===========================================================================
# Conditional transitions
# ===========================================================================


class TestConditionalTransitions:
    def test_first_matching_transition_wins(self):
        """branch phase with path=A should go to path_a."""
        rt, state = _build_and_start(_branching_game())
        assert state.phase == "start"
        # Default path is "A"
        state = rt.advance_phase(state)  # start -> branch (auto) -> path_a
        assert state.phase == "path_a"

    def test_second_transition_when_first_fails(self):
        """Set path=B, branch should go to path_b."""
        rt, state = _build_and_start(_branching_game())
        assert state.phase == "start"
        # Change path to B
        result = rt.start_deal(state, "choose_b", actor_id="p1")
        state = result["state"]
        assert state.vars_["path"] == "B"
        state = rt.advance_phase(state)  # start -> branch (auto) -> path_b
        assert state.phase == "path_b"

    def test_no_transition_matches_falls_to_next(self):
        """If no transitions match and next is set, use next."""
        g = (
            Game("fallback", "Fallback Test", players=(2, 2))
            .resource("gold", initial=10)
            .deal("noop", actor=Ref("actor", "active"), effects=[Boost("actor", "gold", 1)])
            .phase("setup", category="setup", automatic=True, once=True,
                   effects=[SetVar("flag", "C")])
            .phase(
                "check",
                automatic=True,
                transitions=[
                    Game.transition(game.flag == "A", "dest_a"),
                    Game.transition(game.flag == "B", "dest_b"),
                ],
                next="dest_default",
            )
            .phase("dest_a", allows=["noop"])
            .phase("dest_b", allows=["noop"])
            .phase("dest_default", allows=["noop"])
            .victory("done", when=game.round > 2, type="distribution", score=actor.gold)
        )
        rt, state = _build_and_start(g)
        # flag is "C", no transition matches -> falls to next="dest_default"
        assert state.phase == "dest_default"

    def test_no_transition_no_next_uses_linear(self):
        """If no transitions match and no next, use linear."""
        g = (
            Game("linear_fall", "Linear Fallback", players=(2, 2))
            .resource("gold", initial=10)
            .deal("noop", actor=Ref("actor", "active"), effects=[Boost("actor", "gold", 1)])
            .phase("setup", category="setup", automatic=True, once=True,
                   effects=[SetVar("flag", "X")])
            .phase(
                "check",
                automatic=True,
                transitions=[
                    Game.transition(game.flag == "A", "dest_a"),
                ],
            )
            .phase("linear_next", allows=["noop"])
            .phase("dest_a", allows=["noop"])
            .victory("done", when=game.round > 2, type="distribution", score=actor.gold)
        )
        rt, state = _build_and_start(g)
        # flag is "X", no match, no next -> linear scan -> linear_next
        assert state.phase == "linear_next"


# ===========================================================================
# starts_round
# ===========================================================================


class TestStartsRound:
    def test_round_increments_on_starts_round_phase(self):
        """Round increments when entering a starts_round phase."""
        rt, state = _build_and_start(_starts_round_game())
        assert state.phase == "morning"
        assert state.round == 1

        state = rt.advance_phase(state)  # morning -> afternoon
        assert state.phase == "afternoon"
        assert state.round == 1

        state = rt.advance_phase(state)  # afternoon -> evening
        assert state.phase == "evening"
        assert state.round == 1

        state = rt.advance_phase(state)  # evening -> morning (next), starts_round
        assert state.phase == "morning"
        assert state.round == 2

    def test_backward_jump_without_starts_round_no_increment(self):
        """Jumping backward without starts_round does not increment round."""
        g = (
            Game("back", "Backward Jump", players=(2, 2))
            .resource("gold", initial=10)
            .deal("noop", actor=Ref("actor", "active"), effects=[Boost("actor", "gold", 1)])
            .phase("setup", category="setup", automatic=True, once=True)
            .phase("a", allows=["noop"], starts_round=True)
            .phase("b", allows=["noop"])
            .phase("c", allows=["noop"], next="b")  # backward jump, no starts_round on b
            .victory("done", when=game.round > 5, type="distribution", score=actor.gold)
        )
        rt, state = _build_and_start(g)
        assert state.phase == "a"
        assert state.round == 1

        state = rt.advance_phase(state)  # a -> b
        assert state.phase == "b"
        assert state.round == 1

        state = rt.advance_phase(state)  # b -> c
        assert state.phase == "c"
        assert state.round == 1

        state = rt.advance_phase(state)  # c -> b (next, backward, no starts_round)
        assert state.phase == "b"
        assert state.round == 1  # still 1!

    def test_legacy_games_use_wrap_based_rounds(self):
        """Games without starts_round use legacy wrap-based counting."""
        g = (
            Game("legacy", "Legacy Rounds", players=(2, 2))
            .resource("gold", initial=10)
            .deal("noop", actor=Ref("actor", "active"), effects=[Boost("actor", "gold", 1)])
            .phase("setup", category="setup", automatic=True, once=True)
            .phase("a", allows=["noop"])
            .phase("b", allows=["noop"])
            .phase("c", allows=["noop"])
            .victory("done", when=game.round > 5, type="distribution", score=actor.gold)
        )
        rt, state = _build_and_start(g)
        assert state.phase == "a"
        assert state.round == 1

        state = rt.advance_phase(state)  # a -> b
        assert state.round == 1
        state = rt.advance_phase(state)  # b -> c
        assert state.round == 1
        state = rt.advance_phase(state)  # c -> a (wrap)
        assert state.round == 2


# ===========================================================================
# Mixed linear and non-linear
# ===========================================================================


class TestMixedFlow:
    def test_full_branching_cycle(self):
        """Full cycle: start -> branch -> path_a -> merge -> start (round 2)."""
        rt, state = _build_and_start(_branching_game())
        assert state.phase == "start"
        assert state.round == 1

        # start -> branch(auto) -> path_a (transition match on path=A)
        state = rt.advance_phase(state)
        assert state.phase == "path_a"

        # path_a -> merge (unconditional next)
        state = rt.advance_phase(state)
        assert state.phase == "merge"
        assert state.round == 2  # merge has starts_round

        # merge -> start (linear)
        state = rt.advance_phase(state)
        assert state.phase == "start"

    def test_switch_branch_midgame(self):
        """Change path variable to switch branches."""
        rt, state = _build_and_start(_branching_game())
        # Round 1: path=A -> path_a
        state = rt.advance_phase(state)
        assert state.phase == "path_a"
        state = rt.advance_phase(state)  # path_a -> merge
        state = rt.advance_phase(state)  # merge -> start

        # Change to path B
        result = rt.start_deal(state, "choose_b", actor_id="p1")
        state = result["state"]

        # Round 2: path=B -> path_b
        state = rt.advance_phase(state)
        assert state.phase == "path_b"


# ===========================================================================
# Cascade safety
# ===========================================================================


class TestCascadeSafety:
    def test_automatic_cycle_raises_on_advance(self):
        """Automatic phases in a cycle should raise RuntimeError."""
        g = (
            Game("cycle", "Cycle Test", players=(2, 2))
            .resource("gold", initial=10)
            .deal("noop", actor=Ref("actor", "active"), effects=[Boost("actor", "gold", 1)])
            .phase("setup", category="setup", automatic=True, once=True)
            .phase("start", allows=["noop"])
            .phase("a", automatic=True, next="b")
            .phase("b", automatic=True, next="a")
            .phase("c", allows=["noop"])
            .victory("done", when=game.round > 2, type="distribution", score=actor.gold)
        )
        rt, state = _build_and_start(g)
        assert state.phase == "start"
        with pytest.raises(RuntimeError, match="cascad"):
            rt.advance_phase(state)


# ===========================================================================
# Archive replay
# ===========================================================================


class TestArchiveReplay:
    def test_branching_game_archive_replay(self):
        """Archive from a branching game replays identically."""
        from engine.archive import create_archive, replay_with_result

        def advance(st):
            st = st.record_decision({"type": "advance_phase"})
            return rt.advance_phase(st)

        rt, state = _build_and_start(_branching_game())
        compiled = rt.compiled

        # Play through: start -> branch -> path_a -> merge -> start -> branch -> path_b
        state = advance(state)  # -> path_a (via branch)
        assert state.phase == "path_a"
        state = advance(state)  # -> merge
        state = advance(state)  # -> start

        # Switch to path B (start_deal records its own decision)
        result = rt.start_deal(state, "choose_b", actor_id="p1")
        state = result["state"]
        state = advance(state)  # -> path_b (via branch)
        assert state.phase == "path_b"
        state = advance(state)  # -> merge

        # Create archive and replay
        archive = create_archive(compiled, state)
        replay_result = replay_with_result(archive, compiled)
        replayed = replay_result.state

        assert replayed.phase == state.phase
        assert replayed.round == state.round
        assert replayed.phase_transition_count == state.phase_transition_count
        assert replayed.vars_ == state.vars_
        assert not replay_result.failed


# ===========================================================================
# Builder validation
# ===========================================================================


class TestBuilderValidation:
    def test_invalid_next_target(self):
        """next pointing to nonexistent phase raises ValueError."""
        g = (
            Game("bad", "Bad Next", players=(2, 2))
            .resource("gold", initial=10)
            .deal("noop", actor=Ref("actor", "active"), effects=[Boost("actor", "gold", 1)])
            .phase("a", allows=["noop"], next="nonexistent")
            .victory("done", when=game.round > 2, type="distribution", score=actor.gold)
        )
        with pytest.raises(ValueError, match="nonexistent"):
            g.build()

    def test_invalid_transition_target(self):
        """Transition to nonexistent phase raises ValueError."""
        g = (
            Game("bad2", "Bad Transition", players=(2, 2))
            .resource("gold", initial=10)
            .deal("noop", actor=Ref("actor", "active"), effects=[Boost("actor", "gold", 1)])
            .phase(
                "a",
                allows=["noop"],
                transitions=[Game.transition(game.round > 0, "nowhere")],
            )
            .victory("done", when=game.round > 2, type="distribution", score=actor.gold)
        )
        with pytest.raises(ValueError, match="nowhere"):
            g.build()

    def test_valid_transitions_build_ok(self):
        """Valid transitions should build without errors."""
        compiled = _branching_game().build()
        assert compiled.id == "branch"
        # Verify PhaseDef fields
        branch_phase = [p for p in compiled.phases if p.id == "branch"][0]
        assert len(branch_phase.transitions) == 2
        assert branch_phase.transitions[0].target == "path_a"
        path_a = [p for p in compiled.phases if p.id == "path_a"][0]
        assert path_a.next == "merge"


# ===========================================================================
# phase_transition_count
# ===========================================================================


class TestPhaseTransitionCount:
    def test_count_increments_on_every_transition(self):
        """phase_transition_count increments on each phase change."""
        rt, state = _build_and_start(_branching_game())
        assert state.phase_transition_count > 0  # setup -> start already transitioned

        count_at_start = state.phase_transition_count
        state = rt.advance_phase(state)  # start -> branch(auto) -> path_a
        # Two transitions: start->branch, branch->path_a
        assert state.phase_transition_count == count_at_start + 2

        state = rt.advance_phase(state)  # path_a -> merge
        assert state.phase_transition_count == count_at_start + 3

    def test_count_monotonic_with_backward_jumps(self):
        """phase_transition_count always increases even with backward jumps."""
        rt, state = _build_and_start(_starts_round_game())
        counts = [state.phase_transition_count]

        for _ in range(6):
            state = rt.advance_phase(state)
            counts.append(state.phase_transition_count)

        # Must be strictly increasing
        for i in range(1, len(counts)):
            assert counts[i] > counts[i - 1]


# ===========================================================================
# Backward compatibility
# ===========================================================================


class TestBackwardCompatibility:
    def test_all_registry_games_compile(self):
        """All 4 registered games still compile with new PhaseDef fields."""
        from games import REGISTRY

        for gid, compiled in REGISTRY.items():
            assert compiled.phases, f"{gid} has no phases"
            # At least one phase should have starts_round=True (all games migrated)
            has_starts_round = any(p.starts_round for p in compiled.phases)
            assert has_starts_round, f"{gid} has no starts_round phase"

    def test_auction_phase_cycle_unchanged(self):
        """Auction game phase cycle is identical to pre-change behavior."""
        from games import REGISTRY

        compiled = REGISTRY["auction"]
        rt = GameRuntime(compiled)
        state = rt.start_game(["p1", "p2", "p3"])
        state = rt.run_setup(state)

        # Should cascade through setup to first interactive phase
        assert state.phase == "preview"
        assert state.round == 1

        state = rt.advance_phase(state)
        assert state.phase == "format_vote"

        state = rt.advance_phase(state)
        assert state.phase == "bidding"

    def test_werewolf_phase_cycle_unchanged(self):
        """Werewolf first_night -> night -> dawn(auto)->day cycle preserved."""
        from games import REGISTRY

        compiled = REGISTRY["werewolf"]
        rt = GameRuntime(compiled)
        players = [f"p{i}" for i in range(8)]
        state = rt.start_game(players)
        state = rt.run_setup(state)

        assert state.phase == "first_night"
        state = rt.advance_phase(state)
        assert state.phase == "night"
        state = rt.advance_phase(state)
        # dawn is automatic, cascades to day
        assert state.phase == "day"

    def test_exchange_phase_cycle_unchanged(self):
        """Exchange morning_briefing -> open_market -> ... cycle preserved."""
        from games import REGISTRY

        compiled = REGISTRY["exchange"]
        rt = GameRuntime(compiled)
        players = [f"p{i}" for i in range(4)]
        state = rt.start_game(players)
        state = rt.run_setup(state)

        assert state.phase == "morning_briefing"
        state = rt.advance_phase(state)
        assert state.phase == "open_market"
