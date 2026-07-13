"""Shanten: how many tile exchanges a hand is from ready.

Reported on a fixed convention: -1 complete (agari), 0 ready (tenpai), and a
positive count otherwise. Three target shapes are measured -- the standard four
groups and a pair, seven pairs, and thirteen orphans -- and a hand's shanten is
the lowest of the ones that apply.
"""

from __future__ import annotations

from functools import cache
from typing import TYPE_CHECKING

from jansou.core.hand import FULL_HAND_SIZE, MAX_MELDS
from jansou.core.tiles import YAOCHUU_KINDS, counts_by_kind

if TYPE_CHECKING:
    from jansou.core.hand import Hand

_PAIR_SIZE = 2
_SET_SIZE = 3
_SUIT_RANKS = 9
_FULL_CONCEALED = (FULL_HAND_SIZE, FULL_HAND_SIZE + 1)
_STANDARD_EMPTY = 8  # 2 * four sets, minus the head; the ceiling before any block is found
_CHIITOI_TARGET_PAIRS = 6  # six pairs plus a spare of a seventh kind is ready
_CHIITOI_DISTINCT = 7
_KOKUSHI_KINDS = 13

# The count vector split into blocks the standard search decomposes independently.
_BLOCKS = ((0, 9), (9, 18), (18, 27), (27, 34))


def _legal_sizes(num_melds: int) -> tuple[int, int]:
    """The resting and holding concealed-tile counts for a hand with this many melds."""
    rest = 13 - _SET_SIZE * num_melds
    return rest, rest + 1


@cache
def _block_options(block: tuple[int, ...], *, is_honor: bool) -> frozenset[tuple[int, int, int]]:
    """The non-dominated (sets, partials, head) splits of one suit or the honors.

    Each option counts complete sets, partial groups (taatsu, including a pair
    kept toward a triplet), and whether one pair is reserved as the head. Runs
    are available only to a number suit. A rank's leftover tiles are simply not
    used, so the search advances past an index rather than peeling floaters.
    """
    counts = list(block)
    options: set[tuple[int, int, int]] = set()

    def take(removed: tuple[int, ...], i: int, sets: int, partials: int, head: int) -> None:
        for k in removed:
            counts[k] -= 1
        visit(i, sets, partials, head)
        for k in removed:
            counts[k] += 1

    def visit(i: int, sets: int, partials: int, head: int) -> None:
        if i >= len(counts):
            options.add((sets, partials, head))
            return
        visit(i + 1, sets, partials, head)  # leave any tiles at i unused
        if counts[i] >= _SET_SIZE:
            take((i,) * _SET_SIZE, i, sets + 1, partials, head)
        if counts[i] >= _PAIR_SIZE:
            if not head:
                take((i, i), i, sets, partials, 1)
            take((i, i), i, sets, partials + 1, head)
        for run, tiles in _suited_partials(counts, i, is_honor=is_honor):
            take(tiles, i, sets + run, partials + (1 - run), head)

    visit(0, 0, 0, 0)
    return frozenset(_pareto(options))


def _suited_partials(counts: list[int], i: int, *, is_honor: bool) -> list[tuple[int, tuple[int, ...]]]:
    """The run and run-partials startable at index i, each as (is_run, tiles)."""
    if is_honor or not counts[i]:
        return []
    rank = i % _SUIT_RANKS
    shapes: list[tuple[int, tuple[int, ...]]] = []
    if rank <= _SUIT_RANKS - 3 and counts[i + 1] and counts[i + 2]:
        shapes.append((1, (i, i + 1, i + 2)))
    if rank <= _SUIT_RANKS - 2 and counts[i + 1]:
        shapes.append((0, (i, i + 1)))
    if rank <= _SUIT_RANKS - 3 and counts[i + 2]:
        shapes.append((0, (i, i + 2)))
    return shapes


def _pareto(options: set[tuple[int, int, int]]) -> set[tuple[int, int, int]]:
    """Drop options dominated by another with at least as many of every count."""
    return {a for a in options if not any(b != a and b[0] >= a[0] and b[1] >= a[1] and b[2] >= a[2] for b in options)}


def _standard(counts: list[int], num_melds: int) -> int:
    """Shanten toward the standard four-groups-and-a-pair shape."""
    states = {(0, 0, 0)}
    for start, stop in _BLOCKS:
        block = tuple(counts[start:stop])
        block_options = _block_options(block, is_honor=start == _BLOCKS[-1][0])
        states = {
            (sets + bs, partials + bp, head + bh)
            for sets, partials, head in states
            for bs, bp, bh in block_options
            if head + bh <= 1
        }
    best = _STANDARD_EMPTY
    for sets, partials, head in states:
        total_sets = sets + num_melds
        usable = min(partials, MAX_MELDS - total_sets)
        best = min(best, _STANDARD_EMPTY - 2 * total_sets - usable - head)
    return best


def _chiitoi(counts: list[int]) -> int:
    """Shanten toward seven distinct pairs."""
    pairs = sum(1 for c in counts if c >= _PAIR_SIZE)
    kinds = sum(1 for c in counts if c)
    return _CHIITOI_TARGET_PAIRS - pairs + max(0, _CHIITOI_DISTINCT - kinds)


def _kokushi(counts: list[int]) -> int:
    """Shanten toward thirteen orphans."""
    present = sum(1 for k in YAOCHUU_KINDS if counts[k])
    has_pair = any(counts[k] >= _PAIR_SIZE for k in YAOCHUU_KINDS)
    return _KOKUSHI_KINDS - present - (1 if has_pair else 0)


def shanten_counts(counts: list[int], num_melds: int) -> int:
    """The shanten of concealed counts given the meld count.

    Seven pairs and thirteen orphans are measured only for a full concealed
    hand with no melds.

    Args:
        counts: Concealed tile counts indexed by kind.
        num_melds: The number of called melds the hand holds.

    Returns:
        The shanten count: ``-1`` complete, ``0`` ready, positive otherwise.

    Raises:
        ValueError: If the concealed tile total is not legal for the meld count.
    """
    total = sum(counts)
    if total not in _legal_sizes(num_melds):
        raise ValueError(f"a hand with {num_melds} melds cannot have {total} concealed tiles")
    best = _standard(counts, num_melds)
    if num_melds == 0 and total in _FULL_CONCEALED:
        best = min(best, _chiitoi(counts), _kokushi(counts))
    return best


def shanten(hand: Hand) -> int:
    """The shanten of a hand from its concealed tiles and melds.

    Args:
        hand: The hand to measure.

    Returns:
        The shanten count: ``-1`` complete, ``0`` ready, positive otherwise.
    """
    return shanten_counts(counts_by_kind(hand.concealed), len(hand.melds))


def is_tenpai(hand: Hand) -> bool:
    """Whether the hand is ready: shanten zero.

    Args:
        hand: The hand to test.

    Returns:
        ``True`` if the hand is ready (tenpai).
    """
    return shanten(hand) == 0


def is_complete(hand: Hand) -> bool:
    """Whether the hand is complete: shanten minus one.

    Args:
        hand: The hand to test.

    Returns:
        ``True`` if the hand is complete (agari).
    """
    return shanten(hand) == -1
