"""Shanten: how many tile exchanges a hand is from ready.

Reported on a fixed convention: -1 complete (agari), 0 ready (tenpai), and a
positive count otherwise. Three target shapes are measured -- the standard four
groups and a pair, seven pairs, and thirteen orphans -- and a hand's shanten is
the lowest of the ones that apply.

The standard shape is measured as a replacement number: the fewest tiles drawn,
each with a free discard, until the hand contains four groups and a pair, which
is shanten plus one. Replacement honors the four-copy limit -- a shape that
could only complete with a fifth copy of a kind is not counted, so a
four-of-a-kind tanki is one shanten, not ready. Each block's counts key a
lazily filled table of interned distance vectors, and blocks combine through a
cached min-plus merge, so a warm query is a handful of table lookups.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from jansou.core.hand import FULL_HAND_SIZE, MAX_MELDS
from jansou.core.tiles import TILES_PER_KIND, YAOCHUU_KINDS, counts_by_kind

if TYPE_CHECKING:
    from collections.abc import Iterable

    from jansou.core.hand import Hand
    from jansou.core.tiles import TileKind

_PAIR_SIZE = 2
_SET_SIZE = 3
_SUIT_RANKS = 9
_FULL_CONCEALED = (FULL_HAND_SIZE, FULL_HAND_SIZE + 1)
_UNREACHED = 99  # above any real distance; never survives a minimum
_CHIITOI_TARGET_PAIRS = 6  # six pairs plus a spare of a seventh kind is ready
_CHIITOI_DISTINCT = 7
_KOKUSHI_KINDS = 13

# The count vector split into blocks the standard combination folds over.
_BLOCKS = ((0, 9), (9, 18), (18, 27), (27, 34))


def _legal_sizes(num_melds: int) -> tuple[int, int]:
    """The resting and holding concealed-tile counts for a hand with this many melds."""
    rest = FULL_HAND_SIZE - _SET_SIZE * num_melds
    return rest, rest + 1


def _distance_vector(block: tuple[int, ...], *, is_honor: bool) -> tuple[int, ...]:
    """One block's distances: the fewest tiles added to contain each target.

    Entry ``pair * 5 + sets`` is the fewest tiles that must join the block so
    it contains ``sets`` complete groups plus, when ``pair`` is one, a pair,
    never using more than four tiles of a kind -- the constraint that keeps
    every counted shape completable with tiles that exist. A dynamic program
    walks the ranks carrying the runs in progress: entering an index,
    ``due_now`` runs need a tile here only and ``due_next`` runs need one here
    and one at the next rank.
    """
    costs = {(0, 0, 0, 0): 0}
    for i, held in enumerate(block):
        can_run = not is_honor and i <= len(block) - _SET_SIZE
        next_costs: dict[tuple[int, int, int, int], int] = {}
        for (sets, pair, due_now, due_next), cost in costs.items():
            carried = due_now + due_next
            for triplet in range(min(1, MAX_MELDS - sets, (TILES_PER_KIND - carried) // _SET_SIZE) + 1):
                used_fixed = carried + _SET_SIZE * triplet
                for head in range(min(1 - pair, (TILES_PER_KIND - used_fixed) // _PAIR_SIZE) + 1):
                    used_base = used_fixed + _PAIR_SIZE * head
                    max_runs = min(TILES_PER_KIND - used_base, MAX_MELDS - sets - triplet) if can_run else 0
                    for runs in range(max_runs + 1):
                        added = cost + max(0, used_base + runs - held)
                        key = (sets + triplet + runs, pair + head, due_next, runs)
                        if added < next_costs.get(key, _UNREACHED):
                            next_costs[key] = added
        costs = next_costs
    vector = [_UNREACHED] * 10
    for (sets, pair, _, _), cost in costs.items():
        index = pair * 5 + sets
        vector[index] = min(vector[index], cost)
    return tuple(vector)


def _merge(left: tuple[int, ...], right: tuple[int, ...]) -> tuple[int, ...]:
    """The min-plus merge of two distance vectors over shared targets."""
    out = [_UNREACHED] * 10
    for i, left_cost in enumerate(left):
        left_pair, left_sets = divmod(i, 5)
        for j, right_cost in enumerate(right):
            right_pair, right_sets = divmod(j, 5)
            if left_pair + right_pair > 1 or left_sets + right_sets > MAX_MELDS:
                continue
            index = (left_pair + right_pair) * 5 + left_sets + right_sets
            out[index] = min(out[index], left_cost + right_cost)
    return tuple(out)


# Distance vectors are interned: a class is an index into _VECTORS, and the
# lazily filled tables below store classes. Distinct blocks collapse onto a
# small pool of vectors, and merges of classes are computed once per pair, so
# repeat queries reduce to dictionary hits.
_VECTORS: list[tuple[int, ...]] = []
_VECTOR_CLASSES: dict[tuple[int, ...], int] = {}
_BLOCK_CLASSES: dict[tuple[int, ...], int] = {}
_MERGED_CLASSES: dict[tuple[int, int], int] = {}


def _intern(vector: tuple[int, ...]) -> int:
    """The class of a vector, registering it on first sight."""
    got = _VECTOR_CLASSES.get(vector)
    if got is None:
        got = _VECTOR_CLASSES[vector] = len(_VECTORS)
        _VECTORS.append(vector)
    return got


#: The merge identity: zero cost for an empty target, unreached otherwise.
_IDENTITY = _intern((0, *[_UNREACHED] * 9))


def _block_class(block: tuple[int, ...]) -> int:
    """The class of one block's distance vector, filled lazily.

    Suit and honor blocks share one table: their key widths differ, so the
    tuples never collide.
    """
    got = _BLOCK_CLASSES.get(block)
    if got is None:
        vector = _distance_vector(block, is_honor=len(block) < _SUIT_RANKS)
        got = _BLOCK_CLASSES[block] = _intern(vector)
    return got


def _merged_class(left: int, right: int) -> int:
    """The class of two classes' merge, computed once per pair."""
    key = (left, right)
    got = _MERGED_CLASSES.get(key)
    if got is None:
        got = _MERGED_CLASSES[key] = _intern(_merge(_VECTORS[left], _VECTORS[right]))
    return got


def _block_classes(counts: list[int]) -> list[int]:
    """Each block's vector class for the count vector."""
    return [_block_class(tuple(counts[start:stop])) for start, stop in _BLOCKS]


def _standard(counts: list[int], num_melds: int) -> int:
    """Shanten toward the standard four-groups-and-a-pair shape."""
    manzu, pinzu, souzu, honors = _block_classes(counts)
    merged = _merged_class(_merged_class(_merged_class(manzu, pinzu), souzu), honors)
    return _VECTORS[merged][5 + MAX_MELDS - num_melds] - 1


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


def _leave_one_out(classes: list[int]) -> list[int]:
    """For each block, the class of the other blocks' merge.

    Row ``b`` is the merged distance vector of all blocks but ``b``, so
    probing a replacement for block ``b`` is one cached merge away.
    """
    heads = [_IDENTITY]
    for cls in classes[:-1]:
        heads.append(_merged_class(heads[-1], cls))
    tail = _IDENTITY
    rows = [0] * len(classes)
    for index in range(len(classes) - 1, -1, -1):
        rows[index] = _merged_class(heads[index], tail)
        tail = _merged_class(classes[index], tail)
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
    rows = _leave_one_out(_block_classes(counts))
    target = 5 + MAX_MELDS - num_melds
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
        block = tuple(counts[start:stop])
        counts[kind] -= delta
        best = _VECTORS[_merged_class(rows[block_index], _block_class(block))][target] - 1
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
