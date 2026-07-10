"""Yaku: the named scoring patterns a completed hand satisfies, and dora.

Detection runs against a specific decomposition together with the win context;
scoring keeps the highest-value reading. A hand that holds any yakuman is scored
from its yakuman alone, so ordinary yaku and dora are detected only when no
yakuman is present.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum, auto, unique
from typing import TYPE_CHECKING

from jansou.analysis.decompose import GroupType, Shape, WaitShape
from jansou.core.tiles import TileKind
from jansou.scoring.fu import effective_concealed, is_pinfu_shape

if TYPE_CHECKING:
    from collections.abc import Iterable

    from jansou.analysis.decompose import Decomposition, Group
    from jansou.core.hand import Hand
    from jansou.scoring.context import WinContext


@unique
class Yaku(Enum):
    """A scoring pattern, listed in the normative catalog order."""

    RIICHI = auto()
    IPPATSU = auto()
    MENZEN_TSUMO = auto()
    PINFU = auto()
    IIPEIKOU = auto()
    TANYAO = auto()
    YAKUHAI_HAKU = auto()
    YAKUHAI_HATSU = auto()
    YAKUHAI_CHUN = auto()
    YAKUHAI_ROUND = auto()
    YAKUHAI_SEAT = auto()
    HAITEI = auto()
    HOUTEI = auto()
    RINSHAN = auto()
    CHANKAN = auto()
    DOUBLE_RIICHI = auto()
    CHIITOITSU = auto()
    SANSHOKU_DOUJUN = auto()
    ITTSU = auto()
    CHANTA = auto()
    TOITOI = auto()
    SANANKOU = auto()
    SANSHOKU_DOUKOU = auto()
    SANKANTSU = auto()
    SHOUSANGEN = auto()
    HONROUTOU = auto()
    HONITSU = auto()
    JUNCHAN = auto()
    RYANPEIKOU = auto()
    CHINITSU = auto()
    KOKUSHI = auto()
    SUUANKOU = auto()
    CHUUREN = auto()
    DAISANGEN = auto()
    SHOUSUUSHI = auto()
    DAISUUSHI = auto()
    TSUUIISOU = auto()
    CHINROUTOU = auto()
    RYUUIISOU = auto()
    SUUKANTSU = auto()
    TENHOU = auto()
    CHIIHOU = auto()


#: The catalog position of each yaku, for tie-breaks that order by catalog.
CATALOG_ORDER: dict[Yaku, int] = {yaku: index for index, yaku in enumerate(Yaku)}

#: The yaku scored at the limit rather than from han and fu.
YAKUMAN: frozenset[Yaku] = frozenset(
    {
        Yaku.KOKUSHI,
        Yaku.SUUANKOU,
        Yaku.CHUUREN,
        Yaku.DAISANGEN,
        Yaku.SHOUSUUSHI,
        Yaku.DAISUUSHI,
        Yaku.TSUUIISOU,
        Yaku.CHINROUTOU,
        Yaku.RYUUIISOU,
        Yaku.SUUKANTSU,
        Yaku.TENHOU,
        Yaku.CHIIHOU,
    },
)

_GREEN_KINDS = frozenset({TileKind.S2, TileKind.S3, TileKind.S4, TileKind.S6, TileKind.S8, TileKind.HATSU})
_DRAGON_YAKU = {TileKind.HAKU: Yaku.YAKUHAI_HAKU, TileKind.HATSU: Yaku.YAKUHAI_HATSU, TileKind.CHUN: Yaku.YAKUHAI_CHUN}
_ITTSU_STARTS = (1, 4, 7)


@dataclass(frozen=True)
class DoraCount:
    """The bonus han a hand holds outside its yaku.

    Attributes:
        dora: The han from dora indicators.
        ura: The han from ura-dora indicators, under riichi.
        aka: The han from red fives.
        nuki: The han from set-aside North tiles.
    """

    dora: int
    ura: int
    aka: int
    nuki: int

    @property
    def total(self) -> int:
        """The combined bonus han."""
        return self.dora + self.ura + self.aka + self.nuki


def _kinds(hand: Hand) -> list[TileKind]:
    """Every tile kind of the hand, melds included and kan tiles counted."""
    return [tile.kind for tile in hand.all_tiles]


# --- Yakuman detection ------------------------------------------------------


def _standard_yakuman(decomp: Decomposition, hand: Hand, context: WinContext) -> list[tuple[Yaku, int]]:
    """Yakuman found in a standard-shape reading."""
    rules = context.rules
    sets = decomp.groups
    pair = decomp.pair
    triplets = [g for g in sets if g.type in (GroupType.TRIPLET, GroupType.QUAD)]
    winds = [g for g in triplets if g.kind.is_wind]
    dragons = [g for g in triplets if g.kind.is_dragon]
    quads = [g for g in sets if g.type is GroupType.QUAD]
    result: list[tuple[Yaku, int]] = []
    if len(dragons) == 3:
        result.append((Yaku.DAISANGEN, 1))
    if len(winds) == 4:
        result.append((Yaku.DAISUUSHI, 2 if rules.double_yakuman else 1))
    elif len(winds) == 3 and pair is not None and pair.kind.is_wind:
        result.append((Yaku.SHOUSUUSHI, 1))
    if len(quads) == 4:
        result.append((Yaku.SUUKANTSU, 1))
    concealed = [g for g in triplets if effective_concealed(g, decomp, is_tsumo=context.is_tsumo)]
    if len(concealed) == 4 and hand.is_concealed:
        double = decomp.wait is WaitShape.TANKI and rules.double_yakuman
        result.append((Yaku.SUUANKOU, 2 if double else 1))
    chuuren = _chuuren_multiple(hand, decomp, context)
    if chuuren:
        result.append((Yaku.CHUUREN, chuuren))
    return result


def _chuuren_multiple(hand: Hand, decomp: Decomposition, context: WinContext) -> int:
    """The nine-gates multiple (0 none, 1 single, 2 pure), if the hand qualifies."""
    if not hand.is_concealed or hand.melds:
        return 0
    kinds = _kinds(hand)
    if any(k.is_honor for k in kinds) or len({k.suit for k in kinds}) != 1:
        return 0
    ranks = Counter(k.rank for k in kinds)
    gates = ranks[1] >= 3 and ranks[9] >= 3 and all(ranks[r] >= 1 for r in range(2, 9))
    if not gates:
        return 0
    ranks[decomp.winning_tile.rank] -= 1
    pure = ranks[1] == 3 and ranks[9] == 3 and all(ranks[r] == 1 for r in range(2, 9))
    return 2 if pure and context.rules.double_yakuman else 1


def detect_yakuman(decomp: Decomposition, hand: Hand, context: WinContext) -> list[tuple[Yaku, int]]:
    """Every yakuman in a reading, each with its multiple (1 single, 2 double).

    Args:
        decomp: The decomposition to inspect.
        hand: The hand the decomposition reads.
        context: The win context, for the ruleset and situational yakuman.

    Returns:
        Each yakuman found paired with its multiple.
    """
    rules = context.rules
    kinds = _kinds(hand)
    result: list[tuple[Yaku, int]] = []
    if context.tenhou:
        result.append((Yaku.TENHOU, 1))
    if context.chiihou:
        result.append((Yaku.CHIIHOU, 1))
    if all(k.is_honor for k in kinds):
        result.append((Yaku.TSUUIISOU, 1))
    if all(k.is_terminal for k in kinds):
        result.append((Yaku.CHINROUTOU, 1))
    if all(k in _GREEN_KINDS for k in kinds):
        result.append((Yaku.RYUUIISOU, 1))
    if decomp.shape is Shape.KOKUSHI:
        double = decomp.wait is WaitShape.KOKUSHI_THIRTEEN and rules.double_yakuman
        result.append((Yaku.KOKUSHI, 2 if double else 1))
    if decomp.shape is Shape.STANDARD:
        result.extend(_standard_yakuman(decomp, hand, context))
    return result


# --- Ordinary yaku detection ------------------------------------------------


def _situational(context: WinContext, *, concealed: bool) -> list[tuple[Yaku, int]]:
    """Yaku read from the circumstances of the win rather than the tiles."""
    result: list[tuple[Yaku, int]] = []
    if context.double_riichi:
        result.append((Yaku.DOUBLE_RIICHI, 2))
    elif context.riichi:
        result.append((Yaku.RIICHI, 1))
    if context.ippatsu:
        result.append((Yaku.IPPATSU, 1))
    if context.is_tsumo and concealed:
        result.append((Yaku.MENZEN_TSUMO, 1))
    for flag, yaku in (
        (context.haitei, Yaku.HAITEI),
        (context.houtei, Yaku.HOUTEI),
        (context.rinshan, Yaku.RINSHAN),
        (context.chankan, Yaku.CHANKAN),
    ):
        if flag:
            result.append((yaku, 1))
    return result


def _identical_run_pairs(runs: list[Group]) -> int:
    """How many pairs of identical runs the runs contain."""
    counts = Counter(run.kind for run in runs)
    return sum(count // 2 for count in counts.values())


def _sanshoku_doujun(runs: list[Group]) -> bool:
    """Whether the same run appears in all three suits."""
    by_rank: dict[int, set] = {}
    for run in runs:
        by_rank.setdefault(run.kind.rank, set()).add(run.kind.suit)
    return any(len(suits) == 3 for suits in by_rank.values())


def _ittsu(runs: list[Group]) -> bool:
    """Whether one suit holds the 1-4-7 straight."""
    starts: dict[object, set[int]] = {}
    for run in runs:
        starts.setdefault(run.kind.suit, set()).add(run.kind.rank)
    return any(set(_ITTSU_STARTS) <= ranks for ranks in starts.values())


def _sanshoku_doukou(triplets: list[Group]) -> bool:
    """Whether the same triplet appears in all three suits."""
    by_rank: dict[int, set] = {}
    for triplet in triplets:
        if triplet.kind.is_suited:
            by_rank.setdefault(triplet.kind.rank, set()).add(triplet.kind.suit)
    return any(len(suits) == 3 for suits in by_rank.values())


def _chanta_family(sets: list[Group], pair: Group, runs: list[Group], kinds: Iterable[TileKind]) -> Yaku | None:
    """Which terminal-or-honor family the reading belongs to, if any."""
    if not runs:
        return None
    blocks = [*sets, pair]
    if not all(any(k.is_yaochuu for k in block.kinds) for block in blocks):
        return None
    return Yaku.JUNCHAN if not any(k.is_honor for k in kinds) else Yaku.CHANTA


def _han(yaku: Yaku, closed: int, open_: int, *, concealed: bool) -> tuple[Yaku, int]:
    """A yaku paired with its closed or open han."""
    return (yaku, closed if concealed else open_)


def _peikou(runs: list[Group], *, concealed: bool) -> list[tuple[Yaku, int]]:
    """Iipeikou or ryanpeikou from identical runs, on a concealed hand only."""
    if not concealed:
        return []
    pairs = _identical_run_pairs(runs)
    if pairs >= 2:
        return [(Yaku.RYANPEIKOU, 3)]
    if pairs == 1:
        return [(Yaku.IIPEIKOU, 1)]
    return []


def _yakuhai(triplets: list[Group], context: WinContext) -> list[tuple[Yaku, int]]:
    """A han for each triplet of a dragon, the round wind, or the seat wind."""
    result: list[tuple[Yaku, int]] = []
    for triplet in triplets:
        if triplet.kind.is_dragon:
            result.append((_DRAGON_YAKU[triplet.kind], 1))
        if triplet.kind is context.round_wind.tile_kind:
            result.append((Yaku.YAKUHAI_ROUND, 1))
        if triplet.kind is context.seat_wind.tile_kind:
            result.append((Yaku.YAKUHAI_SEAT, 1))
    return result


def _run_yaku(
    sets: list[Group], pair: Group, runs: list[Group], kinds: list[TileKind], *, concealed: bool
) -> list[tuple[Yaku, int]]:
    """The run-based yaku: three-colour runs, the pure straight, and the terminal family."""
    result: list[tuple[Yaku, int]] = []
    if _sanshoku_doujun(runs):
        result.append(_han(Yaku.SANSHOKU_DOUJUN, 2, 1, concealed=concealed))
    if _ittsu(runs):
        result.append(_han(Yaku.ITTSU, 2, 1, concealed=concealed))
    family = _chanta_family(sets, pair, runs, kinds)
    if family is Yaku.JUNCHAN:
        result.append(_han(Yaku.JUNCHAN, 3, 2, concealed=concealed))
    elif family is Yaku.CHANTA:
        result.append(_han(Yaku.CHANTA, 2, 1, concealed=concealed))
    return result


def _triplet_yaku(
    decomp: Decomposition, triplets: list[Group], pair: Group, context: WinContext
) -> list[tuple[Yaku, int]]:
    """The triplet-based yaku: three concealed triplets, three-colour triplets, quads, small dragons."""
    result: list[tuple[Yaku, int]] = []
    concealed = [g for g in triplets if effective_concealed(g, decomp, is_tsumo=context.is_tsumo)]
    if len(concealed) == 3:
        result.append((Yaku.SANANKOU, 2))
    if _sanshoku_doukou(triplets):
        result.append((Yaku.SANSHOKU_DOUKOU, 2))
    if sum(1 for g in triplets if g.type is GroupType.QUAD) == 3:
        result.append((Yaku.SANKANTSU, 2))
    dragons = sum(1 for g in triplets if g.kind.is_dragon)
    if dragons == 2 and pair.kind.is_dragon:
        result.append((Yaku.SHOUSANGEN, 2))
    return result


def _standard_ordinary(decomp: Decomposition, hand: Hand, context: WinContext) -> list[tuple[Yaku, int]]:
    """Ordinary yaku found in a standard-shape reading."""
    concealed = hand.is_concealed
    sets = list(decomp.groups)
    pair = decomp.pair
    assert pair is not None  # noqa: S101 - a standard reading always has a head
    runs = [g for g in sets if g.type is GroupType.RUN]
    triplets = [g for g in sets if g.type in (GroupType.TRIPLET, GroupType.QUAD)]
    result: list[tuple[Yaku, int]] = []
    if is_pinfu_shape(decomp, hand, context):
        result.append((Yaku.PINFU, 1))
    if not runs:
        result.append((Yaku.TOITOI, 2))
    result.extend(_peikou(runs, concealed=concealed))
    result.extend(_yakuhai(triplets, context))
    result.extend(_run_yaku(sets, pair, runs, _kinds(hand), concealed=concealed))
    result.extend(_triplet_yaku(decomp, triplets, pair, context))
    return result


def _color_yaku(hand: Hand, kinds: list[TileKind], *, kuitan: bool) -> list[tuple[Yaku, int]]:
    """Whole-hand suit and terminal-or-honor yaku shared by both shapes."""
    concealed = hand.is_concealed
    result: list[tuple[Yaku, int]] = []
    if all(k.is_simple for k in kinds) and (concealed or kuitan):
        result.append((Yaku.TANYAO, 1))
    suits = {k.suit for k in kinds if k.is_suited}
    if len(suits) == 1:
        if any(k.is_honor for k in kinds):
            result.append(_han(Yaku.HONITSU, 3, 2, concealed=concealed))
        else:
            result.append(_han(Yaku.CHINITSU, 6, 5, concealed=concealed))
    if all(k.is_yaochuu for k in kinds):
        result.append((Yaku.HONROUTOU, 2))
    return result


def detect_ordinary(decomp: Decomposition, hand: Hand, context: WinContext) -> list[tuple[Yaku, int]]:
    """Every ordinary yaku in a reading, each with its han.

    Args:
        decomp: The decomposition to inspect.
        hand: The hand the decomposition reads.
        context: The win context, for situational and rule-gated yaku.

    Returns:
        Each ordinary yaku found paired with its han.
    """
    kinds = _kinds(hand)
    result = _situational(context, concealed=hand.is_concealed)
    if decomp.shape is Shape.CHIITOI:
        result.append((Yaku.CHIITOITSU, 2))
    elif decomp.shape is Shape.STANDARD:
        result.extend(_standard_ordinary(decomp, hand, context))
    result.extend(_color_yaku(hand, kinds, kuitan=context.rules.kuitan))
    return result


# --- Dora -------------------------------------------------------------------


def count_dora(hand: Hand, context: WinContext) -> DoraCount:
    """The dora, ura dora, red dora, and nuki dora han the hand holds.

    Args:
        hand: The hand whose tiles are counted.
        context: The win context, for the indicators, riichi state, and rules.

    Returns:
        The bonus han split by source.
    """
    rules = context.rules
    tiles = hand.all_tiles
    kind_counts = Counter(tile.kind for tile in tiles)
    kind_counts[TileKind.NORTH] += context.nuki_count
    dora = sum(kind_counts[ind.kind.successor(sanma=rules.is_sanma)] for ind in context.dora_indicators)
    ura = 0
    if context.is_riichi and rules.ura_dora:
        ura = sum(kind_counts[ind.kind.successor(sanma=rules.is_sanma)] for ind in context.ura_indicators)
    aka = sum(1 for tile in tiles if tile.red) if rules.aka_dora else 0
    nuki = context.nuki_count if rules.nuki_dora else 0
    return DoraCount(dora, ura, aka, nuki)


def resolve_yakuman(yakuman: list[tuple[Yaku, int]], *, multiple_allowed: bool) -> tuple[list[tuple[Yaku, int]], int]:
    """The kept yakuman and total multiple after applying the accumulation cap.

    Args:
        yakuman: The detected yakuman, each with its multiple.
        multiple_allowed: Whether several yakuman may accumulate rather than only the highest counting.

    Returns:
        The kept yakuman and their combined multiple.
    """
    if multiple_allowed:
        return yakuman, sum(multiple for _, multiple in yakuman)
    best = max(yakuman, key=lambda item: (item[1], -CATALOG_ORDER[item[0]]))
    return [best], best[1]
