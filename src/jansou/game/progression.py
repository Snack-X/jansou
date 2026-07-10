"""Game progression: honba, dealer rotation, ending conditions, and ranking.

The deal-level flow (flow) settles one deal; this module is the outer loop's
arithmetic. Given a deal's outcome and the table position, it decides the next
position or that the game has ended, and at the end it settles leftover deposits
and ranks the players.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from jansou.core.tiles import Wind
from jansou.game.flow import Position

if TYPE_CHECKING:
    from jansou.core.rules import Rules
    from jansou.game.flow import DealOutcome


@dataclass(frozen=True)
class Advance:
    """The next position, or game over, after a deal."""

    position: Position | None  # None means the game has ended
    in_extension: bool


def starting_position() -> Position:
    """East 1, seat 0 as dealer, no honba.

    Returns:
        The table position the first deal starts from.
    """
    return Position(dealer=0, round_wind=Wind.EAST, round_number=1, honba=0)


def next_honba(honba: int, outcome: DealOutcome) -> int:
    """The honba count carried into the next deal (§18.7).

    Args:
        honba: The current deal's honba count.
        outcome: The deal's settled outcome.

    Returns:
        The next honba: one more on a dealer repeat or a draw, otherwise zero.
    """
    if outcome.dealer_repeats or outcome.is_draw:
        return honba + 1
    return 0


def rank(scores: list[int]) -> tuple[int, ...]:
    """Seats ordered best score first, ties broken by the earlier seat.

    Args:
        scores: Each seat's score.

    Returns:
        The seats in ranking order, best first.
    """
    return tuple(sorted(range(len(scores)), key=lambda seat: (-scores[seat], seat)))


def settle_deposits(scores: list[int], pool: int, rules: Rules) -> list[int]:
    """Award or discard deposits left on the table at game end (§19.4).

    Args:
        scores: Each seat's final score.
        pool: The leftover deposit pool.
        rules: The rule set deciding whether leftovers go to first place.

    Returns:
        The scores after settling any leftover deposits.
    """
    settled = list(scores)
    if pool and rules.leftover_deposits_to_first:
        settled[rank(settled)[0]] += pool
    return settled


def advance(
    position: Position, outcome: DealOutcome, scores: list[int], rules: Rules, *, in_extension: bool
) -> Advance:
    """The next position after a deal, or game over (§19.3).

    Args:
        position: The deal's table position.
        outcome: The deal's settled outcome.
        scores: Each seat's score after the deal.
        rules: The rule set governing rotation and ending.
        in_extension: Whether the game is already in an extension (sudden-death) round.

    Returns:
        The next position, or an ``Advance`` whose ``position`` is ``None`` at game over.
    """
    honba = next_honba(position.honba, outcome)
    if not rules.allow_negative_scores and any(score < 0 for score in scores):
        return Advance(None, in_extension=in_extension)
    dealer_won = bool(outcome.winners) and position.dealer in outcome.winners
    if outcome.dealer_repeats:
        if _agari_yame_ends(position, scores, rules, dealer_won=dealer_won, in_extension=in_extension):
            return Advance(None, in_extension=in_extension)
        repeated = Position(position.dealer, position.round_wind, position.round_number, honba)
        return Advance(repeated, in_extension=in_extension)
    return _rotate(position, honba, scores, rules, in_extension=in_extension)


def _holds_first(seat: int, scores: list[int]) -> bool:
    """Whether the seat ranks first, ties resolved to the earlier seat."""
    return rank(scores)[0] == seat


def _final_wind_last(position: Position, rules: Rules) -> bool:
    """Whether the position is the last scheduled deal of the final wind."""
    return position.round_wind is rules.game_length and position.round_number == rules.player_count


def _agari_yame_ends(
    position: Position, scores: list[int], rules: Rules, *, dealer_won: bool, in_extension: bool
) -> bool:
    """Whether a dealer win stops the game in the final or an extension round."""
    if not (rules.agari_yame and dealer_won):
        return False
    if not (in_extension or _final_wind_last(position, rules)):
        return False
    if not _holds_first(position.dealer, scores):
        return False
    return not (rules.sudden_death and max(scores) < rules.sudden_death_target)


def _rotate(position: Position, honba: int, scores: list[int], rules: Rules, *, in_extension: bool) -> Advance:
    """Rotate the dealership, judging scheduled end and extension entry."""
    dealer = (position.dealer + 1) % rules.player_count
    if in_extension:
        return _rotate_in_extension(position, dealer, honba, scores, rules)
    if _final_wind_last(position, rules):
        if rules.sudden_death and max(scores) < rules.sudden_death_target and rules.game_length is not Wind.NORTH:
            wind = Wind(rules.game_length + 1)
            return Advance(Position(dealer, wind, 1, honba), in_extension=True)
        return Advance(None, in_extension=False)
    wind, number = _advanced_round(position, rules.player_count)
    return Advance(Position(dealer, wind, number, honba), in_extension=False)


def _rotate_in_extension(position: Position, dealer: int, honba: int, scores: list[int], rules: Rules) -> Advance:
    """Within the extension wind, end at the target or the wind's final rotation."""
    if max(scores) >= rules.sudden_death_target or position.round_number == rules.player_count:
        return Advance(None, in_extension=True)
    return Advance(Position(dealer, position.round_wind, position.round_number + 1, honba), in_extension=True)


def _advanced_round(position: Position, player_count: int) -> tuple[Wind, int]:
    """The wind and number after a non-final rotation."""
    number = position.round_number + 1
    if number > player_count:
        return Wind(position.round_wind + 1), 1
    return position.round_wind, number
