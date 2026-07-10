"""Tests for game progression: honba, rotation, ending conditions, ranking."""

from __future__ import annotations

from jansou.core.rules import Rules
from jansou.core.tiles import Wind
from jansou.game.flow import DealOutcome, Position
from jansou.game.progression import (
    advance,
    next_honba,
    rank,
    settle_deposits,
    starting_position,
)


def outcome(*, winners: tuple[int, ...] = (), dealer_repeats: bool = False, is_draw: bool = False) -> DealOutcome:
    return DealOutcome(winners=winners, dealer_repeats=dealer_repeats, is_draw=is_draw)


def pos(dealer: int, wind: Wind, number: int, honba: int = 0) -> Position:
    return Position(dealer=dealer, round_wind=wind, round_number=number, honba=honba)


class TestBasics:
    def test_starting_position(self) -> None:
        start = starting_position()
        assert (start.dealer, start.round_wind, start.round_number, start.honba) == (0, Wind.EAST, 1, 0)

    def test_next_honba(self) -> None:
        assert next_honba(0, outcome(dealer_repeats=True)) == 1
        assert next_honba(2, outcome(is_draw=True)) == 3
        assert next_honba(4, outcome(winners=(1,))) == 0  # a non-dealer win clears it

    def test_rank_orders_and_breaks_ties(self) -> None:
        assert rank([25000, 40000, 10000, 25000]) == (1, 0, 3, 2)  # ties to the earlier seat

    def test_settle_deposits(self) -> None:
        assert settle_deposits([25000, 40000, 10000, 25000], 2000, Rules(leftover_deposits_to_first=True))[1] == 42000
        assert settle_deposits([25000, 40000, 10000, 25000], 2000, Rules(leftover_deposits_to_first=False))[1] == 40000


class TestAdvance:
    def test_dealer_repeat(self) -> None:
        step = advance(
            pos(0, Wind.EAST, 1), outcome(winners=(0,), dealer_repeats=True), [25000] * 4, Rules(), in_extension=False
        )
        assert step.position == pos(0, Wind.EAST, 1, honba=1)

    def test_rotation_within_a_wind(self) -> None:
        step = advance(pos(0, Wind.EAST, 1), outcome(winners=(1,)), [25000] * 4, Rules(), in_extension=False)
        assert step.position == pos(1, Wind.EAST, 2)

    def test_rotation_advances_the_wind(self) -> None:
        step = advance(pos(3, Wind.EAST, 4), outcome(is_draw=True), [25000] * 4, Rules(), in_extension=False)
        assert step.position == pos(0, Wind.SOUTH, 1, honba=1)

    def test_tobi_ends_the_game(self) -> None:
        rules = Rules(allow_negative_scores=False)
        step = advance(
            pos(0, Wind.EAST, 1), outcome(winners=(1,)), [40000, 40000, 30000, -10000], rules, in_extension=False
        )
        assert step.position is None

    def test_scheduled_end_when_someone_reached_target(self) -> None:
        step = advance(
            pos(3, Wind.SOUTH, 4), outcome(winners=(2,)), [40000, 20000, 30000, 10000], Rules(), in_extension=False
        )
        assert step.position is None

    def test_extension_entry_when_none_reached_target(self) -> None:
        step = advance(
            pos(3, Wind.SOUTH, 4), outcome(is_draw=True), [26000, 25000, 25000, 24000], Rules(), in_extension=False
        )
        assert step.in_extension
        assert step.position == pos(0, Wind.WEST, 1, honba=1)

    def test_north_final_wind_has_no_extension(self) -> None:
        rules = Rules(game_length=Wind.NORTH)
        step = advance(
            pos(3, Wind.NORTH, 4), outcome(is_draw=True), [26000, 25000, 25000, 24000], rules, in_extension=False
        )
        assert step.position is None

    def test_extension_ends_at_target(self) -> None:
        step = advance(
            pos(0, Wind.WEST, 1), outcome(is_draw=True), [31000, 24000, 24000, 21000], Rules(), in_extension=True
        )
        assert step.position is None

    def test_extension_continues_below_target(self) -> None:
        step = advance(
            pos(0, Wind.WEST, 1), outcome(is_draw=True), [26000, 25000, 25000, 24000], Rules(), in_extension=True
        )
        assert step.position == pos(1, Wind.WEST, 2, honba=1)
        assert step.in_extension

    def test_extension_final_rotation_ends(self) -> None:
        step = advance(
            pos(3, Wind.WEST, 4), outcome(is_draw=True), [26000, 25000, 25000, 24000], Rules(), in_extension=True
        )
        assert step.position is None


class TestAgariYame:
    def test_dealer_win_stops_the_final_round(self) -> None:
        step = advance(
            pos(0, Wind.SOUTH, 4),
            outcome(winners=(0,), dealer_repeats=True),
            [40000, 20000, 20000, 20000],
            Rules(),
            in_extension=False,
        )
        assert step.position is None

    def test_no_stop_when_dealer_not_first(self) -> None:
        step = advance(
            pos(0, Wind.SOUTH, 4),
            outcome(winners=(0,), dealer_repeats=True),
            [20000, 40000, 20000, 20000],
            Rules(),
            in_extension=False,
        )
        assert step.position == pos(0, Wind.SOUTH, 4, honba=1)

    def test_no_stop_below_target_under_extension(self) -> None:
        # Sudden death on: the stop is suppressed until someone reaches the target.
        step = advance(
            pos(0, Wind.SOUTH, 4),
            outcome(winners=(0,), dealer_repeats=True),
            [26000, 25000, 25000, 24000],
            Rules(),
            in_extension=False,
        )
        assert step.position == pos(0, Wind.SOUTH, 4, honba=1)

    def test_stop_off_by_flag(self) -> None:
        rules = Rules(agari_yame=False, sudden_death=False)
        step = advance(
            pos(0, Wind.SOUTH, 4),
            outcome(winners=(0,), dealer_repeats=True),
            [40000, 20000, 20000, 20000],
            rules,
            in_extension=False,
        )
        assert step.position == pos(0, Wind.SOUTH, 4, honba=1)
