"""Tests for hand decomposition and wait shapes."""

from __future__ import annotations

from jansou.analysis.decompose import Decomposition, Shape, WaitShape, decompose, is_complete
from jansou.core.hand import CallSource, Meld, MeldType
from jansou.core.notation import parse_mpsz
from jansou.core.tiles import Tile, TileKind, counts_by_kind


def decompose_full(mpsz: str, won: Tile) -> list[Decomposition]:
    """Decompose a full fourteen-tile hand whose tiles include the winning tile."""
    return decompose(parse_mpsz(mpsz), (), won)


def waits_of(decomps: list[Decomposition]) -> set[WaitShape]:
    return {d.wait for d in decomps}


class TestCompleteness:
    def test_is_complete_across_shapes(self) -> None:
        assert is_complete(counts_by_kind(parse_mpsz("123m456m789m234p55s")), 0)
        assert is_complete(counts_by_kind(parse_mpsz("11223344556677p")), 0)
        assert is_complete(counts_by_kind(parse_mpsz("19m19p19s12345677z")), 0)

    def test_incomplete(self) -> None:
        assert not is_complete(counts_by_kind(parse_mpsz("123m456m789m234p5s")), 0)  # 13 tiles
        assert not is_complete(counts_by_kind(parse_mpsz("123m456m789m235p55s")), 0)  # broken run

    def test_special_shapes_need_no_melds(self) -> None:
        # With a meld, only the standard shape is considered.
        assert not is_complete(counts_by_kind(parse_mpsz("112233445566p")), 1, allow_special=False)


class TestStandardWaits:
    def test_tanki(self) -> None:
        assert WaitShape.TANKI in waits_of(decompose_full("123m456m789m234p55s", Tile(TileKind.S5)))

    def test_ryanmen(self) -> None:
        # Winning 2m completes 234m at the low end.
        assert WaitShape.RYANMEN in waits_of(decompose_full("234m456m789m234p55s", Tile(TileKind.M2)))

    def test_kanchan(self) -> None:
        # Winning 3m fills the middle of 234m.
        assert WaitShape.KANCHAN in waits_of(decompose_full("234m456m789m234p55s", Tile(TileKind.M3)))

    def test_penchan(self) -> None:
        # Winning 3m completes the edge partial 1-2m; only 3 finishes it.
        waits = waits_of(decompose_full("123m456m789m234p55s", Tile(TileKind.M3)))
        assert WaitShape.PENCHAN in waits
        assert WaitShape.RYANMEN not in waits

    def test_shanpon(self) -> None:
        # Winning East turns the 1z pair into a triplet beside the 99p head.
        assert WaitShape.SHANPON in waits_of(decompose_full("111z99p789m123s456s", Tile(TileKind.EAST)))


class TestMultipleReadings:
    def test_triplets_or_runs(self) -> None:
        # 222333444m reads as three triplets or three identical runs, so the
        # winning 2m sits in a triplet (shanpon) and in a run (ryanmen).
        waits = waits_of(decompose_full("222333444m55m678p", Tile(TileKind.M2)))
        assert WaitShape.SHANPON in waits
        assert WaitShape.RYANMEN in waits

    def test_collapses_identical_readings(self) -> None:
        # Two identical 234m runs: completing either is one reading, not two.
        decomps = decompose_full("234234m678678p11s", Tile(TileKind.M2))
        ryanmen = [d for d in decomps if d.wait is WaitShape.RYANMEN]
        assert len(ryanmen) == 1


class TestSpecialShapes:
    def test_chiitoi_and_standard_both(self) -> None:
        # 11223344556677p is complete as both seven pairs and a standard hand.
        shapes = {d.shape for d in decompose_full("11223344556677p", Tile(TileKind.P7))}
        assert Shape.CHIITOI in shapes
        assert Shape.STANDARD in shapes

    def test_chiitoi_only(self) -> None:
        decomps = decompose_full("1188m2299p3355s66z", Tile(TileKind.HATSU))
        assert {d.shape for d in decomps} == {Shape.CHIITOI}
        assert decomps[0].wait is WaitShape.CHIITOI

    def test_kokushi_thirteen_wait(self) -> None:
        # The winning tile duplicates a kind already held in full: a thirteen-way wait.
        decomps = decompose_full("19m19p19s12345677z", Tile(TileKind.CHUN))
        assert [d.shape for d in decomps] == [Shape.KOKUSHI]
        assert decomps[0].wait is WaitShape.KOKUSHI_THIRTEEN

    def test_kokushi_single_wait(self) -> None:
        # The winning 1m is the lone unpaired kind (9m is the duplicate): a single wait.
        decomps = decompose_full("199m19p19s1234567z", Tile(TileKind.M1))
        assert decomps[0].wait is WaitShape.KOKUSHI


class TestCalledMelds:
    def test_fully_called_hand_is_tanki(self) -> None:
        melds = (
            Meld(MeldType.PON, tuple(parse_mpsz("111z")), called=Tile(TileKind.EAST), source=CallSource.TOIMEN),
            Meld(MeldType.PON, tuple(parse_mpsz("222z")), called=Tile(TileKind.SOUTH), source=CallSource.TOIMEN),
            Meld(MeldType.PON, tuple(parse_mpsz("333z")), called=Tile(TileKind.WEST), source=CallSource.TOIMEN),
            Meld(MeldType.PON, tuple(parse_mpsz("555z")), called=Tile(TileKind.HAKU), source=CallSource.TOIMEN),
        )
        won = Tile(TileKind.M9)
        decomps = decompose([won, won], melds, won)
        assert len(decomps) == 1
        assert decomps[0].wait is WaitShape.TANKI

    def test_incomplete_yields_nothing(self) -> None:
        # Fourteen tiles but the pinzu 2-3-5 cannot form the fourth set.
        assert decompose_full("123m456m789m235p55s", Tile(TileKind.P5)) == []
