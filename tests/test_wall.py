"""Tests for the wall's positional mapping."""

from __future__ import annotations

import pytest

from jansou.core.tiles import full_tile_set
from jansou.game.wall import DEAD_WALL_SIZE, Wall, WallError


def yonma_sequence() -> tuple:
    return tuple(full_tile_set(4, aka_dora=False))


def sanma_sequence() -> tuple:
    return tuple(full_tile_set(3, aka_dora=False))


class TestDeal:
    def test_four_player_deal_shape_and_order(self) -> None:
        seq = yonma_sequence()
        wall = Wall(seq)
        hands = wall.deal(4)
        assert len(hands) == 4
        assert all(len(hand) == 13 for hand in hands)
        # Seat 0 takes the first four (s15-s18, 0-based 14-17), then the next
        # round's four (0-based 30-33), then 46-49, then the single s63 (62).
        assert hands[0][:4] == seq[14:18]
        assert hands[0][4:8] == seq[30:34]
        assert hands[0][12] == seq[62]
        assert hands[1][:4] == seq[18:22]

    def test_live_draws_after_deal(self) -> None:
        wall = Wall(yonma_sequence())
        wall.deal(4)
        assert wall.live_draws_remaining == 70  # 136 - 14 - 52

    def test_sanma_live_draws_after_deal(self) -> None:
        wall = Wall(sanma_sequence())
        wall.deal(3)
        assert wall.live_draws_remaining == 55  # 108 - 14 - 39


class TestDraws:
    def test_live_draw_takes_the_front(self) -> None:
        seq = yonma_sequence()
        wall = Wall(seq)
        wall.deal(4)
        assert wall.draw_live() == seq[66]
        assert wall.draw_live() == seq[67]
        assert wall.live_draws_remaining == 68

    def test_replacement_from_dead_wall(self) -> None:
        seq = yonma_sequence()
        wall = Wall(seq)
        wall.deal(4)
        assert wall.draw_replacement() == seq[0]
        assert wall.draw_replacement() == seq[1]
        assert wall.replacements_taken == 2
        assert wall.live_draws_remaining == 68  # each replacement cuts one tail tile

    def test_fifth_and_later_replacements_take_the_cut_tail(self) -> None:
        seq = sanma_sequence()
        wall = Wall(seq)
        wall.deal(3)
        for _ in range(4):
            wall.draw_replacement()
        # The fifth draws sN, the sixth sN-1, descending.
        assert wall.draw_replacement() == seq[len(seq) - 1]
        assert wall.draw_replacement() == seq[len(seq) - 2]

    def test_replacement_needs_a_live_tile(self) -> None:
        wall = Wall(yonma_sequence())
        wall.deal(4)
        for _ in range(70):
            wall.draw_live()
        assert wall.live_draws_remaining == 0
        with pytest.raises(WallError, match="undrawn live tile"):
            wall.draw_replacement()

    def test_live_wall_exhaustion(self) -> None:
        wall = Wall(yonma_sequence())
        wall.deal(4)
        for _ in range(70):
            wall.draw_live()
        with pytest.raises(WallError, match="exhausted"):
            wall.draw_live()


class TestIndicators:
    def test_initial_indicators(self) -> None:
        seq = yonma_sequence()
        wall = Wall(seq)
        assert wall.indicators_revealed == 1
        assert wall.dora_indicators == (seq[4],)
        assert wall.ura_indicators == (seq[5],)

    def test_reveal_advances_in_order(self) -> None:
        seq = yonma_sequence()
        wall = Wall(seq)
        assert wall.reveal_indicator() == seq[6]
        assert wall.dora_indicators == (seq[4], seq[6])
        assert wall.ura_indicators == (seq[5], seq[7])

    def test_reveal_capped_at_five(self) -> None:
        wall = Wall(yonma_sequence())
        for _ in range(4):
            wall.reveal_indicator()
        assert wall.indicators_revealed == 5
        with pytest.raises(WallError, match="already revealed"):
            wall.reveal_indicator()


def test_sequence_too_short() -> None:
    with pytest.raises(WallError, match="at least"):
        Wall(tuple(full_tile_set(4)[: DEAD_WALL_SIZE - 1]))


def test_sequence_is_exposed() -> None:
    seq = yonma_sequence()
    assert Wall(seq).sequence == seq
