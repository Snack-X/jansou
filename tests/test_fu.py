"""Tests for fu computation."""

from __future__ import annotations

import pytest

from jansou.analysis.decompose import WaitShape, decompose
from jansou.core.hand import CallSource, Hand, Meld, MeldType
from jansou.core.notation import parse_mpsz
from jansou.core.rules import Rules
from jansou.core.tiles import Tile, TileKind, Wind
from jansou.scoring.context import WinContext
from jansou.scoring.fu import FuBreakdown, compute_fu


def fu_for(concealed: str, winning: str, wait: WaitShape, *, melds: tuple = (), **ctx: object) -> FuBreakdown:
    won = parse_mpsz(winning)[0]
    hand = Hand(tuple(parse_mpsz(concealed)), melds)
    context = WinContext(rules=ctx.pop("rules", Rules()), **ctx)  # type: ignore[arg-type]
    for decomp in decompose(hand.concealed, hand.melds, won):
        if decomp.wait is wait:
            return compute_fu(decomp, hand, context)
    raise AssertionError(f"no {wait} decomposition")


class TestFixedShapes:
    def test_chiitoitsu_flat_25(self) -> None:
        fu = fu_for("1122m3344p5566s77z", "7z", WaitShape.CHIITOI)
        assert fu.total == 25

    def test_kokushi_flat_30(self) -> None:
        fu = fu_for("19m19p19s12345677z", "7z", WaitShape.KOKUSHI_THIRTEEN)
        assert fu.total == 30

    def test_pinfu_tsumo_flat_20(self) -> None:
        fu = fu_for("234m345m567p234s88s", "2m", WaitShape.RYANMEN, is_tsumo=True)
        assert fu.total == 20

    def test_pinfu_ron_flat_30(self) -> None:
        fu = fu_for("234m345m567p234s88s", "2m", WaitShape.RYANMEN)
        assert fu.total == 30


class TestComponents:
    def test_concealed_simple_triplet_tsumo_rounds_up(self) -> None:
        # base 20 + tsumo 2 + concealed simple triplet 4 = 26, rounded to 30.
        fu = fu_for("222m345p678p234s55s", "3p", WaitShape.RYANMEN, is_tsumo=True)
        assert fu.raw == 26
        assert fu.total == 30

    def test_menzen_ron_adds_ten(self) -> None:
        fu = fu_for("222m345p678p234s55s", "3p", WaitShape.RYANMEN)
        assert fu.raw == 34  # 20 + 10 menzen ron + 4 triplet
        assert fu.total == 40

    def test_terminal_triplet_concealed(self) -> None:
        # base 20 + menzen ron 10 + concealed terminal triplet 8 + kanchan 2 = 40.
        fu = fu_for("111m345p678p234s99s", "3s", WaitShape.KANCHAN)
        assert fu.raw == 40
        assert fu.total == 40

    def test_wait_and_pair_fu(self) -> None:
        # A dragon pair (2) and a tanki wait (2) on a concealed ron.
        fu = fu_for("234m345p678p234s77z", "7z", WaitShape.TANKI)
        assert fu.raw == 20 + 10 + 2 + 2  # base, menzen ron, tanki, dragon pair
        assert fu.total == 40

    def test_seat_wind_pair(self) -> None:
        # South seat in the East round: the seat-wind pair is worth 2 fu.
        fu = fu_for("234m345p678p234s22z", "2z", WaitShape.TANKI, round_wind=Wind.EAST, seat_wind=Wind.SOUTH)
        assert fu.raw == 20 + 10 + 2 + 2  # base, menzen ron, tanki, seat-wind pair

    def test_double_wind_pair(self) -> None:
        # East seat in the East round: the pair is worth 4 fu by default.
        fu = fu_for(
            "234m345p678p234s11z",
            "1z",
            WaitShape.TANKI,
            round_wind=Wind.EAST,
            seat_wind=Wind.EAST,
        )
        assert fu.raw == 20 + 10 + 2 + 4  # base, menzen ron, tanki, double-wind pair
        fu_two = fu_for(
            "234m345p678p234s11z",
            "1z",
            WaitShape.TANKI,
            round_wind=Wind.EAST,
            seat_wind=Wind.EAST,
            rules=Rules(double_wind_fu=2),
        )
        assert fu_two.raw == 20 + 10 + 2 + 2


class TestSetFu:
    def test_open_triplet(self) -> None:
        pon = Meld(MeldType.PON, tuple(parse_mpsz("222m")), called=Tile(TileKind.M2), source=CallSource.TOIMEN)
        # base 20 + tsumo 2 + open simple triplet 2 = 24 -> 30.
        fu = fu_for("345p678p234s55s", "3p", WaitShape.RYANMEN, melds=(pon,), is_tsumo=True)
        assert fu.raw == 24
        assert fu.total == 30

    def test_closed_kan_terminal(self) -> None:
        ankan = Meld(MeldType.ANKAN, tuple(parse_mpsz("1111m")))
        # base 20 + tsumo 2 + concealed terminal quad 32 = 54 -> 60.
        fu = fu_for("345p678p234s55s", "3p", WaitShape.RYANMEN, melds=(ankan,), is_tsumo=True)
        assert fu.raw == 54
        assert fu.total == 60

    def test_open_kan_simple(self) -> None:
        kan = Meld(MeldType.DAIMINKAN, tuple(parse_mpsz("2222m")), called=Tile(TileKind.M2), source=CallSource.SHIMOCHA)
        # base 20 + tsumo 2 + open simple quad 8 = 30.
        fu = fu_for("345p678p234s55s", "3p", WaitShape.RYANMEN, melds=(kan,), is_tsumo=True)
        assert fu.raw == 30
        assert fu.total == 30


class TestShanponRon:
    def test_completed_triplet_counts_open(self) -> None:
        # Ron on a dual-pair wait: the completed triplet scores at the open rate.
        # 111m concealed pair + 99p, ron 1m -> 111m triplet counts open (terminal): 4 fu, not 8.
        ron = fu_for("111m99p234s345s678s", "1m", WaitShape.SHANPON)
        # base 20 + menzen ron 10 + open terminal triplet 4 = 34 -> 40.
        assert ron.raw == 34
        tsumo = fu_for("111m99p234s345s678s", "1m", WaitShape.SHANPON, is_tsumo=True)
        # Tsumo keeps the triplet concealed: base 20 + tsumo 2 + concealed terminal triplet 8 = 30.
        assert tsumo.raw == 30


def test_no_matching_decomposition_raises() -> None:
    with pytest.raises(AssertionError, match="decomposition"):
        fu_for("234m345m567p234s88s", "2m", WaitShape.KANCHAN)
