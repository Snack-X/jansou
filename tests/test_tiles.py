"""Tests for tiles: kinds, red fives, ordering, classification."""

from __future__ import annotations

from collections import Counter

import pytest

from jansou.core.tiles import Suit, Tile, TileKind, Wind, full_tile_set, kinds_in_play, suited_kind


class TestClassification:
    def test_suit_and_rank(self) -> None:
        assert TileKind.M1.suit is Suit.MANZU
        assert TileKind.M1.rank == 1
        assert TileKind.P5.suit is Suit.PINZU
        assert TileKind.P5.rank == 5
        assert TileKind.S9.suit is Suit.SOUZU
        assert TileKind.S9.rank == 9
        assert TileKind.EAST.suit is None
        assert TileKind.EAST.rank is None

    def test_honor_families(self) -> None:
        assert TileKind.EAST.is_wind
        assert TileKind.NORTH.is_wind
        assert not TileKind.HAKU.is_wind
        assert TileKind.HAKU.is_dragon
        assert TileKind.CHUN.is_dragon
        assert not TileKind.EAST.is_dragon
        assert TileKind.EAST.is_honor
        assert not TileKind.M1.is_honor

    def test_terminal_simple_yaochuu(self) -> None:
        assert TileKind.M1.is_terminal
        assert TileKind.S9.is_terminal
        assert not TileKind.P5.is_terminal
        assert not TileKind.EAST.is_terminal
        assert TileKind.P2.is_simple
        assert TileKind.M8.is_simple
        assert not TileKind.M9.is_simple
        assert not TileKind.CHUN.is_simple
        assert TileKind.M1.is_yaochuu
        assert TileKind.CHUN.is_yaochuu
        assert not TileKind.S5.is_yaochuu

    def test_adjacency(self) -> None:
        assert TileKind.M1.is_adjacent(TileKind.M2)
        assert TileKind.S8.is_adjacent(TileKind.S9)
        assert not TileKind.M9.is_adjacent(TileKind.P1)  # suit boundary
        assert not TileKind.M9.is_adjacent(TileKind.M1)  # no wrap
        assert not TileKind.EAST.is_adjacent(TileKind.SOUTH)  # honors have no neighbors
        assert not TileKind.M3.is_adjacent(TileKind.M5)


class TestSuccessor:
    def test_suited_wraps_nine_to_one(self) -> None:
        assert TileKind.M1.successor() is TileKind.M2
        assert TileKind.M9.successor() is TileKind.M1
        assert TileKind.P9.successor() is TileKind.P1
        assert TileKind.S4.successor() is TileKind.S5

    def test_wind_cycle(self) -> None:
        assert TileKind.EAST.successor() is TileKind.SOUTH
        assert TileKind.SOUTH.successor() is TileKind.WEST
        assert TileKind.WEST.successor() is TileKind.NORTH
        assert TileKind.NORTH.successor() is TileKind.EAST

    def test_dragon_cycle(self) -> None:
        assert TileKind.HAKU.successor() is TileKind.HATSU
        assert TileKind.HATSU.successor() is TileKind.CHUN
        assert TileKind.CHUN.successor() is TileKind.HAKU

    def test_sanma_manzu_contraction(self) -> None:
        assert TileKind.M1.successor(sanma=True) is TileKind.M9
        assert TileKind.M9.successor(sanma=True) is TileKind.M1
        # Other suits and honors are unaffected.
        assert TileKind.P1.successor(sanma=True) is TileKind.P2
        assert TileKind.NORTH.successor(sanma=True) is TileKind.EAST


class TestTile:
    def test_exact_vs_shape_equality(self) -> None:
        red = Tile(TileKind.P5, red=True)
        plain = Tile(TileKind.P5)
        assert red != plain  # exact equality
        assert red.kind is plain.kind  # shape equality

    def test_red_only_for_fives(self) -> None:
        with pytest.raises(ValueError, match="only fives"):
            Tile(TileKind.M1, red=True)
        with pytest.raises(ValueError, match="only fives"):
            Tile(TileKind.EAST, red=True)

    def test_suited_factory(self) -> None:
        assert Tile.suited(Suit.MANZU, 3) == Tile(TileKind.M3)
        assert Tile.suited(Suit.SOUZU, 5, red=True) == Tile(TileKind.S5, red=True)
        with pytest.raises(ValueError, match="rank"):
            suited_kind(Suit.MANZU, 0)

    def test_canonical_order(self) -> None:
        tiles = [
            Tile(TileKind.CHUN),
            Tile(TileKind.EAST),
            Tile(TileKind.S1),
            Tile(TileKind.P9),
            Tile(TileKind.M1),
        ]
        assert sorted(tiles) == [
            Tile(TileKind.M1),
            Tile(TileKind.P9),
            Tile(TileKind.S1),
            Tile(TileKind.EAST),
            Tile(TileKind.CHUN),
        ]

    def test_classification_pass_throughs(self) -> None:
        red = Tile(TileKind.S5, red=True)
        assert red.is_suited
        assert not red.is_honor
        assert red.suit is Suit.SOUZU
        assert red.rank == 5
        assert red.is_simple
        assert not red.is_terminal
        assert not red.is_yaochuu
        assert not red.is_wind
        assert not red.is_dragon
        assert Tile(TileKind.EAST).is_wind
        assert Tile(TileKind.CHUN).is_dragon
        assert Tile(TileKind.M1).is_terminal

    def test_ordering_against_non_tile_is_a_type_error(self) -> None:
        with pytest.raises(TypeError):
            _ = Tile(TileKind.M1) < 1  # type: ignore[operator]

    def test_red_five_sorts_immediately_before_ordinary_fives(self) -> None:
        tiles = [Tile(TileKind.P5), Tile(TileKind.P5, red=True), Tile(TileKind.P4), Tile(TileKind.P6)]
        assert sorted(tiles) == [
            Tile(TileKind.P4),
            Tile(TileKind.P5, red=True),
            Tile(TileKind.P5),
            Tile(TileKind.P6),
        ]


class TestWind:
    def test_tile_kind(self) -> None:
        assert Wind.EAST.tile_kind is TileKind.EAST
        assert Wind.NORTH.tile_kind is TileKind.NORTH


class TestFullSet:
    def test_yonma_set(self) -> None:
        tiles = full_tile_set()
        assert len(tiles) == 136
        counts = Counter(tile.kind for tile in tiles)
        assert set(counts) == set(TileKind)
        assert all(count == 4 for count in counts.values())
        reds = [tile for tile in tiles if tile.red]
        assert sorted(tile.kind for tile in reds) == [TileKind.M5, TileKind.P5, TileKind.S5]

    def test_yonma_set_without_red_fives(self) -> None:
        tiles = full_tile_set(aka_dora=False)
        assert len(tiles) == 136
        assert not any(tile.red for tile in tiles)
        assert sum(tile.kind is TileKind.M5 for tile in tiles) == 4

    def test_sanma_set(self) -> None:
        tiles = full_tile_set(3)
        assert len(tiles) == 108
        kinds = {tile.kind for tile in tiles}
        assert TileKind.M1 in kinds
        assert TileKind.M9 in kinds
        assert all(TileKind(k) not in kinds for k in range(TileKind.M2, TileKind.M9))
        # Sanma has no manzu five, so only pinzu and souzu reds.
        reds = sorted(tile.kind for tile in tiles if tile.red)
        assert reds == [TileKind.P5, TileKind.S5]

    def test_kinds_in_play(self) -> None:
        assert len(kinds_in_play(4)) == 34
        assert len(kinds_in_play(3)) == 27
        with pytest.raises(ValueError, match="player count"):
            kinds_in_play(2)
