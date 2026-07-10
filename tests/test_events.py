"""Tests for the event vocabulary and per-seat masking."""

from __future__ import annotations

from jansou.core.hand import Hand
from jansou.core.notation import parse_mpsz
from jansou.core.rules import Rules
from jansou.core.tiles import Tile, TileKind, Wind
from jansou.game.events import (
    DealStart,
    Discard,
    Draw,
    GameStart,
    Ryuukyoku,
    RyuukyokuKind,
    Win,
)
from jansou.scoring.context import WinContext
from jansou.scoring.score import score


class TestDrawMasking:
    def test_owner_sees_the_tile(self) -> None:
        event = Draw(seat=1, tile=Tile(TileKind.M5))
        assert event.mask_for(1) is event

    def test_others_see_no_tile(self) -> None:
        event = Draw(seat=1, tile=Tile(TileKind.M5), replacement=True)
        masked = event.mask_for(2)
        assert masked.tile is None
        assert masked.seat == 1
        assert masked.replacement is True


class TestDealStartMasking:
    def test_only_own_hand_is_visible(self) -> None:
        hands = tuple(tuple(parse_mpsz(h)) for h in ("123m456p", "789s111z", "222z333z", "444z555z"))
        event = DealStart(
            dealer=0,
            round_wind=Wind.EAST,
            round_number=1,
            honba=0,
            deposits=0,
            scores=(25000, 25000, 25000, 25000),
            hands=hands,
            dora_indicator=Tile(TileKind.M1),
        )
        masked = event.mask_for(2)
        assert masked.hands[2] == hands[2]
        assert masked.hands[0] is None
        assert masked.hands[1] is None
        assert masked.hands[3] is None
        assert masked.dora_indicator == Tile(TileKind.M1)


class TestPublicEvents:
    def test_discard_is_public(self) -> None:
        event = Discard(seat=0, tile=Tile(TileKind.P3), tsumogiri=True)
        assert event.mask_for(3) is event

    def test_game_start_is_public(self) -> None:
        event = GameStart(player_count=4, names=("a", "b", "c", "d"), starting_scores=(25000,) * 4)
        assert event.mask_for(1) is event

    def test_ryuukyoku_is_public(self) -> None:
        event = Ryuukyoku(kind=RyuukyokuKind.EXHAUSTIVE, counted_ready=frozenset({0, 2}))
        assert event.mask_for(0) is event


def test_win_carries_scoring_detail() -> None:
    hand = Hand(tuple(parse_mpsz("19m19p19s12345677z")))
    winning = parse_mpsz("1m")[0]
    result = score(hand, winning, WinContext(rules=Rules()))
    event = Win(seat=0, from_seat=None, winning_tile=winning, hand=hand, result=result)
    assert event.mask_for(1) is event
    assert event.result.is_yakuman
