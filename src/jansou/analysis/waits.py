"""Waits: the tiles that complete a ready hand.

A tile is a wait exactly when adding it to the hand produces at least one valid
decomposition. Waits are expressed as kinds -- any copy of a waited kind
completes the hand -- and are a question of shape alone.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from jansou.analysis.decompose import is_complete
from jansou.core.tiles import TILES_PER_KIND, Tile, TileKind, counts_by_kind, kinds_in_play

if TYPE_CHECKING:
    from collections.abc import Iterable

    from jansou.core.hand import Hand, Meld


def _meld_counts(melds: tuple[Meld, ...]) -> list[int]:
    """Counts by kind across all meld tiles."""
    return counts_by_kind(tile for meld in melds for tile in meld.tiles)


def waits_counts(concealed: list[int], melds: tuple[Meld, ...], *, player_count: int = 4) -> set[TileKind]:
    """The kinds that complete a ready hand, given concealed counts and melds.

    A kind the hand already holds in all four copies is excluded: no fifth copy
    exists to complete on. The result is empty for a hand that is not ready and,
    exceptionally, for a ready hand every completing kind of which it already
    holds in full (karaten).

    Args:
        concealed: Concealed tile counts indexed by kind.
        melds: The called melds the hand holds.
        player_count: The number of players, which fixes the kinds in play.

    Returns:
        The kinds that complete the hand; empty when it is not ready or is karaten.
    """
    num_melds = len(melds)
    meld_counts = _meld_counts(melds)
    result: set[TileKind] = set()
    for kind in kinds_in_play(player_count):
        if concealed[kind] + meld_counts[kind] >= TILES_PER_KIND:
            continue
        concealed[kind] += 1
        if is_complete(concealed, num_melds):
            result.add(kind)
        concealed[kind] -= 1
    return result


def waits(hand: Hand, *, player_count: int = 4) -> set[TileKind]:
    """The wait set of a hand at ready size.

    Args:
        hand: The ready-size hand to inspect.
        player_count: The number of players, which fixes the kinds in play.

    Returns:
        The kinds that complete the hand.
    """
    return waits_counts(counts_by_kind(hand.concealed), hand.melds, player_count=player_count)


def completes(concealed: Iterable[Tile], melds: tuple[Meld, ...], candidate: Tile, *, player_count: int = 4) -> bool:
    """Whether a specific candidate tile completes the hand.

    Shape-only: the tile is valid exactly when it is among the hand's waits.

    Args:
        concealed: The concealed tiles the hand holds.
        melds: The called melds the hand holds.
        candidate: The tile to test.
        player_count: The number of players, which fixes the kinds in play.

    Returns:
        ``True`` if the candidate's kind is among the hand's waits.
    """
    return candidate.kind in waits_counts(counts_by_kind(concealed), melds, player_count=player_count)
