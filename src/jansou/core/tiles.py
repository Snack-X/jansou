"""Tiles: kinds, red-five marking, ordering, and classification."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum, unique
from functools import total_ordering
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

#: Copies of each kind in the full set.
TILES_PER_KIND = 4

#: The number of distinct tile kinds.
NUM_KINDS = 34

_RANKS_PER_SUIT = 9
_WIND_COUNT = 4
_DRAGON_COUNT = 3


@unique
class Suit(Enum):
    """A number suit."""

    MANZU = "m"
    PINZU = "p"
    SOUZU = "s"


@unique
class TileKind(IntEnum):
    """One of the 34 tile kinds, numbered in canonical order."""

    M1 = 0
    M2 = 1
    M3 = 2
    M4 = 3
    M5 = 4
    M6 = 5
    M7 = 6
    M8 = 7
    M9 = 8
    P1 = 9
    P2 = 10
    P3 = 11
    P4 = 12
    P5 = 13
    P6 = 14
    P7 = 15
    P8 = 16
    P9 = 17
    S1 = 18
    S2 = 19
    S3 = 20
    S4 = 21
    S5 = 22
    S6 = 23
    S7 = 24
    S8 = 25
    S9 = 26
    EAST = 27
    SOUTH = 28
    WEST = 29
    NORTH = 30
    HAKU = 31
    HATSU = 32
    CHUN = 33

    @property
    def is_suited(self) -> bool:
        """Whether this is a number tile."""
        return self < TileKind.EAST

    @property
    def is_honor(self) -> bool:
        """Whether this is a wind or dragon."""
        return self >= TileKind.EAST

    @property
    def suit(self) -> Suit | None:
        """The number suit, or ``None`` for an honor."""
        if self.is_honor:
            return None
        return (Suit.MANZU, Suit.PINZU, Suit.SOUZU)[self // _RANKS_PER_SUIT]

    @property
    def rank(self) -> int | None:
        """The rank 1-9, or ``None`` for an honor."""
        if self.is_honor:
            return None
        return self % _RANKS_PER_SUIT + 1

    @property
    def is_wind(self) -> bool:
        """Whether this is one of the four winds."""
        return TileKind.EAST <= self <= TileKind.NORTH

    @property
    def is_dragon(self) -> bool:
        """Whether this is one of the three dragons."""
        return self >= TileKind.HAKU

    @property
    def is_terminal(self) -> bool:
        """Whether this is a one or a nine of a suit (routouhai)."""
        return self.is_suited and self.rank in (1, _RANKS_PER_SUIT)

    @property
    def is_simple(self) -> bool:
        """Whether this is a two through eight of a suit (tanyaohai)."""
        return self.is_suited and not self.is_terminal

    @property
    def is_yaochuu(self) -> bool:
        """Whether this is a terminal or an honor."""
        return self.is_honor or self.is_terminal

    def is_adjacent(self, other: TileKind) -> bool:
        """Whether the two kinds are neighboring ranks of the same suit.

        Honors have no neighbors, and the nine-one boundary is not adjacent.

        Args:
            other: The kind to compare against.

        Returns:
            ``True`` if both are suited, share a suit, and differ by one rank.
        """
        return self.is_suited and other.is_suited and self.suit is other.suit and abs(self - other) == 1

    def successor(self, *, sanma: bool = False) -> TileKind:
        """The wrapping next kind, used only for dora designation.

        Under sanma the manzu cycle contracts to its two surviving ranks:
        1m and 9m succeed each other.

        Args:
            sanma: Whether three-player rules apply, collapsing the manzu cycle
                so that ``M1`` and ``M9`` succeed each other.

        Returns:
            The next kind cyclically, within the winds, the dragons, or a suit.
        """
        if self.is_wind:
            return TileKind(TileKind.EAST + (self - TileKind.EAST + 1) % _WIND_COUNT)
        if self.is_dragon:
            return TileKind(TileKind.HAKU + (self - TileKind.HAKU + 1) % _DRAGON_COUNT)
        if sanma and self is TileKind.M1:
            return TileKind.M9
        if sanma and self is TileKind.M9:
            return TileKind.M1
        suit_base = self // _RANKS_PER_SUIT * _RANKS_PER_SUIT
        return TileKind(suit_base + (self - suit_base + 1) % _RANKS_PER_SUIT)


@unique
class Wind(IntEnum):
    """A wind, as a seat wind or round wind."""

    EAST = 0
    SOUTH = 1
    WEST = 2
    NORTH = 3

    @property
    def tile_kind(self) -> TileKind:
        """The honor tile kind carrying this wind."""
        return TileKind(TileKind.EAST + self)


#: The three kinds whose fives can be red.
FIVE_KINDS = frozenset({TileKind.M5, TileKind.P5, TileKind.S5})

#: The kinds sanma removes from the set: manzu two through eight.
SANMA_REMOVED_KINDS = frozenset(TileKind(k) for k in range(TileKind.M2, TileKind.M9))

#: The thirteen terminal-and-honor kinds.
YAOCHUU_KINDS = frozenset(k for k in TileKind if k.is_yaochuu)


def suited_kind(suit: Suit, rank: int) -> TileKind:
    """The kind of the given suit and rank.

    Args:
        suit: The number suit.
        rank: The rank from 1 to 9.

    Returns:
        The suited ``TileKind`` for that suit and rank.

    Raises:
        ValueError: If ``rank`` is outside 1-9.
    """
    if not 1 <= rank <= _RANKS_PER_SUIT:
        raise ValueError(f"rank must be 1-9, got {rank}")
    base = {Suit.MANZU: TileKind.M1, Suit.PINZU: TileKind.P1, Suit.SOUZU: TileKind.S1}[suit]
    return TileKind(base + rank - 1)


@total_ordering
@dataclass(frozen=True)
class Tile:
    """A single tile: a kind, refined by red-five marking.

    Dataclass equality is *exact* equality (kind and red marking agree);
    *shape* equality is comparison of ``kind`` alone.

    Attributes:
        kind: The tile kind.
        red: Whether this is the red-five (aka dora) variant of its kind.
    """

    kind: TileKind
    red: bool = False

    def __post_init__(self) -> None:
        """Reject a red marking on any kind that has no red five.

        Raises:
            ValueError: If ``red`` is set on a kind that is not a five.
        """
        if self.red and self.kind not in FIVE_KINDS:
            raise ValueError(f"only fives can be red, got {self.kind.name}")

    @classmethod
    def suited(cls, suit: Suit, rank: int, *, red: bool = False) -> Tile:
        """A suited tile of the given suit and rank.

        Args:
            suit: The number suit.
            rank: The rank from 1 to 9.
            red: Whether to make the red-five variant, valid only for a five.

        Returns:
            The requested suited ``Tile``.
        """
        return cls(suited_kind(suit, rank), red=red)

    def __lt__(self, other: Tile) -> bool:
        """Return whether this tile sorts before ``other`` in canonical order."""
        if not isinstance(other, Tile):
            return NotImplemented
        return self.sort_key < other.sort_key

    @property
    def sort_key(self) -> tuple[int, int]:
        """Canonical ordering key: a red five sorts immediately before ordinary fives."""
        return (self.kind, 0 if self.red else 1)

    # Classification pass-throughs.

    @property
    def is_suited(self) -> bool:
        """Whether this is a number tile."""
        return self.kind.is_suited

    @property
    def is_honor(self) -> bool:
        """Whether this is a wind or dragon."""
        return self.kind.is_honor

    @property
    def suit(self) -> Suit | None:
        """The number suit, or ``None`` for an honor."""
        return self.kind.suit

    @property
    def rank(self) -> int | None:
        """The rank 1-9, or ``None`` for an honor."""
        return self.kind.rank

    @property
    def is_wind(self) -> bool:
        """Whether this is a wind."""
        return self.kind.is_wind

    @property
    def is_dragon(self) -> bool:
        """Whether this is a dragon."""
        return self.kind.is_dragon

    @property
    def is_terminal(self) -> bool:
        """Whether this is a one or a nine of a suit."""
        return self.kind.is_terminal

    @property
    def is_simple(self) -> bool:
        """Whether this is a two through eight of a suit."""
        return self.kind.is_simple

    @property
    def is_yaochuu(self) -> bool:
        """Whether this is a terminal or an honor."""
        return self.kind.is_yaochuu


def kinds_in_play(player_count: int = 4) -> tuple[TileKind, ...]:
    """The tile kinds present for the given player count: 34 for yonma, 27 for sanma.

    Args:
        player_count: The number of players, 3 or 4.

    Returns:
        The kinds in play, in canonical order; sanma omits manzu two through eight.

    Raises:
        ValueError: If ``player_count`` is neither 3 nor 4.
    """
    if player_count == 4:
        return tuple(TileKind)
    if player_count == 3:
        return tuple(k for k in TileKind if k not in SANMA_REMOVED_KINDS)
    raise ValueError(f"player count must be 3 or 4, got {player_count}")


def full_tile_set(player_count: int = 4, *, aka_dora: bool = True) -> list[Tile]:
    """The full set in play: 136 tiles for yonma, 108 for sanma.

    With red fives on, one five per applicable suit is replaced by its red
    variant; the total count is unchanged.

    Args:
        player_count: The number of players, 3 or 4.
        aka_dora: Whether one five of each applicable suit is a red five.

    Returns:
        Every physical tile of the set, kinds in canonical order.

    Raises:
        ValueError: If ``player_count`` is neither 3 nor 4.
    """
    tiles: list[Tile] = []
    for kind in kinds_in_play(player_count):
        copies = TILES_PER_KIND
        if aka_dora and kind in FIVE_KINDS:
            tiles.append(Tile(kind, red=True))
            copies -= 1
        tiles.extend([Tile(kind)] * copies)
    return tiles


def counts_by_kind(tiles: Iterable[Tile]) -> list[int]:
    """A 34-length list counting how many tiles of each kind are present.

    Red-five marking is ignored: this is a shape-level tally by kind.

    Args:
        tiles: The tiles to tally.

    Returns:
        A list of length 34 indexed by ``TileKind`` value.
    """
    counts = [0] * NUM_KINDS
    for tile in tiles:
        counts[tile.kind] += 1
    return counts
