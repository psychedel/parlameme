"""Deterministic RNG — Linear Congruential Generator.

Same parameters as the Clojure version for cross-platform replay compatibility:
a=1103515245, c=12345, m=2^31
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DeterministicRNG:
    """Seedable, deterministic RNG for game replay."""
    state: int

    def __init__(self, seed: int):
        self.state = seed & 0x7FFFFFFF

    def next_int(self) -> tuple[int, DeterministicRNG]:
        """Return (value, new_rng). Does NOT mutate self."""
        new_state = (1103515245 * self.state + 12345) & 0x7FFFFFFF
        return new_state, DeterministicRNG(new_state)

    def next_range(self, n: int) -> tuple[int, DeterministicRNG]:
        """Return (0..n-1 int, new_rng). Convenience wrapper over next_int()."""
        val, rng = self.next_int()
        return val % n, rng

    def next_float(self) -> tuple[float, DeterministicRNG]:
        """Return (0.0-1.0 float, new_rng)."""
        val, rng = self.next_int()
        return val / 0x7FFFFFFF, rng

    def shuffle(self, items: list) -> tuple[list, DeterministicRNG]:
        """Fisher-Yates shuffle. Returns (shuffled_copy, new_rng)."""
        result = list(items)
        rng = DeterministicRNG(self.state)
        for i in range(len(result) - 1, 0, -1):
            val, rng = rng.next_int()
            j = val % (i + 1)
            result[i], result[j] = result[j], result[i]
        return result, rng

    def choice(self, items: list) -> tuple:
        """Pick random item. Returns (item, new_rng)."""
        val, rng = self.next_int()
        idx = val % len(items)
        return items[idx], rng
