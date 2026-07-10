"""Tests for game and per-player state."""

from __future__ import annotations

from jansou.core.hand import CallSource, Hand, Meld, MeldType
from jansou.core.notation import parse_mpsz
from jansou.core.rules import Rules
from jansou.core.tiles import Tile, TileKind, Wind, full_tile_set
from jansou.game.state import Discard, GameState, Liability, PlayerState
from jansou.game.wall import Wall
from jansou.scoring.yaku import Yaku


def make_state(player_count: int = 4, dealer: int = 0) -> GameState:
    wall = Wall(tuple(full_tile_set(player_count, aka_dora=False)))
    return GameState(
        rules=Rules(player_count=player_count),
        scores=[25000] * player_count,
        wall=wall,
        dealer=dealer,
        round_wind=Wind.EAST,
        round_number=1,
        honba=0,
        deposit_pool=0,
        players=[PlayerState() for _ in range(player_count)],
        current_player=dealer,
    )


class TestDiscard:
    def test_defaults(self) -> None:
        discard = Discard(tile=Tile(TileKind.M1))
        assert discard.tsumogiri is False
        assert discard.riichi is False
        assert discard.called_away is False


def test_liability_holds_a_shape() -> None:
    mark = Liability(beneficiary=0, payer=2, shape=Yaku.DAISANGEN)
    assert mark.shape is Yaku.DAISANGEN


class TestPlayerState:
    def test_concealed_with_closed_kan_stays_concealed(self) -> None:
        ankan = Meld(MeldType.ANKAN, tuple(parse_mpsz("1111m")))
        player = PlayerState(melds=[ankan])
        assert player.is_concealed

    def test_open_meld_breaks_concealment(self) -> None:
        pon = Meld(MeldType.PON, tuple(parse_mpsz("222m")), called=Tile(TileKind.M2), source=CallSource.TOIMEN)
        player = PlayerState(melds=[pon])
        assert not player.is_concealed

    def test_is_riichi(self) -> None:
        assert PlayerState(riichi=True).is_riichi
        assert PlayerState(double_riichi=True).is_riichi
        assert not PlayerState().is_riichi

    def test_as_hand_includes_drawn(self) -> None:
        player = PlayerState(concealed=list(parse_mpsz("123m456p789s11z22z")), drawn=Tile(TileKind.M4))
        assert player.as_hand() == Hand((*parse_mpsz("123m456p789s11z22z"), Tile(TileKind.M4)))

    def test_as_hand_can_exclude_drawn(self) -> None:
        player = PlayerState(concealed=list(parse_mpsz("123m")), drawn=Tile(TileKind.M4))
        assert player.as_hand(include_drawn=False) == Hand(tuple(parse_mpsz("123m")))


class TestGameState:
    def test_player_count(self) -> None:
        assert make_state().player_count == 4
        assert make_state(player_count=3).player_count == 3

    def test_seat_winds_rotate_with_the_dealer(self) -> None:
        state = make_state(dealer=1)
        assert state.seat_wind(1) is Wind.EAST
        assert state.seat_wind(2) is Wind.SOUTH
        assert state.seat_wind(3) is Wind.WEST
        assert state.seat_wind(0) is Wind.NORTH

    def test_is_dealer(self) -> None:
        state = make_state(dealer=2)
        assert state.is_dealer(2)
        assert not state.is_dealer(0)

    def test_next_seat_wraps(self) -> None:
        state = make_state()
        assert state.next_seat(3) == 0
        assert state.next_seat(1) == 2

    def test_relative_source_four_player(self) -> None:
        state = make_state()
        assert state.relative_source(discarder=3, caller=0) is CallSource.KAMICHA
        assert state.relative_source(discarder=1, caller=0) is CallSource.SHIMOCHA
        assert state.relative_source(discarder=2, caller=0) is CallSource.TOIMEN

    def test_relative_source_three_player_has_no_toimen(self) -> None:
        state = make_state(player_count=3)
        assert state.relative_source(discarder=2, caller=0) is CallSource.KAMICHA
        assert state.relative_source(discarder=1, caller=0) is CallSource.SHIMOCHA
