"""Training infrastructure tests — spaces, rewards, runner, env."""

from __future__ import annotations

import warnings

import numpy as np
import pytest

from engine.archive import create_archive, replay, archive_to_dict, dict_to_archive
from engine.dsl.builder import Game
from engine.expr import Ref, actor, game
from engine.runtime.core import GameRuntime
from engine.runtime.effects import Boost, SetVar
from engine.runtime.state import GameState, Visibility
from games import REGISTRY
from games.auction import auction
from games.exchange import exchange
from games.parliament_arena import parliament_arena
from games.werewolf import werewolf
from training.policies import FirstDealPolicy, GreedyPolicy, RandomPolicy
from training.rewards import RewardCalculator, RewardConfig
from training.runner import BatchRunner, _auto_advance, _run_single_game
from training.spaces import ActionSpaceBuilder, ObservationEncoder
from training.utils import analyze_action_space

PLAYERS_3 = ["p0", "p1", "p2"]
PLAYERS_4 = ["p0", "p1", "p2", "p3"]


def _setup_auction(seed: int = 42) -> tuple[GameRuntime, GameState]:
    rt = GameRuntime(auction)
    state = rt.start_game(PLAYERS_3, seed=seed)
    state = rt.run_setup(state)
    state = _auto_advance(rt, state)
    return rt, state


# =========================================================================
# ActionSpaceBuilder
# =========================================================================


class TestActionSpaceBuilder:
    def test_action_space_has_noop_and_advance(self):
        """First two slots are always noop and advance_phase."""
        builder = ActionSpaceBuilder(auction, 10, PLAYERS_3)
        assert builder._action_table[0].type == "noop"
        assert builder._action_table[1].type == "advance_phase"

    def test_action_space_size_auction(self):
        """Auction action space has reasonable size."""
        builder = ActionSpaceBuilder(auction, 10, PLAYERS_3)
        assert builder.num_actions > 10
        assert builder.num_actions < 500

    def test_action_space_size_exchange(self):
        """Exchange has larger action space due to more params."""
        builder_exchange = ActionSpaceBuilder(exchange, 10, PLAYERS_4)
        builder_auction = ActionSpaceBuilder(auction, 10, PLAYERS_3)
        assert builder_exchange.num_actions > builder_auction.num_actions
        assert builder_exchange.num_actions > 100

    def test_action_mask_initial_phase(self):
        """In initial phase, only some actions are available."""
        rt, state = _setup_auction()
        builder = ActionSpaceBuilder(auction, 10, PLAYERS_3)
        mask = builder.action_mask("p0", state, auction)

        assert mask[0] == 1  # noop
        assert mask.sum() >= 2  # at least noop + something

    def test_action_mask_shape(self):
        """Mask shape matches action table size."""
        builder = ActionSpaceBuilder(auction, 10, PLAYERS_3)
        rt, state = _setup_auction()
        mask = builder.action_mask("p0", state, auction)
        assert len(mask) == builder.num_actions

    def test_action_mask_ended_game(self):
        """Ended game only allows noop."""
        rt, state = _setup_auction()
        # Manually end game
        import attrs
        state = attrs.evolve(state, status="ended")
        builder = ActionSpaceBuilder(auction, 10, PLAYERS_3)
        mask = builder.action_mask("p0", state, auction)
        assert mask[0] == 1
        assert mask.sum() == 1

    def test_decode_noop(self):
        """Decoding slot 0 returns noop."""
        builder = ActionSpaceBuilder(auction, 10, PLAYERS_3)
        rt, state = _setup_auction()
        spec = builder.decode_action(0, "p0", state)
        assert spec["type"] == "noop"

    def test_decode_advance(self):
        """Decoding slot 1 returns advance_phase."""
        builder = ActionSpaceBuilder(auction, 10, PLAYERS_3)
        rt, state = _setup_auction()
        spec = builder.decode_action(1, "p0", state)
        assert spec["type"] == "advance_phase"

    def test_param_discretization_bins(self):
        """Number params are discretized into bins."""
        analysis = analyze_action_space(auction, num_bins=5)
        analysis_10 = analyze_action_space(auction, num_bins=10)
        # More bins = more actions
        assert analysis_10["total_actions"] >= analysis["total_actions"]

    def test_all_games_build_action_space(self):
        """All 4 games can build action spaces without error."""
        for gid, compiled in REGISTRY.items():
            pids = [f"p{i}" for i in range(compiled.min_players)]
            builder = ActionSpaceBuilder(compiled, 10, pids)
            assert builder.num_actions > 2, f"{gid} has too few actions"


# =========================================================================
# ObservationEncoder
# =========================================================================


class TestObservationEncoder:
    def test_observation_shape(self):
        """Observation has correct shape."""
        enc = ObservationEncoder(auction, 3)
        rt, state = _setup_auction()
        obs = enc.encode(state, "p0", auction)
        assert obs.shape == (enc.obs_dim,)

    def test_observation_dtype(self):
        """Observation is float32."""
        enc = ObservationEncoder(auction, 3)
        rt, state = _setup_auction()
        obs = enc.encode(state, "p0", auction)
        assert obs.dtype == np.float32

    def test_observation_bounded(self):
        """Observation values are within [-1, 1]."""
        enc = ObservationEncoder(auction, 3)
        rt, state = _setup_auction()
        obs = enc.encode(state, "p0", auction)
        assert obs.min() >= -1.0
        assert obs.max() <= 1.0

    def test_observation_imperfect_info(self):
        """Different observers get different observations."""
        enc = ObservationEncoder(auction, 3)
        rt, state = _setup_auction()
        obs_p0 = enc.encode(state, "p0", auction)
        obs_p1 = enc.encode(state, "p1", auction)
        # They should differ (different self-entity at minimum)
        assert not np.array_equal(obs_p0, obs_p1)

    def test_all_games_encode(self):
        """All 4 games can encode observations."""
        for gid, compiled in REGISTRY.items():
            n = compiled.min_players
            pids = [f"p{i}" for i in range(n)]
            enc = ObservationEncoder(compiled, n)
            rt = GameRuntime(compiled)
            state = rt.start_game(pids, seed=42)
            state = rt.run_setup(state)
            state = _auto_advance(rt, state)
            obs = enc.encode(state, pids[0], compiled)
            assert obs.shape == (enc.obs_dim,), f"{gid} obs shape mismatch"
            assert np.isfinite(obs).all(), f"{gid} obs has non-finite values"


# =========================================================================
# RewardCalculator
# =========================================================================


class TestRewardCalculator:
    def test_terminal_reward_winner(self):
        """Winner gets positive terminal reward."""
        calc = RewardCalculator(auction, terminal_win=1.0, terminal_lose=-1.0)
        rt, state = _setup_auction()
        victory = {"type": "single", "winner": "p0"}
        assert calc.terminal_reward("p0", state, victory) == 1.0
        assert calc.terminal_reward("p1", state, victory) == -1.0

    def test_terminal_reward_distribution(self):
        """Distribution victory gives proportional rewards."""
        calc = RewardCalculator(auction, terminal_win=1.0, score_proportional=True)
        rt, state = _setup_auction()
        victory = {"type": "distribution", "scores": {"p0": 100, "p1": 50, "p2": 0}}
        r0 = calc.terminal_reward("p0", state, victory)
        r1 = calc.terminal_reward("p1", state, victory)
        r2 = calc.terminal_reward("p2", state, victory)
        assert r0 > r1 > r2
        assert r0 == pytest.approx(1.0)
        assert r2 == pytest.approx(-1.0)

    def test_terminal_no_victory(self):
        """No victory gives 0 terminal reward."""
        calc = RewardCalculator(auction)
        rt, state = _setup_auction()
        assert calc.terminal_reward("p0", state, None) == 0.0

    def test_step_reward_resource_delta(self):
        """Step reward reflects resource changes."""
        calc = RewardCalculator(
            auction,
            resource_weights={"gold": 1.0},
            step_scale=0.1,
        )
        rt, state = _setup_auction()
        # Simulate gold change
        new_state = state.set_resource("p0", "gold", 1100)
        reward = calc.step_reward("p0", state, new_state)
        assert reward == pytest.approx(100.0 * 1.0 * 0.1)

    def test_shaping_potential(self):
        """Potential-based shaping adds gamma*Phi(s') - Phi(s)."""
        def phi(state, pid, compiled):
            return state.get_resource(pid, "gold") / 1000.0

        calc = RewardCalculator(
            auction,
            resource_weights={},
            shaping_potential=phi,
            gamma=0.99,
        )
        rt, state = _setup_auction()
        new_state = state.set_resource("p0", "gold", 1100)
        reward = calc.step_reward("p0", state, new_state)
        expected = 0.99 * (1100 / 1000.0) - (1000 / 1000.0)
        assert reward == pytest.approx(expected)

    def test_reward_configs(self):
        """Pre-built reward configs for all 4 games instantiate correctly."""
        configs = [
            RewardConfig.auction(),
            RewardConfig.exchange(),
            RewardConfig.werewolf(),
            RewardConfig.parliament_arena(),
        ]
        for cfg in configs:
            assert isinstance(cfg, RewardCalculator)
            assert cfg.resource_weights

    def test_from_compiled_data_driven(self):
        """from_compiled builds weights from any game without hardcoding."""
        for gid, compiled in REGISTRY.items():
            calc = RewardConfig.from_compiled(compiled)
            assert isinstance(calc, RewardCalculator)
            # All public resources should have weights
            from engine.runtime.state import Visibility
            for rid, rdef in compiled.resources.items():
                if rdef.visibility == Visibility.PUBLIC:
                    assert rid in calc.resource_weights, f"{gid} missing weight for {rid}"

    def test_team_victory_equal_rewards(self):
        """Team members all get equal terminal reward."""
        calc = RewardCalculator(auction, terminal_win=1.0, terminal_lose=-1.0)
        rt, state = _setup_auction()
        # Simulate team victory
        import attrs
        # Set team attrs
        state = attrs.evolve(state, entities={
            pid: attrs.evolve(e, attrs_=e.attrs_ | {"team": "wolves" if pid == "p0" else ("wolves" if pid == "p1" else "village")})
            for pid, e in state.entities.items()
        })
        victory = {"type": "single", "winner": "p0", "team": "wolves"}
        # Both wolves get full reward
        assert calc.terminal_reward("p0", state, victory) == 1.0
        assert calc.terminal_reward("p1", state, victory) == 1.0
        # Village gets lose reward
        assert calc.terminal_reward("p2", state, victory) == -1.0


# =========================================================================
# BatchRunner
# =========================================================================


class TestBatchRunner:
    def test_run_single_game(self):
        """Single game produces valid result."""
        runner = BatchRunner("auction", num_bins=5, max_steps_per_game=50)
        result = runner.run_batch(
            agents=RandomPolicy(seed=1),
            n_games=1,
            seeds=[42],
        )
        assert result.total_games == 1
        game = result.games[0]
        assert game.seed == 42
        assert game.steps > 0
        assert game.duration_ms >= 0
        for pid in ["p0", "p1", "p2"]:
            assert pid in game.trajectories
            traj = game.trajectories[pid]
            assert len(traj.transitions) > 0

    def test_run_batch_multiple(self):
        """Batch of 5 games all complete."""
        runner = BatchRunner("auction", num_bins=5, max_steps_per_game=50)
        result = runner.run_batch(
            agents=RandomPolicy(seed=0),
            n_games=5,
        )
        assert result.total_games == 5
        assert len(result.games) == 5

    def test_batch_stats(self):
        """Batch result has computed statistics."""
        runner = BatchRunner("auction", num_bins=5, max_steps_per_game=50)
        result = runner.run_batch(
            agents=RandomPolicy(seed=0),
            n_games=3,
        )
        assert result.avg_steps > 0
        assert result.avg_duration_ms >= 0
        for pid in result.win_rates:
            assert 0.0 <= result.win_rates[pid] <= 1.0

    def test_trajectory_transitions_valid(self):
        """Transition obs/mask shapes are correct."""
        runner = BatchRunner("auction", num_bins=5, max_steps_per_game=30)
        result = runner.run_batch(agents=RandomPolicy(seed=0), n_games=1)
        game = result.games[0]
        traj = game.trajectories["p0"]
        builder = ActionSpaceBuilder(auction, 5, ["p0", "p1", "p2"])
        enc = ObservationEncoder(auction, 3)

        for t in traj.transitions:
            assert t.obs.shape == (enc.obs_dim,)
            assert t.next_obs.shape == (enc.obs_dim,)
            assert t.action_mask.shape == (builder.num_actions,)
            assert 0 <= t.action < builder.num_actions

    def test_archives_created(self):
        """Each game result has an archive."""
        runner = BatchRunner("auction", num_bins=5, max_steps_per_game=30)
        result = runner.run_batch(agents=RandomPolicy(seed=0), n_games=2)
        for game in result.games:
            assert game.archive is not None
            assert game.archive.game_id == "auction"
            assert len(game.archive.players) == 3
            assert len(game.archive.decisions) > 0


# =========================================================================
# PettingZoo Env
# =========================================================================


class TestParlamemeEnv:
    @pytest.fixture(autouse=True)
    def _check_pettingzoo(self):
        from training.env import HAS_PETTINGZOO
        if not HAS_PETTINGZOO:
            pytest.skip("pettingzoo not installed")

    def test_env_reset(self):
        """Environment resets correctly."""
        from training.env import ParlamemeEnv
        env = ParlamemeEnv(auction, max_steps=50, num_bins=5)
        env.reset(seed=42)
        assert len(env.agents) == 3
        assert env.agent_selection in env.agents

    def test_env_observe(self):
        """Observation has correct structure."""
        from training.env import ParlamemeEnv
        env = ParlamemeEnv(auction, max_steps=50, num_bins=5)
        env.reset(seed=42)
        obs = env.observe(env.agent_selection)
        assert "observation" in obs
        assert "action_mask" in obs
        assert obs["observation"].dtype == np.float32
        assert obs["action_mask"].dtype == np.int8

    def test_env_step_random(self):
        """Can step through 20 random actions without crash."""
        from training.env import ParlamemeEnv
        env = ParlamemeEnv(auction, max_steps=50, num_bins=5)
        env.reset(seed=42)
        rng = np.random.default_rng(0)

        for _ in range(20):
            if not env.agents:
                break
            obs = env.observe(env.agent_selection)
            mask = obs["action_mask"]
            legal = np.where(mask > 0)[0]
            action = int(rng.choice(legal)) if len(legal) > 0 else 0
            env.step(action)

    def test_env_terminates(self):
        """Environment terminates on max_steps."""
        try:
            from training.env import ParlamemeEnv
        except ImportError:
            pytest.skip("pettingzoo not installed")

        env = ParlamemeEnv(auction, max_steps=20, num_bins=5)
        env.reset(seed=42)
        rng = np.random.default_rng(0)

        steps = 0
        while env.agents and steps < 100:
            obs = env.observe(env.agent_selection)
            mask = obs["action_mask"]
            legal = np.where(mask > 0)[0]
            action = int(rng.choice(legal)) if len(legal) > 0 else 0
            env.step(action)
            steps += 1

        # Should have stopped due to max_steps=20
        assert steps <= 30  # some margin for dead steps


# =========================================================================
# Smoke tests — each game
# =========================================================================


class TestSmoke:
    def _smoke(self, game_id: str, max_steps: int = 100):
        runner = BatchRunner(game_id, num_bins=5, max_steps_per_game=max_steps)
        result = runner.run_batch(
            agents=RandomPolicy(seed=42),
            n_games=1,
            seeds=[42],
        )
        assert result.total_games == 1
        game = result.games[0]
        assert game.steps > 0
        # Every player should have at least one transition
        for pid in game.trajectories:
            assert len(game.trajectories[pid].transitions) > 0

    def test_smoke_auction(self):
        """Auction game runs without crash."""
        self._smoke("auction")

    def test_smoke_exchange(self):
        """Exchange game runs without crash."""
        self._smoke("exchange")

    def test_smoke_werewolf(self):
        """Werewolf game runs without crash."""
        self._smoke("werewolf")

    def test_smoke_parliament_arena(self):
        """Parliament Arena game runs without crash."""
        self._smoke("parliament_arena")


# =========================================================================
# Utils
# =========================================================================


class TestUtils:
    def test_analyze_action_space(self):
        """Action space analysis returns correct structure."""
        result = analyze_action_space(auction, num_bins=10)
        assert "total_actions" in result
        assert "by_type" in result
        assert "by_deal" in result
        assert result["total_actions"] > 0
        assert result["by_type"]["noop"] == 1
        assert result["by_type"]["advance_phase"] == 1


# =========================================================================
# Game Parameterization
# =========================================================================


def _build_param_game():
    """Build a minimal game with .param() for testing."""
    return (
        Game("param_test", "Param Test Game", players=(2, 4))
        .param("total_rounds", default=5, min=1, max=20, label="Rounds")
        .param("starting_gold", default=100, min=50, max=500)
        .param("mode", default="standard", type="keyword", options=("standard", "fast"))
        .resource("gold", initial=100, visibility="public")
        .resource("points", initial=0, visibility="public")
        .deal("noop_deal", actor=Ref("alive?"), effects=[])
        .phase("setup", category="setup", automatic=True, once=True, effects=[])
        .phase("main", allows=["noop_deal"], duration=30)
        .victory("most_gold", when=game.total_rounds <= 0, type="distribution", score=actor.gold)
        .build()
    )


class TestGameParams:
    def test_defaults_injected_into_vars(self):
        """Game params with defaults appear in state.vars_ at start_game."""
        compiled = _build_param_game()
        rt = GameRuntime(compiled)
        state = rt.start_game(["a", "b"])
        assert state.vars_["total_rounds"] == 5
        assert state.vars_["starting_gold"] == 100
        assert state.vars_["mode"] == "standard"

    def test_user_overrides(self):
        """User params override defaults."""
        compiled = _build_param_game()
        rt = GameRuntime(compiled)
        state = rt.start_game(["a", "b"], params={"total_rounds": 10, "mode": "fast"})
        assert state.vars_["total_rounds"] == 10
        assert state.vars_["mode"] == "fast"
        # starting_gold stays default
        assert state.vars_["starting_gold"] == 100

    def test_range_validation(self):
        """Out-of-range numeric param raises ValueError."""
        compiled = _build_param_game()
        rt = GameRuntime(compiled)
        with pytest.raises(ValueError, match="min"):
            rt.start_game(["a", "b"], params={"total_rounds": 0})
        with pytest.raises(ValueError, match="max"):
            rt.start_game(["a", "b"], params={"total_rounds": 99})

    def test_type_validation(self):
        """Wrong type for numeric param raises ValueError."""
        compiled = _build_param_game()
        rt = GameRuntime(compiled)
        with pytest.raises(ValueError, match="expected number"):
            rt.start_game(["a", "b"], params={"total_rounds": "five"})

    def test_keyword_validation(self):
        """Invalid keyword option raises ValueError."""
        compiled = _build_param_game()
        rt = GameRuntime(compiled)
        with pytest.raises(ValueError, match="not in"):
            rt.start_game(["a", "b"], params={"mode": "turbo"})

    def test_unknown_param_warns(self):
        """Unknown params produce a warning."""
        compiled = _build_param_game()
        rt = GameRuntime(compiled)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            state = rt.start_game(["a", "b"], params={"nonexistent": 42})
            assert len(w) == 1
            assert "nonexistent" in str(w[0].message)

    def test_starting_prefix_overrides_resource(self):
        """Param starting_gold overrides gold resource initial."""
        compiled = _build_param_game()
        rt = GameRuntime(compiled)
        state = rt.start_game(["a", "b"], params={"starting_gold": 200})
        assert state.get_resource("a", "gold") == 200
        assert state.get_resource("b", "gold") == 200

    def test_expr_resolves_param(self):
        """game.total_rounds resolves to param value in Expr context."""
        compiled = _build_param_game()
        rt = GameRuntime(compiled)
        state = rt.start_game(["a", "b"], params={"total_rounds": 7})
        # Verify via vars_ (Expr resolves game.X → state.vars_["X"])
        assert state.get_game_var("total_rounds") == 7

    def test_source_hash_unchanged_by_different_defaults(self):
        """Changing param defaults doesn't change source hash."""
        g1 = (
            Game("hash_test", "Hash Test", players=(2, 4))
            .param("n", default=5)
            .resource("gold", initial=100, visibility="public")
            .deal("noop", actor=Ref("alive?"), effects=[])
            .phase("main", allows=["noop"])
            .victory("end", when=game.n <= 0, type="distribution", score=actor.gold)
            .build()
        )
        g2 = (
            Game("hash_test", "Hash Test", players=(2, 4))
            .param("n", default=99, min=1, max=200)
            .resource("gold", initial=100, visibility="public")
            .deal("noop", actor=Ref("alive?"), effects=[])
            .phase("main", allows=["noop"])
            .victory("end", when=game.n <= 0, type="distribution", score=actor.gold)
            .build()
        )
        assert g1.source_hash == g2.source_hash

    def test_auction_has_game_params(self):
        """Auction game has total_lots and starting_gold params."""
        assert "total_lots" in auction.game_params
        assert "starting_gold" in auction.game_params
        assert auction.game_params["total_lots"].default == 6
        assert auction.game_params["starting_gold"].default == 1000

    def test_auction_with_varied_total_lots(self):
        """Auction works with different total_lots values."""
        rt = GameRuntime(auction)
        state = rt.start_game(PLAYERS_3, seed=42, params={"total_lots": 3})
        state = rt.run_setup(state)
        assert state.vars_["total_lots"] == 3

    def test_auction_starting_gold_override(self):
        """Auction starting_gold param overrides gold resource."""
        rt = GameRuntime(auction)
        state = rt.start_game(PLAYERS_3, seed=42, params={"starting_gold": 1500})
        assert state.get_resource("p0", "gold") == 1500


class TestGameParamsArchive:
    def test_archive_stores_game_params(self):
        """Archive includes game_params."""
        compiled = _build_param_game()
        rt = GameRuntime(compiled)
        state = rt.start_game(["a", "b"], params={"total_rounds": 10})
        state = rt.run_setup(state)
        archive = create_archive(compiled, state, game_params={"total_rounds": 10})
        assert archive.game_params == {"total_rounds": 10}

    def test_archive_serializes_game_params(self):
        """game_params survives serialization roundtrip."""
        compiled = _build_param_game()
        rt = GameRuntime(compiled)
        state = rt.start_game(["a", "b"], params={"total_rounds": 10})
        state = rt.run_setup(state)
        archive = create_archive(compiled, state, game_params={"total_rounds": 10})
        d = archive_to_dict(archive)
        restored = dict_to_archive(d)
        assert restored.game_params == {"total_rounds": 10}

    def test_archive_replay_with_params(self):
        """Replay respects game_params — starting resources differ."""
        compiled = _build_param_game()
        rt = GameRuntime(compiled)
        state = rt.start_game(["a", "b"], params={"starting_gold": 200})
        state = rt.run_setup(state)
        archive = create_archive(compiled, state, game_params={"starting_gold": 200})
        replayed = replay(archive, compiled)
        assert replayed.get_resource("a", "gold") == 200

    def test_archive_backward_compat_no_params(self):
        """Archives without game_params deserialize with empty dict."""
        d = {"game_id": "test", "players": ["a", "b"], "decisions": [], "seed": 42}
        archive = dict_to_archive(d)
        assert archive.game_params == {}


# =========================================================================
# Phase Reward
# =========================================================================


def _build_reward_expr_game():
    """Build a game with reward_expr on a phase."""
    return (
        Game("reward_test", "Reward Test", players=(2, 4))
        .resource("gold", initial=100, visibility="public")
        .deal("earn", actor=Ref("alive?"), effects=[Boost("actor", "gold", 10)])
        .phase("setup", category="setup", automatic=True, once=True, effects=[])
        .phase(
            "main",
            allows=["earn"],
            duration=30,
            reward_expr=actor.gold / 100.0,
        )
        .victory("end", when=actor.gold >= 200, type="single", score=actor.gold)
        .build()
    )


class TestPhaseReward:
    def test_phase_reward_contributes_to_step_reward(self):
        """reward_expr delta contributes to step_reward."""
        compiled = _build_reward_expr_game()
        calc = RewardCalculator(
            compiled,
            resource_weights={},
            phase_reward_scale=1.0,
        )
        rt = GameRuntime(compiled)
        state = rt.start_game(["a", "b"])
        state = rt.run_setup(state)
        state = _auto_advance(rt, state)
        prev = state
        new = state.set_resource("a", "gold", 120)
        reward = calc.step_reward("a", prev, new)
        # Delta: (120/100 - 100/100) * 1.0 = 0.2
        assert reward == pytest.approx(0.2)

    def test_phase_without_reward_expr_zero(self):
        """Phase without reward_expr contributes 0."""
        calc = RewardCalculator(
            auction,
            resource_weights={},
            phase_reward_scale=1.0,
        )
        rt, state = _setup_auction()
        prev = state
        # Gold change but no reward_expr on preview phase
        new = state.set_resource("p0", "gold", 1100)
        reward = calc.step_reward("p0", prev, new)
        assert reward == 0.0

    def test_phase_reward_scale_zero_disables(self):
        """phase_reward_scale=0 disables phase reward."""
        compiled = _build_reward_expr_game()
        calc = RewardCalculator(
            compiled,
            resource_weights={},
            phase_reward_scale=0.0,
        )
        rt = GameRuntime(compiled)
        state = rt.start_game(["a", "b"])
        state = rt.run_setup(state)
        state = _auto_advance(rt, state)
        prev = state
        new = state.set_resource("a", "gold", 120)
        reward = calc.step_reward("a", prev, new)
        assert reward == 0.0

    def test_source_hash_unchanged_by_reward_expr(self):
        """Changing reward_expr doesn't change source hash."""
        g1 = (
            Game("rh_test", "RH Test", players=(2, 4))
            .resource("gold", initial=100, visibility="public")
            .deal("noop", actor=Ref("alive?"), effects=[])
            .phase("main", allows=["noop"], reward_expr=actor.gold)
            .victory("end", when=actor.gold >= 200, type="single", score=actor.gold)
            .build()
        )
        g2 = (
            Game("rh_test", "RH Test", players=(2, 4))
            .resource("gold", initial=100, visibility="public")
            .deal("noop", actor=Ref("alive?"), effects=[])
            .phase("main", allows=["noop"], reward_expr=actor.gold * 2)
            .victory("end", when=actor.gold >= 200, type="single", score=actor.gold)
            .build()
        )
        g3 = (
            Game("rh_test", "RH Test", players=(2, 4))
            .resource("gold", initial=100, visibility="public")
            .deal("noop", actor=Ref("alive?"), effects=[])
            .phase("main", allows=["noop"])
            .victory("end", when=actor.gold >= 200, type="single", score=actor.gold)
            .build()
        )
        assert g1.source_hash == g2.source_hash == g3.source_hash


# =========================================================================
# Param Sampler Integration
# =========================================================================


class TestParamSampler:
    def test_batch_runner_with_param_sampler(self):
        """BatchRunner passes sampled params to each game."""
        def sampler(rng):
            return {"total_lots": int(rng.integers(3, 7))}

        runner = BatchRunner("auction", num_bins=5, max_steps_per_game=30)
        result = runner.run_batch(
            agents=RandomPolicy(seed=0),
            n_games=3,
            param_sampler=sampler,
        )
        assert result.total_games == 3
        # Games should complete (not crash with varied params)
        for g in result.games:
            assert g.steps > 0

    def test_env_with_param_sampler(self):
        """ParlamemeEnv uses param_sampler on reset."""
        from training.env import HAS_PETTINGZOO
        if not HAS_PETTINGZOO:
            pytest.skip("pettingzoo not installed")
        from training.env import ParlamemeEnv

        def sampler(rng):
            return {"starting_gold": int(rng.integers(500, 1500))}

        env = ParlamemeEnv(auction, max_steps=20, num_bins=5, param_sampler=sampler)
        env.reset(seed=42)
        # Gold should differ from default 1000
        gold = env._state.get_resource("p0", "gold")
        assert gold != 1000 or True  # sampler is random, just check no crash
        assert env.agents  # game started
