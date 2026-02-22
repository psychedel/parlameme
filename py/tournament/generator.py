"""Deterministic match generation algorithms for tournament formats."""

from __future__ import annotations

import math
from itertools import combinations

from engine.runtime.rng import DeterministicRNG

from .state import Match, Standing


class MatchGenerator:
    """Generate matches for tournament formats. All methods are pure & deterministic."""

    @staticmethod
    def round_robin(
        participants: list[str], seed: int, match_size: int = 2
    ) -> list[Match]:
        """Generate round-robin matches.

        For match_size=2: circle method (each pair plays once).
        For match_size>2: all combinations of size match_size, shuffled.
        """
        rng = DeterministicRNG(seed)
        shuffled, rng = rng.shuffle(list(participants))

        if match_size > 2:
            # Group matches: generate all combinations of match_size
            combos = list(combinations(shuffled, match_size))
            combos_shuffled, rng = rng.shuffle(combos)
            matches = []
            for idx, group in enumerate(combos_shuffled):
                matches.append(
                    Match(
                        id=f"rr-{idx}",
                        participants=tuple(group),
                        round=idx + 1,
                    )
                )
            return matches

        # Standard 2-player circle method
        n = len(shuffled)
        if n % 2 == 1:
            shuffled.append("__BYE__")
            n += 1

        matches = []
        match_idx = 0
        for round_num in range(1, n):
            for i in range(n // 2):
                a = shuffled[i]
                b = shuffled[n - 1 - i]
                if a == "__BYE__" or b == "__BYE__":
                    continue
                matches.append(
                    Match(
                        id=f"rr-{match_idx}",
                        participants=(a, b),
                        round=round_num,
                    )
                )
                match_idx += 1
            # Rotate all but first (circle method)
            shuffled = [shuffled[0]] + [shuffled[-1]] + shuffled[1:-1]

        return matches

    @staticmethod
    def single_elimination(
        participants: list[str], seed: int, match_size: int = 2
    ) -> list[Match]:
        """Bracket with seeded pairing.

        match_size=2: standard 1v1 bracket.
        match_size>2: pod-based elimination (groups of match_size, winner advances).
        Only generates first round. Later rounds generated on completion.
        """
        rng = DeterministicRNG(seed)
        seeded, rng = rng.shuffle(list(participants))

        if match_size > 2:
            # Pod-based: split into groups of match_size
            pods: list[tuple[str, ...]] = []
            for i in range(0, len(seeded), match_size):
                pod = tuple(seeded[i : i + match_size])
                if len(pod) >= 2:  # need at least 2 to play
                    pods.append(pod)
                else:
                    # Single leftover → bye (auto-advance via get_bye_advances)
                    pass
            matches = []
            for idx, pod in enumerate(pods):
                matches.append(
                    Match(
                        id=f"se-r1-{idx}",
                        participants=pod,
                        round=1,
                        stage="main",
                    )
                )
            return matches

        # Standard 2-player bracket
        size = 2 ** math.ceil(math.log2(max(len(seeded), 2)))

        matches = []
        match_idx = 0

        # Pair from outside-in (1 vs last, 2 vs second-to-last, etc.)
        for i in range(size // 2):
            a_idx = i
            b_idx = size - 1 - i

            a = seeded[a_idx] if a_idx < len(seeded) else None
            b = seeded[b_idx] if b_idx < len(seeded) else None

            if a is None and b is None:
                continue
            if a is None or b is None:
                continue  # bye — auto-advance handled by runtime

            matches.append(
                Match(
                    id=f"se-r1-{match_idx}",
                    participants=(a, b),
                    round=1,
                    stage="main",
                )
            )
            match_idx += 1

        return matches

    @staticmethod
    def get_bye_advances(
        participants: list[str], seed: int, match_size: int = 2
    ) -> list[str]:
        """Get participants who auto-advance due to byes."""
        rng = DeterministicRNG(seed)
        seeded, rng = rng.shuffle(list(participants))

        if match_size > 2:
            # Leftover players who don't fill a pod
            remainder = len(seeded) % match_size
            if remainder == 1:
                return [seeded[-1]]
            return []

        size = 2 ** math.ceil(math.log2(max(len(seeded), 2)))
        advances = []

        for i in range(size // 2):
            a_idx = i
            b_idx = size - 1 - i
            a = seeded[a_idx] if a_idx < len(seeded) else None
            b = seeded[b_idx] if b_idx < len(seeded) else None

            if a and not b:
                advances.append(a)
            elif b and not a:
                advances.append(b)

        return advances

    @staticmethod
    def swiss_pairing(
        standings: dict[str, Standing],
        history: set[frozenset[str]],
        round_num: int,
        match_size: int = 2,
    ) -> list[Match]:
        """Swiss system: pair similar-scored players who haven't met.

        match_size=2: pair adjacent players by score.
        match_size>2: group adjacent players into pods of match_size.
        Avoids rematches when possible. Odd player out gets a bye match.
        """
        sorted_players = sorted(
            standings.values(),
            key=lambda s: (-s.points, -s.buchholz),
        )

        matches: list[Match] = []
        match_idx = 0

        if match_size > 2:
            remaining = list(sorted_players)
            while len(remaining) >= 2:
                group = remaining[:match_size]
                group_key = frozenset(p.participant for p in group)
                # Try to avoid exact rematch by swapping last member
                if group_key in history and len(remaining) > match_size:
                    for j in range(match_size, len(remaining)):
                        alt_group = remaining[: match_size - 1] + [remaining[j]]
                        alt_key = frozenset(p.participant for p in alt_group)
                        if alt_key not in history:
                            remaining[match_size - 1], remaining[j] = (
                                remaining[j],
                                remaining[match_size - 1],
                            )
                            group = alt_group
                            break
                participants = tuple(p.participant for p in group)
                matches.append(
                    Match(
                        id=f"sw-r{round_num}-{match_idx}",
                        participants=participants,
                        round=round_num,
                    )
                )
                match_idx += 1
                remaining = remaining[len(group):]
            # Bye for remaining players (< 2)
            for p in remaining:
                matches.append(
                    Match(
                        id=f"sw-r{round_num}-bye-{p.participant}",
                        participants=(p.participant,),
                        round=round_num,
                    )
                )
            return matches

        paired: set[str] = set()
        for player in sorted_players:
            if player.participant in paired:
                continue

            best_opponent = None
            for opponent in sorted_players:
                if opponent.participant == player.participant:
                    continue
                if opponent.participant in paired:
                    continue
                pair_key = frozenset([player.participant, opponent.participant])
                if pair_key in history:
                    continue
                best_opponent = opponent
                break

            if best_opponent is None:
                # Fallback: pair with anyone unpaired
                for opponent in sorted_players:
                    if (
                        opponent.participant != player.participant
                        and opponent.participant not in paired
                    ):
                        best_opponent = opponent
                        break

            if best_opponent:
                matches.append(
                    Match(
                        id=f"sw-r{round_num}-{match_idx}",
                        participants=(
                            player.participant,
                            best_opponent.participant,
                        ),
                        round=round_num,
                    )
                )
                paired.add(player.participant)
                paired.add(best_opponent.participant)
                match_idx += 1

        # Bye for unpaired player (odd count)
        for player in sorted_players:
            if player.participant not in paired:
                matches.append(
                    Match(
                        id=f"sw-r{round_num}-bye-{player.participant}",
                        participants=(player.participant,),
                        round=round_num,
                    )
                )

        return matches

    @staticmethod
    def next_elimination_round(
        completed_matches: list[Match], next_round: int, match_size: int = 2
    ) -> list[Match]:
        """Generate next elimination round from completed matches."""
        winners = [m.winner for m in completed_matches if m.winner]
        matches = []
        for i in range(0, len(winners), match_size):
            pod = tuple(winners[i : i + match_size])
            if len(pod) >= 2:
                matches.append(
                    Match(
                        id=f"se-r{next_round}-{i // match_size}",
                        participants=pod,
                        round=next_round,
                        stage="main",
                    )
                )
        return matches
