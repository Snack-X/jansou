"""Shanten: how many tile exchanges a hand is from ready.

Reported on a fixed convention: -1 complete (agari), 0 ready (tenpai), and a
positive count otherwise. Three target shapes are measured -- the standard four
groups and a pair, seven pairs, and thirteen orphans -- and a hand's shanten is
the lowest of the ones that apply.

The standard shape is measured as a replacement number: the fewest tiles drawn,
each with a free discard, until the hand contains four groups and a pair, which
is shanten plus one. Replacement honors the four-copy limit -- a shape that
could only complete with a fifth copy of a kind is not counted, so a
four-of-a-kind tanki is one shanten, not ready.

A block's counts, read as a base-five number, index a shipped table of interned
distance vectors, and blocks combine through a cached min-plus merge -- so a
query is radix arithmetic and a few list reads, and a whole probe sweep is
memoised for the expectimax callers that ask about one hand from many
directions. The recursive search that priced the tables remains as the fallback
for a missing table file and for counts past the four-copy radix.
"""

from __future__ import annotations

import zlib
from contextlib import suppress
from importlib import resources
from itertools import islice
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
    kinds = tuple(kinds)
    if not kinds:
        return {}
    if max(counts) > TILES_PER_KIND:
        # Beyond the tables' radix; the recursive path prices any counts.
        return _probed_shantens(counts, num_melds, kinds, 1)
    probes = probe_shantens(counts, num_melds, 1)
    result = {}
    for kind in kinds:
        value = probes[kind]
        if value is None:
            # A fifth-copy draw: inert, priced only by the recursive path.
            value = _probed_shantens(counts, num_melds, (kind,), 1)[kind]
        result[kind] = value
    return result


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
    if not kinds:
        return {}
    if max(counts) > TILES_PER_KIND:
        # Beyond the tables' radix; the recursive path prices any counts.
        return _probed_shantens(counts, num_melds, kinds, -1)
    probes = probe_shantens(counts, num_melds, -1)
    return {kind: probes[kind] for kind in kinds}


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


# The tables below replace per-probe tuple building and dictionary hashing with
# radix arithmetic: a block's counts read as a base-five number index straight
# into a table of vector classes, so probing a kind is an index offset. The
# tables ship precomputed (see examples/generate_shanten_tables.py) and fall
# back to lazy filling through _distance_vector when the file is absent, so the
# semantics cannot differ between the two paths.
_RADIX = TILES_PER_KIND + 1
_HONOR_WIDTH = _BLOCKS[-1][1] - _BLOCKS[-1][0]
_UNFILLED = -1
_TABLE_RESOURCE = "shanten_blocks.bin"
_TABLE_MAGIC = b"JSBT"
_VECTOR_WIDTH = 10
_SENTINEL_BYTE = 255

_PROBE_MEMO_SIZE = 60_000
"""Whole probe sweeps the memo holds before evicting.

An expectimax caller asks for the same hand's sweep from every candidate that
shares it, so the hit rate is high (~75% measured) while entries stay small.
"""

_COUNT_MEMO_SIZE = 50_000
"""Plain shanten lookups the memo holds before evicting."""

_TABLES_READY = False
_SUIT_CLASSES: list[int] = []
_HONOR_CLASSES: list[int] = []
_PROBE_VALUES: dict[int, int] = {}
_PROBE_MEMO: dict[tuple[bytes, int, int], list[int | None]] = {}
_COUNT_MEMO: dict[tuple[bytes, int], int] = {}


def _trim(memo: dict, limit: int) -> None:
    """Drop an oldest batch once a memo passes its limit, amortising eviction."""
    for key in list(islice(iter(memo), limit // 8)):
        del memo[key]


def _table_bytes() -> bytes:
    """The packed table file's raw bytes."""
    return (resources.files("jansou.analysis") / _TABLE_RESOURCE).read_bytes()


def _parse_tables(payload: bytes) -> tuple[list[int], list[int]]:
    """Both tables from a decompressed payload, classes re-interned.

    The file stores its own class numbering, so every stored vector is interned
    on load and the tables translated -- byte by byte through a lookup -- into
    this process's numbering.

    Raises:
        ValueError: If the payload is not a well-formed table file.
    """
    if payload[: len(_TABLE_MAGIC)] != _TABLE_MAGIC:
        raise ValueError("shanten table file lacks its magic prefix")
    stored = payload[len(_TABLE_MAGIC)]
    offset = len(_TABLE_MAGIC) + 1
    suit_slots = _RADIX**_SUIT_RANKS
    honor_slots = _RADIX**_HONOR_WIDTH
    if len(payload) != offset + stored * _VECTOR_WIDTH + suit_slots + honor_slots:
        raise ValueError("shanten table file has the wrong length")
    translate = [_UNFILLED] * (_SENTINEL_BYTE + 1)
    for index in range(stored):
        vector = tuple(payload[offset : offset + _VECTOR_WIDTH])
        translate[index] = _intern(vector)
        offset += _VECTOR_WIDTH
    suit = [translate[byte] for byte in payload[offset : offset + suit_slots]]
    honor = [translate[byte] for byte in payload[offset + suit_slots :]]
    return suit, honor


def _load_tables() -> None:
    """Fill the block tables from the shipped file, or leave them lazy.

    A missing or malformed file is not an error: the tables start unfilled and
    every block is priced on first sight instead, slower but identical.
    """
    global _TABLES_READY, _SUIT_CLASSES, _HONOR_CLASSES  # noqa: PLW0603
    suit = [_UNFILLED] * _RADIX**_SUIT_RANKS
    honor = [_UNFILLED] * _RADIX**_HONOR_WIDTH
    with suppress(OSError, ValueError, zlib.error):
        suit, honor = _parse_tables(zlib.decompress(_table_bytes()))
    _SUIT_CLASSES = suit
    _HONOR_CLASSES = honor
    _TABLES_READY = True


def _fill_block(table: list[int], index: int, width: int) -> int:
    """Price one table slot on first sight, from its radix digits."""
    left = index
    block = []
    for _ in range(width):
        block.append(left % _RADIX)
        left //= _RADIX
    cls = _intern(_distance_vector(tuple(block), is_honor=width == _HONOR_WIDTH))
    table[index] = cls
    return cls


def _radix_indices(c: list[int]) -> tuple[int, int, int, int]:
    """Each block's table index: its counts read as a base-five number."""
    i0 = (
        c[0]
        + 5 * c[1]
        + 25 * c[2]
        + 125 * c[3]
        + 625 * c[4]
        + 3125 * c[5]
        + 15625 * c[6]
        + 78125 * c[7]
        + 390625 * c[8]
    )
    i1 = (
        c[9]
        + 5 * c[10]
        + 25 * c[11]
        + 125 * c[12]
        + 625 * c[13]
        + 3125 * c[14]
        + 15625 * c[15]
        + 78125 * c[16]
        + 390625 * c[17]
    )
    i2 = (
        c[18]
        + 5 * c[19]
        + 25 * c[20]
        + 125 * c[21]
        + 625 * c[22]
        + 3125 * c[23]
        + 15625 * c[24]
        + 78125 * c[25]
        + 390625 * c[26]
    )
    i3 = c[27] + 5 * c[28] + 25 * c[29] + 125 * c[30] + 625 * c[31] + 3125 * c[32] + 15625 * c[33]
    return i0, i1, i2, i3


def _class_at(table: list[int], index: int, width: int) -> int:
    """The vector class in a table slot, filling it on first sight."""
    cls = table[index]
    if cls < 0:
        cls = _fill_block(table, index, width)
    return cls


def _probe_value(row: int, cls: int, target: int, packed: int) -> int:
    """One probe's standard shanten, cached under its packed key."""
    value = _VECTORS[_merged_class(row, cls)][target] - 1
    _PROBE_VALUES[packed] = value
    return value


def _standard_probes(c: list[int], num_melds: int, delta: int) -> list[int | None]:
    """Every kind's standard shanten after a one-tile probe, by table lookups."""
    suit = _SUIT_CLASSES
    honor = _HONOR_CLASSES
    i0, i1, i2, i3 = _radix_indices(c)
    b0 = _class_at(suit, i0, _SUIT_RANKS)
    b1 = _class_at(suit, i1, _SUIT_RANKS)
    b2 = _class_at(suit, i2, _SUIT_RANKS)
    b3 = _class_at(honor, i3, _HONOR_WIDTH)
    merged = _merged_class
    tail = merged(b2, b3)
    head = merged(b0, b1)
    target = 5 + MAX_MELDS - num_melds
    result: list[int | None] = [None] * len(c)
    values = _PROBE_VALUES
    rows = (
        (0, i0, merged(b1, tail), suit, _SUIT_RANKS),
        (9, i1, merged(b0, tail), suit, _SUIT_RANKS),
        (18, i2, merged(head, b3), suit, _SUIT_RANKS),
        (27, i3, merged(head, b2), honor, _HONOR_WIDTH),
    )
    for start, index, row, table, width in rows:
        packed_row = (row << 18) | target
        step = delta
        for offset in range(width):
            kind = start + offset
            after = c[kind] + delta
            if 0 <= after <= TILES_PER_KIND:
                cls = table[index + step]
                if cls < 0:
                    cls = _fill_block(table, index + step, width)
                packed = packed_row | (cls << 4)
                value = values.get(packed)
                if value is None:
                    value = _probe_value(row, cls, target, packed)
                result[kind] = value
            step += step << 2  # step *= radix, keeping delta's sign
    return result


def _closed_probes(c: list[int], delta: int, result: list[int | None]) -> None:
    """Fold seven pairs and thirteen orphans into a meld-free hand's probes."""
    distinct = len(c) - c.count(0)
    pairs = distinct - c.count(1)
    present = yao_pairs = 0
    for kind in YAOCHUU_KINDS:
        count = c[kind]
        if count:
            present += 1
            if count >= _PAIR_SIZE:
                yao_pairs += 1
    kokushi_still = _KOKUSHI_KINDS - present - (yao_pairs > 0)
    for kind, standard in enumerate(result):
        if standard is None:
            continue
        held = c[kind]
        after = held + delta
        probed_pairs = pairs + (after >= _PAIR_SIZE) - (held >= _PAIR_SIZE)
        missing = _CHIITOI_DISTINCT - distinct - (after > 0) + (held > 0)
        chiitoi = _CHIITOI_TARGET_PAIRS - probed_pairs + max(0, missing)
        if _IS_YAOCHUU[kind]:
            probed_present = present + (after > 0) - (held > 0)
            probed_pairs = yao_pairs + (after >= _PAIR_SIZE) - (held >= _PAIR_SIZE)
            kokushi = _KOKUSHI_KINDS - probed_present - (probed_pairs > 0)
        else:
            kokushi = kokushi_still
        result[kind] = min(standard, chiitoi, kokushi)


def probe_shantens(counts: list[int], num_melds: int, delta: int) -> list[int | None]:
    """The shanten after changing each kind's count by one tile, for every kind.

    The batch core behind ``draw_shantens`` and ``discard_shantens``: one call
    prices all kinds at once and is memoised whole, so an expectimax that asks
    about the same hand from many directions pays for the sweep once. The
    returned list is shared with the memo and must not be mutated.

    Args:
        counts: Concealed tile counts indexed by kind, each at most four.
        num_melds: The number of called melds the hand holds.
        delta: The probe, ``1`` for a draw or ``-1`` for a discard.

    Returns:
        Each kind's shanten after the probe, indexed by kind; ``None`` where the
        probe would leave that kind's count outside zero to four.

    Raises:
        ValueError: If a count exceeds four, or the concealed tile total after
            a probe is not legal for the meld count.
    """
    key = (bytes(counts), num_melds, delta)
    found = _PROBE_MEMO.get(key)
    if found is not None:
        return found
    total = sum(counts)
    if total + delta not in _legal_sizes(num_melds):
        raise ValueError(f"a hand with {num_melds} melds cannot have {total + delta} concealed tiles")
    if max(counts) > TILES_PER_KIND:
        raise ValueError("probe counts must not exceed four copies of a kind")
    if not _TABLES_READY:
        _load_tables()
    result = _standard_probes(counts, num_melds, delta)
    if num_melds == 0 and total + delta in _FULL_CONCEALED:
        _closed_probes(counts, delta, result)
    _PROBE_MEMO[key] = result
    if len(_PROBE_MEMO) > _PROBE_MEMO_SIZE:
        _trim(_PROBE_MEMO, _PROBE_MEMO_SIZE)
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
    key = (bytes(counts), num_melds)
    found = _COUNT_MEMO.get(key)
    if found is not None:
        return found
    total = sum(counts)
    if total not in _legal_sizes(num_melds):
        raise ValueError(f"a hand with {num_melds} melds cannot have {total} concealed tiles")
    if max(counts) > TILES_PER_KIND:
        # Beyond the tables' radix; the recursive path prices any counts.
        best = _standard(counts, num_melds)
    else:
        if not _TABLES_READY:
            _load_tables()
        i0, i1, i2, i3 = _radix_indices(counts)
        b0 = _class_at(_SUIT_CLASSES, i0, _SUIT_RANKS)
        b1 = _class_at(_SUIT_CLASSES, i1, _SUIT_RANKS)
        b2 = _class_at(_SUIT_CLASSES, i2, _SUIT_RANKS)
        b3 = _class_at(_HONOR_CLASSES, i3, _HONOR_WIDTH)
        merged = _merged_class(_merged_class(_merged_class(b0, b1), b2), b3)
        best = _VECTORS[merged][5 + MAX_MELDS - num_melds] - 1
    if num_melds == 0 and total in _FULL_CONCEALED:
        best = min(best, _chiitoi(counts), _kokushi(counts))
    _COUNT_MEMO[key] = best
    if len(_COUNT_MEMO) > _COUNT_MEMO_SIZE:
        _trim(_COUNT_MEMO, _COUNT_MEMO_SIZE)
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
