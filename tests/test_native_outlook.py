"""Differential tests: the native outlook solver against the pure solver.

The pure solver in ``jansou.analysis.ev`` is the reference. The native port
mirrors its structure and floating-point operation order, so curves are
compared at near-bitwise tolerance. Hands are built from random groups plus
filler under a fixed seed and bucketed by distance, which keeps every distance
represented without unbounded sampling.
"""

from __future__ import annotations

import random
import zlib

import jansou_native
import pytest

from jansou.analysis.ev import Valuation, _value, hand_outlook
from jansou.analysis.shanten import _table_bytes, shanten_counts
from jansou.core.rules import Rules
from jansou.core.tiles import NUM_KINDS, Tile, TileKind, kinds_in_play

_SEED = 20260802
_HORIZON = 10
_NO_VISIBLE = [0] * NUM_KINDS


@pytest.fixture(scope="module")
def solver() -> jansou_native.OutlookSolver:
    return jansou_native.OutlookSolver(zlib.decompress(_table_bytes()))


def _structured_hand(rng: random.Random, player_count: int, groups: int, size: int) -> list[int]:
    """Counts for ``size`` tiles: ``groups`` random sets plus pool filler."""
    kinds = [int(kind) for kind in kinds_in_play(player_count)]
    run_starts = [k for k in kinds if k < 27 and k % 9 <= 6 and (player_count == 4 or k >= 9)]
    counts = [0] * NUM_KINDS
    placed = tries = 0
    while placed < groups and tries < 50:
        tries += 1
        if rng.random() < 0.3:
            kind = rng.choice(kinds)
            if counts[kind] <= 1:
                counts[kind] += 3
                placed += 1
        else:
            start = rng.choice(run_starts)
            if all(counts[start + offset] < 4 for offset in range(3)):
                for offset in range(3):
                    counts[start + offset] += 1
                placed += 1
    pool = [kind for kind in kinds for _ in range(4 - counts[kind])]
    rng.shuffle(pool)
    for kind in pool[: size - sum(counts)]:
        counts[kind] += 1
    return counts


def _hands_by_distance(rng: random.Random, player_count: int, num_melds: int, want: dict[int, int]) -> list[list[int]]:
    """Hands bucketed by shanten, ``want[distance]`` of each where reachable."""
    found: dict[int, list[list[int]]] = {distance: [] for distance in want}
    size = 13 - 3 * num_melds
    for _ in range(5000):
        if all(len(group) >= want[distance] for distance, group in found.items()):
            break
        counts = _structured_hand(rng, player_count, rng.randrange(size // 3 + 1), size)
        distance = shanten_counts(counts, num_melds)
        if distance in found and len(found[distance]) < want[distance]:
            found[distance].append(counts)
    hands = [counts for group in found.values() for counts in group]
    assert hands, "the generator must reach at least one wanted distance"
    return hands


def _visible_sample(rng: random.Random, counts: list[int], player_count: int, tiles: int) -> list[int]:
    """Counts of ``tiles`` visible tiles drawn from outside the hand."""
    kinds = [int(kind) for kind in kinds_in_play(player_count)]
    pool = [kind for kind in kinds for _ in range(4 - counts[kind])]
    rng.shuffle(pool)
    visible = [0] * NUM_KINDS
    for kind in pool[:tiles]:
        visible[kind] += 1
    return visible


def _assert_curves_match(solver, counts, num_melds, visible, player_count, beam) -> None:
    pure = hand_outlook(
        counts,
        num_melds,
        _HORIZON,
        visible=visible,
        player_count=player_count,
        beam=beam,
        max_distance=None,
    )
    ready, win = solver.solve(counts, num_melds, _HORIZON, visible or _NO_VISIBLE, player_count, beam)
    assert ready == pytest.approx(list(pure.ready), rel=1e-12, abs=1e-15)
    assert win == pytest.approx(list(pure.win), rel=1e-12, abs=1e-15)


def test_beam_one_matches_pure(solver) -> None:
    rng = random.Random(_SEED)
    for counts in _hands_by_distance(rng, 4, 0, {0: 4, 1: 4, 2: 3, 3: 2}):
        _assert_curves_match(solver, counts, 0, None, 4, 1)


def test_exact_matches_pure(solver) -> None:
    rng = random.Random(_SEED + 1)
    for counts in _hands_by_distance(rng, 4, 0, {0: 3, 1: 3, 2: 2}):
        _assert_curves_match(solver, counts, 0, None, 4, None)


def test_wider_beam_matches_pure(solver) -> None:
    rng = random.Random(_SEED + 2)
    for counts in _hands_by_distance(rng, 4, 0, {1: 2, 2: 2, 3: 1}):
        _assert_curves_match(solver, counts, 0, None, 4, 2)


@pytest.mark.parametrize("num_melds", [1, 2])
def test_melded_hands_match_pure(solver, num_melds: int) -> None:
    rng = random.Random(_SEED + 3 + num_melds)
    for counts in _hands_by_distance(rng, 4, num_melds, {0: 2, 1: 2, 2: 2}):
        _assert_curves_match(solver, counts, num_melds, None, 4, 1)


def test_sanma_matches_pure(solver) -> None:
    rng = random.Random(_SEED + 6)
    for counts in _hands_by_distance(rng, 3, 0, {0: 2, 1: 2, 2: 2}):
        _assert_curves_match(solver, counts, 0, None, 3, 1)


def test_visible_tiles_match_pure(solver) -> None:
    rng = random.Random(_SEED + 7)
    for counts in _hands_by_distance(rng, 4, 0, {0: 2, 1: 2, 2: 2}):
        visible = _visible_sample(rng, counts, 4, 30)
        _assert_curves_match(solver, counts, 0, visible, 4, 1)


def test_zero_horizon(solver) -> None:
    rng = random.Random(_SEED + 8)
    counts = _hands_by_distance(rng, 4, 0, {0: 1})[0]
    ready, win = solver.solve(counts, 0, 0, _NO_VISIBLE)
    assert ready == [1.0]
    assert win == [0.0]


def test_points_curve_matches_pure(solver) -> None:
    rng = random.Random(_SEED + 9)
    valuation = Valuation(rules=Rules(), dora_indicators=(Tile(TileKind.P5),))
    for counts in _hands_by_distance(rng, 4, 0, {0: 3, 1: 2, 2: 2}):
        pure = hand_outlook(counts, 0, _HORIZON, beam=1, max_distance=None, valuation=valuation)
        entries = solver.pricing_entries(counts, 0, _HORIZON, 4, 1)
        worths = [_value(tuple(state), int(kind), valuation) for state, kind in entries]
        points = solver.points_curve(counts, 0, _HORIZON, _NO_VISIBLE, worths, 4, 1)
        assert points == pytest.approx(list(pure.points), rel=1e-12, abs=1e-15)


def test_validation(solver) -> None:
    hand = [1] * 13 + [0] * 21
    with pytest.raises(ValueError, match="max draws must not be negative"):
        solver.solve(hand, 0, -1, _NO_VISIBLE)
    with pytest.raises(ValueError, match="beam must be at least one"):
        solver.solve(hand, 0, _HORIZON, _NO_VISIBLE, 4, 0)
    with pytest.raises(ValueError, match="player count must be 3 or 4"):
        solver.solve(hand, 0, _HORIZON, _NO_VISIBLE, 5, 1)
    with pytest.raises(ValueError, match="more copies visible and held"):
        solver.solve(hand, 0, _HORIZON, [4] + [0] * 33)
    with pytest.raises(ValueError, match="fewer than"):
        solver.solve(hand, 0, _HORIZON, [4 - held for held in hand])
    with pytest.raises(ValueError, match="34 entries"):
        solver.solve([0] * 33, 0, _HORIZON, _NO_VISIBLE)
    with pytest.raises(ValueError, match="worths"):
        solver.points_curve(hand, 0, _HORIZON, _NO_VISIBLE, [1000.0] * 99, 4, 1)
