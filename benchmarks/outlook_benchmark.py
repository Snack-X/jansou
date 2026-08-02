"""Regenerate the single-player outlook's cost table.

Costs are reported per decision -- every candidate discard of one post-draw hand
solved to the horizon -- broken down by the hand's distance from ready, which is
what the cost actually tracks.

Two things this benchmark is careful about, both learned the hard way:

- The hands are **sampled from engine self-play**, not written by hand. A tidy
  constructed hand with three finished runs has far fewer improving draws than a
  real mid-game hand at the same distance, and benchmarking on constructed hands
  understates the real cost by two orders of magnitude.
- The graph cache makes a second solve of the same hand far cheaper, so timing a
  priced pass straight after an unpriced one flatters the priced one. Pricing is
  measured as the difference between two **warm** passes, then added to a
  **cold** pass, which is what a conversion actually pays.

    uv run python benchmarks/outlook_benchmark.py
"""

from __future__ import annotations

import time

from jansou.analysis.ev import Valuation, discard_outlooks
from jansou.analysis.shanten import shanten_counts
from jansou.core.rules import Rules
from jansou.core.tiles import Tile, TileKind, counts_by_kind
from jansou.game.agents import SmartEfficiencyAgent
from jansou.game.environment import Environment

HORIZON = 18
GAMES = 3
GATES = (2, 3, None)
VALUATION = Valuation(rules=Rules(), dora_indicators=(Tile(TileKind.M1),))
PER_DISTANCE = {0: 60, 1: 60, 2: 60, 3: 30, 4: 6, 5: 3}

Position = tuple[list[int], int, list[int]]


def sample_positions() -> list[Position]:
    """Post-draw positions from self-play, as concealed counts, melds, visible."""
    found: list[Position] = []

    class Sampler(SmartEfficiencyAgent):
        def act(self, seat, kind, actions):  # noqa: ANN001, ANN201, D102
            hand = self._view.hand()  # noqa: SLF001
            concealed = counts_by_kind(hand.concealed)
            if sum(concealed) % 3 == 2:
                found.append((concealed, len(hand.melds), list(self._view.visible)))  # noqa: SLF001
            return super().act(seat, kind, actions)

    for seed in range(GAMES):
        Environment(Rules(), seed=seed).run([Sampler(seed=seed * 10 + i) for i in range(4)])
    return found


def solve(sample: list[Position], valuation: Valuation | None) -> float:
    """Seconds to solve every position in the sample once."""
    start = time.perf_counter()
    for concealed, melds, visible in sample:
        discard_outlooks(concealed, melds, HORIZON, visible=visible, max_distance=None, valuation=valuation)
    return time.perf_counter() - start


def main() -> None:
    """Print the cost table and what the distance gate buys."""
    by_distance: dict[int, list[Position]] = {}
    for position in sample_positions():
        distance = shanten_counts(position[0], position[1])
        if distance >= 0:
            by_distance.setdefault(distance, []).append(position)
    total = sum(len(group) for group in by_distance.values())

    print(f"per decision, full curve to {HORIZON} draws, hands sampled from self-play")
    print(f"{'from ready':>10s} {'share':>7s} {'n':>4s} {'ms/decision':>12s} {'with points':>12s}")
    bare: dict[int, float] = {}
    priced: dict[int, float] = {}
    share: dict[int, float] = {}
    for distance in sorted(by_distance):
        group = by_distance[distance]
        # Concealed hands only, so a valuation needs no invented melds.
        sample = [row for row in group if not row[1]][: PER_DISTANCE.get(distance, 3)]
        if not sample:
            continue
        cold = solve(sample, None)
        warm = solve(sample, None)
        scoring = max(solve(sample, VALUATION) - warm, 0.0)
        bare[distance] = cold / len(sample) * 1000
        priced[distance] = (cold + scoring) / len(sample) * 1000
        share[distance] = len(group) / total
        print(
            f"{distance:10d} {share[distance]:6.1%} {len(sample):4d} {bare[distance]:11.1f} {priced[distance]:12.1f}"
        )

    print(f"\n{'gate':>6s} {'coverage':>9s} {'ms/decision':>12s} {'with points':>12s} {'ratio':>6s}")
    for gate in GATES:
        reach = [d for d in bare if gate is None or d <= gate]
        weighted = sum(share[d] * bare[d] for d in reach)
        with_points = sum(share[d] * priced[d] for d in reach)
        label = "none" if gate is None else str(gate)
        print(
            f"{label:>6s} {sum(share[d] for d in reach):8.1%} {weighted:11.1f} "
            f"{with_points:12.1f} {with_points / weighted:5.2f}x"
        )
    print("\nthe shipped default gate is 2; pricing is off unless a valuation is given")


if __name__ == "__main__":
    main()
