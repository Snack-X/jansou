"""Replaying a parsed record through the engine as a live decision stream.

`replay_paifu` drives each round of a `Paifu` through the same flow that runs
live play, so every decision point carries exactly the menu a live game would
offer -- deal mechanics, call windows, swap-calling restrictions, riichi locks
-- paired with the action the logged player actually took, resolved to a
member of that menu. Reaction windows are complete: seats that could have
called or won but did not appear with an explicit pass. The wall is rebuilt
from the round's record through the wall's positional mapping, every engine
event is checked against the record as it is emitted, and any divergence
raises `ReplayError` naming the round and event, so a dirty round can be
skipped and counted without crashing a batch.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from jansou.core.hand import FULL_HAND_SIZE, MeldType
from jansou.core.rules import RIICHI_DEPOSIT
from jansou.core.tiles import Tile, full_tile_set
from jansou.game.actions import (
    Action,
    AddedKan,
    Chii,
    ClosedKan,
    DeclareTenpai,
    NineTerminals,
    Nuki,
    OpenKan,
    Pass,
    Pon,
    Riichi,
    Ron,
    Tsumo,
)
from jansou.game.actions import (
    Discard as DiscardAction,
)
from jansou.game.environment import DecisionRequest
from jansou.game.events import (
    Call as GameCall,
)
from jansou.game.events import (
    Discard as GameDiscard,
)
from jansou.game.events import (
    Draw as GameDraw,
)
from jansou.game.events import (
    IndicatorReveal,
    NorthExtraction,
)
from jansou.game.flow import DecisionKind, Position, deal_steps, new_deal
from jansou.game.wall import DEAD_WALL_SIZE, Wall
from jansou.io.paifu import Call, Discard, DoraReveal, Draw, Kita, Ryuukyoku, canonical_kind

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from jansou.core.hand import Meld
    from jansou.core.rules import Rules
    from jansou.game.events import Event as GameEvent
    from jansou.game.flow import DecisionPoint
    from jansou.io.paifu import Event, Paifu, RoundLog

#: The wall's documented dead-wall mapping: replacement draws sit at s1..s4,
#: dora indicators at s5, s7, s9, s11, s13, and ura indicators beneath them.
_DEAD_WALL_REPLACEMENTS = 4
_DORA_SLOTS = (4, 6, 8, 10, 12)
_URA_SLOTS = (5, 7, 9, 11, 13)
_DEAL_PATTERN = (4, 4, 4, 1)

_CLAIM_TYPES = (MeldType.PON, MeldType.CHII, MeldType.DAIMINKAN)


class ReplayError(ValueError):
    """A recorded round the engine cannot faithfully replay.

    Attributes:
        round_index: The index of the round that failed.
        event_index: The index of the offending event within the round's
            events, or ``None`` when the failure is not tied to one event.
    """

    def __init__(self, message: str, round_index: int, event_index: int | None = None) -> None:
        """Build the error with its location prefixed to the message.

        Args:
            message: What made the round unreplayable.
            round_index: The index of the round that failed.
            event_index: The offending event's index, when tied to one event.
        """
        where = f"round {round_index}" if event_index is None else f"round {round_index}, event {event_index}"
        super().__init__(f"{where}: {message}")
        self.round_index = round_index
        self.event_index = event_index


@dataclass(frozen=True)
class ReplayedDecision:
    """One replayed decision: the live-play request and the logged choice.

    Attributes:
        round_index: The index of the round within the game record.
        request: The decision as live play would pose it, its events masked
            for the deciding seat.
        chosen: The action the record holds, a member of ``request.actions``.
    """

    round_index: int
    request: DecisionRequest
    chosen: Action


def replay_paifu(paifu: Paifu, *, observe: Callable[[GameEvent], None] | None = None) -> Iterator[ReplayedDecision]:
    """Replay a parsed game, yielding the live-play decision experience.

    Args:
        paifu: The parsed game to replay, under its own rules.
        observe: An optional callback fed every unmasked engine event as it
            is emitted; mask per seat with ``Event.mask_for``, as for the
            environment's observers.

    Yields:
        Every decision of every round in game order, each carrying the
        request live play would pose and the recorded choice.

    Raises:
        ReplayError: If a round's record diverges from the engine's replay.
    """
    for round_index in range(len(paifu.rounds)):
        yield from replay_round_decisions(paifu, round_index, observe=observe)


def replay_round_decisions(
    paifu: Paifu, round_index: int, *, observe: Callable[[GameEvent], None] | None = None
) -> Iterator[ReplayedDecision]:
    """Replay a single round of a parsed game as its decision stream.

    The per-round entry point behind ``replay_paifu``, for consumers that
    skip and count unreplayable rounds instead of dropping the whole game.

    Args:
        paifu: The parsed game the round belongs to.
        round_index: The index of the round to replay.
        observe: An optional callback fed every unmasked engine event.

    Yields:
        The round's decisions in play order.

    Raises:
        ReplayError: If the round's record diverges from the engine's replay.
    """
    round_log = paifu.rounds[round_index]
    try:
        yield from _replay_round(paifu.rules, round_log, round_index, observe)
    except ReplayError:
        raise
    except Exception as error:
        raise ReplayError(str(error), round_index) from error


def _replay_round(
    rules: Rules, round_log: RoundLog, round_index: int, observe: Callable[[GameEvent], None] | None
) -> Iterator[ReplayedDecision]:
    """Drive one round's record through the live flow, yielding its decisions."""
    wall = Wall(_WallBuilder(rules, round_log, round_index).sequence())
    position = Position(
        dealer=round_log.dealer,
        round_wind=round_log.round_wind,
        round_number=round_log.dealer + 1,
        honba=round_log.honba,
    )
    state = new_deal(rules, wall, position, list(round_log.scores), round_log.riichi_sticks * RIICHI_DEPOSIT)
    script = _Script(round_log, round_index)
    pending: list[GameEvent] = []

    def emit(event: GameEvent) -> None:
        script.check(event)
        pending.append(event)
        if observe is not None:
            observe(event)

    steps = deal_steps(state, emit)
    point = next(steps)
    while True:
        events = tuple(event.mask_for(point.seat) for event in pending)
        pending.clear()
        chosen = script.resolve(point)
        yield ReplayedDecision(round_index, DecisionRequest(point.seat, point.kind, point.actions, events), chosen)
        try:
            point = steps.send(chosen)
        except StopIteration:
            break
    script.assert_finished()


# --- Wall reconstruction ------------------------------------------------------


class _WallBuilder:
    """Rebuilds a wall sequence that deals and draws exactly as a record did.

    Every tile the record shows -- hands, draws, replacement draws, indicators,
    ura -- is placed at the slot the wall's positional mapping assigns it; the
    slots the round never touched are filled with the unused remainder of the
    tile set, which no replayed decision can depend on.
    """

    def __init__(self, rules: Rules, round_log: RoundLog, round_index: int) -> None:
        self._rules = rules
        self._round = round_log
        self._round_index = round_index
        self._slots: list[Tile | None] = [None] * len(full_tile_set(rules.player_count, aka_dora=False))

    def sequence(self) -> tuple[Tile, ...]:
        """The full wall sequence the record implies."""
        self._place_deal()
        self._place_indicators()
        self._place_draws()
        return self._fill()

    def _place(self, index: int, tile: Tile, event_index: int | None = None) -> None:
        """Pin a recorded tile to its wall slot."""
        if not 0 <= index < len(self._slots) or self._slots[index] is not None:
            raise ReplayError("the record's tiles do not fit the wall", self._round_index, event_index)
        self._slots[index] = tile

    def _place_deal(self) -> None:
        """Lay the dealt hands over the live wall's front in the deal pattern."""
        if any(len(hand) != FULL_HAND_SIZE for hand in self._round.hands):
            raise ReplayError("a dealt hand does not hold thirteen tiles", self._round_index)
        cursor = DEAD_WALL_SIZE
        taken = [0] * len(self._round.hands)
        for count in _DEAL_PATTERN:
            for seat, hand in enumerate(self._round.hands):
                for _ in range(count):
                    self._place(cursor, hand[taken[seat]])
                    taken[seat] += 1
                    cursor += 1

    def _place_indicators(self) -> None:
        """Pin the revealed dora indicators and any recorded ura beneath them."""
        reveals = [event.indicator for event in self._round.events if isinstance(event, DoraReveal)]
        if len(reveals) >= len(_DORA_SLOTS):
            raise ReplayError("the record reveals more dora indicators than the wall holds", self._round_index)
        self._place(_DORA_SLOTS[0], self._round.initial_dora)
        for offset, indicator in enumerate(reveals):
            self._place(_DORA_SLOTS[offset + 1], indicator)
        for offset, indicator in enumerate(self._recorded_ura()):
            self._place(_URA_SLOTS[offset], indicator)

    def _recorded_ura(self) -> tuple[Tile, ...]:
        """The ura indicators the outcome revealed, when any win shows them."""
        if isinstance(self._round.outcome, Ryuukyoku):
            return ()
        return max((agari.ura_indicators for agari in self._round.outcome), key=len, default=())

    def _place_draws(self) -> None:
        """Pin every recorded draw: live ones in order, replacements to the dead wall."""
        live = DEAD_WALL_SIZE + FULL_HAND_SIZE * len(self._round.hands)
        replacements = 0
        replacement_pending = False
        for event_index, event in enumerate(self._round.events):
            if isinstance(event, Draw):
                if replacement_pending:
                    replacements += 1
                    self._place(self._replacement_slot(replacements), event.tile, event_index)
                    replacement_pending = False
                else:
                    self._place(live, event.tile, event_index)
                    live += 1
            elif isinstance(event, Kita) or (isinstance(event, Call) and event.meld.is_kan):
                replacement_pending = True

    def _replacement_slot(self, count: int) -> int:
        """The slot of the count-th replacement: the dead wall, then the cut tail."""
        if count <= _DEAD_WALL_REPLACEMENTS:
            return count - 1
        return len(self._slots) - count + _DEAD_WALL_REPLACEMENTS

    def _fill(self) -> tuple[Tile, ...]:
        """The finished sequence, unused slots filled from the remaining set."""
        pool = Counter(
            (tile.kind, tile.red) for tile in full_tile_set(self._rules.player_count, aka_dora=self._rules.aka_dora)
        )
        for tile in self._slots:
            if tile is None:
                continue
            key = (tile.kind, tile.red)
            if not pool[key]:
                raise ReplayError(f"the record uses more copies of {tile!r} than the set holds", self._round_index)
            pool[key] -= 1
        remaining = (Tile(kind, red=red) for kind, red in pool.elements())
        return tuple(slot if slot is not None else next(remaining) for slot in self._slots)


# --- The recorded script ------------------------------------------------------


class _Script:
    """A cursor over one round's record, driving and checking the replay.

    The engine's emitted events are matched against the recorded ones as play
    advances, and each decision's recorded choice is read from the events (or
    the outcome) just past the cursor.
    """

    def __init__(self, round_log: RoundLog, round_index: int) -> None:
        self._round_index = round_index
        self._outcome = round_log.outcome
        indexed = list(enumerate(round_log.events))
        self._steps = [(index, event) for index, event in indexed if not isinstance(event, DoraReveal)]
        self._reveals = [(index, event) for index, event in indexed if isinstance(event, DoraReveal)]
        self._cursor = 0
        self._revealed = 0
        self._last_discard: Tile | None = None

    # -- Engine-event verification --

    def check(self, event: GameEvent) -> None:
        """Match one emitted engine event against the record, advancing past it."""
        if isinstance(event, GameDraw):
            index, logged = self._take("a draw")
            matches = isinstance(logged, Draw) and logged.seat == event.seat and logged.tile == event.tile
        elif isinstance(event, GameDiscard):
            index, logged = self._take("a discard")
            matches = (
                isinstance(logged, Discard)
                and logged.seat == event.seat
                and logged.tile == event.tile
                and logged.riichi == event.riichi
                and logged.tsumogiri == event.tsumogiri
            )
            self._last_discard = event.tile
        elif isinstance(event, GameCall):
            index, logged = self._take("a call")
            matches = (
                isinstance(logged, Call)
                and logged.seat == event.caller
                and logged.meld.type is event.meld_type
                and sorted(logged.meld.tiles) == sorted(event.tiles)
            )
        elif isinstance(event, NorthExtraction):
            index, logged = self._take("a North extraction")
            matches = isinstance(logged, Kita) and logged.seat == event.seat
        elif isinstance(event, IndicatorReveal):
            self._check_reveal(event)
            return
        else:
            return  # deal starts, riichi acceptances, wins, draws, and settlements have no record counterpart
        if not matches:
            raise self._error(f"the engine produced {event!r} where the record holds {logged!r}", index)

    def _check_reveal(self, event: IndicatorReveal) -> None:
        """Count a revealed indicator off against the record's reveals.

        The tiles cannot disagree: the wall was rebuilt from these same
        recorded reveals, so only the reveal counts can diverge.
        """
        _ = event
        if self._revealed >= len(self._reveals):
            raise self._error("the engine revealed a dora indicator the record does not have")
        self._revealed += 1

    def assert_finished(self) -> None:
        """Require every recorded event to have been replayed."""
        if self._cursor < len(self._steps):
            index, logged = self._steps[self._cursor]
            raise self._error(f"the round ended with {logged!r} still unplayed", index)
        if self._revealed < len(self._reveals):
            index, reveal = self._reveals[self._revealed]
            raise self._error(f"the round ended with {reveal!r} still unplayed", index)

    # -- Decision resolution --

    def resolve(self, point: DecisionPoint) -> Action:
        """The recorded choice at a decision point, as a member of its menu."""
        if point.kind is DecisionKind.SELF:
            wanted = self._self_action(point.seat)
        elif point.kind is DecisionKind.DISCARD_REACTION:
            wanted = self._reaction(point.seat)
        elif point.kind is DecisionKind.TENPAI:
            wanted = self._tenpai(point.seat)
        else:  # the robbed-kan and North windows share the robbing shape
            wanted = self._rob_reaction(point.seat)
        candidates = wanted if isinstance(wanted, tuple) else (wanted,)
        for candidate in candidates:
            for action in point.actions:
                if action == candidate:
                    return action
        index, _ = self._peek()
        raise self._error(f"the recorded choice {candidates!r} is not among the offered actions", index)

    def _self_action(self, seat: int) -> Action | tuple[Action, ...]:
        """The turn holder's recorded action: a discard, a kan, a North, or the end."""
        index, logged = self._peek()
        if logged is None:
            return self._self_ending(seat)
        if isinstance(logged, Discard) and logged.seat == seat:
            option = Riichi if logged.riichi else DiscardAction
            return option(logged.tile, tsumogiri=logged.tsumogiri)
        if isinstance(logged, Call) and logged.seat == seat and logged.meld.type is MeldType.ANKAN:
            return ClosedKan(logged.meld.tiles[0].kind)
        if isinstance(logged, Call) and logged.seat == seat and logged.meld.type is MeldType.SHOUMINKAN:
            return AddedKan(logged.meld.added)  # type: ignore[arg-type]
        if isinstance(logged, Kita) and logged.seat == seat:
            return Nuki()
        raise self._error(f"seat {seat} is to act but the record continues with {logged!r}", index)

    def _self_ending(self, seat: int) -> Action | tuple[Action, ...]:
        """The action behind a record whose events end on this seat's turn."""
        if not isinstance(self._outcome, Ryuukyoku):
            if any(agari.winner == seat and agari.is_tsumo for agari in self._outcome):
                return Tsumo()
            if any(not agari.is_tsumo for agari in self._outcome):
                # A robbed kan or North some records never complete: the engine's
                # own records stop at the attempt, so rebuild it from the robbed
                # tile and let the menu pick the shape.
                tile = self._outcome[0].winning_tile
                return (AddedKan(tile), ClosedKan(tile.kind), Nuki())
        elif canonical_kind(self._outcome.kind) == "yao9":
            return NineTerminals()
        raise self._error(f"the record ends with seat {seat} still to act")

    def _reaction(self, seat: int) -> Action:
        """A seat's recorded reaction to the current discard: claim, win, or pass."""
        _, logged = self._peek()
        if logged is None:
            # A triple-ron abort names no winners, but every offered reaction
            # was one of the three ron declarations that caused it.
            triple = isinstance(self._outcome, Ryuukyoku) and canonical_kind(self._outcome.kind) == "ron3"
            return Ron() if self._won_by_ron(seat) or triple else Pass()
        claims = (
            isinstance(logged, Call)
            and logged.seat == seat
            and logged.meld.type in _CLAIM_TYPES
            and logged.meld.called == self._last_discard
        )
        if not claims:
            return Pass()
        return self._claim_action(logged.meld)  # type: ignore[union-attr]

    def _claim_action(self, meld: Meld) -> Action:
        """The menu action a recorded claim corresponds to."""
        if meld.type is MeldType.DAIMINKAN:
            return OpenKan()
        from_hand = list(meld.tiles)
        from_hand.remove(meld.called)  # type: ignore[arg-type]
        first, second = sorted(from_hand)
        return Pon((first, second)) if meld.type is MeldType.PON else Chii((first, second))

    def _rob_reaction(self, seat: int) -> Action:
        """A seat's recorded answer to a robbable kan or North, before it completes."""
        _, logged = self._peek()
        if logged is None:
            # A first robber already consumed the kan or North event.
            return Ron() if self._won_by_ron(seat) else Pass()
        _, following = self._peek(1)
        if following is None and self._won_by_ron(seat):
            self._cursor += 1  # the robbed kan or North is never completed or emitted
            return Ron()
        return Pass()

    def _tenpai(self, seat: int) -> Action:
        """A seat's recorded readiness declaration at the exhaustive draw."""
        if not isinstance(self._outcome, Ryuukyoku):
            raise self._error("a readiness declaration is requested but the record did not end in a draw")
        declared = seat < len(self._outcome.tenpai) and self._outcome.tenpai[seat]
        return DeclareTenpai(declare=bool(declared))

    def _won_by_ron(self, seat: int) -> bool:
        """Whether the outcome records this seat winning on another's tile."""
        if isinstance(self._outcome, Ryuukyoku):
            return False
        return any(agari.winner == seat and not agari.is_tsumo for agari in self._outcome)

    # -- Cursor plumbing --

    def _peek(self, offset: int = 0) -> tuple[int | None, Event | None]:
        """The record event at the cursor plus an offset, or None past the end."""
        position = self._cursor + offset
        if position >= len(self._steps):
            return None, None
        return self._steps[position]

    def _take(self, what: str) -> tuple[int, Event]:
        """Consume and return the next record event, failing past the end."""
        if self._cursor >= len(self._steps):
            raise self._error(f"the engine produced {what} beyond the record's end")
        index, logged = self._steps[self._cursor]
        self._cursor += 1
        return index, logged

    def _error(self, message: str, event_index: int | None = None) -> ReplayError:
        """The round's replay error at an optional event."""
        return ReplayError(message, self._round_index, event_index)
