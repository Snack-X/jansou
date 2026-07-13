"""Scoring: turning a completed hand into han, fu, and points.

Han and fu combine into base points, base points scale into per-player payments
by seat and win type, fixed tiers replace the formula for large hands and
yakuman, and the carried bonus is added. Because a hand may read several ways,
each is evaluated and the highest-value one returned.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto, unique
from typing import TYPE_CHECKING

from jansou.analysis.decompose import decompose
from jansou.core.rules import RIICHI_DEPOSIT
from jansou.scoring.fu import FuBreakdown, compute_fu
from jansou.scoring.yaku import (
    CATALOG_ORDER,
    DoraCount,
    Yaku,
    count_dora,
    detect_ordinary,
    detect_yakuman,
    resolve_yakuman,
)

if TYPE_CHECKING:
    from jansou.analysis.decompose import Decomposition
    from jansou.core.hand import Hand
    from jansou.core.rules import Rules
    from jansou.core.tiles import Tile
    from jansou.scoring.context import WinContext

_MANGAN_BASE = 2000
_YAKUMAN_BASE = 8000
_MANGAN_HAN = 5
_HANEMAN_HAN = 7
_BAIMAN_HAN = 10
_SANBAIMAN_HAN = 12
_KAZOE_HAN = 13
_KIRIAGE_RAW = 1920
_DEALER_RON_MULT = 6
_NON_DEALER_RON_MULT = 4
_DEALER_SHARE_MULT = 2
_NON_DEALER_SHARE_MULT = 1
_HONBA_DIVISOR = 3


class ScoringError(ValueError):
    """A hand that cannot be scored: no decomposition, or no yaku to win on."""


@unique
class LimitTier(Enum):
    """The tier a hand's value falls in."""

    NONE = auto()
    MANGAN = auto()
    HANEMAN = auto()
    BAIMAN = auto()
    SANBAIMAN = auto()
    KAZOE_YAKUMAN = auto()
    YAKUMAN = auto()


@dataclass(frozen=True)
class YakuValue:
    """A yaku with its contribution: han for an ordinary yaku, multiple for a yakuman.

    Attributes:
        yaku: The scoring pattern.
        value: Its han, or its multiple when the yaku is a yakuman.
    """

    yaku: Yaku
    value: int


@dataclass(frozen=True)
class Payment:
    """The points a win moves, by payer role, plus the table bonuses.

    Attributes:
        ron: The lump sum a discarder pays, or zero on a self-draw.
        tsumo_dealer: The share the dealer pays on a self-draw.
        tsumo_non_dealer: The share each non-dealer pays on a self-draw.
        honba: The repeat-counter bonus added to the total.
        sticks: The riichi deposit points collected by the winner.
        total: The full points the winner gains.
    """

    ron: int
    tsumo_dealer: int
    tsumo_non_dealer: int
    honba: int
    sticks: int
    total: int


@dataclass(frozen=True)
class ScoreResult:
    """The full value of a scored hand.

    Attributes:
        yaku: The yaku of the winning reading, each with its contribution.
        is_yakuman: Whether the hand scores as a yakuman.
        han: The total han, zero for a yakuman.
        fu: The fu breakdown of the winning reading.
        dora: The bonus han the hand holds.
        base: The base points the payments scale from.
        limit: The limit tier the value falls in.
        payment: The points the win moves.
    """

    yaku: tuple[YakuValue, ...]
    is_yakuman: bool
    han: int
    fu: FuBreakdown
    dora: DoraCount
    base: int
    limit: LimitTier
    payment: Payment


@dataclass(frozen=True)
class _Interpretation:
    """One decomposition's evaluated value, before payments."""

    yaku: tuple[YakuValue, ...]
    is_yakuman: bool
    han: int
    fu: FuBreakdown
    dora: DoraCount
    base: int
    limit: LimitTier


def _ceil(value: int, step: int) -> int:
    """Round up to the next multiple of step."""
    return -(-value // step) * step


def _limit_tier(han: int, rules: Rules) -> tuple[int, LimitTier]:
    """The base and tier of a five-or-more-han hand, fixed by han count."""
    if han == _MANGAN_HAN:
        return _MANGAN_BASE, LimitTier.MANGAN
    if han <= _HANEMAN_HAN:
        return 3000, LimitTier.HANEMAN
    if han <= _BAIMAN_HAN:
        return 4000, LimitTier.BAIMAN
    if han <= _SANBAIMAN_HAN:
        return 6000, LimitTier.SANBAIMAN
    if rules.kazoe_yakuman:
        return _YAKUMAN_BASE, LimitTier.KAZOE_YAKUMAN
    return 6000, LimitTier.SANBAIMAN


def base_points(han: int, fu: int, context: WinContext) -> tuple[int, LimitTier]:
    """The base points and tier for an ordinary hand of the given han and fu.

    Args:
        han: The hand's total han.
        fu: The hand's rounded fu total.
        context: The win context, for the limit and rounding rules.

    Returns:
        The base points and the limit tier they fall in.
    """
    if han >= _MANGAN_HAN:
        return _limit_tier(han, context.rules)
    raw = fu * 2 ** (han + 2)
    if (context.rules.kiriage_mangan and raw == _KIRIAGE_RAW) or raw >= _MANGAN_BASE:
        return _MANGAN_BASE, LimitTier.MANGAN
    return raw, LimitTier.NONE


def _payment(base: int, context: WinContext) -> Payment:
    """The payment a base scales to, given the seat and win type."""
    rules = context.rules
    honba_total = 0
    ron = tsumo_dealer = tsumo_non_dealer = 0
    if context.is_tsumo:
        if context.is_dealer:
            tsumo_non_dealer = _ceil(_DEALER_SHARE_MULT * base, 100)
            base_total = tsumo_non_dealer * (rules.player_count - 1)
        else:
            tsumo_dealer = _ceil(_DEALER_SHARE_MULT * base, 100)
            tsumo_non_dealer = _ceil(_NON_DEALER_SHARE_MULT * base, 100)
            base_total = tsumo_dealer + tsumo_non_dealer * (rules.player_count - 2)
        honba_total = (rules.player_count - 1) * (rules.honba_value // _HONBA_DIVISOR) * context.honba
    else:
        multiplier = _DEALER_RON_MULT if context.is_dealer else _NON_DEALER_RON_MULT
        ron = _ceil(multiplier * base, 100)
        base_total = ron
        # The discarder pays the whole honba: one share per non-winner (two in sanma).
        honba_total = (rules.player_count - 1) * (rules.honba_value // _HONBA_DIVISOR) * context.honba
    sticks = context.riichi_sticks * RIICHI_DEPOSIT
    return Payment(ron, tsumo_dealer, tsumo_non_dealer, honba_total, sticks, base_total + honba_total + sticks)


def _evaluate(decomp: Decomposition, hand: Hand, context: WinContext) -> _Interpretation | None:
    """Evaluate one decomposition, or None when it carries no yaku."""
    yakuman = detect_yakuman(decomp, hand, context)
    fu = compute_fu(decomp, hand, context)
    if yakuman:
        kept, multiple = resolve_yakuman(yakuman, multiple_allowed=context.rules.multiple_yakuman)
        yaku = tuple(YakuValue(y, m) for y, m in kept)
        return _Interpretation(
            yaku,
            is_yakuman=True,
            han=0,
            fu=fu,
            dora=DoraCount(0, 0, 0, 0),
            base=_YAKUMAN_BASE * multiple,
            limit=LimitTier.YAKUMAN,
        )
    ordinary = detect_ordinary(decomp, hand, context)
    if not ordinary:
        return None
    dora = count_dora(hand, context)
    han = sum(h for _, h in ordinary) + dora.total
    base, limit = base_points(han, fu.total, context)
    yaku = tuple(YakuValue(y, h) for y, h in ordinary)
    return _Interpretation(yaku, is_yakuman=False, han=han, fu=fu, dora=dora, base=base, limit=limit)


def _selection_key(interp: _Interpretation) -> tuple[int, int, int, int]:
    """Higher is better: base, then yakuman, then han, then fu."""
    return (interp.base, int(interp.is_yakuman), interp.han, interp.fu.total)


def _residual_key(interp: _Interpretation) -> tuple[int, ...]:
    """The catalog-ordered yaku ordinals, smallest winning a residual tie."""
    return tuple(sorted(CATALOG_ORDER[value.yaku] for value in interp.yaku))


def _select(interps: list[_Interpretation]) -> _Interpretation:
    """The best interpretation by base, han, fu, then catalog-order residual."""
    best = max(_selection_key(interp) for interp in interps)
    tied = [interp for interp in interps if _selection_key(interp) == best]
    return min(tied, key=_residual_key)


def score(hand: Hand, winning_tile: Tile, context: WinContext) -> ScoreResult:
    """Score a completed hand, returning its highest-value legal reading.

    The hand's concealed tiles must include the winning tile. Scoring fails
    when the hand does not complete, or when no reading carries a non-dora yaku.

    Args:
        hand: The completed hand, its concealed tiles including the winning tile.
        winning_tile: The tile that completed the hand.
        context: The situation the win is scored against.

    Returns:
        The highest-value legal reading of the hand.

    Raises:
        ScoringError: If the hand does not complete, or no reading carries a non-dora yaku.
    """
    decomps = decompose(hand.concealed, hand.melds, winning_tile)
    if not decomps:
        raise ScoringError("hand does not form a complete hand")
    interps = [interp for decomp in decomps if (interp := _evaluate(decomp, hand, context)) is not None]
    if not interps:
        raise ScoringError("hand has no yaku and cannot win")
    best = _select(interps)
    return ScoreResult(
        best.yaku,
        best.is_yakuman,
        best.han,
        best.fu,
        best.dora,
        best.base,
        best.limit,
        _payment(best.base, context),
    )
