"""Tile notations: MPSZ, MJAI, and 136-tile, in both directions."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from jansou.core.tiles import FIVE_KINDS, Suit, Tile, TileKind

if TYPE_CHECKING:
    from collections.abc import Iterable

_SUIT_OF_LETTER = {"m": Suit.MANZU, "p": Suit.PINZU, "s": Suit.SOUZU}

_HONOR_DIGITS = "1234567"
_RANK_DIGITS = "123456789"
_RED_RANK_DIGIT = "0"

_MJAI_HONOR_OF_LETTER = {
    "E": TileKind.EAST,
    "S": TileKind.SOUTH,
    "W": TileKind.WEST,
    "N": TileKind.NORTH,
    "P": TileKind.HAKU,
    "F": TileKind.HATSU,
    "C": TileKind.CHUN,
}
_MJAI_LETTER_OF_HONOR = {kind: letter for letter, kind in _MJAI_HONOR_OF_LETTER.items()}

#: The three reserved red-five indices of the 136-tile layout.
_RED_INDICES = frozenset({16, 52, 88})
_INDEX_COUNT = 136
_SLOTS_PER_KIND = 4


class NotationError(ValueError):
    """Malformed notation input, or a collection the notation cannot express."""


# --- MPSZ -------------------------------------------------------


def parse_mpsz(text: str) -> list[Tile]:
    """Parse MPSZ notation into tiles.

    Runs may appear in any order and need not be sorted. Whitespace delimits
    runs; a run's digits must be followed by their suit letter before any
    whitespace or end of input.

    Args:
        text: MPSZ notation such as ``"123m 0p 55s 77z"``, where a suit letter
            follows its digits and ``0`` denotes a red five.

    Returns:
        The tiles named, in the order they appear in the text.

    Raises:
        NotationError: If digits are not followed by a suit letter, a suit
            letter has no preceding digits, or an unexpected character appears.
    """
    tiles: list[Tile] = []
    pending: list[tuple[int, str]] = []  # (position, digit) awaiting a suit letter
    for position, char in enumerate(text):
        if char.isspace():
            if pending:
                raise NotationError(f"digits without a suit letter at position {pending[0][0]}")
        elif char.isdigit():
            pending.append((position, char))
        elif char in _SUIT_OF_LETTER or char == "z":
            if not pending:
                raise NotationError(f"suit letter {char!r} with no preceding digits at position {position}")
            tiles.extend(_mpsz_run(char, pending))
            pending.clear()
        else:
            raise NotationError(f"unexpected character {char!r} at position {position}")
    if pending:
        raise NotationError(f"digits without a suit letter at position {pending[0][0]}")
    return tiles


def _mpsz_run(letter: str, digits: list[tuple[int, str]]) -> list[Tile]:
    """The tiles of one digit run given its suit letter."""
    if letter == "z":
        tiles = []
        for position, digit in digits:
            if digit not in _HONOR_DIGITS:
                raise NotationError(f"honor index {digit!r} out of range 1-7 at position {position}")
            tiles.append(Tile(TileKind(TileKind.EAST + int(digit) - 1)))
        return tiles
    suit = _SUIT_OF_LETTER[letter]
    return [
        Tile.suited(suit, 5, red=True) if digit == _RED_RANK_DIGIT else Tile.suited(suit, int(digit))
        for _, digit in digits
    ]


def dump_mpsz(tiles: Iterable[Tile]) -> str:
    """Serialize tiles to MPSZ in canonical order.

    A red five is written as `0` immediately before the ordinary fives of
    its suit; an empty collection yields the empty string.

    Args:
        tiles: The tiles to serialize, in any order.

    Returns:
        The canonical MPSZ string (suits grouped and sorted), or ``""`` when
        given no tiles.
    """
    runs: dict[str, list[str]] = {"m": [], "p": [], "s": [], "z": []}
    for tile in sorted(tiles):
        if tile.is_honor:
            runs["z"].append(str(tile.kind - TileKind.EAST + 1))
        else:
            suit: Suit = tile.suit  # type: ignore[assignment]
            runs[suit.value].append(_RED_RANK_DIGIT if tile.red else str(tile.rank))
    return "".join(f"{''.join(digits)}{letter}" for letter, digits in runs.items() if digits)


# --- MJAI -------------------------------------------------------


def parse_mjai(text: str) -> list[Tile]:
    """Parse whitespace-separated MJAI tokens into tiles.

    Args:
        text: MJAI tokens separated by whitespace, e.g. ``"5mr P 3s"`` (``5mr``
            is a red five, honors are letters).

    Returns:
        The tiles named, in order.

    Raises:
        NotationError: If any token is not a recognized MJAI tile.
    """
    return [_mjai_tile(token) for token in text.split()]


def _mjai_tile(token: str) -> Tile:
    """The tile named by one MJAI token."""
    honor = _MJAI_HONOR_OF_LETTER.get(token)
    if honor is not None:
        return Tile(honor)
    if len(token) == 2 and token[0] in _RANK_DIGITS and token[1] in _SUIT_OF_LETTER:
        return Tile.suited(_SUIT_OF_LETTER[token[1]], int(token[0]))
    if len(token) == 3 and token[0] == "5" and token[1] in _SUIT_OF_LETTER and token[2] == "r":
        return Tile.suited(_SUIT_OF_LETTER[token[1]], 5, red=True)
    raise NotationError(f"unrecognized MJAI token {token!r}")


def dump_mjai(tiles: Iterable[Tile]) -> str:
    """Serialize tiles to space-separated MJAI tokens, in the given order.

    Args:
        tiles: The tiles to serialize; their order is preserved.

    Returns:
        The tiles as space-separated MJAI tokens.
    """
    return " ".join(_mjai_token(tile) for tile in tiles)


def _mjai_token(tile: Tile) -> str:
    """The MJAI token naming one tile."""
    if tile.is_honor:
        return _MJAI_LETTER_OF_HONOR[tile.kind]
    suit: Suit = tile.suit  # type: ignore[assignment]
    return f"{tile.rank}{suit.value}{'r' if tile.red else ''}"


# --- 136-tile ---------------------------------------------------


def parse_136(indices: Iterable[int]) -> list[Tile]:
    """Parse 136-tile integers into tiles.

    Each index names one physical tile, so a duplicate index is rejected.
    The red-slot indices (16, 52, 88) always decode as red fives.

    Args:
        indices: 136-tile integers in the range 0-135, each naming a distinct
            physical tile.

    Returns:
        The tiles named, in order.

    Raises:
        NotationError: If an index is not an integer in 0-135, or a physical
            tile is named more than once.
    """
    tiles: list[Tile] = []
    seen: set[int] = set()
    for position, value in enumerate(indices):
        if not isinstance(value, int) or not 0 <= value < _INDEX_COUNT:
            raise NotationError(f"136-tile index must be an integer in 0-135, got {value!r} at position {position}")
        if value in seen:
            raise NotationError(f"duplicate 136-tile index {value} at position {position}")
        seen.add(value)
        tiles.append(Tile(TileKind(value // _SLOTS_PER_KIND), red=value in _RED_INDICES))
    return tiles


def dump_136(tiles: Iterable[Tile]) -> list[int]:
    """Serialize tiles to distinct 136-tile integers, in the given order.

    Each tile takes the lowest free slot of its variant: a red five its one
    reserved slot, an ordinary five the three slots above it, every other kind
    its four. A collection with more copies of a variant than it has slots
    (four ordinary fives of one suit) is rejected.

    Args:
        tiles: The tiles to serialize; their order is preserved.

    Returns:
        Distinct 136-tile integers, one per tile.

    Raises:
        NotationError: If a variant appears more times than it has physical
            copies (e.g. four ordinary fives of one suit).
    """
    used: Counter[tuple[TileKind, bool]] = Counter()
    indices: list[int] = []
    for tile in tiles:
        base = tile.kind * _SLOTS_PER_KIND
        if tile.red:
            first, capacity = base, 1
        elif tile.kind in FIVE_KINDS:
            first, capacity = base + 1, _SLOTS_PER_KIND - 1
        else:
            first, capacity = base, _SLOTS_PER_KIND
        slot = used[tile.kind, tile.red]
        if slot >= capacity:
            raise NotationError(f"no 136-tile slot left for {dump_mpsz([tile])!r} (already used {capacity})")
        used[tile.kind, tile.red] += 1
        indices.append(first + slot)
    return indices
