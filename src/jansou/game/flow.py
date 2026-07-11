"""Turn and call flow, and round resolution, for a single deal.

A deal advances as a repeating cycle: the turn holder draws and acts, a discard
opens a reaction window, reactions resolve by priority, and the turn passes or is
redirected by a call. This module runs that cycle to a resolution -- a win, an
exhaustive draw, or an abort -- applying payments, deposits, and the dealer
repeat rule along the way.

The flow is decoupled from agents: it reports through an emit callback and asks
for decisions by yielding them -- deal_steps is a generator that yields each
DecisionPoint and is resumed with the chosen action -- so the environment
supplies the agents and the recording while this module owns the rules.
play_deal wraps the generator for callers that prefer a decide callback.
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from dataclasses import dataclass
from enum import Enum, auto, unique
from typing import TYPE_CHECKING

from jansou.core.hand import Hand, Meld, MeldType
from jansou.core.tiles import Tile, TileKind
from jansou.game.actions import (
    Action,
    AddedKan,
    Chii,
    ClosedKan,
    NineTerminals,
    Nuki,
    OpenKan,
    Pon,
    Riichi,
    Ron,
    Tsumo,
    current_waits,
    discard_reactions,
    kuikae_banned_kinds,
    north_reactions,
    robbed_kan_reactions,
    self_actions,
    tenpai_declaration_options,
    win_context,
    win_result,
)
from jansou.game.events import (
    Call,
    DealStart,
    Draw,
    Event,
    GameEnd,
    IndicatorReveal,
    NorthExtraction,
    RiichiAccepted,
    Ryuukyoku,
    RyuukyokuKind,
    ScoreChange,
    Win,
)
from jansou.game.events import (
    Discard as DiscardEvent,
)
from jansou.game.state import Discard as DiscardMark
from jansou.game.state import GameState, Liability, PlayerState
from jansou.scoring.context import WinContext
from jansou.scoring.score import ScoringError, score
from jansou.scoring.yaku import Yaku

if TYPE_CHECKING:
    from jansou.core.rules import Rules
    from jansou.core.tiles import Wind
    from jansou.game.wall import Wall
    from jansou.scoring.score import ScoreResult

Decide = Callable[[int, "DecisionKind", list[Action]], Action]
Emit = Callable[[Event], None]

_RIICHI_DEPOSIT = 1000
_KAN_CAP = 4
_TRIPLE_RON = 3
_DOUBLE_RON = 2
_DAISANGEN_DRAGONS = 3
_DAISUUSHI_WINDS = 4
_MANGAN_BASE = 2000
_DEALER_SHARE = 2
_HONBA_DIVISOR = 3


@unique
class DecisionKind(Enum):
    """The five kinds of decision the environment requests from an agent."""

    SELF = auto()
    DISCARD_REACTION = auto()
    ROBBED_KAN = auto()
    NORTH_REACTION = auto()
    TENPAI = auto()


@unique
class _RobMode(Enum):
    """What tile a robbing window is offered against."""

    ADDED_KAN = auto()
    CLOSED_KAN = auto()
    NORTH = auto()


@dataclass(frozen=True)
class DecisionPoint:
    """One pending decision, yielded by the flow and answered with an action."""

    seat: int
    kind: DecisionKind
    actions: tuple[Action, ...]


@dataclass(frozen=True)
class Position:
    """A deal's table position: dealer, round wind and number, and honba."""

    dealer: int
    round_wind: Wind
    round_number: int
    honba: int


@dataclass
class DealOutcome:
    """The settled result of a deal, for the game loop to advance from."""

    winners: tuple[int, ...]
    dealer_repeats: bool
    is_draw: bool
    is_abortive: bool = False


@dataclass(frozen=True)
class _RonClaim:
    """The tile and source a ron is declared against."""

    from_seat: int
    tile: Tile
    chankan: bool = False


@dataclass
class _WinRecord:
    """One winner's scored result, gathered before payments are applied."""

    seat: int
    from_seat: int | None
    tile: Tile
    result: ScoreResult
    collects_pot: bool = True
    ura_indicators: tuple[Tile, ...] = ()


@dataclass
class _Terminal:
    """An internal signal that the deal has ended, carrying its resolution."""

    outcome: DealOutcome


@dataclass
class _Next:
    """How the next step proceeds: whether it draws, and whether off a kan."""

    draws: bool
    rinshan: bool


def new_deal(rules: Rules, wall: Wall, position: Position, scores: list[int], deposit_pool: int) -> GameState:
    """Deal a fresh hand from a wall and return the ready-to-play state.

    Args:
        rules: The rule set for the deal.
        wall: The wall to deal from.
        position: The table position for the deal.
        scores: Each seat's score entering the deal.
        deposit_pool: Riichi deposits carried on the table.

    Returns:
        The game state, dealt and ready for the dealer's first draw.
    """
    hands = wall.deal(rules.player_count)
    players = [PlayerState(concealed=list(hand)) for hand in hands]
    return GameState(
        rules=rules,
        scores=list(scores),
        wall=wall,
        dealer=position.dealer,
        round_wind=position.round_wind,
        round_number=position.round_number,
        honba=position.honba,
        deposit_pool=deposit_pool,
        players=players,
        current_player=position.dealer,
    )


def play_deal(state: GameState, decide: Decide, emit: Emit) -> DealOutcome:
    """Run a dealt state to its resolution, emitting events along the way.

    Args:
        state: The dealt, ready-to-play game state.
        decide: The callback asked for each agent decision.
        emit: The callback fed every event as it happens.

    Returns:
        The deal's settled outcome, for the game loop to advance from.
    """
    steps = deal_steps(state, emit)
    try:
        point = next(steps)
        while True:
            point = steps.send(decide(point.seat, point.kind, list(point.actions)))
    except StopIteration as stop:
        return stop.value


def deal_steps(state: GameState, emit: Emit) -> Generator[DecisionPoint, Action, DealOutcome]:
    """Run a dealt state step by step, yielding each decision to the caller.

    Args:
        state: The dealt, ready-to-play game state.
        emit: The callback fed every event as it happens.

    Yields:
        Each decision point; the generator must be resumed with the chosen
        action, which is trusted to be among the offered ones.

    Returns:
        The deal's settled outcome, as the generator's return value.
    """
    emit(_deal_start_event(state))
    return (yield from _run(state, emit))


def _deal_start_event(state: GameState) -> DealStart:
    return DealStart(
        dealer=state.dealer,
        round_wind=state.round_wind,
        round_number=state.round_number,
        honba=state.honba,
        deposits=state.deposit_pool,
        scores=tuple(state.scores),
        hands=tuple(tuple(player.concealed) for player in state.players),
        dora_indicator=state.wall.dora_indicators[0],
    )


def _run(state: GameState, emit: Emit) -> Generator[DecisionPoint, Action, DealOutcome]:
    """The turn cycle, from the dealer's first draw to a resolution."""
    draws = True
    rinshan = False
    while True:
        seat = state.current_player
        if draws:
            player = state.players[seat]
            player.temporary_furiten = False
            player.drawn = state.wall.draw_live()
            emit(Draw(seat, player.drawn, replacement=False))
            rinshan = False
        step = yield from _act(state, emit, rinshan=rinshan)
        if isinstance(step, _Terminal):
            return step.outcome
        draws, rinshan = step.draws, step.rinshan


def _act(state: GameState, emit: Emit, *, rinshan: bool) -> Generator[DecisionPoint, Action, _Terminal | _Next]:
    """Resolve one self-decision into a terminal or the next step's draw flags."""
    seat = state.current_player
    actions = self_actions(state, rinshan=rinshan)
    choice = yield DecisionPoint(seat, DecisionKind.SELF, tuple(actions))
    if isinstance(choice, Tsumo):
        return _win_by_tsumo(state, seat, emit, rinshan=rinshan)
    if isinstance(choice, NineTerminals):
        return _abortive_draw(RyuukyokuKind.NINE_TERMINALS, emit)
    if isinstance(choice, (ClosedKan, AddedKan, Nuki)):
        terminal = yield from _self_kan_or_nuki(state, seat, choice, emit)
        return terminal if terminal is not None else _Next(draws=False, rinshan=True)
    return (yield from _handle_discard(state, seat, choice, emit))


# --- Kans and North extraction ------------------------------------------------


def _self_kan_or_nuki(
    state: GameState, seat: int, choice: Action, emit: Emit
) -> Generator[DecisionPoint, Action, _Terminal | None]:
    """Apply a closed kan, added kan, or North, after its robbing window."""
    tile, mode = _rob_target(choice)
    terminal = yield from _robbing_window(state, seat, tile, mode, emit)
    if terminal is not None:
        return terminal
    _break_ippatsu(state)
    state.first_go_around = False
    if isinstance(choice, Nuki):
        _complete_nuki(state, seat, emit)
    else:
        _complete_kan(state, seat, choice, emit)
    return None


def _rob_target(choice: Action) -> tuple[Tile, _RobMode]:
    """The tile a robbing window is offered against, and its mode."""
    if isinstance(choice, AddedKan):
        return choice.tile, _RobMode.ADDED_KAN
    if isinstance(choice, ClosedKan):
        return Tile(choice.kind), _RobMode.CLOSED_KAN
    return Tile(TileKind.NORTH), _RobMode.NORTH


def _complete_kan(state: GameState, seat: int, choice: Action, emit: Emit) -> None:
    """Complete a closed or added kan: meld, reveal, and replacement draw."""
    player = state.players[seat]
    if isinstance(choice, AddedKan):
        meld = _upgrade_pon(player, choice.tile)
        emit(Call(MeldType.SHOUMINKAN, seat, seat, meld.tiles))
        immediate = state.rules.open_kan_indicator_immediate
    else:
        meld = _form_closed_kan(player, choice.kind)
        emit(Call(MeldType.ANKAN, seat, seat, meld.tiles))
        immediate = state.rules.closed_kan_indicator_immediate
    state.kans += 1
    _reveal_dora(state, emit, immediate=immediate)
    player.drawn = state.wall.draw_replacement()
    emit(Draw(seat, player.drawn, replacement=True))


def _form_closed_kan(player: PlayerState, kind: TileKind) -> Meld:
    """Move the four copies of a kind into a closed kan meld."""
    pool = [*player.concealed, player.drawn] if player.drawn is not None else list(player.concealed)
    kan_tiles = [tile for tile in pool if tile.kind is kind]
    for tile in kan_tiles:
        pool.remove(tile)
    player.concealed = pool
    player.drawn = None
    meld = Meld(MeldType.ANKAN, tuple(kan_tiles))
    player.melds.append(meld)
    return meld


def _upgrade_pon(player: PlayerState, added: Tile) -> Meld:
    """Turn a pon into an added kan by adding the fourth tile from hand."""
    _remove_tile(player, added)
    for index, meld in enumerate(player.melds):
        if meld.type is MeldType.PON and meld.tiles[0].kind is added.kind:
            upgraded = Meld(
                MeldType.SHOUMINKAN,
                (*meld.tiles, added),
                called=meld.called,
                source=meld.source,
                added=added,
            )
            player.melds[index] = upgraded
            return upgraded
    # An added kan is only ever offered over an existing pon, so this is unreachable.
    raise RuntimeError("no pon to add to")  # pragma: no cover


def _complete_nuki(state: GameState, seat: int, emit: Emit) -> None:
    """Set aside a North and take a replacement draw."""
    player = state.players[seat]
    _remove_tile(player, Tile(TileKind.NORTH))
    if player.drawn is not None:  # the North came from the concealed hand; retire the drawn tile
        player.concealed.append(player.drawn)
        player.drawn = None
    player.nuki_count += 1
    emit(NorthExtraction(seat, Tile(TileKind.NORTH)))
    player.drawn = state.wall.draw_replacement()
    emit(Draw(seat, player.drawn, replacement=True))


def _reveal_dora(state: GameState, emit: Emit, *, immediate: bool) -> None:
    """Reveal a new indicator now or defer it, per the kan-dora rules."""
    if not state.rules.kan_dora:
        return
    if immediate:
        emit(IndicatorReveal(state.wall.reveal_indicator()))
    else:
        state.deferred_reveal = True


# --- Discards and reactions ---------------------------------------------------


def _handle_discard(
    state: GameState, seat: int, choice: Action, emit: Emit
) -> Generator[DecisionPoint, Action, _Terminal | _Next]:
    """Place the discard, run its reaction window, and finalize the turn."""
    tile = choice.tile
    is_riichi = isinstance(choice, Riichi)
    tsumogiri = choice.tsumogiri
    _place_discard(state, seat, tile, tsumogiri=tsumogiri, riichi=is_riichi)
    emit(DiscardEvent(seat, tile, tsumogiri=tsumogiri, riichi=is_riichi))
    if is_riichi:
        state.pending_riichi = seat
    final = state.wall.live_draws_remaining == 0
    terminal = yield from _reaction_window(state, seat, tile, emit, final=final)
    if terminal is not None:
        return terminal
    if state.current_player != seat:  # a call redirected the turn to the claimant
        drew_replacement = state.players[state.current_player].drawn is not None
        return _Next(draws=False, rinshan=drew_replacement)
    finished = yield from _finalize(state, emit)
    if finished is not None:
        return finished
    state.current_player = state.next_seat(seat)
    return _Next(draws=True, rinshan=False)


def _place_discard(state: GameState, seat: int, tile: Tile, *, tsumogiri: bool, riichi: bool) -> None:
    """Remove the discarded tile from hand and record it in the pile."""
    player = state.players[seat]
    if player.ippatsu:
        player.ippatsu = False
    _remove_tile(player, tile)
    if player.drawn is not None:
        player.concealed.append(player.drawn)
        player.drawn = None
    player.discards.append(DiscardMark(tile=tile, tsumogiri=tsumogiri, riichi=riichi))
    player.temporary_furiten = False
    state.last_discard = (seat, tile)
    state.post_call_restriction = frozenset()


def _reaction_window(
    state: GameState, discarder: int, tile: Tile, emit: Emit, *, final: bool
) -> Generator[DecisionPoint, Action, _Terminal | None]:
    """Gather and resolve every opponent's reaction to the discard."""
    choices: dict[int, Action] = {}
    for seat in _opponents(state, discarder):
        actions = discard_reactions(state, seat, final=final)
        if not actions:
            continue
        choice = yield DecisionPoint(seat, DecisionKind.DISCARD_REACTION, tuple(actions))
        choices[seat] = choice
        if any(isinstance(action, Ron) for action in actions) and not isinstance(choice, Ron):
            _mark_passed_ron(state, seat)
    return _resolve_reactions(state, discarder, tile, choices, emit)


def _resolve_reactions(
    state: GameState, discarder: int, tile: Tile, choices: dict[int, Action], emit: Emit
) -> _Terminal | None:
    """Apply reaction priority: ron over call over chii; mark the tile if claimed."""
    ron_seats = [seat for seat, choice in choices.items() if isinstance(choice, Ron)]
    if ron_seats:
        return _resolve_ron(state, _RonClaim(discarder, tile), ron_seats, emit)
    for seat, choice in choices.items():
        if isinstance(choice, (Pon, OpenKan)):
            _apply_meld_call(state, seat, choice, emit)
            return None
    for seat, choice in choices.items():
        if isinstance(choice, Chii):
            _apply_meld_call(state, seat, choice, emit)
            return None
    return None


def _apply_meld_call(state: GameState, caller: int, choice: Action, emit: Emit) -> None:
    """Expose a claimed meld, redirect the turn, and record any liability."""
    discarder, tile = state.last_discard  # type: ignore[misc]
    state.players[discarder].discards[-1].called_away = True
    source = state.relative_source(discarder, caller)
    player = state.players[caller]
    if isinstance(choice, OpenKan):
        used = [held for held in player.concealed if held.kind is tile.kind][: _KAN_CAP - 1]
        meld = Meld(MeldType.DAIMINKAN, (*used, tile), called=tile, source=source)
    else:
        used = list(choice.tiles)
        meld_type = MeldType.PON if isinstance(choice, Pon) else MeldType.CHII
        meld = Meld(meld_type, tuple(sorted((*used, tile))), called=tile, source=source)
    for used_tile in used:
        player.concealed.remove(used_tile)
    player.melds.append(meld)
    emit(Call(meld.type, caller, discarder, meld.tiles))
    _break_ippatsu(state)
    state.first_go_around = False
    _record_liability(state, caller, discarder, meld)
    _redirect_after_call(state, caller, choice, tile, emit)


def _redirect_after_call(state: GameState, caller: int, choice: Action, tile: Tile, emit: Emit) -> None:
    """Make the caller the turn holder, with kan replacement or a call ban."""
    state.current_player = caller
    state.last_discard = None
    if isinstance(choice, OpenKan):
        state.kans += 1
        _reveal_dora(state, emit, immediate=state.rules.open_kan_indicator_immediate)
        state.players[caller].drawn = state.wall.draw_replacement()
        emit(Draw(caller, state.players[caller].drawn, replacement=True))
    elif state.rules.kuikae_ban:
        state.post_call_restriction = kuikae_banned_kinds(choice, tile)


# --- Robbing window -----------------------------------------------------------


def _robbing_window(
    state: GameState, actor: int, tile: Tile, mode: _RobMode, emit: Emit
) -> Generator[DecisionPoint, Action, _Terminal | None]:
    """Offer ron on a kan or North tile before the act completes."""
    ron_seats: list[int] = []
    for seat in _opponents(state, actor):
        actions = _rob_reactions(state, seat, tile, mode)
        if not actions:
            continue
        kind = DecisionKind.NORTH_REACTION if mode is _RobMode.NORTH else DecisionKind.ROBBED_KAN
        choice = yield DecisionPoint(seat, kind, tuple(actions))
        if isinstance(choice, Ron):
            ron_seats.append(seat)
        else:
            _mark_passed_ron(state, seat)
    if not ron_seats:
        return None
    claim = _RonClaim(actor, tile, chankan=mode is not _RobMode.NORTH)
    return _resolve_ron(state, claim, ron_seats, emit)


def _rob_reactions(state: GameState, seat: int, tile: Tile, mode: _RobMode) -> list[Action]:
    """The ron-or-pass reactions available against a robbed tile."""
    if mode is _RobMode.NORTH:
        return north_reactions(state, seat, tile)
    return robbed_kan_reactions(state, seat, tile, added_kan=mode is _RobMode.ADDED_KAN)


# --- Ron and tsumo resolution -------------------------------------------------


def _resolve_ron(state: GameState, claim: _RonClaim, ron_seats: list[int], emit: Emit) -> _Terminal:
    """Resolve one or more ron declarations into wins or a triple-ron abort."""
    if len(ron_seats) >= _TRIPLE_RON and state.rules.abort_sanchahou:
        return _abortive_draw(RyuukyokuKind.TRIPLE_RON, emit)
    ordered = sorted(ron_seats, key=lambda seat: (seat - claim.from_seat) % state.player_count)
    if len(ordered) == _DOUBLE_RON and not state.rules.multiple_ron:
        ordered = ordered[:1]
    _accept_pending_riichi(state, emit)
    records = [_score_ron(state, seat, claim, first=index == 0) for index, seat in enumerate(ordered)]
    _apply_wins(state, records, emit)
    winners = tuple(record.seat for record in records)
    return _Terminal(DealOutcome(winners, _dealer_in(state, winners), is_draw=False))


def _score_ron(state: GameState, seat: int, claim: _RonClaim, *, first: bool) -> _WinRecord:
    """Score one ron winner in its full context."""
    context = win_context(state, seat, is_tsumo=False, chankan=claim.chankan)
    result = win_result(state, seat, claim.tile, context)
    if result is None:  # pragma: no cover - the window only offered a winning ron
        raise RuntimeError("ron on a non-winning tile")
    return _WinRecord(
        seat, claim.from_seat, claim.tile, result, collects_pot=first, ura_indicators=context.ura_indicators
    )


def _win_by_tsumo(state: GameState, seat: int, emit: Emit, *, rinshan: bool) -> _Terminal:
    """Resolve a self-draw win."""
    _accept_pending_riichi(state, emit)
    player = state.players[seat]
    context = win_context(state, seat, is_tsumo=True, rinshan=rinshan)
    result = win_result(state, seat, player.drawn, context)
    if result is None:  # pragma: no cover - tsumo was only offered on a win
        raise RuntimeError("tsumo on a non-winning draw")
    record = _WinRecord(seat, None, player.drawn, result, ura_indicators=context.ura_indicators)
    _apply_wins(state, [record], emit)
    return _Terminal(DealOutcome((seat,), state.is_dealer(seat), is_draw=False))


# --- Payments -----------------------------------------------------------------


def _apply_wins(state: GameState, records: list[_WinRecord], emit: Emit) -> None:
    """Apply every winner's payment, collect the pot, and emit the results."""
    deltas = [0] * state.player_count
    for record in records:
        emit(
            Win(
                record.seat,
                record.from_seat,
                record.tile,
                _win_hand_of(state, record),
                record.result,
                record.ura_indicators,
            )
        )
        _score_one_win(state, record, deltas)
    for seat in range(state.player_count):
        state.scores[seat] += deltas[seat]
    emit(ScoreChange(tuple(deltas), tuple(state.scores)))


def _win_hand_of(state: GameState, record: _WinRecord) -> Hand:
    """The winning hand as it stood, with the winning tile included."""
    player = state.players[record.seat]
    if record.from_seat is None:
        return player.as_hand()
    return Hand((*player.concealed, record.tile), tuple(player.melds))


def _score_one_win(state: GameState, record: _WinRecord, deltas: list[int]) -> None:
    """Add one winner's deltas, honoring liability and the deposit pool."""
    pao = _find_liability(state, record)
    if record.from_seat is None:
        _tsumo_deltas(state, record, deltas, pao)
    else:
        _ron_deltas(state, record, deltas, pao)
    if record.collects_pot:
        deltas[record.seat] += state.deposit_pool
        state.deposit_pool = 0


def _tsumo_deltas(state: GameState, record: _WinRecord, deltas: list[int], pao: Liability | None) -> None:
    """Distribute a tsumo's payments across the payers (or the liable player)."""
    payment = record.result.payment
    honba_each = state.rules.honba_value // _HONBA_DIVISOR * state.honba
    gains = 0
    for seat in range(state.player_count):
        if seat == record.seat:
            continue
        share = payment.tsumo_dealer if state.is_dealer(seat) else payment.tsumo_non_dealer
        pay = share + honba_each
        deltas[pao.payer if pao is not None else seat] -= pay
        gains += pay
    deltas[record.seat] += gains


def _ron_deltas(state: GameState, record: _WinRecord, deltas: list[int], pao: Liability | None) -> None:
    """Distribute a ron's payment, splitting the base under liability."""
    payment = record.result.payment
    honba_total = state.rules.honba_value * state.honba if record.collects_pot else 0
    deltas[record.seat] += payment.ron + honba_total
    if pao is None:
        deltas[record.from_seat] -= payment.ron + honba_total  # type: ignore[index]
        return
    liable_share = payment.ron // 2
    deltas[pao.payer] -= liable_share
    deltas[record.from_seat] -= payment.ron - liable_share + honba_total  # type: ignore[index]


# --- Liability ----------------------------------------------------------------


def _record_liability(state: GameState, caller: int, source: int, meld: Meld) -> None:
    """Mark pao when a call locks in a big-three or big-four winds shape."""
    rules = state.rules
    kind = meld.tiles[0].kind
    player = state.players[caller]
    if rules.pao_daisangen and kind.is_dragon and _dragon_melds(player) == _DAISANGEN_DRAGONS:
        state.liabilities.append(Liability(caller, source, Yaku.DAISANGEN))
    elif rules.pao_daisuushi and kind.is_wind and _wind_melds(player) == _DAISUUSHI_WINDS:
        state.liabilities.append(Liability(caller, source, Yaku.DAISUUSHI))
    elif rules.pao_suukantsu and meld.is_kan and _kan_melds(player) == _KAN_CAP:
        state.liabilities.append(Liability(caller, source, Yaku.SUUKANTSU))


def _find_liability(state: GameState, record: _WinRecord) -> Liability | None:
    """The liability that answers for this winning hand, if any."""
    if not record.result.is_yakuman:
        return None
    shapes = {value.yaku for value in record.result.yaku}
    for mark in state.liabilities:
        if mark.beneficiary == record.seat and mark.shape in shapes:
            return mark
    return None


def _dragon_melds(player: PlayerState) -> int:
    return sum(1 for meld in player.melds if meld.tiles[0].kind.is_dragon and meld.type is not MeldType.CHII)


def _wind_melds(player: PlayerState) -> int:
    return sum(1 for meld in player.melds if meld.tiles[0].kind.is_wind and meld.type is not MeldType.CHII)


def _kan_melds(player: PlayerState) -> int:
    return sum(1 for meld in player.melds if meld.is_kan)


# --- Finalization and draws ---------------------------------------------------


def _finalize(state: GameState, emit: Emit) -> Generator[DecisionPoint, Action, _Terminal | None]:
    """Bank a pending riichi, reveal deferred dora, then check aborts and walls.

    Only reached when no call redirected the turn; the caller advances the seat.
    """
    _accept_pending_riichi(state, emit)
    if state.deferred_reveal:
        emit(IndicatorReveal(state.wall.reveal_indicator()))
        state.deferred_reveal = False
    abort = _pending_abort(state)
    if abort is not None:
        return _abortive_draw(abort, emit)
    if state.wall.live_draws_remaining == 0:
        return (yield from _exhaustive_draw(state, emit))
    return None


def _accept_pending_riichi(state: GameState, emit: Emit) -> None:
    """Bank a surviving riichi's deposit and open its ippatsu window."""
    if state.pending_riichi is None:
        return
    seat = state.pending_riichi
    state.pending_riichi = None
    player = state.players[seat]
    if state.first_go_around and len(player.discards) == 1:
        player.double_riichi = True
    else:
        player.riichi = True
    player.ippatsu = state.rules.ippatsu
    state.scores[seat] -= _RIICHI_DEPOSIT
    state.deposit_pool += _RIICHI_DEPOSIT
    state.riichi_declarations += 1
    emit(RiichiAccepted(seat))


def _pending_abort(state: GameState) -> RyuukyokuKind | None:
    """Any accumulated-state abort that fires at finalization."""
    rules = state.rules
    if rules.abort_suukaikan and state.kans == _KAN_CAP and _distinct_kan_makers(state) >= _DOUBLE_RON:
        return RyuukyokuKind.FOUR_KANS
    if rules.abort_suucha_riichi and state.riichi_declarations == state.player_count:
        return RyuukyokuKind.FOUR_RIICHI
    if rules.abort_suufon_renda and _four_winds_discarded(state):
        return RyuukyokuKind.FOUR_WINDS
    return None


def _distinct_kan_makers(state: GameState) -> int:
    return sum(1 for player in state.players if _kan_melds(player) > 0)


def _four_winds_discarded(state: GameState) -> bool:
    """Whether every player's uninterrupted first discard was the same wind."""
    if state.player_count != _KAN_CAP or not state.first_go_around:
        return False
    firsts = [player.discards[0].tile.kind for player in state.players if player.discards]
    if len(firsts) != state.player_count:
        return False
    return all(kind.is_wind and kind is firsts[0] for kind in firsts)


def _abortive_draw(kind: RyuukyokuKind, emit: Emit) -> _Terminal:
    """End the deal with no payments; the dealer always repeats."""
    emit(Ryuukyoku(kind=kind))
    return _Terminal(DealOutcome((), dealer_repeats=True, is_draw=True, is_abortive=True))


def _exhaustive_draw(state: GameState, emit: Emit) -> Generator[DecisionPoint, Action, _Terminal]:
    """End with the wall spent: tenpai payments or nagashi, then the repeat."""
    ready = yield from _counted_ready(state)
    state.counted_ready = ready
    nagashi = _nagashi_seats(state)
    deltas = [0] * state.player_count
    if nagashi:
        _nagashi_deltas(state, nagashi, deltas)
    else:
        _noten_deltas(state, ready, deltas)
    for seat in range(state.player_count):
        state.scores[seat] += deltas[seat]
    revealed = tuple((seat, state.players[seat].as_hand(include_drawn=False)) for seat in sorted(ready))
    emit(Ryuukyoku(kind=RyuukyokuKind.EXHAUSTIVE, revealed=revealed, counted_ready=ready))
    emit(ScoreChange(tuple(deltas), tuple(state.scores)))
    return _Terminal(DealOutcome((), _dealer_repeats_on_draw(state, ready), is_draw=True))


def _counted_ready(state: GameState) -> Generator[DecisionPoint, Action, frozenset[int]]:
    """The players who count as ready for payments and the repeat."""
    ready: set[int] = set()
    for seat in range(state.player_count):
        if not _is_yaku_ready(state, seat):
            continue
        if state.rules.tenpai_declaration:
            choice = yield DecisionPoint(seat, DecisionKind.TENPAI, tuple(tenpai_declaration_options()))
            if not getattr(choice, "declare", False):
                continue
        ready.add(seat)
    return frozenset(ready)


def _is_yaku_ready(state: GameState, seat: int) -> bool:
    """Whether a hand's waits include a completion that could score."""
    waits = current_waits(state, seat)
    if not waits:
        return False
    if state.rules.formal_tenpai:
        return True
    return any(_claim_scores(state, seat, kind) for kind in waits)


def _claim_scores(state: GameState, seat: int, kind: TileKind) -> bool:
    """Whether a hypothetical claimed ron on a waited kind carries a yaku."""
    player = state.players[seat]
    hand = Hand((*player.concealed, Tile(kind)), tuple(player.melds))
    context = WinContext(
        rules=state.rules,
        round_wind=state.round_wind,
        seat_wind=state.seat_wind(seat),
        is_tsumo=False,
        riichi=player.riichi,
        double_riichi=player.double_riichi,
    )
    try:
        score(hand, Tile(kind), context)
    except ScoringError:
        return False
    return True


def _nagashi_seats(state: GameState) -> list[int]:
    """Players whose every discard is a terminal or honor, none called away."""
    if not state.rules.nagashi_mangan:
        return []
    return [
        seat
        for seat, player in enumerate(state.players)
        if player.discards and all(mark.tile.is_yaochuu and not mark.called_away for mark in player.discards)
    ]


def _nagashi_deltas(state: GameState, nagashi: list[int], deltas: list[int]) -> None:
    """Pay each nagashi player as a mangan self-draw, without honba."""
    for winner in nagashi:
        gains = 0
        for seat in range(state.player_count):
            if seat == winner:
                continue
            dealer_rate = state.is_dealer(seat) or state.is_dealer(winner)
            share = _MANGAN_BASE * _DEALER_SHARE if dealer_rate else _MANGAN_BASE
            deltas[seat] -= share
            gains += share
        deltas[winner] += gains


def _noten_deltas(state: GameState, ready: frozenset[int], deltas: list[int]) -> None:
    """Move the configured pool from the not-ready to the ready, split evenly."""
    pool = state.rules.noten_penalty_pool
    not_ready = [seat for seat in range(state.player_count) if seat not in ready]
    if not ready or not not_ready:
        return
    receive = pool // len(ready)
    pay = pool // len(not_ready)
    for seat in ready:
        deltas[seat] += receive
    for seat in not_ready:
        deltas[seat] -= pay


def _dealer_repeats_on_draw(state: GameState, ready: frozenset[int]) -> bool:
    """Whether the dealer keeps the deal after an exhaustive draw."""
    if not state.rules.dealer_repeat_on_tenpai:
        return False
    return state.dealer in ready


# --- Small shared helpers -----------------------------------------------------


def _opponents(state: GameState, seat: int) -> list[int]:
    """The other seats, in turn order starting after the given seat."""
    return [(seat + offset) % state.player_count for offset in range(1, state.player_count)]


def _remove_tile(player: PlayerState, tile: Tile) -> None:
    """Remove one copy of a tile from the drawn tile or the concealed hand."""
    if player.drawn is not None and player.drawn == tile:
        player.drawn = None
        return
    player.concealed.remove(tile)


def _break_ippatsu(state: GameState) -> None:
    """Clear every player's ippatsu window."""
    for player in state.players:
        player.ippatsu = False


def _mark_passed_ron(state: GameState, seat: int) -> None:
    """Apply furiten to a player who passed a tile they could have ronned."""
    player = state.players[seat]
    player.temporary_furiten = True
    if player.is_riichi:
        player.riichi_furiten = True


def _dealer_in(state: GameState, winners: tuple[int, ...]) -> bool:
    """Whether any winner is the dealer (the dealer then repeats)."""
    return state.dealer in winners


def emit_game_end(emit: Emit, final_scores: tuple[int, ...], ranking: tuple[int, ...]) -> None:
    """Emit the game-end event; used by the environment at game end.

    Args:
        emit: The callback fed the event.
        final_scores: Each seat's final score.
        ranking: The seats in ranking order, best first.
    """
    emit(GameEnd(final_scores=final_scores, ranking=ranking))
