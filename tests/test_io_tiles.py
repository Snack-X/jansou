"""Tests for the log tile-code conversions."""

from __future__ import annotations

import pytest

from jansou.core.tiles import Tile, TileKind
from jansou.io.tiles import tile_from_136, tile_from_tenhou, tile_to_136, tile_to_tenhou

_FIVE_KINDS = {TileKind.M5, TileKind.P5, TileKind.S5}


def _variants() -> list[Tile]:
    """Every tile variant, red fives included."""
    tiles = [Tile(kind) for kind in TileKind]
    tiles += [Tile(kind, red=True) for kind in _FIVE_KINDS]
    return tiles


class TestOneThirtySix:
    def test_first_and_last(self) -> None:
        assert tile_from_136(0) == Tile(TileKind.M1)
        assert tile_from_136(135).kind is TileKind.CHUN

    def test_copy_index_ignored_for_kind(self) -> None:
        assert all(tile_from_136(index).kind is TileKind.M1 for index in range(4))

    @pytest.mark.parametrize("index", [16, 52, 88])
    def test_red_five_slots(self, index: int) -> None:
        tile = tile_from_136(index)
        assert tile.red
        assert tile.kind in {TileKind.M5, TileKind.P5, TileKind.S5}

    def test_ordinary_five_is_not_red(self) -> None:
        assert not tile_from_136(17).red  # 17 // 4 == 4 == M5, but not the red slot

    @pytest.mark.parametrize("index", [-1, 136, 200])
    def test_out_of_range_rejected(self, index: int) -> None:
        with pytest.raises(ValueError, match="136-tile index"):
            tile_from_136(index)


class TestTenhou:
    @pytest.mark.parametrize(
        ("code", "kind"),
        [
            (11, TileKind.M1),
            (19, TileKind.M9),
            (21, TileKind.P1),
            (39, TileKind.S9),
            (41, TileKind.EAST),
            (47, TileKind.CHUN),
        ],
    )
    def test_suits_and_honors(self, code: int, kind: TileKind) -> None:
        assert tile_from_tenhou(code).kind is kind
        assert not tile_from_tenhou(code).red

    @pytest.mark.parametrize(("code", "kind"), [(51, TileKind.M5), (52, TileKind.P5), (53, TileKind.S5)])
    def test_red_fives(self, code: int, kind: TileKind) -> None:
        tile = tile_from_tenhou(code)
        assert tile.kind is kind
        assert tile.red

    @pytest.mark.parametrize("code", [10, 40, 48, 99, 30])
    def test_bad_codes_rejected(self, code: int) -> None:
        with pytest.raises(ValueError, match="Tenhou tile code"):
            tile_from_tenhou(code)


class TestInverseCodecs:
    @pytest.mark.parametrize("tile", _variants())
    def test_one_thirty_six_round_trips(self, tile: Tile) -> None:
        assert tile_from_136(tile_to_136(tile)) == tile

    @pytest.mark.parametrize("tile", _variants())
    def test_tenhou_round_trips(self, tile: Tile) -> None:
        assert tile_from_tenhou(tile_to_tenhou(tile)) == tile

    def test_red_five_takes_its_reserved_slot(self) -> None:
        assert tile_to_136(Tile(TileKind.P5, red=True)) == 52

    def test_ordinary_five_avoids_the_red_slot(self) -> None:
        assert tile_to_136(Tile(TileKind.P5)) != 52
        assert not tile_from_136(tile_to_136(Tile(TileKind.P5))).red

    def test_red_five_uses_its_own_tenhou_code(self) -> None:
        assert tile_to_tenhou(Tile(TileKind.S5, red=True)) == 53
