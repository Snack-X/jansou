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
    from collections.abc import Iterable

    from jansou.core.hand import Hand
    from jansou.core.tiles import TileKind

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


# Combination runs over packed states -- sets * 10 + partials * 2 + head -- with
# both counts clamped at four. The clamps lose nothing: a legal hand never fits
# more than four sets, and the score only reads min(partials, 4 - sets), so a
# fifth partial can never matter. Addition and scoring become table lookups.
_STATE_SPACE = 50


def _pack(sets: int, partials: int, head: int) -> int:
    """One packed state index, its counts clamped to the useful range."""
    return min(sets, MAX_MELDS) * 10 + min(partials, MAX_MELDS) * 2 + head


def _added_states() -> tuple[tuple[int, ...], ...]:
    """The packed-state addition table; -1 marks the two-heads conflict."""
    table = []
    for a in range(_STATE_SPACE):
        row = []
        for b in range(_STATE_SPACE):
            if a % 2 and b % 2:
                row.append(-1)
            else:
                row.append(_pack(a // 10 + b // 10, a % 10 // 2 + b % 10 // 2, a % 2 + b % 2))
        table.append(tuple(row))
    return tuple(table)


def _state_scores() -> tuple[tuple[int, ...], ...]:
    """Each packed state's standard shanten, one row per meld count."""
    table = []
    for num_melds in range(MAX_MELDS + 1):
        row = []
        for state in range(_STATE_SPACE):
            total_sets = state // 10 + num_melds
            usable = min(state % 10 // 2, MAX_MELDS - total_sets)
            row.append(_STANDARD_EMPTY - 2 * total_sets - usable - state % 2)
        table.append(tuple(row))
    return tuple(table)


_ADD = _added_states()
_SCORES = _state_scores()


def _summed_scores() -> tuple[tuple[tuple[int, ...], ...], ...]:
    """The score of each pairwise state sum, one table per meld count.

    Scoring a sum directly skips materializing the combined state, so a final
    combination can take a plain minimum. The two-heads conflict scores one
    above the empty-hand ceiling, where no minimum ever picks it.
    """
    return tuple(
        tuple(
            tuple(scores[added] if (added := _ADD[a][b]) >= 0 else _STANDARD_EMPTY + 1 for b in range(_STATE_SPACE))
            for a in range(_STATE_SPACE)
        )
        for scores in _SCORES
    )


_SUMMED_SCORES = _summed_scores()


@cache
def _packed_options(block: tuple[int, ...]) -> tuple[int, ...]:
    """One block's non-dominated splits as packed states.

    The honors block is the seven-wide one; only the nine-wide suit blocks
    may form runs.
    """
    return tuple({_pack(*option) for option in _block_options(block, is_honor=len(block) < _SUIT_RANKS)})


def _combined(states: set[int] | tuple[int, ...], options: set[int] | tuple[int, ...]) -> set[int]:
    """Every conflict-free pairwise sum of two packed-state collections."""
    add = _ADD
    return {added for state in states for option in options if (added := add[state][option]) >= 0}


def _packed_blocks(counts: list[int]) -> list[tuple[int, ...]]:
    """Each block's packed options for the count vector."""
    return [_packed_options(tuple(counts[start:stop])) for start, stop in _BLOCKS]


def _standard(counts: list[int], num_melds: int) -> int:
    """Shanten toward the standard four-groups-and-a-pair shape."""
    blocks = _packed_blocks(counts)
    states: set[int] | tuple[int, ...] = (0,)
    for options in blocks[:-1]:
        states = _combined(states, options)
    summed = _SUMMED_SCORES[num_melds]
    return min(summed[state][option] for state in states for option in blocks[-1])


def _chiitoi(counts: list[int]) -> int:
    """Shanten toward seven distinct pairs."""
    pairs = kinds = 0
    for count in counts:
        if count:
            kinds += 1
            if count >= _PAIR_SIZE:
                pairs += 1
    missing_kinds = _CHIITOI_DISTINCT - kinds
    return _CHIITOI_TARGET_PAIRS - pairs + (max(0, missing_kinds))


def _kokushi(counts: list[int]) -> int:
    """Shanten toward thirteen orphans."""
    present = has_pair = 0
    for kind in YAOCHUU_KINDS:
        count = counts[kind]
        if count:
            present += 1
            if count >= _PAIR_SIZE:
                has_pair = 1
    return _KOKUSHI_KINDS - present - has_pair


#: Each kind's block index; the honors (27..33) share the last block.
_BLOCK_INDEX = tuple(min(kind // _SUIT_RANKS, len(_BLOCKS) - 1) for kind in range(34))

#: Whether each kind is a terminal or honor, indexed by kind.
_IS_YAOCHUU = tuple(kind in YAOCHUU_KINDS for kind in range(34))


def _leave_one_out(blocks: list[tuple[int, ...]], summed: tuple[tuple[int, ...], ...]) -> list[tuple[int, ...]]:
    """For each block, the other blocks' combination folded to a score row.

    Row ``b`` maps every packed state to the best score it reaches against
    the combined states of all blocks but ``b``, so probing a replacement for
    block ``b`` is one lookup per option.
    """
    heads = [{0}]
    for options in blocks[:-1]:
        heads.append(_combined(heads[-1], options))
    tail: set[int] = {0}
    rows: list[tuple[int, ...]] = [()] * len(blocks)
    for index in range(len(blocks) - 1, -1, -1):
        states = _combined(heads[index], tail)
        rows[index] = tuple(map(min, zip(*(summed[state] for state in states), strict=True)))
        tail = _combined(tail, blocks[index])
    return rows


def draw_shantens(counts: list[int], num_melds: int, kinds: Iterable[TileKind]) -> dict[TileKind, int]:
    """The shanten after drawing one tile of each kind, sharing work across kinds.

    Equivalent to raising ``counts[kind]`` by one and calling ``shanten_counts``,
    kind by kind, at a fraction of the cost: the unchanged blocks' combination
    and the closed forms' tallies are computed once and reused by every probe.

    Args:
        counts: Concealed tile counts indexed by kind, before any draw.
        num_melds: The number of called melds the hand holds.
        kinds: The kinds to probe, each as a one-tile draw.

    Returns:
        Each probed kind mapped to the shanten of the hand after drawing it.

    Raises:
        ValueError: If a kind is probed and the concealed tile total after a
            draw is not legal for the meld count.
    """
    return _probed_shantens(counts, num_melds, tuple(kinds), 1)


def discard_shantens(counts: list[int], num_melds: int, kinds: Iterable[TileKind]) -> dict[TileKind, int]:
    """The shanten after discarding one tile of each held kind, sharing work.

    The one-discard counterpart of ``draw_shantens``: equivalent to lowering
    ``counts[kind]`` by one and calling ``shanten_counts``, kind by kind.

    Args:
        counts: Concealed tile counts indexed by kind, before any discard.
        num_melds: The number of called melds the hand holds.
        kinds: The kinds to probe, each as a one-tile discard; every probed
            kind must be held.

    Returns:
        Each probed kind mapped to the shanten of the hand after discarding it.

    Raises:
        ValueError: If a probed kind is not held, or a kind is probed and the
            concealed tile total after a discard is not legal for the meld count.
    """
    kinds = tuple(kinds)
    for kind in kinds:
        if not counts[kind]:
            raise ValueError(f"cannot discard kind {kind}: the hand holds none")
    return _probed_shantens(counts, num_melds, kinds, -1)


def _probed_shantens(counts: list[int], num_melds: int, kinds: tuple[TileKind, ...], delta: int) -> dict[TileKind, int]:
    """The shanten after changing each probed kind's count by one tile."""
    if not kinds:
        return {}
    total = sum(counts)
    if total + delta not in _legal_sizes(num_melds):
        raise ValueError(f"a hand with {num_melds} melds cannot have {total + delta} concealed tiles")
    rows = _leave_one_out(_packed_blocks(counts), _SUMMED_SCORES[num_melds])
    closed_forms = num_melds == 0 and total + delta in _FULL_CONCEALED
    if closed_forms:
        pairs = distinct = 0
        for count in counts:
            if count:
                distinct += 1
                if count >= _PAIR_SIZE:
                    pairs += 1
        present = yao_pairs = 0
        for yao in YAOCHUU_KINDS:
            if counts[yao]:
                present += 1
                if counts[yao] >= _PAIR_SIZE:
                    yao_pairs += 1
    result: dict[TileKind, int] = {}
    for kind in kinds:
        block_index = _BLOCK_INDEX[kind]
        start, stop = _BLOCKS[block_index]
        counts[kind] += delta
        options = _packed_options(tuple(counts[start:stop]))
        counts[kind] -= delta
        row = rows[block_index]
        best = min(row[option] for option in options)
        if closed_forms:
            held = counts[kind]
            after = held + delta
            probed_pairs = pairs + (after >= _PAIR_SIZE) - (held >= _PAIR_SIZE)
            missing_kinds = _CHIITOI_DISTINCT - distinct - (after > 0) + (held > 0)
            chiitoi = _CHIITOI_TARGET_PAIRS - probed_pairs + max(0, missing_kinds)
            best = min(best, chiitoi)
            if _IS_YAOCHUU[kind]:
                probed_present = present + (after > 0) - (held > 0)
                probed_yao_pairs = yao_pairs + (after >= _PAIR_SIZE) - (held >= _PAIR_SIZE)
                kokushi = _KOKUSHI_KINDS - probed_present - (probed_yao_pairs > 0)
            else:
                kokushi = _KOKUSHI_KINDS - present - (yao_pairs > 0)
            best = min(best, kokushi)
        result[kind] = best
    return result


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
