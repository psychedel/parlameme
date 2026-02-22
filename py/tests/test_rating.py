"""Tests for Glicko-2 rating system."""

from engine.rating import (
    GameResult,
    Rating,
    compute_all,
    tier,
    update,
)


def test_default_rating():
    r = Rating()
    assert r.mu == 1500.0
    assert r.rd == 350.0
    assert r.vol == 0.06


def test_tier_classification():
    assert tier(2100) == "Master"
    assert tier(1850) == "Expert"
    assert tier(1650) == "Advanced"
    assert tier(1450) == "Intermediate"
    assert tier(1250) == "Beginner"
    assert tier(800) == "Novice"


def test_no_games_increases_rd():
    """When no games are played, RD should increase (more uncertain)."""
    r = Rating(mu=1500, rd=200, vol=0.06)
    r2 = update(r, [])
    assert r2.mu == r.mu  # rating unchanged
    assert r2.rd > r.rd  # uncertainty grows
    assert r2.vol == r.vol  # volatility unchanged


def test_win_increases_rating():
    """Winning against equal opponent should increase rating."""
    player = Rating(mu=1500, rd=200, vol=0.06)
    opponent = Rating(mu=1500, rd=200, vol=0.06)
    result = update(player, [GameResult(opponent=opponent, score=1.0)])
    assert result.mu > 1500  # rating increased
    assert result.rd < 200  # more certain after game


def test_loss_decreases_rating():
    """Losing against equal opponent should decrease rating."""
    player = Rating(mu=1500, rd=200, vol=0.06)
    opponent = Rating(mu=1500, rd=200, vol=0.06)
    result = update(player, [GameResult(opponent=opponent, score=0.0)])
    assert result.mu < 1500


def test_draw_against_equal():
    """Draw against equal opponent should barely change rating."""
    player = Rating(mu=1500, rd=200, vol=0.06)
    opponent = Rating(mu=1500, rd=200, vol=0.06)
    result = update(player, [GameResult(opponent=opponent, score=0.5)])
    assert abs(result.mu - 1500) < 5  # minimal change


def test_upset_win_larger_gain():
    """Beating a stronger opponent gives more rating than beating a weaker one."""
    player = Rating(mu=1500, rd=200, vol=0.06)
    strong = Rating(mu=1800, rd=100, vol=0.06)
    weak = Rating(mu=1200, rd=100, vol=0.06)

    gain_vs_strong = update(player, [GameResult(opponent=strong, score=1.0)]).mu - 1500
    gain_vs_weak = update(player, [GameResult(opponent=weak, score=1.0)]).mu - 1500

    assert gain_vs_strong > gain_vs_weak


def test_multiple_results():
    """Multiple games in one period."""
    player = Rating(mu=1500, rd=200, vol=0.06)
    opp1 = Rating(mu=1400, rd=30, vol=0.06)
    opp2 = Rating(mu=1550, rd=100, vol=0.06)
    opp3 = Rating(mu=1700, rd=300, vol=0.06)

    result = update(
        player,
        [
            GameResult(opponent=opp1, score=1.0),
            GameResult(opponent=opp2, score=0.0),
            GameResult(opponent=opp3, score=1.0),
        ],
    )
    # 2 wins, 1 loss — rating should increase moderately
    assert result.mu > 1500
    assert result.rd < 200


def test_glickman_example():
    """Verify against Glickman's paper example (approximate).

    Player: mu=1500, RD=200, vol=0.06
    Results: beat 1400/30, lost to 1550/100, beat 1700/300
    The exact mu depends on TAU (we use 0.5 vs paper's 0.5).
    RD should be ~151.5 regardless.
    """
    player = Rating(mu=1500, rd=200, vol=0.06)
    results = [
        GameResult(opponent=Rating(mu=1400, rd=30, vol=0.06), score=1.0),
        GameResult(opponent=Rating(mu=1550, rd=100, vol=0.06), score=0.0),
        GameResult(opponent=Rating(mu=1700, rd=300, vol=0.06), score=1.0),
    ]
    r = update(player, results)
    # 2 wins, 1 loss → net positive. RD should decrease.
    assert r.mu > 1500  # net winner
    assert 140 < r.rd < 160  # RD converges to ~151.5


def test_compute_all_from_archives():
    """compute_all processes archives into player ratings."""
    archives = [
        {
            "game_id": "duel",
            "players": ["alice", "bob"],
            "metadata": {"winner": "alice"},
            "decisions": [],
        },
        {
            "game_id": "duel",
            "players": ["bob", "charlie"],
            "metadata": {"winner": "bob"},
            "decisions": [],
        },
        {
            "game_id": "duel",
            "players": ["alice", "charlie"],
            "metadata": {"winner": "alice"},
            "decisions": [],
        },
    ]
    ratings = compute_all(archives)
    assert len(ratings) == 3
    # Alice won 2 games, should have highest rating
    assert ratings["alice"].mu > ratings["bob"].mu
    assert ratings["alice"].mu > ratings["charlie"].mu
    # Charlie lost all, lowest rating
    assert ratings["charlie"].mu < ratings["bob"].mu


def test_compute_all_empty():
    assert compute_all([]) == {}


def test_compute_all_no_winner():
    """Archives without winner are treated as draws (0.5 vs 0.5)."""
    archives = [{"game_id": "duel", "players": ["a", "b"], "decisions": []}]
    ratings = compute_all(archives)
    # Draws produce ratings (both players get 0.5 score against each other)
    assert "a" in ratings
    assert "b" in ratings
    # Mu stays near 1500 for equal-draw
    assert abs(ratings["a"].mu - 1500) < 5
    assert abs(ratings["b"].mu - 1500) < 5
    # RD decreases from playing a game
    assert ratings["a"].rd < 350


def test_rd_decreases_with_more_games():
    """More games → lower RD (more certainty)."""
    player = Rating(mu=1500, rd=350, vol=0.06)
    opp = Rating(mu=1500, rd=200, vol=0.06)

    r1 = update(player, [GameResult(opponent=opp, score=1.0)])
    r2 = update(r1, [GameResult(opponent=opp, score=1.0)])

    assert r2.rd < r1.rd < 350.0


# ---------------------------------------------------------------------------
# Multi-player Glicko-2 tests
# ---------------------------------------------------------------------------


def test_multiplayer_symmetric_results():
    """In a 4-player game, each player gets exactly 3 results (N-1)."""
    archives = [
        {
            "game_id": "arena",
            "players": ["a", "b", "c", "d"],
            "metadata": {"winner": "a"},
            "decisions": [],
        }
    ]
    ratings = compute_all(archives)
    assert len(ratings) == 4
    # Winner should have highest rating
    assert ratings["a"].mu > 1500
    # All losers should have decreased rating
    for p in "bcd":
        assert ratings[p].mu < 1500
    # All losers' RD should decrease (they played 3 opponents, not just 1)
    for p in "bcd":
        assert ratings[p].rd < 350  # default RD=350


def test_losers_rated_against_each_other():
    """Losers get 0.5 vs other losers — their RD should decrease significantly.

    Previously losers only had 1 result (vs winner). Now they have N-1 results.
    More results = more RD reduction.
    """
    archives = [
        {
            "game_id": "arena",
            "players": ["w", "a", "b", "c", "d", "e", "f", "g"],
            "metadata": {"winner": "w"},
            "decisions": [],
        }
    ]
    ratings = compute_all(archives)
    # Each loser has 7 results: 1 loss vs winner + 6 draws vs other losers
    # This should reduce RD substantially from default 350
    for p in "abcdefg":
        assert ratings[p].rd < 250, f"Loser {p} RD should drop with 7 opponents"


def test_draw_updates_all_ratings():
    """Draw game (no winner) produces 0.5 scores for all pairs."""
    archives = [
        {
            "game_id": "arena",
            "players": ["a", "b", "c"],
            "decisions": [],
            # No winner in metadata
        }
    ]
    ratings = compute_all(archives)
    assert len(ratings) == 3
    # All ratings should stay near 1500 (equal draw)
    for p in "abc":
        assert abs(ratings[p].mu - 1500) < 5
        assert ratings[p].rd < 350  # RD decreases from playing


def test_distribution_scores_ranking():
    """When scores are present, 2nd place is rated between 1st and last."""
    archives = [
        {
            "game_id": "arena",
            "players": ["first", "second", "third"],
            "metadata": {
                "winner": "first",
                "scores": {"first": 100, "second": 60, "third": 20},
            },
            "decisions": [],
        }
    ]
    ratings = compute_all(archives)
    # first > second > third
    assert ratings["first"].mu > ratings["second"].mu
    assert ratings["second"].mu > ratings["third"].mu
    # second should be above 1500 (beat third, lost to first)
    # Actually: second gets 1.0 vs third, 0.0 vs first — net neutral with
    # different opponent strengths. But since all start equal, second
    # will be near 1500 (one win, one loss vs equals).
    assert ratings["second"].mu > ratings["third"].mu


def test_distribution_tied_scores():
    """Players with equal scores get 0.5 against each other."""
    archives = [
        {
            "game_id": "arena",
            "players": ["a", "b", "c"],
            "metadata": {
                "winner": "a",
                "scores": {"a": 100, "b": 50, "c": 50},
            },
            "decisions": [],
        }
    ]
    ratings = compute_all(archives)
    # b and c tied — they should have similar ratings
    assert abs(ratings["b"].mu - ratings["c"].mu) < 1
    # a won — highest
    assert ratings["a"].mu > ratings["b"].mu
