"""Tests for wait enumeration and winning-tile validation."""

from __future__ import annotations

from jansou.analysis.shanten import shanten
from jansou.analysis.waits import completes, waits, waits_counts
from jansou.core.hand import CallSource, Hand, Meld, MeldType
from jansou.core.notation import parse_mpsz
from jansou.core.tiles import SANMA_REMOVED_KINDS, YAOCHUU_KINDS, Tile, TileKind, counts_by_kind


def kinds(mpsz: str) -> set[TileKind]:
    return {tile.kind for tile in parse_mpsz(mpsz)}


class TestWaitEnumeration:
    def test_multi_wait_example(self) -> None:
        # 34555m 567p 789s 22s waits on 2m and 5m (ryanmen beside 555m) and
        # 2s (the 5m/2s dual pair reading).
        hand = Hand(tuple(parse_mpsz("34555m567p789s22s")))
        assert waits(hand) == {TileKind.M2, TileKind.M5, TileKind.S2}

    def test_kokushi_thirteen_way(self) -> None:
        hand = Hand(tuple(parse_mpsz("19m19p19s1234567z")))
        assert waits(hand) == set(YAOCHUU_KINDS)

    def test_seven_pairs_single_wait(self) -> None:
        hand = Hand(tuple(parse_mpsz("1188m2299p3355s6z")))
        assert waits(hand) == {TileKind.HATSU}

    def test_not_ready_has_no_waits(self) -> None:
        hand = Hand(tuple(parse_mpsz("123m456m789m13p68s")))  # two kanchan, no pair
        assert shanten(hand) > 0
        assert waits(hand) == set()

    def test_holding_size_hand_has_no_waits(self) -> None:
        # Waits are a ready-size question; a holding-size shape has none.
        counts = counts_by_kind(parse_mpsz("123m456m789m234p55s"))
        assert waits_counts(list(counts), ()) == set()

    def test_called_hand_waits(self) -> None:
        chii = Meld(MeldType.CHII, tuple(parse_mpsz("123m")), called=Tile(TileKind.M1), source=CallSource.KAMICHA)
        hand = Hand(tuple(parse_mpsz("456m789m234p5p")), (chii,))
        # 234p5p accepts 5p (55p pair, 234p set) and 2p (22p pair, 345p set).
        assert waits(hand) == {TileKind.P2, TileKind.P5}


class TestKaraten:
    def test_four_copy_tanki_is_not_ready(self) -> None:
        # 1111p held: the tanki on 1p waits on a fifth copy that does not
        # exist, so the hand is not ready at all — and has no waits.
        hand = Hand(tuple(parse_mpsz("123m456m789m1111p")))
        assert shanten(hand) == 1
        assert waits(hand) == set()

    def test_exhausted_kind_is_dropped_from_the_waits(self) -> None:
        # All four 2m concealed: only the live tanki is a wait, and a fifth
        # 2m (which would complete on paper) is never probed.
        hand = Hand(tuple(parse_mpsz("2222m34m567p789s5s")))
        assert shanten(hand) == 0
        assert waits(hand) == {TileKind.S5}

    def test_empty_waits_when_own_kans_hold_all_copies(self) -> None:
        # Closed kans exhaust both sides of the ryanmen: shanten cannot see
        # meld-held copies, so the hand is ready yet waits on nothing.
        kans = tuple(Meld(MeldType.ANKAN, tuple(parse_mpsz(tiles))) for tiles in ("2222s", "5555s"))
        hand = Hand(tuple(parse_mpsz("34s111z88p")), kans)
        assert shanten(hand) == 0  # ready by shape
        assert waits(hand) == set()  # yet karaten


class TestWinningTileValidation:
    def test_completes_matches_waits(self) -> None:
        concealed = parse_mpsz("34555m567p789s22s")
        assert completes(concealed, (), Tile(TileKind.M2))
        assert completes(concealed, (), Tile(TileKind.M5, red=True))  # red marking irrelevant
        assert not completes(concealed, (), Tile(TileKind.M3))


class TestMemoization:
    def test_repeated_calls_return_equal_but_independent_sets(self) -> None:
        # The wait set is cached per hand shape; a caller mutating its copy
        # must not poison the next caller's answer.
        counts = counts_by_kind(parse_mpsz("34555m567p789s22s"))
        first = waits_counts(list(counts), ())
        first.clear()
        assert waits_counts(list(counts), ()) == kinds("25m2s")


class TestSanma:
    def test_only_in_play_kinds_are_waits(self) -> None:
        # A sanma kokushi shape waits on the missing 9m and never on absent
        # middle manzu.
        hand = Hand(tuple(parse_mpsz("11m19p19s1234567z")))
        result = waits(hand, player_count=3)
        assert result == {TileKind.M9}
        assert result.isdisjoint(SANMA_REMOVED_KINDS)
