"""Tile-code conversions for the external log formats.

Two integer encodings appear in the Tenhou logs: the 136-tile scheme that
names every physical tile (used by mjlog), and the two-digit suit-and-rank
scheme (used by Tenhou JSON). Both fold the red fives onto dedicated codes.
MJAI names tiles as strings and reuses the core notation parser.
"""

from __future__ import annotations

from jansou.core.tiles import Suit, Tile, TileKind

#: The three 136-tile indices that name red fives, one per suit.
RED_136 = frozenset({16, 52, 88})

_SLOTS_PER_KIND = 4


def tile_from_136(index: int) -> Tile:
    """The tile named by a 136-tile index (0-135).

    The three reserved red-five indices (16, 52, 88) decode as red fives; every
    other index decodes to an ordinary tile of its kind.

    Args:
        index: A 136-tile index in the range 0-135, naming one physical tile.

    Returns:
        The tile that ``index`` names.

    Raises:
        ValueError: If ``index`` is not an integer in the range 0-135.
    """
    if not 0 <= index < _SLOTS_PER_KIND * len(TileKind):
        raise ValueError(f"136-tile index must be in 0-135, got {index}")
    return Tile(TileKind(index // _SLOTS_PER_KIND), red=index in RED_136)


#: The 136-tile red-five index of each red-carrying kind, by its kind value.
_RED_136_OF_KIND = {index // _SLOTS_PER_KIND: index for index in RED_136}


def tile_to_136(tile: Tile) -> int:
    """A 136-tile index naming this tile (a red five takes its reserved slot).

    The copy chosen need not be unique across a hand: the decoding only reads a
    tile's kind and red flag back, so any copy of the right variant round-trips.

    Args:
        tile: The tile to encode.

    Returns:
        A 136-tile index naming ``tile``, the inverse of ``tile_from_136``.
    """
    if tile.red:
        return _RED_136_OF_KIND[tile.kind]
    base = tile.kind * _SLOTS_PER_KIND
    # Copy 0 of a five is its red slot, so a plain five takes the next copy.
    return base + 1 if tile.kind in _RED_136_OF_KIND else base


#: The first two-digit code of each suit block, indexed by the leading digit.
_TENHOU_SUIT_BASE = {1: TileKind.M1, 2: TileKind.P1, 3: TileKind.S1, 4: TileKind.EAST}
_TENHOU_RED = {51: TileKind.M5, 52: TileKind.P5, 53: TileKind.S5}


def tile_from_tenhou(code: int) -> Tile:
    """The tile named by a two-digit Tenhou JSON code (e.g. 11=1m, 45=haku, 52=red 5p).

    The codes ``51``, ``52``, ``53`` name the red fives of man, pin, and sou; the
    honors run ``41``-``47``, and each numbered suit its rank after a leading digit.

    Args:
        code: A two-digit Tenhou JSON tile code.

    Returns:
        The tile that ``code`` names.

    Raises:
        ValueError: If ``code`` is not a recognized Tenhou tile code.
    """
    red_kind = _TENHOU_RED.get(code)
    if red_kind is not None:
        return Tile(red_kind, red=True)
    suit, rank = divmod(code, 10)
    base = _TENHOU_SUIT_BASE.get(suit)
    if base is None or not 1 <= rank <= (7 if suit == 4 else 9):
        raise ValueError(f"unrecognized Tenhou tile code {code}")
    return Tile(TileKind(base + rank - 1))


_TENHOU_DIGIT_OF_SUIT = {Suit.MANZU: 1, Suit.PINZU: 2, Suit.SOUZU: 3}
_TENHOU_RED_CODE = {kind: code for code, kind in _TENHOU_RED.items()}
_TENHOU_HONOR_DIGIT = 4


def tile_to_tenhou(tile: Tile) -> int:
    """Encode a tile as its two-digit Tenhou JSON code.

    Args:
        tile: The tile to encode.

    Returns:
        The two-digit Tenhou code, the inverse of `tile_from_tenhou`.
    """
    if tile.red:
        return _TENHOU_RED_CODE[tile.kind]
    if tile.is_honor:
        return _TENHOU_HONOR_DIGIT * 10 + (tile.kind - TileKind.EAST + 1)
    return _TENHOU_DIGIT_OF_SUIT[tile.suit] * 10 + tile.rank
