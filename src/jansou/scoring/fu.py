"""Fu: the minor-points half of a hand's value, computed per decomposition.

Fu is a base plus each contributing component -- the win condition, the
triplets and quads, the wait, and the pair -- rounded up to the next ten with a
floor of thirty. A few whole-hand shapes take a fixed fu instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from jansou.analysis.decompose import GroupType, Shape, WaitShape

if TYPE_CHECKING:
    from jansou.analysis.decompose import Decomposition, Group
    from jansou.core.hand import Hand
    from jansou.core.tiles import TileKind
    from jansou.scoring.context import WinContext

_BASE = 20
_MENZEN_RON = 10
_TSUMO = 2
_WAIT_FU = 2
_PAIR_FU = 2
_CHIITOI_FU = 25
_KOKUSHI_FU = 30
_PINFU_TSUMO_FU = 20
_PINFU_RON_FU = 30
_FU_FLOOR = 30
_FU_STEP = 10

_TWO_FU_WAITS = frozenset({WaitShape.KANCHAN, WaitShape.PENCHAN, WaitShape.TANKI})


@dataclass(frozen=True)
class FuComponent:
    """One named contribution to the fu total.

    Attributes:
        reason: A short label naming what the fu is for.
        value: The fu the component adds.
    """

    reason: str
    value: int


@dataclass(frozen=True)
class FuBreakdown:
    """The fu total with the components that produced it.

    Attributes:
        components: The contributions that were summed.
        raw: The component sum before rounding.
        total: The fu after rounding up to the next ten, floored at thirty.
    """

    components: tuple[FuComponent, ...]
    raw: int
    total: int


def _fixed(value: int, reason: str) -> FuBreakdown:
    """A whole-hand fixed fu that bypasses rounding."""
    return FuBreakdown((FuComponent(reason, value),), value, value)


def effective_concealed(group: Group, decomp: Decomposition, *, is_tsumo: bool) -> bool:
    """Whether a group counts as concealed, applying the shanpon-ron exception.

    A triplet completed by a ron on a dual-pair wait counts as open: the
    completing tile came from an opponent even though its other two were held.

    Args:
        group: The group to classify within ``decomp``.
        decomp: The decomposition the group belongs to.
        is_tsumo: Whether the hand was won by self-draw.

    Returns:
        ``True`` when the group counts as concealed for fu and yaku.
    """
    if decomp.wait is WaitShape.SHANPON and not is_tsumo and group is decomp.winning_group:
        return False
    return group.concealed


def _set_fu(group: Group, *, concealed: bool) -> int:
    """The fu a triplet or quad contributes; runs contribute none."""
    if group.type is GroupType.RUN:
        return 0
    value = 8 if group.type is GroupType.QUAD else 2
    if group.kind.is_yaochuu:
        value *= 2
    if concealed:
        value *= 2
    return value


def pair_fu(pair_kind: TileKind, context: WinContext) -> int:
    """The fu the pair contributes by its tile.

    Args:
        pair_kind: The tile kind forming the pair.
        context: The win context, for the round and seat winds.

    Returns:
        The fu the pair adds: a value for a dragon or a matching wind, otherwise zero.
    """
    if pair_kind.is_dragon:
        return _PAIR_FU
    is_round = pair_kind is context.round_wind.tile_kind
    is_seat = pair_kind is context.seat_wind.tile_kind
    if is_round and is_seat:
        return context.rules.double_wind_fu
    if is_round or is_seat:
        return _PAIR_FU
    return 0


def is_pinfu_shape(decomp: Decomposition, hand: Hand, context: WinContext) -> bool:
    """Whether the decomposition is pinfu: all runs, a valueless pair, a two-sided wait.

    Args:
        decomp: The decomposition to test.
        hand: The hand the decomposition reads, which must be concealed.
        context: The win context, for the pair's wind value.

    Returns:
        ``True`` when the reading qualifies as pinfu.
    """
    return (
        hand.is_concealed
        and decomp.shape is Shape.STANDARD
        and decomp.wait is WaitShape.RYANMEN
        and all(group.type is GroupType.RUN for group in decomp.groups)
        and decomp.pair is not None
        and pair_fu(decomp.pair.kind, context) == 0
    )


def _set_reason(group: Group, *, concealed: bool) -> str:
    """A short label for a triplet or quad fu component."""
    openness = "concealed" if concealed else "open"
    kind = "quad" if group.type is GroupType.QUAD else "triplet"
    tiles = "terminal/honor" if group.kind.is_yaochuu else "simple"
    return f"{openness} {kind} ({tiles})"


def _win_condition_fu(hand: Hand, context: WinContext) -> list[FuComponent]:
    """The fu the win condition adds: a self-draw, or a concealed claimed win."""
    if context.is_tsumo:
        return [FuComponent("tsumo", _TSUMO)]
    if hand.is_concealed:
        return [FuComponent("menzen ron", _MENZEN_RON)]
    return []


def _standard_components(decomp: Decomposition, hand: Hand, context: WinContext) -> list[FuComponent]:
    """Every fu component of an ordinary calculation, before rounding."""
    components = [FuComponent("base", _BASE), *_win_condition_fu(hand, context)]
    for group in decomp.groups:
        concealed = effective_concealed(group, decomp, is_tsumo=context.is_tsumo)
        value = _set_fu(group, concealed=concealed)
        if value:
            components.append(FuComponent(_set_reason(group, concealed=concealed), value))
    if decomp.wait in _TWO_FU_WAITS:
        components.append(FuComponent("wait", _WAIT_FU))
    if decomp.pair is not None and (value := pair_fu(decomp.pair.kind, context)):
        components.append(FuComponent("pair", value))
    return components


def compute_fu(decomp: Decomposition, hand: Hand, context: WinContext) -> FuBreakdown:
    """The fu of a specific decomposition, with its component breakdown.

    Args:
        decomp: The decomposition to score.
        hand: The hand the decomposition reads.
        context: The win context, for the win condition and pair value.

    Returns:
        The fu total and the components that produced it.
    """
    if decomp.shape is Shape.CHIITOI:
        return _fixed(_CHIITOI_FU, "chiitoitsu")
    if decomp.shape is Shape.KOKUSHI:
        return _fixed(_KOKUSHI_FU, "kokushi")
    if is_pinfu_shape(decomp, hand, context):
        return _fixed(_PINFU_TSUMO_FU, "pinfu tsumo") if context.is_tsumo else _fixed(_PINFU_RON_FU, "pinfu ron")
    components = _standard_components(decomp, hand, context)
    raw = sum(component.value for component in components)
    total = max(_FU_FLOOR, -(-raw // _FU_STEP) * _FU_STEP)
    return FuBreakdown(tuple(components), raw, total)
