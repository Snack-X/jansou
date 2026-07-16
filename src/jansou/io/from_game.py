"""Turning a played game's recorded events into the neutral `Paifu`.

An `Environment` run with recording keeps one event list per deal. This folds
those lists into the same `Paifu` the parsers produce, so a game the engine
played can be written back out to any of the log formats. The winning hand,
value, and revealed ura come straight from the recorded `Win`; the situational
context is left for the replay to derive from the event stream, exactly as it
is for a parsed log.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from jansou.core.hand import CallSource, Meld, MeldType
from jansou.core.rules import RIICHI_DEPOSIT
from jansou.game.events import (
    Call as GameCall,
)
from jansou.game.events import (
    DealStart,
    IndicatorReveal,
    NorthExtraction,
    RyuukyokuKind,
    ScoreChange,
)
from jansou.game.events import (
    Discard as GameDiscard,
)
from jansou.game.events import (
    Draw as GameDraw,
)
from jansou.game.events import (
    Ryuukyoku as GameRyuukyoku,
)
from jansou.game.events import (
    Win as GameWin,
)
from jansou.io.paifu import (
    Agari,
    Call,
    Discard,
    DoraReveal,
    Draw,
    Event,
    Kita,
    Paifu,
    RoundLog,
    Ryuukyoku,
)

if TYPE_CHECKING:
    from jansou.core.rules import Rules
    from jansou.core.tiles import Tile
    from jansou.game.environment import Environment

_SOURCE_OF_RELATIVE = {1: CallSource.KAMICHA, 2: CallSource.TOIMEN, 3: CallSource.SHIMOCHA}


def paifu_from_game(environment: Environment) -> Paifu:
    """Build the neutral game record for a finished game recorded by its environment.

    Args:
        environment: A finished environment run with recording enabled, carrying
            its per-deal event lists and rules.

    Returns:
        The neutral game record for the recorded game.
    """
    return paifu_from_records(environment.records, environment.rules)


def paifu_from_records(records: list[list[Event]], rules: Rules) -> Paifu:
    """Build the neutral game record from the per-deal event lists a recording environment kept.

    Args:
        records: One recorded event list per deal, in play order.
        rules: The rules configuration the game was played under.

    Returns:
        The neutral game record for the recorded deals.
    """
    rounds = [_round_from_events(deal, rules) for deal in records]
    return Paifu(rules=rules, player_count=rules.player_count, rounds=tuple(rounds))


def _round_from_events(events: list[Event], rules: Rules) -> RoundLog:
    """One `RoundLog` from a single deal's recorded events."""
    player_count = rules.player_count
    start = next(event for event in events if isinstance(event, DealStart))
    normalized: list[Event] = []
    last_discard: Tile | None = None
    wins: list[GameWin] = []
    draw: GameRyuukyoku | None = None
    settlement: ScoreChange | None = None
    for event in events:
        if isinstance(event, GameDraw):
            normalized.append(Draw(event.seat, event.tile))
        elif isinstance(event, GameDiscard):
            last_discard = event.tile
            normalized.append(Discard(event.seat, event.tile, riichi=event.riichi, tsumogiri=event.tsumogiri))
        elif isinstance(event, GameCall):
            normalized.append(Call(event.caller, _meld_of(event, last_discard, player_count)))
        elif isinstance(event, IndicatorReveal):
            normalized.append(DoraReveal(event.tile))
        elif isinstance(event, NorthExtraction):
            normalized.append(Kita(event.seat))
        elif isinstance(event, GameWin):
            wins.append(event)
        elif isinstance(event, GameRyuukyoku):
            draw = event
        elif isinstance(event, ScoreChange):
            settlement = event
    outcome = _outcome(wins, draw, settlement, start, rules)
    return RoundLog(
        round_wind=start.round_wind,
        dealer=start.dealer,
        honba=start.honba,
        riichi_sticks=start.deposits // RIICHI_DEPOSIT,  # the engine pools points; the record counts sticks
        initial_dora=start.dora_indicator,
        scores=tuple(start.scores),
        hands=tuple(tuple(hand) for hand in start.hands),  # recorded hands are unmasked
        events=tuple(normalized),
        outcome=outcome,
    )


def _meld_of(call: GameCall, last_discard: Tile | None, player_count: int) -> Meld:
    """The core meld a recorded call describes, its claimed tile and source restored."""
    tiles = tuple(call.tiles)
    if call.meld_type is MeldType.ANKAN:
        return Meld(MeldType.ANKAN, tiles)
    if call.meld_type is MeldType.SHOUMINKAN:
        # The engine appends the promoting tile last; its original source does not score.
        added = tiles[-1]
        return Meld(MeldType.SHOUMINKAN, tiles, called=added, source=CallSource.KAMICHA, added=added)
    source = _SOURCE_OF_RELATIVE[(call.caller - call.source) % player_count]
    return Meld(call.meld_type, tiles, called=last_discard, source=source)


def _outcome(
    wins: list[GameWin], draw: GameRyuukyoku | None, settlement: ScoreChange | None, start: DealStart, rules: Rules
) -> tuple[Agari, ...] | Ryuukyoku:
    """A round's wins or its draw, from the recorded terminal events."""
    if wins:
        return tuple(_agari_of(win, start, settlement, rules, first=index == 0) for index, win in enumerate(wins))
    deltas = tuple(settlement.deltas) if settlement is not None else ()
    kind = "exhaustive" if draw is None or draw.kind is RyuukyokuKind.EXHAUSTIVE else draw.kind.name.lower()
    tenpai = tuple(seat in draw.counted_ready for seat in range(rules.player_count)) if draw is not None else ()
    return Ryuukyoku(kind=kind, deltas=deltas, tenpai=tenpai)


def _agari_of(win: GameWin, start: DealStart, settlement: ScoreChange | None, rules: Rules, *, first: bool) -> Agari:
    """One `Agari` from a recorded win, with value and deltas that a reader recovers."""
    is_tsumo = win.from_seat is None
    payment = win.result.payment
    value = payment.total - payment.honba - payment.sticks
    honba = start.honba if first else 0
    pot = payment.sticks if first else 0  # the whole pool -- carried and banked this round -- rides the first winner
    single_win = settlement is not None and first and _is_only_win(settlement, win, rules.player_count)
    deltas = tuple(settlement.deltas) if single_win else _reconstructed_deltas(win, start.dealer, honba, rules, pot)
    return Agari(
        winner=win.seat,
        from_seat=win.seat if is_tsumo else win.from_seat,
        winning_tile=win.winning_tile,
        ura_indicators=tuple(win.ura_indicators),
        honba=honba,
        riichi_sticks=pot // RIICHI_DEPOSIT,
        deltas=deltas,
        fu=win.result.fu.total,
        value=value,
        hand=win.hand,
    )


def _is_only_win(settlement: ScoreChange, win: GameWin, player_count: int) -> bool:
    """Whether the recorded settlement moved points for just this one winner."""
    receivers = sum(1 for seat in range(player_count) if settlement.deltas[seat] > 0)
    return receivers == 1 and settlement.deltas[win.seat] > 0


def _reconstructed_deltas(win: GameWin, dealer: int, honba: int, rules: Rules, pot: int) -> tuple[int, ...]:
    """Per-winner deltas that reproduce the win value under a reader's honba rule.

    The pot -- the deposit points this win sweeps off the table -- joins the
    winner's gain with no paying seat, exactly as the engine settles it.
    """
    payment = win.result.payment
    deltas = [0] * rules.player_count
    if win.from_seat is None:
        for seat in range(rules.player_count):
            if seat == win.seat:
                continue
            share = payment.tsumo_dealer if seat == dealer else payment.tsumo_non_dealer
            pay = share + rules.honba_value * honba
            deltas[seat] -= pay
            deltas[win.seat] += pay
    else:
        total = payment.ron + rules.honba_per_counter * honba
        deltas[win.seat] += total
        deltas[win.from_seat] -= total
    deltas[win.seat] += pot
    return tuple(deltas)
