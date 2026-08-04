"""Baseline benchmark for the native rewrite of the analysis layer.

Covers the two rewrite targets and nothing else:

- the shanten kernel: plain lookups and whole probe sweeps
- the outlook solver: cost per decision by distance, beam and exact

Hands are sampled from engine self-play under fixed seeds, then deduplicated,
so the sample is identical on every run and the numbers compare across
implementations. Constructed hands would understate the real cost (see
``outlook_benchmark.py``).

Kernel costs are reported per call, twice: **cold** empties the per-hand memos
first, which is what a stream of unseen hands pays and the path a native
kernel replaces; **warm** repeats the same pass and is the memo ceiling. The
persistent class tables stay warm in both, as they do in real use. Solver
costs are per decision with every cache cleared before each bucket.

    uv run python benchmarks/analysis_benchmark.py
"""

from __future__ import annotations

import time
from math import inf

from jansou.analysis import _native, ev, shanten
from jansou.analysis.ev import discard_outlooks
from jansou.analysis.shanten import probe_shantens, shanten_counts
from jansou.core.rules import Rules
from jansou.core.tiles import counts_by_kind
from jansou.game.agents import SmartEfficiencyAgent
from jansou.game.environment import Environment

GAMES = 4
KERNEL_REPS = 5
HORIZON = 18

#: Decisions timed per distance; a missing distance is skipped for that column.
BEAM_SAMPLES = {0: 40, 1: 30, 2: 12, 3: 4, 4: 1}
EXACT_SAMPLES = {0: 20, 1: 12, 2: 6, 3: 3, 4: 1}

Position = tuple[list[int], int, list[int]]


def sample_positions() -> tuple[list[Position], list[Position]]:
    """Distinct resting and post-draw positions from engine self-play."""
    seen: set[tuple[bytes, int]] = set()
    resting: list[Position] = []
    post_draw: list[Position] = []

    class Sampler(SmartEfficiencyAgent):
        def act(self, seat, kind, actions):  # noqa: ANN001, ANN201, D102
            hand = self._view.hand()  # noqa: SLF001
            concealed = counts_by_kind(hand.concealed)
            key = (bytes(concealed), len(hand.melds))
            if key not in seen:
                seen.add(key)
                position = (concealed, len(hand.melds), list(self._view.visible))  # noqa: SLF001
                (post_draw if sum(concealed) % 3 == 2 else resting).append(position)
            return super().act(seat, kind, actions)

    for seed in range(GAMES):
        Environment(Rules(), seed=seed).run([Sampler(seed=seed * 10 + i) for i in range(4)])
    return resting, post_draw


def clear_kernel_memos() -> None:
    """Empty the per-hand memos, leaving the persistent class tables warm."""
    shanten._PROBE_MEMO.clear()  # noqa: SLF001
    shanten._COUNT_MEMO.clear()  # noqa: SLF001


def clear_solver_caches() -> None:
    """Empty every cache the solver reads, kernel memos included."""
    clear_kernel_memos()
    ev._EXPANSIONS.clear()  # noqa: SLF001
    ev._BREADTHS.clear()  # noqa: SLF001
    ev._VALUES.clear()  # noqa: SLF001
    ev._CACHE = ev._GraphCache(ev._GRAPH_CACHE_STATES)  # noqa: SLF001
    # The native solver keeps its own graph and expansion caches; start fresh.
    _native.SOLVER = _native.build()[1]


def kernel_report(resting: list[Position], post_draw: list[Position]) -> None:
    """Print each kernel call's cold and warm cost per call."""
    jobs = (
        ("shanten_counts", resting + post_draw, 0),
        ("probe sweep, draw", resting, 1),
        ("probe sweep, discard", post_draw, -1),
    )
    print(f"kernel, us per call over distinct self-play hands, best of {KERNEL_REPS}")
    print(f"{'call':>22s} {'n':>6s} {'cold':>8s} {'warm':>8s}")
    for label, hands, delta in jobs:
        cold = warm = inf
        for _ in range(KERNEL_REPS):
            clear_kernel_memos()
            start = time.perf_counter()
            if delta:
                for counts, melds, _ in hands:
                    probe_shantens(counts, melds, delta)
            else:
                for counts, melds, _ in hands:
                    shanten_counts(counts, melds)
            cold = min(cold, time.perf_counter() - start)
            start = time.perf_counter()
            if delta:
                for counts, melds, _ in hands:
                    probe_shantens(counts, melds, delta)
            else:
                for counts, melds, _ in hands:
                    shanten_counts(counts, melds)
            warm = min(warm, time.perf_counter() - start)
        scale = 1e6 / len(hands)
        print(f"{label:>22s} {len(hands):6d} {cold * scale:8.2f} {warm * scale:8.2f}")


def solve(sample: list[Position], beam: int | None) -> float:
    """Milliseconds per decision to solve the sample, caches cleared first."""
    clear_solver_caches()
    start = time.perf_counter()
    for concealed, melds, visible in sample:
        discard_outlooks(concealed, melds, HORIZON, visible=visible, max_distance=None, beam=beam)
    return (time.perf_counter() - start) / len(sample) * 1000


def solver_report(post_draw: list[Position]) -> None:
    """Print the solver's cost per decision by distance, beam and exact."""
    by_distance: dict[int, list[Position]] = {}
    for position in post_draw:
        if position[1]:
            continue  # concealed hands only; melds change the shape, not the story
        distance = shanten_counts(position[0], position[1])
        if distance >= 0:
            by_distance.setdefault(distance, []).append(position)

    print(f"\nsolver, ms per decision, full curve to {HORIZON} draws, cold caches")
    print(f"{'from ready':>10s} {'n':>4s} {'beam=1':>10s} {'n':>4s} {'exact':>10s}")
    for distance in sorted(by_distance):
        group = by_distance[distance]
        cells = []
        for samples, beam in ((BEAM_SAMPLES, 1), (EXACT_SAMPLES, None)):
            wanted = min(samples.get(distance, 0), len(group))
            if wanted:
                cells.append(f"{wanted:4d} {solve(group[:wanted], beam):10.2f}")
            else:
                cells.append(f"{'-':>4s} {'-':>10s}")
        print(f"{distance:10d} {cells[0]} {cells[1]}")


def main() -> None:
    """Sample once, then report both targets."""
    resting, post_draw = sample_positions()
    kernel_report(resting, post_draw)
    solver_report(post_draw)


if __name__ == "__main__":
    main()
