"""Tests for scoring: base points, payments, limits, and interpretation choice."""

from __future__ import annotations

import pytest

from jansou.core.hand import Hand
from jansou.core.notation import parse_mpsz
from jansou.core.rules import Rules
from jansou.core.tiles import Tile, Wind
from jansou.scoring.context import WinContext
from jansou.scoring.score import LimitTier, ScoreResult, ScoringError, base_points, score


def score_hand(
    concealed: str, winning: str, *, melds: tuple = (), rules: Rules | None = None, **ctx: object
) -> ScoreResult:
    hand = Hand(tuple(parse_mpsz(concealed)), melds)
    return score(hand, parse_mpsz(winning)[0], WinContext(rules=rules or Rules(), **ctx))


def names(result: ScoreResult) -> set[str]:
    return {value.yaku.name for value in result.yaku}


class TestBasePoints:
    @pytest.mark.parametrize(
        ("han", "fu", "base", "tier"),
        [
            (1, 30, 240, LimitTier.NONE),
            (3, 40, 1280, LimitTier.NONE),
            (4, 30, 1920, LimitTier.NONE),  # just below mangan
            (3, 60, 1920, LimitTier.NONE),
            (4, 40, 2000, LimitTier.MANGAN),  # formula reaches the cap
            (5, 30, 2000, LimitTier.MANGAN),
            (6, 20, 3000, LimitTier.HANEMAN),
            (7, 20, 3000, LimitTier.HANEMAN),
            (8, 20, 4000, LimitTier.BAIMAN),
            (10, 20, 4000, LimitTier.BAIMAN),
            (11, 20, 6000, LimitTier.SANBAIMAN),
            (12, 20, 6000, LimitTier.SANBAIMAN),
            (13, 20, 8000, LimitTier.KAZOE_YAKUMAN),
        ],
    )
    def test_table(self, han: int, fu: int, base: int, tier: LimitTier) -> None:
        assert base_points(han, fu, WinContext(rules=Rules())) == (base, tier)

    def test_kiriage_promotes_borderline(self) -> None:
        context = WinContext(rules=Rules(kiriage_mangan=True))
        assert base_points(4, 30, context) == (2000, LimitTier.MANGAN)
        assert base_points(3, 60, context) == (2000, LimitTier.MANGAN)

    def test_kazoe_off_caps_at_sanbaiman(self) -> None:
        assert base_points(13, 20, WinContext(rules=Rules(kazoe_yakuman=False))) == (6000, LimitTier.SANBAIMAN)


class TestPayments:
    #: A single yakuman (base 8000), for clean payment arithmetic.
    DAISANGEN = "555z666z777z789s11p"

    def test_non_dealer_ron(self) -> None:
        result = score_hand(self.DAISANGEN, "7s", seat_wind=Wind.SOUTH)
        assert result.payment.ron == 32000
        assert result.payment.total == 32000

    def test_dealer_ron(self) -> None:
        result = score_hand(self.DAISANGEN, "7s", seat_wind=Wind.EAST)
        assert result.payment.ron == 48000

    def test_non_dealer_tsumo(self) -> None:
        result = score_hand(self.DAISANGEN, "7s", seat_wind=Wind.SOUTH, is_tsumo=True)
        assert result.payment.tsumo_dealer == 16000
        assert result.payment.tsumo_non_dealer == 8000
        assert result.payment.total == 32000  # 16000 + 8000 + 8000

    def test_dealer_tsumo(self) -> None:
        result = score_hand(self.DAISANGEN, "7s", seat_wind=Wind.EAST, is_tsumo=True)
        assert result.payment.tsumo_dealer == 0
        assert result.payment.tsumo_non_dealer == 16000
        assert result.payment.total == 48000

    def test_honba_on_ron(self) -> None:
        result = score_hand(self.DAISANGEN, "7s", seat_wind=Wind.SOUTH, honba=2)
        assert result.payment.honba == 600
        assert result.payment.total == 32600

    def test_honba_on_tsumo(self) -> None:
        result = score_hand(self.DAISANGEN, "7s", seat_wind=Wind.SOUTH, is_tsumo=True, honba=1)
        assert result.payment.honba == 300  # three payers, 100 each

    def test_riichi_sticks_collected(self) -> None:
        result = score_hand(self.DAISANGEN, "7s", seat_wind=Wind.SOUTH, riichi_sticks=2)
        assert result.payment.sticks == 2000
        assert result.payment.total == 34000

    def test_rounding_up_to_hundred(self) -> None:
        # Riichi only, 40 fu (concealed terminal triplet 8 + tanki 2): base 320,
        # non-dealer ron 4 x 320 = 1280 -> 1300.
        result = score_hand("111m456p789p234s55s", "5s", riichi=True, seat_wind=Wind.SOUTH)
        assert result.han == 1
        assert result.fu.total == 40
        assert result.payment.ron == 1300


class TestSanma:
    def test_tsumo_loss_two_payers(self) -> None:
        rules = Rules(player_count=3)
        result = score_hand("555z666z777z789s11p", "7s", rules=rules, seat_wind=Wind.SOUTH, is_tsumo=True)
        # Only the dealer and one non-dealer pay; the missing share is not made up.
        assert result.payment.total == 16000 + 8000

    def test_honba_two_payers(self) -> None:
        rules = Rules(player_count=3)
        result = score_hand("555z666z777z789s11p", "7s", rules=rules, seat_wind=Wind.SOUTH, is_tsumo=True, honba=1)
        assert result.payment.honba == 200  # two payers, 100 each


class TestYakuman:
    def test_kokushi_single(self) -> None:
        result = score_hand("19m19p19s12345677z", "1m")
        assert result.is_yakuman
        assert result.base == 8000
        assert "KOKUSHI" in names(result)

    def test_kokushi_thirteen_wait_double(self) -> None:
        result = score_hand("19m19p19s12345677z", "7z")  # winning tile is the duplicate
        assert result.base == 16000

    def test_suuankou_tanki_double(self) -> None:
        result = score_hand("111m333p555s777z22m", "2m", is_tsumo=True, round_wind=Wind.SOUTH, seat_wind=Wind.SOUTH)
        assert result.base == 16000
        assert "SUUANKOU" in names(result)

    def test_stacking_when_allowed(self) -> None:
        # Daisangen plus a double suuankou: 8000 + 16000 = 24000.
        result = score_hand("555z666z777z111m22m", "2m", is_tsumo=True, round_wind=Wind.SOUTH, seat_wind=Wind.SOUTH)
        assert result.base == 24000

    def test_capped_when_multiple_off(self) -> None:
        rules = Rules(multiple_yakuman=False)
        result = score_hand(
            "555z666z777z111m22m",
            "2m",
            rules=rules,
            is_tsumo=True,
            round_wind=Wind.SOUTH,
            seat_wind=Wind.SOUTH,
        )
        assert result.base == 16000  # the double suuankou alone

    def test_yakuman_drops_dora(self) -> None:
        result = score_hand("19m19p19s12345677z", "1m", dora_indicators=(Tile(parse_mpsz("9m")[0].kind),))
        assert result.dora.total == 0


class TestInterpretationSelection:
    def test_highest_base_wins(self) -> None:
        # Read as three concealed triplets (sanankou, 40 fu) rather than three
        # identical runs (pinfu iipeikou, 20 fu); the triplet reading scores more.
        result = score_hand("111222333m456p99p", "4p", is_tsumo=True, round_wind=Wind.SOUTH, seat_wind=Wind.SOUTH)
        assert "SANANKOU" in names(result)
        assert "PINFU" not in names(result)
        assert result.base == 1280


class TestFailures:
    def test_incomplete_hand(self) -> None:
        with pytest.raises(ScoringError, match="complete"):
            score_hand("123m456m789m235p55s", "5s")

    def test_no_yaku(self) -> None:
        # A concealed ron with a kanchan wait and terminals: no yaku at all.
        with pytest.raises(ScoringError, match="no yaku"):
            score_hand("123m456p789p123s99p", "2s", round_wind=Wind.SOUTH, seat_wind=Wind.SOUTH)
