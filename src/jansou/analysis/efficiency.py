"""Tile efficiency: acceptance, discard evaluation, and improvement tiles.

A shape-and-count analysis built on shanten: a draw is useful when it lowers
shanten, and a discard is judged by the shanten and acceptance it leaves
behind. It weighs neither yaku nor value nor safety, only the distance to ready
and the count of tiles that shorten it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from jansou.analysis.shanten import shanten_counts
from jansou.core.tiles import TILES_PER_KIND, TileKind, counts_by_kind, kinds_in_play

if TYPE_CHECKING:
    from collections.abc import Mapping

    from jansou.core.hand import Hand

_POST_DRAW_SIZES = (2, 5, 8, 11, 14)


@dataclass(frozen=True)
class DiscardOption:
    """The outcome of discarding one kind from a post-draw hand.

    Attributes:
        discard: The kind discarded.
        shanten: The shanten of the hand left behind.
        acceptance: The accepting kinds of the resulting hand, each mapped to the
            copies still available to draw.
        total_acceptance: The total accepting draws, the sum of ``acceptance``.
    """

    discard: TileKind
    shanten: int
    acceptance: Mapping[TileKind, int]
    total_acceptance: int


def _remaining(kind: TileKind, hand_counts: list[int], visible: list[int]) -> int:
    """How many copies of a kind are still available to draw."""
    return TILES_PER_KIND - hand_counts[kind] - visible[kind]


def acceptance_counts(
    concealed: list[int],
    num_melds: int,
    *,
    visible: list[int] | None = None,
    player_count: int = 4,
) -> dict[TileKind, int]:
    """Kinds that strictly lower the hand's shanten, each with copies remaining.

    ``visible`` counts tiles known to be outside the hand -- discards, melds, and
    revealed indicators -- which are deducted from the remaining copies. A kind
    with no copies left is not accepted.

    Args:
        concealed: Concealed tile counts indexed by kind.
        num_melds: The number of called melds the hand holds.
        visible: Counts by kind of tiles known to be outside the hand. Defaults
            to none visible.
        player_count: The number of players, which fixes the kinds in play.

    Returns:
        Each accepting kind mapped to the copies still available to draw.
    """
    visible = visible or [0] * len(concealed)
    base = shanten_counts(concealed, num_melds)
    result: dict[TileKind, int] = {}
    for kind in kinds_in_play(player_count):
        remaining = _remaining(kind, concealed, visible)
        if remaining <= 0:
            continue
        concealed[kind] += 1
        improved = shanten_counts(concealed, num_melds) < base
        concealed[kind] -= 1
        if improved:
            result[kind] = remaining
    return result


def acceptance(hand: Hand, *, visible: list[int] | None = None, player_count: int = 4) -> dict[TileKind, int]:
    """The acceptance of a resting hand: the kinds that bring it closer to ready.

    Args:
        hand: The resting hand to analyse.
        visible: Counts by kind of tiles known to be outside the hand. Defaults
            to none visible.
        player_count: The number of players, which fixes the kinds in play.

    Returns:
        Each accepting kind mapped to the copies still available to draw.
    """
    return acceptance_counts(
        counts_by_kind(hand.concealed),
        len(hand.melds),
        visible=visible,
        player_count=player_count,
    )


def discard_evaluation(
    hand: Hand,
    *,
    visible: list[int] | None = None,
    player_count: int = 4,
) -> list[DiscardOption]:
    """Every distinct discard from a post-draw hand, most efficient first.

    Each option reports the resulting shanten, the acceptance of the resulting
    hand, and the total accepting draws. Options are ordered by resulting shanten
    ascending first -- a discard that stays closer to ready always ranks higher --
    then by total acceptance descending, ties broken by the discard kind in
    ascending canonical order.

    Args:
        hand: The post-draw hand to evaluate.
        visible: Counts by kind of tiles known to be outside the hand. Defaults
            to none visible.
        player_count: The number of players, which fixes the kinds in play.

    Returns:
        One option per distinct discardable kind, most efficient first.

    Raises:
        ValueError: If the hand is not at a post-draw tile count.
    """
    concealed = counts_by_kind(hand.concealed)
    num_melds = len(hand.melds)
    if sum(concealed) not in _POST_DRAW_SIZES:
        raise ValueError(f"discard evaluation needs a post-draw hand, got {sum(concealed)} concealed tiles")
    options: list[DiscardOption] = []
    for kind in kinds_in_play(player_count):
        if concealed[kind] == 0:
            continue
        concealed[kind] -= 1
        result_shanten = shanten_counts(concealed, num_melds)
        accepts = acceptance_counts(concealed, num_melds, visible=visible, player_count=player_count)
        concealed[kind] += 1
        options.append(DiscardOption(kind, result_shanten, accepts, sum(accepts.values())))
    options.sort(key=lambda option: (option.shanten, -option.total_acceptance, option.discard))
    return options


def improvements(
    hand: Hand,
    *,
    visible: list[int] | None = None,
    player_count: int = 4,
) -> dict[TileKind, dict[TileKind, int]]:
    """Draws that keep shanten but widen acceptance, each with the wider acceptance.

    An acceptance-upgrade analysis on a resting hand: for a draw that does not
    reduce shanten, the best same-shanten discard is taken and the resulting
    acceptance compared against the current hand's. Only strictly wider results
    are reported.

    Args:
        hand: The resting hand to analyse.
        visible: Counts by kind of tiles known to be outside the hand. Defaults
            to none visible.
        player_count: The number of players, which fixes the kinds in play.

    Returns:
        Each qualifying draw mapped to the wider acceptance it unlocks.
    """
    concealed = counts_by_kind(hand.concealed)
    num_melds = len(hand.melds)
    base = shanten_counts(concealed, num_melds)
    base_total = sum(acceptance_counts(concealed, num_melds, visible=visible, player_count=player_count).values())

    def best_upgrade() -> dict[TileKind, int] | None:
        # The widest same-shanten acceptance reachable by one discard, if it beats the base.
        best_acc: dict[TileKind, int] | None = None
        best_total = base_total
        for discard in kinds_in_play(player_count):
            if concealed[discard] == 0:
                continue
            concealed[discard] -= 1
            if shanten_counts(concealed, num_melds) == base:
                accepts = acceptance_counts(concealed, num_melds, visible=visible, player_count=player_count)
                total = sum(accepts.values())
                if total > best_total:
                    best_total, best_acc = total, accepts
            concealed[discard] += 1
        return best_acc

    result: dict[TileKind, dict[TileKind, int]] = {}
    for draw in kinds_in_play(player_count):
        if _remaining(draw, concealed, visible or [0] * len(concealed)) <= 0:
            continue
        concealed[draw] += 1
        if shanten_counts(concealed, num_melds) == base:
            best = best_upgrade()
            if best is not None:
                result[draw] = best
        concealed[draw] -= 1
    return result
