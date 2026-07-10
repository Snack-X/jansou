"""Tests for yaku detection and dora counting."""

from __future__ import annotations

from dataclasses import replace

import pytest

from jansou.analysis.decompose import decompose
from jansou.core.hand import CallSource, Hand, Meld, MeldType
from jansou.core.notation import parse_mpsz
from jansou.core.rules import Rules
from jansou.core.tiles import Tile, TileKind, Wind
from jansou.scoring.context import WinContext
from jansou.scoring.score import ScoreResult, ScoringError, score
from jansou.scoring.yaku import Yaku, count_dora, detect_ordinary, resolve_yakuman


def score_hand(
    concealed: str, winning: str, *, melds: tuple = (), rules: Rules | None = None, **ctx: object
) -> ScoreResult:
    hand = Hand(tuple(parse_mpsz(concealed)), melds)
    return score(hand, parse_mpsz(winning)[0], WinContext(rules=rules or Rules(), **ctx))


def names(result) -> set[str]:
    return {value.yaku.name for value in result.yaku}


def value_of(result, name: str) -> int:
    return next(value.value for value in result.yaku if value.yaku.name == name)


def chii(tiles: str, called: str) -> Meld:
    return Meld(MeldType.CHII, tuple(parse_mpsz(tiles)), called=parse_mpsz(called)[0], source=CallSource.KAMICHA)


class TestKuitan:
    def test_open_tanyao_counts_when_kuitan_is_on(self) -> None:
        result = score_hand("234m567p234s88s", "2m", melds=(chii("456p", "4p"),))
        assert "TANYAO" in names(result)

    def test_open_tanyao_has_no_yaku_when_kuitan_is_off(self) -> None:
        with pytest.raises(ScoringError):
            score_hand("234m567p234s88s", "2m", melds=(chii("456p", "4p"),), rules=replace(Rules(), kuitan=False))

    def test_concealed_tanyao_is_unaffected_by_kuitan(self) -> None:
        result = score_hand("234m345m567p234s88s", "2m", is_tsumo=True, rules=replace(Rules(), kuitan=False))
        assert "TANYAO" in names(result)


class TestPatternYaku:
    def test_tanyao(self) -> None:
        assert "TANYAO" in names(score_hand("234m345m567p234s88s", "2m", is_tsumo=True))

    def test_yakuhai_dragon(self) -> None:
        result = score_hand("234m345p678p555z88s", "2m", round_wind=Wind.SOUTH, seat_wind=Wind.SOUTH)
        assert "YAKUHAI_HAKU" in names(result)

    def test_double_wind_two_han(self) -> None:
        # East seat in the East round: an East triplet scores both round and seat.
        result = score_hand("111z234m567p888s99p", "9p", round_wind=Wind.EAST, seat_wind=Wind.EAST)
        assert {"YAKUHAI_ROUND", "YAKUHAI_SEAT"} <= names(result)

    def test_sanshoku_doujun_closed_and_open(self) -> None:
        closed = score_hand("234m234p234s567p88s", "2m", round_wind=Wind.SOUTH, seat_wind=Wind.SOUTH)
        assert value_of(closed, "SANSHOKU_DOUJUN") == 2
        open_hand = score_hand(
            "234m234s567p88s",
            "2m",
            melds=(chii("234p", "2p"),),
            round_wind=Wind.SOUTH,
            seat_wind=Wind.SOUTH,
        )
        assert value_of(open_hand, "SANSHOKU_DOUJUN") == 1

    def test_ittsu(self) -> None:
        assert "ITTSU" in names(score_hand("123456789m234p88s", "2p", round_wind=Wind.SOUTH, seat_wind=Wind.SOUTH))

    def test_chanta(self) -> None:
        result = score_hand("123m789p123s111z99m", "1m", round_wind=Wind.SOUTH, seat_wind=Wind.SOUTH)
        assert "CHANTA" in names(result)

    def test_junchan(self) -> None:
        result = score_hand("123m789p123s789s99m", "1m", round_wind=Wind.SOUTH, seat_wind=Wind.SOUTH)
        assert "JUNCHAN" in names(result)
        assert "CHANTA" not in names(result)

    def test_iipeikou(self) -> None:
        assert "IIPEIKOU" in names(score_hand("112233m456p789p55s", "1m"))

    def test_ryanpeikou_excludes_iipeikou(self) -> None:
        result = score_hand("112233m112233p55s", "1m")
        assert "RYANPEIKOU" in names(result)
        assert "IIPEIKOU" not in names(result)

    def test_toitoi_and_sanankou(self) -> None:
        pon = Meld(MeldType.PON, tuple(parse_mpsz("111m")), called=Tile(TileKind.M1), source=CallSource.TOIMEN)
        result = score_hand("333p555s222z99m", "9m", melds=(pon,), round_wind=Wind.SOUTH, seat_wind=Wind.SOUTH)
        assert {"TOITOI", "SANANKOU"} <= names(result)

    def test_honitsu(self) -> None:
        result = score_hand("123m456m789m111z22z", "2z", round_wind=Wind.SOUTH, seat_wind=Wind.SOUTH)
        assert value_of(result, "HONITSU") == 3

    def test_chinitsu(self) -> None:
        assert "CHINITSU" in names(score_hand("123m456m789m234m55m", "2m"))

    def test_shousangen(self) -> None:
        # Two dragon triplets and a dragon pair, plus a wind triplet.
        result = score_hand("555z666z77z 111m 234p", "2p", round_wind=Wind.SOUTH, seat_wind=Wind.SOUTH)
        assert "SHOUSANGEN" in names(result)

    def test_sanshoku_doukou(self) -> None:
        result = score_hand("222m222p222s456m99s", "5m", round_wind=Wind.SOUTH, seat_wind=Wind.SOUTH)
        assert "SANSHOKU_DOUKOU" in names(result)

    def test_sankantsu(self) -> None:
        ankan = [Meld(MeldType.ANKAN, tuple(parse_mpsz(f"{r}{r}{r}{r}m"))) for r in (1, 2, 3)]
        result = score_hand("567p99s", "7p", melds=tuple(ankan), round_wind=Wind.SOUTH, seat_wind=Wind.SOUTH)
        assert "SANKANTSU" in names(result)

    def test_honroutou_with_chiitoitsu(self) -> None:
        result = score_hand("1199m1199p1199s77z", "7z")
        assert {"HONROUTOU", "CHIITOITSU"} <= names(result)


class TestYakuman:
    def test_daisuushi(self) -> None:
        result = score_hand("111z222z333z444z55m", "5m", is_tsumo=True)
        assert "DAISUUSHI" in names(result)

    def test_shousuushi(self) -> None:
        result = score_hand("111z222z333z44z234m", "2m", round_wind=Wind.SOUTH, seat_wind=Wind.SOUTH)
        assert "SHOUSUUSHI" in names(result)

    def test_suukantsu(self) -> None:
        ankan = [Meld(MeldType.ANKAN, tuple(parse_mpsz(f"{r}{r}{r}{r}p"))) for r in (1, 2, 3, 4)]
        result = score_hand("55m", "5m", melds=tuple(ankan), is_tsumo=True)
        assert "SUUKANTSU" in names(result)

    def test_tsuuiisou(self) -> None:
        result = score_hand("111z222z555z666z77z", "7z", is_tsumo=True)
        assert "TSUUIISOU" in names(result)

    def test_chinroutou(self) -> None:
        result = score_hand("111m999m111p999p11s", "1s", is_tsumo=True)
        assert "CHINROUTOU" in names(result)

    def test_ryuuiisou(self) -> None:
        result = score_hand("234s234s666s888s66z", "2s", round_wind=Wind.SOUTH, seat_wind=Wind.SOUTH)
        assert "RYUUIISOU" in names(result)

    def test_chuuren_pure_double(self) -> None:
        result = score_hand("11123455678999m", "5m")
        assert "CHUUREN" in names(result)
        assert result.base == 16000

    def test_chuuren_single(self) -> None:
        result = score_hand("11122345678999m", "1m")
        assert "CHUUREN" in names(result)
        assert result.base == 8000

    def test_tenhou(self) -> None:
        result = score_hand("234m345m567p234s88s", "2m", is_tsumo=True, tenhou=True)
        assert "TENHOU" in names(result)

    def test_chiihou(self) -> None:
        result = score_hand("234m345m567p234s88s", "2m", is_tsumo=True, chiihou=True, seat_wind=Wind.SOUTH)
        assert "CHIIHOU" in names(result)


class TestConcealmentGating:
    def test_open_hand_loses_iipeikou(self) -> None:
        hand = Hand(tuple(parse_mpsz("112233m456p99s")), (chii("789p", "7p"),))
        won = Tile(TileKind.M1)
        context = WinContext(rules=Rules(), round_wind=Wind.SOUTH, seat_wind=Wind.SOUTH)
        found: set[Yaku] = set()
        for decomp in decompose(hand.concealed, hand.melds, won):
            found |= {y for y, _ in detect_ordinary(decomp, hand, context)}
        assert Yaku.IIPEIKOU not in found

    def test_menzen_tsumo_requires_concealed(self) -> None:
        hand = Hand(tuple(parse_mpsz("234m456p234s55s")), (chii("789p", "7p"),))
        won = Tile(TileKind.M2)
        context = WinContext(rules=Rules(), is_tsumo=True)
        for decomp in decompose(hand.concealed, hand.melds, won):
            assert Yaku.MENZEN_TSUMO not in {y for y, _ in detect_ordinary(decomp, hand, context)}

    def test_ordinary_detection_on_kokushi_shape(self) -> None:
        # detect_ordinary on a thirteen-orphans reading yields only whole-hand
        # yaku (honroutou), never the standard or seven-pairs yaku.
        hand = Hand(tuple(parse_mpsz("19m19p19s12345677z")))
        context = WinContext(rules=Rules())
        for decomp in decompose(hand.concealed, hand.melds, Tile(TileKind.M1)):
            found = {y for y, _ in detect_ordinary(decomp, hand, context)}
            assert Yaku.CHIITOITSU not in found
            assert Yaku.HONROUTOU in found


class TestSituational:
    def test_riichi_ippatsu_tsumo(self) -> None:
        result = score_hand("234m345m567p234s88s", "2m", is_tsumo=True, riichi=True, ippatsu=True)
        assert {"RIICHI", "IPPATSU", "MENZEN_TSUMO", "PINFU", "TANYAO"} <= names(result)

    def test_double_riichi_replaces_riichi(self) -> None:
        result = score_hand("234m345m567p234s88s", "2m", double_riichi=True)
        assert "DOUBLE_RIICHI" in names(result)
        assert "RIICHI" not in names(result)

    def test_haitei(self) -> None:
        assert "HAITEI" in names(score_hand("234m345m567p234s88s", "2m", is_tsumo=True, haitei=True))


class TestDora:
    def test_indicator_dora(self) -> None:
        # Indicator 1m designates 2m as dora; the hand holds two 2m.
        hand = Hand(tuple(parse_mpsz("223344m567p88s")))
        count = count_dora(hand, WinContext(rules=Rules(), dora_indicators=(Tile(TileKind.M1),)))
        assert count.dora == 2
        assert count.total == 2

    def test_red_five(self) -> None:
        hand = Hand(tuple(parse_mpsz("0m234m567p234s88s")))
        assert count_dora(hand, WinContext(rules=Rules())).aka == 1

    def test_ura_only_for_riichi(self) -> None:
        hand = Hand(tuple(parse_mpsz("223344m567p88s")))
        ura = (Tile(TileKind.M1),)
        assert count_dora(hand, WinContext(rules=Rules(), ura_indicators=ura)).ura == 0
        assert count_dora(hand, WinContext(rules=Rules(), ura_indicators=ura, riichi=True)).ura == 2

    def test_nuki_dora_and_north_indicator(self) -> None:
        rules = Rules(player_count=3, nuki_dora=True)
        hand = Hand(tuple(parse_mpsz("123p456p789p11s22s")))
        # A West indicator points at North; the two set-aside North count as both.
        count = count_dora(hand, WinContext(rules=rules, nuki_count=2, dora_indicators=(Tile(TileKind.WEST),)))
        assert count.nuki == 2
        assert count.dora == 2
        assert count.total == 4


class TestResolveYakuman:
    def test_accumulates_when_allowed(self) -> None:
        kept, total = resolve_yakuman([(Yaku.DAISANGEN, 1), (Yaku.TSUUIISOU, 1)], multiple_allowed=True)
        assert total == 2
        assert len(kept) == 2

    def test_keeps_highest_when_capped(self) -> None:
        kept, total = resolve_yakuman([(Yaku.DAISANGEN, 1), (Yaku.SUUANKOU, 2)], multiple_allowed=False)
        assert total == 2
        assert kept == [(Yaku.SUUANKOU, 2)]

    def test_cap_tie_breaks_by_catalog(self) -> None:
        kept, total = resolve_yakuman([(Yaku.TSUUIISOU, 1), (Yaku.DAISANGEN, 1)], multiple_allowed=False)
        assert total == 1
        assert kept == [(Yaku.DAISANGEN, 1)]  # earlier in the catalog


def test_yakuless_open_hand_cannot_win() -> None:
    with pytest.raises(ScoringError, match="no yaku"):
        score_hand(
            "789p234s88s",
            "7p",
            melds=(chii("123m", "1m"), chii("456p", "4p")),
            round_wind=Wind.SOUTH,
            seat_wind=Wind.SOUTH,
        )
