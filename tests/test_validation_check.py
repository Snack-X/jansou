"""Tests for scoring a rebuilt win against the value the log recorded."""

from __future__ import annotations

from dataclasses import replace

from jansou.core.hand import Hand
from jansou.core.rules import Rules
from jansou.core.tiles import Tile, TileKind, Wind
from jansou.io.paifu import AgariRecord
from jansou.scoring.context import WinContext
from jansou.scoring.score import score
from jansou.validation.check import check_win


def _t(name: str) -> Tile:
    return Tile(TileKind[name])


# A concealed tanyao tsumo: 234m 345p 678p 234s 55s, winning tile 4m.
_HAND = Hand(
    (
        _t("M2"),
        _t("M3"),
        _t("M4"),
        _t("P3"),
        _t("P4"),
        _t("P5"),
        _t("P6"),
        _t("P7"),
        _t("P8"),
        _t("S2"),
        _t("S3"),
        _t("S4"),
        _t("S5"),
        _t("S5"),
    ),
)
_WINNING = _t("M4")
_RULES = Rules()
_CONTEXT = WinContext(rules=_RULES, is_tsumo=True)


def _record(**overrides: object) -> AgariRecord:
    result = score(_HAND, _WINNING, _CONTEXT)
    value = result.payment.total - result.payment.honba - result.payment.sticks
    base = {
        "hand": _HAND,
        "winning_tile": _WINNING,
        "context": _CONTEXT,
        "winner": 0,
        "from_seat": 0,
        "expected_fu": result.fu.total,
        "expected_value": value,
        "expected_deltas": (),
    }
    return AgariRecord(**{**base, **overrides})


class TestCheckWin:
    def test_matching_value_and_fu_passes(self) -> None:
        assert check_win(_record()).passed

    def test_wrong_value_fails(self) -> None:
        verdict = check_win(_record(expected_value=_record().expected_value + 100))
        assert not verdict.passed
        assert "value" in verdict.detail

    def test_wrong_fu_fails(self) -> None:
        verdict = check_win(_record(expected_fu=_record().expected_fu + 10))
        assert not verdict.passed
        assert "fu" in verdict.detail

    def test_absent_expectations_are_not_checked(self) -> None:
        assert check_win(_record(expected_value=None, expected_fu=None)).passed

    def test_chiitoitsu_fu_convention_accepted(self) -> None:
        # A log reports 25 fu for seven pairs; any computed fu is accepted.
        assert check_win(_record(expected_fu=25)).passed

    def test_limit_fu_convention_accepted(self) -> None:
        assert check_win(_record(expected_fu=0)).passed

    def test_yakuman_fu_is_not_checked(self) -> None:
        # Chiihou makes this pinfu-shaped hand a yakuman; its fu is cosmetic (a
        # log may record the pinfu-shape 20 where we compute 30), so a differing
        # fu must not fail the check.
        context = replace(_CONTEXT, chiihou=True, seat_wind=Wind.SOUTH)
        result = score(_HAND, _WINNING, context)
        assert result.is_yakuman
        value = result.payment.total - result.payment.honba - result.payment.sticks
        record = AgariRecord(
            hand=_HAND,
            winning_tile=_WINNING,
            context=context,
            winner=0,
            from_seat=0,
            expected_fu=result.fu.total + 10,
            expected_value=value,
            expected_deltas=(),
        )
        assert check_win(record).passed

    def test_unscorable_hand_reported(self) -> None:
        broken = Hand((*_HAND.concealed[:-1], _t("CHUN")))
        verdict = check_win(_record(hand=broken))
        assert not verdict.passed
        assert "unscorable" in verdict.detail
