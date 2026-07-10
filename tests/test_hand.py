"""Tests for hand representation: melds, concealed part, validity."""

from __future__ import annotations

import pytest

from jansou.core.hand import CallSource, Hand, InvalidHandError, Meld, MeldType
from jansou.core.notation import parse_mpsz
from jansou.core.rules import Rules
from jansou.core.tiles import Tile, TileKind


def chii_123m() -> Meld:
    return Meld(
        MeldType.CHII,
        tuple(parse_mpsz("123m")),
        called=Tile(TileKind.M3),
        source=CallSource.KAMICHA,
    )


def pon_east(source: CallSource = CallSource.TOIMEN) -> Meld:
    return Meld(MeldType.PON, tuple(parse_mpsz("111z")), called=Tile(TileKind.EAST), source=source)


def ankan_9s() -> Meld:
    return Meld(MeldType.ANKAN, tuple(parse_mpsz("9999s")))


class TestMeld:
    def test_valid_melds(self) -> None:
        assert chii_123m().is_open
        assert pon_east().is_open
        assert not ankan_9s().is_open
        assert ankan_9s().is_kan
        assert not pon_east().is_kan

    def test_daiminkan(self) -> None:
        meld = Meld(
            MeldType.DAIMINKAN,
            tuple(parse_mpsz("5555p")),
            called=Tile(TileKind.P5),
            source=CallSource.SHIMOCHA,
        )
        assert meld.is_open
        assert meld.is_kan

    def test_shouminkan_records_added_tile(self) -> None:
        red = Tile(TileKind.P5, red=True)
        meld = Meld(
            MeldType.SHOUMINKAN,
            (Tile(TileKind.P5), Tile(TileKind.P5), Tile(TileKind.P5), red),
            called=Tile(TileKind.P5),
            source=CallSource.KAMICHA,
            added=red,
        )
        assert meld.added == red

    def test_chii_only_from_kamicha(self) -> None:
        with pytest.raises(InvalidHandError, match="kamicha"):
            Meld(MeldType.CHII, tuple(parse_mpsz("123m")), called=Tile(TileKind.M3), source=CallSource.TOIMEN)

    def test_chii_must_be_consecutive_one_suit(self) -> None:
        with pytest.raises(InvalidHandError, match="consecutive"):
            Meld(MeldType.CHII, tuple(parse_mpsz("124m")), called=Tile(TileKind.M4), source=CallSource.KAMICHA)
        with pytest.raises(InvalidHandError, match="one suit"):
            Meld(MeldType.CHII, tuple(parse_mpsz("12m3p")), called=Tile(TileKind.P3), source=CallSource.KAMICHA)
        with pytest.raises(InvalidHandError, match="one suit"):
            Meld(MeldType.CHII, tuple(parse_mpsz("123z")), called=Tile(TileKind.WEST), source=CallSource.KAMICHA)

    def test_pon_must_be_identical(self) -> None:
        with pytest.raises(InvalidHandError, match="identical"):
            Meld(MeldType.PON, tuple(parse_mpsz("112z")), called=Tile(TileKind.EAST), source=CallSource.TOIMEN)

    def test_pon_red_five_variants_are_one_kind(self) -> None:
        tiles = (Tile(TileKind.S5, red=True), Tile(TileKind.S5), Tile(TileKind.S5))
        meld = Meld(MeldType.PON, tiles, called=Tile(TileKind.S5), source=CallSource.SHIMOCHA)
        assert meld.tiles == tiles

    def test_wrong_sizes(self) -> None:
        with pytest.raises(InvalidHandError, match="tiles"):
            Meld(MeldType.PON, tuple(parse_mpsz("1111z")), called=Tile(TileKind.EAST), source=CallSource.TOIMEN)
        with pytest.raises(InvalidHandError, match="tiles"):
            Meld(MeldType.ANKAN, tuple(parse_mpsz("999s")))

    def test_claim_bookkeeping(self) -> None:
        # A call must distinguish its claimed tile and source.
        with pytest.raises(InvalidHandError, match="claimed"):
            Meld(MeldType.PON, tuple(parse_mpsz("111z")), source=CallSource.TOIMEN)
        with pytest.raises(InvalidHandError, match="claimed"):
            Meld(MeldType.PON, tuple(parse_mpsz("111z")), called=Tile(TileKind.SOUTH), source=CallSource.TOIMEN)
        with pytest.raises(InvalidHandError, match="opponent"):
            Meld(MeldType.PON, tuple(parse_mpsz("111z")), called=Tile(TileKind.EAST))
        # A closed kan has neither.
        with pytest.raises(InvalidHandError, match="closed kan"):
            Meld(MeldType.ANKAN, tuple(parse_mpsz("9999s")), called=Tile(TileKind.S9))
        # An added kan must distinguish the added tile; others must not carry one.
        with pytest.raises(InvalidHandError, match="added"):
            Meld(MeldType.SHOUMINKAN, tuple(parse_mpsz("5555p")), called=Tile(TileKind.P5), source=CallSource.TOIMEN)
        with pytest.raises(InvalidHandError, match="added"):
            Meld(
                MeldType.PON,
                tuple(parse_mpsz("111z")),
                called=Tile(TileKind.EAST),
                source=CallSource.TOIMEN,
                added=Tile(TileKind.EAST),
            )

    def test_claimed_tile_matched_exactly(self) -> None:
        # Exact equality: a red claimed tile must be among the tiles as red.
        tiles = (Tile(TileKind.S5), Tile(TileKind.S5), Tile(TileKind.S5))
        with pytest.raises(InvalidHandError, match="claimed"):
            Meld(MeldType.PON, tiles, called=Tile(TileKind.S5, red=True), source=CallSource.TOIMEN)


class TestHand:
    def test_full_concealed_hand_valid(self) -> None:
        hand = Hand(tuple(parse_mpsz("123456789m11p77z")))
        hand.validate()
        assert hand.is_valid()
        assert hand.is_concealed
        assert not hand.has_melds
        assert hand.rest_size == 13

    def test_holding_fourteenth_tile_valid(self) -> None:
        assert Hand(tuple(parse_mpsz("123456789m112p77z"))).is_valid()

    def test_wrong_sizes_rejected(self) -> None:
        with pytest.raises(InvalidHandError, match="concealed"):
            Hand(tuple(parse_mpsz("123m"))).validate()
        with pytest.raises(InvalidHandError, match="concealed"):
            Hand(tuple(parse_mpsz("123456789m1122p77z"))).validate()  # 15 tiles

    def test_meld_sizes(self) -> None:
        hand = Hand(tuple(parse_mpsz("456m456p77z55s")), (chii_123m(),))
        hand.validate()
        assert hand.rest_size == 10
        assert not hand.is_concealed
        assert hand.has_melds

    def test_kan_does_not_change_concealed_count(self) -> None:
        # A kan fills one slot; its fourth tile is balanced by the replacement draw.
        hand = Hand(tuple(parse_mpsz("123m456p77z55s")), (ankan_9s(),))
        hand.validate()
        assert hand.is_concealed  # a closed kan does not break concealment

    def test_four_melds_leave_one_tile(self) -> None:
        melds = (
            chii_123m(),
            pon_east(),
            ankan_9s(),
            Meld(MeldType.PON, tuple(parse_mpsz("777z")), called=Tile(TileKind.CHUN), source=CallSource.SHIMOCHA),
        )
        hand = Hand(tuple(parse_mpsz("4p")), melds)
        hand.validate()
        assert hand.rest_size == 1

    def test_more_than_four_melds_rejected(self) -> None:
        melds = (
            chii_123m(),
            pon_east(),
            ankan_9s(),
            Meld(MeldType.PON, tuple(parse_mpsz("777z")), called=Tile(TileKind.CHUN), source=CallSource.SHIMOCHA),
            Meld(MeldType.PON, tuple(parse_mpsz("666z")), called=Tile(TileKind.HATSU), source=CallSource.TOIMEN),
        )
        with pytest.raises(InvalidHandError, match="melds"):
            Hand((), melds).validate()

    def test_is_valid_is_false_on_an_invalid_hand(self) -> None:
        assert not Hand(tuple(parse_mpsz("123m"))).is_valid()

    def test_fifth_copy_rejected_across_melds(self) -> None:
        # Pon of East plus two concealed Easts plus ... five copies total.
        hand = Hand(tuple(parse_mpsz("1122z123456m")), (pon_east(),))
        with pytest.raises(InvalidHandError, match="EAST"):
            hand.validate()

    def test_two_red_fives_of_a_suit_rejected(self) -> None:
        tiles = (*parse_mpsz("00p"), *parse_mpsz("123456789m11z"))
        with pytest.raises(InvalidHandError, match="red"):
            Hand(tiles).validate()

    def test_red_five_rejected_when_rule_off(self) -> None:
        hand = Hand(tuple(parse_mpsz("0p123456789m112z")))
        hand.validate()  # fine by default
        with pytest.raises(InvalidHandError, match="red"):
            hand.validate(Rules(aka_dora=False))

    def test_all_tiles_spans_melds(self) -> None:
        hand = Hand(tuple(parse_mpsz("123m456p77z55s")), (ankan_9s(),))
        assert len(hand.all_tiles) == 10 + 4
