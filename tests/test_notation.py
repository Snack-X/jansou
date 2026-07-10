"""Tests for notations: MPSZ, MJAI, 136-tile."""

from __future__ import annotations

from collections import Counter

import pytest

from jansou.core.notation import (
    NotationError,
    dump_136,
    dump_mjai,
    dump_mpsz,
    parse_136,
    parse_mjai,
    parse_mpsz,
)
from jansou.core.tiles import Tile, TileKind, full_tile_set


def multiset(tiles: list[Tile]) -> Counter[Tile]:
    return Counter(tiles)


class TestMpszParse:
    def test_spec_example(self) -> None:
        # 1m 2m 3m, a red five and an ordinary five and two sixes of pinzu,
        # and three red dragons.
        tiles = parse_mpsz("123m0566p777z")
        assert multiset(tiles) == multiset(
            [
                Tile(TileKind.M1),
                Tile(TileKind.M2),
                Tile(TileKind.M3),
                Tile(TileKind.P5, red=True),
                Tile(TileKind.P5),
                Tile(TileKind.P6),
                Tile(TileKind.P6),
                Tile(TileKind.CHUN),
                Tile(TileKind.CHUN),
                Tile(TileKind.CHUN),
            ],
        )

    def test_thirteen_orphans_example(self) -> None:
        tiles = parse_mpsz("19m19p19s1234567z")
        assert len(tiles) == 13
        assert len(set(tiles)) == 13
        assert all(tile.is_yaochuu for tile in tiles)

    def test_runs_in_any_order_and_unsorted(self) -> None:
        assert multiset(parse_mpsz("777z321m6065p")) == multiset(parse_mpsz("123m0566p777z"))

    def test_empty_string(self) -> None:
        assert parse_mpsz("") == []

    def test_whitespace_between_runs(self) -> None:
        assert multiset(parse_mpsz(" 123m  456p ")) == multiset(parse_mpsz("123m456p"))

    def test_more_than_four_copies_is_not_a_parse_error(self) -> None:
        # Semantic conditions are hand validity's concern, not parsing's.
        assert len(parse_mpsz("11111m")) == 5

    @pytest.mark.parametrize(
        "text",
        [
            "123",  # trailing digits with no suit letter
            "12 3m",  # whitespace splits the run before its letter
            "123m45",  # trailing digits after a valid run
            "0z",  # no red honor
            "8z",  # honor index out of range
            "9z",
            "m",  # letter with no digits
            "123x",  # unknown suit letter
            "1,2m",  # stray character
        ],
    )
    def test_rejects(self, text: str) -> None:
        with pytest.raises(NotationError):
            parse_mpsz(text)


class TestMpszDump:
    def test_canonical_order_and_red_placement(self) -> None:
        # A red five, an ordinary five, and two sixes of pinzu — the red
        # five written as 0 immediately before the ordinary five.
        tiles = parse_mpsz("777z66p05p321m")
        assert dump_mpsz(tiles) == "123m0566p777z"

    def test_empty(self) -> None:
        assert dump_mpsz([]) == ""

    def test_round_trip_exact(self) -> None:
        tiles = parse_mpsz("19m0055p123s1122334455667z")
        assert multiset(parse_mpsz(dump_mpsz(tiles))) == multiset(tiles)


class TestMjai:
    def test_spec_example_names_same_collection(self) -> None:
        # The MJAI tokens name the same collection as the MPSZ form.
        assert multiset(parse_mjai("1m 2m 3m 5pr 5p 6p 6p C C C")) == multiset(parse_mpsz("123m0566p777z"))

    def test_honor_letters(self) -> None:
        assert parse_mjai("E S W N P F C") == [
            Tile(TileKind.EAST),
            Tile(TileKind.SOUTH),
            Tile(TileKind.WEST),
            Tile(TileKind.NORTH),
            Tile(TileKind.HAKU),
            Tile(TileKind.HATSU),
            Tile(TileKind.CHUN),
        ]

    def test_empty(self) -> None:
        assert parse_mjai("") == []
        assert parse_mjai("   ") == []
        assert dump_mjai([]) == ""

    def test_dump_preserves_order(self) -> None:
        tiles = [Tile(TileKind.CHUN), Tile(TileKind.M5, red=True), Tile(TileKind.M1)]
        assert dump_mjai(tiles) == "C 5mr 1m"

    def test_round_trip_exact(self) -> None:
        tiles = parse_mpsz("123m0566p777z")
        assert parse_mjai(dump_mjai(tiles)) == tiles

    @pytest.mark.parametrize("token", ["0m", "10m", "5z", "5mrr", "3mr", "x", "5m r"])
    def test_rejects(self, token: str) -> None:
        with pytest.raises(NotationError, match="token"):
            parse_mjai(token)


class Test136:
    def test_spec_examples(self) -> None:
        # The red five of pinzu is 52; the ordinary pinzu fives are 53-55;
        # the first East is 108.
        assert parse_136([52]) == [Tile(TileKind.P5, red=True)]
        assert parse_136([53, 54, 55]) == [Tile(TileKind.P5)] * 3
        assert parse_136([108]) == [Tile(TileKind.EAST)]
        assert parse_136([16]) == [Tile(TileKind.M5, red=True)]
        assert parse_136([88]) == [Tile(TileKind.S5, red=True)]

    def test_red_slot_is_context_free(self) -> None:
        assert parse_136([52])[0].red
        assert not parse_136([51])[0].red  # a 4p slot

    def test_blocks(self) -> None:
        assert parse_136([0]) == [Tile(TileKind.M1)]
        assert parse_136([35]) == [Tile(TileKind.M9)]
        assert parse_136([36]) == [Tile(TileKind.P1)]
        assert parse_136([72]) == [Tile(TileKind.S1)]
        assert parse_136([135]) == [Tile(TileKind.CHUN)]

    @pytest.mark.parametrize("values", [[136], [-1], [3.5], [5, 5]])
    def test_rejects(self, values: list[int]) -> None:
        with pytest.raises(NotationError):
            parse_136(values)

    def test_dump_assigns_lowest_free_slots(self) -> None:
        tiles = [Tile(TileKind.P5), Tile(TileKind.P5, red=True), Tile(TileKind.P5)]
        assert dump_136(tiles) == [53, 52, 54]

    def test_dump_rejects_four_ordinary_fives(self) -> None:
        # Only three ordinary-five slots exist per suit.
        with pytest.raises(NotationError, match="slot"):
            dump_136([Tile(TileKind.M5)] * 4)

    def test_dump_rejects_two_red_fives(self) -> None:
        with pytest.raises(NotationError, match="slot"):
            dump_136([Tile(TileKind.M5, red=True)] * 2)

    def test_dump_rejects_five_copies(self) -> None:
        with pytest.raises(NotationError, match="slot"):
            dump_136([Tile(TileKind.EAST)] * 5)

    def test_full_set_round_trip(self) -> None:
        tiles = full_tile_set()
        indices = dump_136(tiles)
        assert sorted(indices) == list(range(136))
        assert parse_136(indices) == tiles

    def test_round_trip_exact(self) -> None:
        tiles = parse_mpsz("19m0555p123s1122z")
        assert parse_136(dump_136(tiles)) == tiles
