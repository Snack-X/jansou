"""Agents: the decision interface and the reference implementations.

An agent decides for one seat. The interface is two methods: observe, which
receives every event the seat may see (masked per §21.2), and act, which returns
one of the offered legal actions. Agents are handed no game state -- an agent
knows only what its events have told it, so the stateful reference agents rebuild
their own hand from the event stream.

The reference agents exist to exercise the environment and document the interface
by example; none is a strong player.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

from jansou.analysis.efficiency import discard_evaluation
from jansou.analysis.shanten import shanten_counts
from jansou.core.hand import Hand, Meld, MeldType
from jansou.core.tiles import NUM_KINDS, Tile, TileKind, Wind, counts_by_kind
from jansou.game.actions import (
    Action,
    Chii,
    ClosedKan,
    DeclareTenpai,
    Discard,
    NineTerminals,
    Nuki,
    OpenKan,
    Pass,
    Pon,
    Riichi,
    Ron,
    Tsumo,
)
from jansou.game.events import Call, DealStart, Draw, IndicatorReveal, NorthExtraction
from jansou.game.events import Discard as DiscardEvent
from jansou.game.flow import DecisionKind

if TYPE_CHECKING:
    from jansou.game.events import Event

_ISOLATION_REACH = 2
_OPEN_KAN_FROM_HAND = 3
_KAN_TILES = 4

#: A stand-in meld used only to carry the meld count into hand evaluation.
_MELD_PLACEHOLDER = Meld(MeldType.ANKAN, (Tile(TileKind.M1),) * _KAN_TILES)


class Agent:
    """The decision-maker for one seat.

    The default observes nothing and must have act overridden; the reference
    agents below subclass it.
    """

    def observe(self, event: Event) -> None:
        """Receive one event the seat is allowed to see. Ignored by default.

        Args:
            event: The event, already masked for this seat.
        """

    def act(self, seat: int, kind: DecisionKind, actions: list[Action]) -> Action:
        """Choose one of the offered legal actions.

        Args:
            seat: The seat this agent plays.
            kind: The kind of decision being requested.
            actions: The legal actions offered; the choice must be one of these.

        Returns:
            The chosen action.

        Raises:
            NotImplementedError: Always, unless a subclass overrides this.
        """
        raise NotImplementedError


def _first(actions: list[Action], types: tuple[type, ...]) -> Action | None:
    """The first offered action of any of the given types."""
    return next((action for action in actions if isinstance(action, types)), None)


class RandomAgent(Agent):
    """Picks uniformly among the offered actions, from its own seeded source."""

    def __init__(self, seed: int | None = None) -> None:
        """Seed the agent's own randomness source.

        Args:
            seed: The seed for this agent's random choices; ``None`` for unseeded.
        """
        self._rng = random.Random(seed)

    def act(self, seat: int, kind: DecisionKind, actions: list[Action]) -> Action:
        """Return a uniformly random choice among the offered actions."""
        _ = (seat, kind)
        return self._rng.choice(actions)


class SimpleAgent(Agent):
    """Wins when it can, riichis when offered, otherwise discards its draw."""

    def act(self, seat: int, kind: DecisionKind, actions: list[Action]) -> Action:
        """Win or riichi when offered, else discard (tsumogiri) or pass."""
        _ = seat
        win = _first(actions, (Tsumo, Ron))
        if win is not None:
            return win
        riichis = [action for action in actions if isinstance(action, Riichi)]
        if riichis:
            return min(riichis, key=lambda action: action.tile.sort_key)
        if kind is DecisionKind.SELF:
            return self._discard(actions)
        if kind is DecisionKind.TENPAI:
            return DeclareTenpai(declare=True)
        return next(action for action in actions if isinstance(action, Pass))

    def _discard(self, actions: list[Action]) -> Action:
        """Tsumogiri when the draw is discardable, else the earliest kind."""
        discards = [action for action in actions if isinstance(action, Discard)]
        tsumogiri = next((action for action in discards if action.tsumogiri), None)
        return tsumogiri if tsumogiri is not None else min(discards, key=lambda action: action.tile.sort_key)


class _PlayerView:
    """A seat's own hand and the tiles it has seen, rebuilt from events."""

    def __init__(self) -> None:
        self.seat = 0
        self.dealer = 0
        self.round_wind = Wind.EAST
        self.player_count = 4
        self.concealed: list[Tile] = []
        self.meld_open: list[bool] = []
        self.drawn: Tile | None = None
        self.nuki = 0
        self.visible = [0] * NUM_KINDS
        self.last_discard: Tile | None = None

    def observe(self, event: Event) -> None:
        if isinstance(event, DealStart):
            self._start(event)
        elif isinstance(event, Draw) and event.seat == self.seat and event.tile is not None:
            self.drawn = event.tile
        elif isinstance(event, DiscardEvent):
            self._discard(event)
        elif isinstance(event, Call) and event.caller == self.seat:
            self._call(event)
        elif isinstance(event, NorthExtraction) and event.seat == self.seat:
            self._nuki()
        elif isinstance(event, IndicatorReveal):
            self.visible[event.tile.kind] += 1

    def _start(self, event: DealStart) -> None:
        self.seat = next(index for index, hand in enumerate(event.hands) if hand is not None)
        self.dealer = event.dealer
        self.round_wind = event.round_wind
        self.player_count = len(event.hands)
        self.concealed = list(event.hands[self.seat])  # type: ignore[arg-type]
        self.meld_open = []
        self.drawn = None
        self.nuki = 0
        self.visible = [0] * NUM_KINDS
        self.visible[event.dora_indicator.kind] += 1

    def _discard(self, event: DiscardEvent) -> None:
        self.last_discard = event.tile
        self.visible[event.tile.kind] += 1
        if event.seat == self.seat:
            pool = self._pool()
            pool.remove(event.tile)
            self.concealed = pool
            self.drawn = None

    def _call(self, event: Call) -> None:
        pool = self._pool()
        if event.source == self.seat:  # a closed or added kan of one's own
            kind = event.tiles[0].kind
            removed = _KAN_TILES if event.meld_type is MeldType.ANKAN else 1
            for _ in range(removed):
                pool.remove(next(tile for tile in pool if tile.kind is kind))
            if event.meld_type is MeldType.ANKAN:
                self.meld_open.append(False)
        else:
            contributed = list(event.tiles)
            contributed.remove(self.last_discard)  # type: ignore[arg-type]
            for tile in contributed:
                pool.remove(tile)
            self.meld_open.append(True)
        self.concealed = pool
        self.drawn = None

    def _nuki(self) -> None:
        pool = self._pool()
        pool.remove(Tile(TileKind.NORTH))
        self.concealed = pool
        self.drawn = None
        self.nuki += 1

    def _pool(self) -> list[Tile]:
        return [*self.concealed, self.drawn] if self.drawn is not None else list(self.concealed)

    @property
    def meld_count(self) -> int:
        return len(self.meld_open)

    def is_concealed(self) -> bool:
        return not any(self.meld_open)

    def hand(self) -> Hand:
        """The current hand, drawn tile included, with placeholder melds."""
        return Hand(tuple(self._pool()), (_MELD_PLACEHOLDER,) * self.meld_count)

    def seat_wind(self) -> Wind:
        return Wind((self.seat - self.dealer) % self.player_count)


class EfficiencyAgent(Agent):
    """Plays a pure acceptance strategy: closest to ready, value ignored."""

    #: Whether acceptance deducts the tiles the agent has seen (§9.1).
    _deducts_visible = False

    def __init__(self, seed: int | None = None) -> None:
        """Seed tie-breaking and start a fresh event-rebuilt view.

        Args:
            seed: The seed for breaking ties among equally efficient discards.
        """
        self._rng = random.Random(seed)
        self._view = _PlayerView()

    def observe(self, event: Event) -> None:
        """Feed the event into the rebuilt view of this seat's hand."""
        self._view.observe(event)

    def act(self, seat: int, kind: DecisionKind, actions: list[Action]) -> Action:
        """Win when offered, else discard by acceptance or pass."""
        _ = seat
        win = _first(actions, (Tsumo, Ron))
        if win is not None:
            return win
        if kind is DecisionKind.SELF:
            return self._self_action(actions)
        if kind is DecisionKind.TENPAI:
            return DeclareTenpai(declare=True)
        return self._react(actions)

    def _self_action(self, actions: list[Action]) -> Action:
        abort = _first(actions, (NineTerminals,))
        if abort is not None:
            return abort
        if any(isinstance(action, Nuki) for action in actions):
            return Nuki()
        kan = self._neutral_kan(actions)
        if kan is not None:
            return kan
        return self._by_acceptance(actions)

    def _neutral_kan(self, actions: list[Action]) -> Action | None:
        for action in actions:
            if isinstance(action, ClosedKan) and _shape_neutral(action.kind, self._view.concealed):
                return action
        return None

    def _by_acceptance(self, actions: list[Action]) -> Action:
        visible = self._view.visible if self._deducts_visible else None
        options = discard_evaluation(self._view.hand(), visible=visible, player_count=self._view.player_count)
        fewest = min(option.shanten for option in options)
        closest = [option for option in options if option.shanten == fewest]
        widest = max(option.total_acceptance for option in closest)
        kind = self._rng.choice([option.discard for option in closest if option.total_acceptance == widest])
        riichi = next((a for a in actions if isinstance(a, Riichi) and a.tile.kind is kind), None)
        if riichi is not None:
            return riichi
        chosen = next((a for a in actions if isinstance(a, Discard) and a.tile.kind is kind), None)
        if chosen is not None:
            return chosen
        return min((a for a in actions if isinstance(a, Discard)), key=lambda a: a.tile.sort_key)

    def _react(self, actions: list[Action]) -> Action:
        return next(action for action in actions if isinstance(action, Pass))


class SmartEfficiencyAgent(EfficiencyAgent):
    """Efficiency with visibility deductions and calls."""

    _deducts_visible = True

    def _react(self, actions: list[Action]) -> Action:
        call = self._concealed_call(actions) if self._view.is_concealed() else self._open_call(actions)
        return call if call is not None else next(action for action in actions if isinstance(action, Pass))

    def _concealed_call(self, actions: list[Action]) -> Action | None:
        """While concealed, pon only a value triplet."""
        tile = self._view.last_discard
        if tile is not None and _is_yakuhai(tile.kind, self._view):
            return _first(actions, (Pon,))
        return None

    def _open_call(self, actions: list[Action]) -> Action | None:
        """Once open, take the call giving the lowest non-worsening shanten."""
        current = shanten_counts(counts_by_kind(self._view.concealed), self._view.meld_count)
        best_action: Action | None = None
        best_shanten = current
        for action in actions:
            if isinstance(action, (Pon, Chii, OpenKan)) and (resulting := self._shanten_after(action)) <= best_shanten:
                best_shanten, best_action = resulting, action
        return best_action

    def _shanten_after(self, action: Action) -> int:
        counts = counts_by_kind(self._view.concealed)
        for tile in self._used_tiles(action):
            counts[tile.kind] -= 1
        return shanten_counts(counts, self._view.meld_count + 1)

    def _used_tiles(self, action: Action) -> list[Tile]:
        if isinstance(action, (Pon, Chii)):
            return list(action.tiles)
        kind = self._view.last_discard.kind  # type: ignore[union-attr]
        return [tile for tile in self._view.concealed if tile.kind is kind][:_OPEN_KAN_FROM_HAND]


def _shape_neutral(kind: TileKind, concealed: list[Tile]) -> bool:
    """Whether a closed kan of a kind leaves the hand's shape untouched."""
    if kind.is_honor:
        return True
    rank = kind.rank or 0
    return not any(
        tile.suit is kind.suit and tile.kind is not kind and abs((tile.rank or 0) - rank) <= _ISOLATION_REACH
        for tile in concealed
    )


def _is_yakuhai(kind: TileKind, view: _PlayerView) -> bool:
    """Whether a kind scores as a value triplet for this seat."""
    if kind.is_dragon:
        return True
    return kind in (view.round_wind.tile_kind, view.seat_wind().tile_kind)
