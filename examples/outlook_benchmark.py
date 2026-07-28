"""Regenerate the single-player outlook's cost table.

Costs are reported per decision -- every candidate discard of one post-draw hand
solved to the horizon -- broken down by the hand's distance from ready, which is
what the cost actually tracks.

The hands are **sampled from engine self-play** rather than written by hand.
That matters: a tidy constructed hand with three finished runs has far fewer
improving draws than a real mid-game hand at the same distance, and benchmarking
on constructed hands understates the real cost by two orders of magnitude.

    uv run python examples/outlook_benchmark.py
"""

from __future__ import annotations

import time

from jansou.analysis.ev import discard_outlooks
from jansou.analysis.shanten import shanten_counts
from jansou.core.rules import Rules
from jansou.core.tiles import counts_by_kind
from jansou.game.agents import SmartEfficiencyAgent
from jansou.game.environment import Environment

HORIZON = 18
GAMES = 2
PER_DISTANCE = 6
CORPUS_DECISIONS = 890_000 * 70
GATES = (2, 3, None)


def sample_positions() -> list[tuple[list[int], int, list[int]]]:
    """Post-draw positions from self-play, as concealed counts, melds, visible."""
    found: list[tuple[list[int], int, list[int]]] = []

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


def main() -> None:
    """Print the cost table and the corpus projection it implies."""
    positions = sample_positions()
    by_distance: dict[int, list[tuple[list[int], int, list[int]]]] = {}
    for position in positions:
        distance = shanten_counts(position[0], position[1])
        if distance >= 0:
            by_distance.setdefault(distance, []).append(position)

    print(f"per decision, full curve to {HORIZON} draws, hands sampled from self-play")
    print(f"{'from ready':>10s} {'share':>7s} {'ms/decision':>12s}")
    total = sum(len(group) for group in by_distance.values())
    cost: dict[int, float] = {}
    share: dict[int, float] = {}
    for distance in sorted(by_distance):
        group = by_distance[distance]
        sample = group[:PER_DISTANCE]
        start = time.perf_counter()
        for concealed, melds, visible in sample:
            discard_outlooks(concealed, melds, HORIZON, visible=visible, max_distance=None)
        cost[distance] = (time.perf_counter() - start) / len(sample) * 1000
        share[distance] = len(group) / total
        print(f"{distance:10d} {share[distance]:6.1%} {cost[distance]:11.1f}")

    print(f"\n{'gate':>6s} {'coverage':>9s} {'ms/decision':>12s} {'core-hours':>12s}")
    for gate in GATES:
        reach = [d for d in cost if gate is None or d <= gate]
        weighted = sum(share[d] * cost[d] for d in reach)
        hours = CORPUS_DECISIONS * weighted / 1000 / 3600
        label = "none" if gate is None else str(gate)
        print(f"{label:>6s} {sum(share[d] for d in reach):8.1%} {weighted:11.1f} {hours:11,.0f}")
    print(f"\nover {CORPUS_DECISIONS / 1e6:.0f}M decisions; the shipped default gate is 2")


if __name__ == "__main__":
    main()
