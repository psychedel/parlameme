"""Glicko-2 rating system — pure functions, no side effects.

Implementation of Mark Glickman's Glicko-2 algorithm.
Reference: http://www.glicko.net/glicko/glicko2.pdf

Key concepts:
- mu (rating): skill estimate, default 1500
- rd (rating deviation): uncertainty — high RD means uncertain skill
- vol (volatility): expected fluctuation in player strength

All internal math uses Glicko-2 scale (mu_g2 = (mu - 1500) / 173.7178).
External API uses traditional scale (1500-centered).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

# Glicko-2 system constant — constrains volatility change.
# Smaller = more conservative. Glickman recommends 0.3-1.2.
TAU = 0.5

# Convergence tolerance for volatility iteration
EPSILON = 1e-6

# Scale factor between Glicko-1 and Glicko-2
SCALE = 173.7178


@dataclass(frozen=True)
class Rating:
    """Player rating with uncertainty."""

    mu: float = 1500.0  # skill estimate
    rd: float = 350.0  # rating deviation (uncertainty)
    vol: float = 0.06  # volatility (expected fluctuation)


@dataclass(frozen=True)
class GameResult:
    """One game outcome against an opponent."""

    opponent: Rating
    score: float  # 1.0 = win, 0.5 = draw, 0.0 = loss


# ---------------------------------------------------------------------------
# Tier classification
# ---------------------------------------------------------------------------

TIERS = [
    (2000, "Master"),
    (1800, "Expert"),
    (1600, "Advanced"),
    (1400, "Intermediate"),
    (1200, "Beginner"),
    (0, "Novice"),
]


def tier(mu: float) -> str:
    """Classify a rating into a human-readable tier."""
    return next(label for threshold, label in TIERS if mu >= threshold)


# ---------------------------------------------------------------------------
# Core Glicko-2 math (internal scale)
# ---------------------------------------------------------------------------


def _to_g2(r: Rating) -> tuple[float, float, float]:
    """Convert to Glicko-2 internal scale."""
    return (r.mu - 1500) / SCALE, r.rd / SCALE, r.vol


def _from_g2(mu_g2: float, phi_g2: float, vol: float) -> Rating:
    """Convert from Glicko-2 internal scale."""
    return Rating(mu=mu_g2 * SCALE + 1500, rd=phi_g2 * SCALE, vol=vol)


def _g(phi: float) -> float:
    """Glicko-2 g function — reduces impact of uncertain opponents."""
    return 1.0 / math.sqrt(1.0 + 3.0 * phi * phi / (math.pi * math.pi))


def _E(mu: float, mu_j: float, phi_j: float) -> float:
    """Expected score against opponent j."""
    return 1.0 / (1.0 + math.exp(-_g(phi_j) * (mu - mu_j)))


def _new_volatility(vol: float, delta: float, phi: float, v: float) -> float:
    """Compute new volatility via Illinois algorithm (Step 5 of Glicko-2)."""
    a = math.log(vol * vol)

    def f(x: float) -> float:
        ex = math.exp(x)
        d2 = delta * delta
        p2 = phi * phi
        return (ex * (d2 - p2 - v - ex)) / (2.0 * (p2 + v + ex) ** 2) - (x - a) / (
            TAU * TAU
        )

    # Bracket the root
    A = a
    if delta * delta > phi * phi + v:
        B = math.log(delta * delta - phi * phi - v)
    else:
        k = 1
        while f(a - k * TAU) < 0:
            k += 1
        B = a - k * TAU

    # Illinois method (bisection variant)
    fA = f(A)
    fB = f(B)
    for _ in range(100):  # safety limit
        if abs(B - A) < EPSILON:
            break
        C = A + (A - B) * fA / (fB - fA)
        fC = f(C)
        if fC * fB <= 0:
            A = B
            fA = fB
        else:
            fA /= 2.0
        B = C
        fB = fC

    return math.exp(A / 2.0)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def update(rating: Rating, results: list[GameResult]) -> Rating:
    """Single Glicko-2 update step. Pure function.

    If no games played, only RD increases (uncertainty grows over time).
    """
    mu, phi, vol = _to_g2(rating)

    if not results:
        # Step 6 special case: no games → RD grows
        phi_star = math.sqrt(phi * phi + vol * vol)
        return _from_g2(mu, phi_star, vol)

    # Step 3: Compute v (estimated variance)
    v_inv = 0.0
    for r in results:
        mu_j, phi_j, _ = _to_g2(r.opponent)
        g_j = _g(phi_j)
        e_j = _E(mu, mu_j, phi_j)
        v_inv += g_j * g_j * e_j * (1.0 - e_j)

    v = 1.0 / v_inv if v_inv > 0 else 1e10

    # Step 4: Compute delta (improvement)
    delta = 0.0
    for r in results:
        mu_j, phi_j, _ = _to_g2(r.opponent)
        g_j = _g(phi_j)
        e_j = _E(mu, mu_j, phi_j)
        delta += g_j * (r.score - e_j)
    delta *= v

    # Step 5: New volatility
    new_vol = _new_volatility(vol, delta, phi, v)

    # Step 6: Update RD (pre-rating)
    phi_star = math.sqrt(phi * phi + new_vol * new_vol)

    # Step 7: Update rating and RD
    phi_new = 1.0 / math.sqrt(1.0 / (phi_star * phi_star) + 1.0 / v)
    mu_new = mu + phi_new * phi_new * sum(
        _g(_to_g2(r.opponent)[1]) * (r.score - _E(mu, *_to_g2(r.opponent)[:2]))
        for r in results
    )

    return _from_g2(mu_new, phi_new, new_vol)


def compute_all(archives: list[dict[str, Any]]) -> dict[str, Rating]:
    """Process archives chronologically → {player_id: Rating}.

    Each archive is one game. We decompose multi-player games into
    all-pairs results: every player gets N-1 GameResults per game.

    Scoring:
    - Single winner: winner=1.0 vs all, losers=0.0 vs winner, 0.5 vs each other
    - Distribution (scores): pairwise comparison by score values
    - Draw (no winner): everyone=0.5 vs everyone
    """
    from engine.archive import extract_winner

    ratings: dict[str, Rating] = {}

    for archive in archives:
        players = archive.get("players", [])
        if len(players) < 2:
            continue

        winner = extract_winner(archive)
        scores = archive.get("metadata", {}).get("scores")

        # Build all-pairs results: every player rated vs every other
        player_results: dict[str, list[GameResult]] = {p: [] for p in players}

        for p in players:
            for other in players:
                if other == p:
                    continue
                opp = ratings.get(other, Rating())
                if winner is None:
                    # Draw: everyone 0.5 vs everyone
                    player_results[p].append(GameResult(opponent=opp, score=0.5))
                elif scores:
                    # Distribution victory: compare by score
                    p_score = scores.get(p, 0)
                    o_score = scores.get(other, 0)
                    if p_score > o_score:
                        s = 1.0
                    elif p_score < o_score:
                        s = 0.0
                    else:
                        s = 0.5
                    player_results[p].append(GameResult(opponent=opp, score=s))
                else:
                    # Single winner: winner beats all, losers tie each other
                    if p == winner:
                        s = 1.0
                    elif other == winner:
                        s = 0.0
                    else:
                        s = 0.5
                    player_results[p].append(GameResult(opponent=opp, score=s))

        # Apply Glicko-2 updates
        for p, results in player_results.items():
            current = ratings.get(p, Rating())
            ratings[p] = update(current, results)

    return ratings
