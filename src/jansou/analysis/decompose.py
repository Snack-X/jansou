"""Hand decomposition: every valid reading of a complete hand, with wait shapes.

A complete hand takes one of three finished shapes -- the standard four groups
and a pair, seven pairs, or thirteen orphans. Decomposition returns every valid
reading across all three, records how the winning tile fits (its wait shape),
and doubles as the completeness test: a hand is complete exactly when it has at
least one valid decomposition.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto, unique
from typing import TYPE_CHECKING

from jansou.core.hand import FULL_HAND_SIZE, MAX_MELDS, MeldType
from jansou.core.tiles import NUM_KINDS, YAOCHUU_KINDS, Tile, TileKind, counts_by_kind

if TYPE_CHECKING:
    from collections.abc import Iterable

    from jansou.core.hand import Meld

_PAIR_SIZE = 2
_SET_SIZE = 3
_COMPLETE_SIZE = FULL_HAND_SIZE + 1  # tiles in a complete concealed hand with no melds
_CHIITOI_PAIRS = 7
_SUITED_KINDS = 27
_LAST_RUN_START = 6  # a run may start at ranks 1-7, i.e. index i with i % 9 <= 6


@unique
class Shape(Enum):
    """A finished-hand shape."""

    STANDARD = auto()
    CHIITOI = auto()
    KOKUSHI = auto()


@unique
class GroupType(Enum):
    """The kind of a single group within a decomposition."""

    RUN = auto()
    TRIPLET = auto()
    QUAD = auto()
    PAIR = auto()


@unique
class WaitShape(Enum):
    """How the winning tile completes its group."""

    RYANMEN = auto()  # two-sided run partial
    KANCHAN = auto()  # closed (middle) run partial
    PENCHAN = auto()  # edge run partial
    SHANPON = auto()  # dual pair
    TANKI = auto()  # pair wait
    CHIITOI = auto()  # the seventh pair of seven pairs
    KOKUSHI = auto()  # the missing kind of a single-wait thirteen orphans
    KOKUSHI_THIRTEEN = auto()  # any of the thirteen, a thirteen orphans thirteen-wait


@dataclass(frozen=True)
class Group:
    """One group of a decomposition: a run, triplet, quad, or pair.

    Attributes:
        type: The kind of group -- run, triplet, quad, or pair.
        kinds: The group's tile kinds in ascending order.
        concealed: ``True`` for a group formed from concealed tiles or a closed
            kan, ``False`` for a group exposed by a call.
    """

    type: GroupType
    kinds: tuple[TileKind, ...]
    concealed: bool

    @property
    def kind(self) -> TileKind:
        """The group's defining kind: the low tile of a run, else the repeated tile."""
        return self.kinds[0]


@dataclass(frozen=True)
class Decomposition:
    """One valid reading of a complete hand.

    For the standard shape, ``groups`` holds the four sets (melds and concealed
    groups together) and ``pair`` the head. For seven pairs, ``groups`` holds the
    seven pairs and ``pair`` is ``None``. For thirteen orphans, ``groups`` is empty.

    Attributes:
        shape: The finished-hand shape this reading takes.
        groups: The sets of the reading, excluding the head pair.
        pair: The head pair, or ``None`` for seven pairs and thirteen orphans.
        wait: How the winning tile completes its group.
        winning_tile: The tile that completed the hand.
        winning_group: The block the winning tile completed, or ``None`` for
            thirteen orphans.
    """

    shape: Shape
    groups: tuple[Group, ...]
    pair: Group | None
    wait: WaitShape
    winning_tile: Tile
    winning_group: Group | None

    @property
    def all_groups(self) -> tuple[Group, ...]:
        """Every group including the head pair, for whole-hand scans."""
        return (*self.groups, self.pair) if self.pair is not None else self.groups


def _meld_group(meld: Meld) -> Group:
    """The fixed group a called meld contributes."""
    kinds = tuple(sorted(tile.kind for tile in meld.tiles))
    group_type = {
        MeldType.CHII: GroupType.RUN,
        MeldType.PON: GroupType.TRIPLET,
        MeldType.DAIMINKAN: GroupType.QUAD,
        MeldType.ANKAN: GroupType.QUAD,
        MeldType.SHOUMINKAN: GroupType.QUAD,
    }[meld.type]
    return Group(group_type, kinds, concealed=meld.type is MeldType.ANKAN)


# --- Completeness tests -----------------------------------------------------


def _standard_complete(counts: list[int], need_sets: int) -> bool:
    """Whether the counts form exactly `need_sets` sets plus one pair.

    The caller guarantees the tile count matches; the recursion consumes every
    tile, so a mismatched count simply fails to complete.
    """
    return _consume(counts, need_sets, pair_used=False)


def _consume(counts: list[int], sets_left: int, *, pair_used: bool) -> bool:
    """Recursively peel a pair and sets from the lowest occupied kind."""
    i = next((k for k in range(NUM_KINDS) if counts[k] > 0), None)
    if i is None:
        return sets_left == 0 and pair_used
    if not pair_used and counts[i] >= _PAIR_SIZE:
        counts[i] -= _PAIR_SIZE
        if _consume(counts, sets_left, pair_used=True):
            counts[i] += _PAIR_SIZE
            return True
        counts[i] += _PAIR_SIZE
    if sets_left > 0:
        if counts[i] >= _SET_SIZE:
            counts[i] -= _SET_SIZE
            if _consume(counts, sets_left - 1, pair_used=pair_used):
                counts[i] += _SET_SIZE
                return True
            counts[i] += _SET_SIZE
        if _can_run(counts, i):
            _take_run(counts, i, -1)
            if _consume(counts, sets_left - 1, pair_used=pair_used):
                _take_run(counts, i, +1)
                return True
            _take_run(counts, i, +1)
    return False


def _can_run(counts: list[int], i: int) -> bool:
    """Whether a run can start at kind index i given the counts."""
    return i < _SUITED_KINDS and i % 9 <= _LAST_RUN_START and counts[i + 1] > 0 and counts[i + 2] > 0


def _take_run(counts: list[int], i: int, sign: int) -> None:
    """Add `sign` to each of the three ranks of the run starting at i."""
    counts[i] += sign
    counts[i + 1] += sign
    counts[i + 2] += sign


def _chiitoi_complete(counts: list[int]) -> bool:
    """Whether the counts are seven distinct pairs."""
    return sum(1 for c in counts if c == _PAIR_SIZE) == _CHIITOI_PAIRS and all(c in (0, _PAIR_SIZE) for c in counts)


def _kokushi_complete(counts: list[int]) -> bool:
    """Whether the counts are one of each terminal-or-honor plus a duplicate."""
    if any(counts[k] for k in range(NUM_KINDS) if TileKind(k) not in YAOCHUU_KINDS):
        return False
    present = [counts[k] for k in YAOCHUU_KINDS]
    return all(present) and sum(present) == len(YAOCHUU_KINDS) + 1


def is_complete(counts: list[int], num_melds: int, *, allow_special: bool | None = None) -> bool:
    """Whether the concealed counts complete the hand given the meld count.

    Seven pairs and thirteen orphans apply only to a full concealed hand; by
    default they are considered exactly when there are no melds. Pass
    ``allow_special`` to force the choice.

    Args:
        counts: Concealed tile counts indexed by kind, including the winning tile.
        num_melds: The number of called melds the hand holds.
        allow_special: Whether to consider the seven-pairs and thirteen-orphans
            shapes. Defaults to ``None``, which enables them exactly when there
            are no melds.

    Returns:
        ``True`` if the counts complete the hand under any applicable shape.
    """
    total = sum(counts)
    special = (num_melds == 0) if allow_special is None else allow_special
    if total == (MAX_MELDS - num_melds) * _SET_SIZE + _PAIR_SIZE and _standard_complete(counts, MAX_MELDS - num_melds):
        return True
    return bool(special and total == _COMPLETE_SIZE and (_chiitoi_complete(counts) or _kokushi_complete(counts)))


# --- Full enumeration -------------------------------------------------------


def _all_set_partitions(counts: list[int], need_sets: int) -> list[tuple[tuple[str, int], ...]]:
    """Every way to partition the counts fully into `need_sets` sets.

    Each set is encoded as ('run', low_index) or ('trip', index).
    """
    i = next((k for k in range(NUM_KINDS) if counts[k] > 0), None)
    if i is None:
        return [()] if need_sets == 0 else []
    if need_sets == 0:
        return []
    out: list[tuple[tuple[str, int], ...]] = []
    if counts[i] >= _SET_SIZE:
        counts[i] -= _SET_SIZE
        out.extend((("trip", i), *rest) for rest in _all_set_partitions(counts, need_sets - 1))
        counts[i] += _SET_SIZE
    if _can_run(counts, i):
        _take_run(counts, i, -1)
        out.extend((("run", i), *rest) for rest in _all_set_partitions(counts, need_sets - 1))
        _take_run(counts, i, +1)
    return out


def _run_wait(run_kinds: tuple[TileKind, ...], won: TileKind) -> WaitShape:
    """The wait shape when the winning tile completes a run."""
    low, mid, high = (k.rank for k in run_kinds)
    if won.rank == mid:
        return WaitShape.KANCHAN
    partial = {r for r in (low, mid, high) if r != won.rank}
    if partial in ({1, 2}, {8, 9}):
        return WaitShape.PENCHAN
    return WaitShape.RYANMEN


def _standard_decompositions(counts: list[int], melds: tuple[Meld, ...], winning: Tile) -> list[Decomposition]:
    """Every standard-shape reading of the concealed counts around the melds."""
    need_sets = MAX_MELDS - len(melds)
    if sum(counts) != need_sets * _SET_SIZE + _PAIR_SIZE:
        return []
    meld_groups = tuple(_meld_group(meld) for meld in melds)
    seen: set[tuple[object, ...]] = set()
    out: list[Decomposition] = []

    def emit(concealed_sets: tuple[Group, ...], pair: Group) -> None:
        # One reading per concealed block the winning tile can complete.
        groups = meld_groups + concealed_sets
        for block in (*concealed_sets, pair):
            if winning.kind not in block.kinds:
                continue
            wait = _block_wait(block, winning.kind)
            key = (tuple(sorted(groups, key=_group_key)), pair, wait)
            if key in seen:
                continue
            seen.add(key)
            out.append(Decomposition(Shape.STANDARD, groups, pair, wait, winning, block))

    for pair_kind in range(NUM_KINDS):
        if counts[pair_kind] < _PAIR_SIZE:
            continue
        counts[pair_kind] -= _PAIR_SIZE
        pair = Group(GroupType.PAIR, (TileKind(pair_kind), TileKind(pair_kind)), concealed=True)
        for partition in _all_set_partitions(counts, need_sets):
            emit(tuple(_partition_group(entry) for entry in partition), pair)
        counts[pair_kind] += _PAIR_SIZE
    return out


def _partition_group(entry: tuple[str, int]) -> Group:
    """The concealed Group for an encoded set."""
    label, i = entry
    if label == "trip":
        kind = TileKind(i)
        return Group(GroupType.TRIPLET, (kind, kind, kind), concealed=True)
    return Group(GroupType.RUN, (TileKind(i), TileKind(i + 1), TileKind(i + 2)), concealed=True)


def _block_wait(block: Group, won: TileKind) -> WaitShape:
    """The wait shape when the winning tile completes the given concealed block."""
    if block.type is GroupType.PAIR:
        return WaitShape.TANKI
    if block.type is GroupType.TRIPLET:
        return WaitShape.SHANPON
    return _run_wait(block.kinds, won)


def _group_key(group: Group) -> tuple[int, tuple[int, ...], bool]:
    """A sortable, hashable key for a group."""
    return (group.type.value, tuple(group.kinds), group.concealed)


def _special_decompositions(counts: list[int], winning: Tile) -> list[Decomposition]:
    """The seven-pairs and thirteen-orphans readings, if either applies."""
    out: list[Decomposition] = []
    if _chiitoi_complete(counts):
        pairs = tuple(
            Group(GroupType.PAIR, (TileKind(k), TileKind(k)), concealed=True) for k in range(NUM_KINDS) if counts[k]
        )
        won_pair = next(group for group in pairs if group.kind == winning.kind)
        out.append(Decomposition(Shape.CHIITOI, pairs, None, WaitShape.CHIITOI, winning, won_pair))
    if _kokushi_complete(counts):
        thirteen = counts[winning.kind] == _PAIR_SIZE
        wait = WaitShape.KOKUSHI_THIRTEEN if thirteen else WaitShape.KOKUSHI
        out.append(Decomposition(Shape.KOKUSHI, (), None, wait, winning, None))
    return out


def decompose(concealed: Iterable[Tile], melds: tuple[Meld, ...], winning_tile: Tile) -> list[Decomposition]:
    """Every valid reading of the complete hand across all three shapes.

    ``concealed`` includes the winning tile. An incomplete hand yields an empty
    list. Readings identical in their groups, pair, and wait shape are
    collapsed to one.

    Args:
        concealed: The concealed tiles, including the winning tile.
        melds: The called melds, each contributing one fixed group.
        winning_tile: The tile that completed the hand.

    Returns:
        Every distinct reading of the hand; empty if the hand is not complete.
    """
    concealed = tuple(concealed)
    counts = counts_by_kind(concealed)
    results = _standard_decompositions(counts, melds, winning_tile)
    if not melds and sum(counts) == _COMPLETE_SIZE:
        results.extend(_special_decompositions(counts, winning_tile))
    return results
