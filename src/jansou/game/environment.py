"""The game environment: the referee that runs a whole game.

Constructed once per game with its configuration, an optional seed, and
optionally predefined walls. There are two ways to play: ``run`` is a single
call -- hand it one agent per seat and it plays to the end -- and ``play`` is
its inversion, a generator that yields each decision for the caller to answer,
so many games can be multiplexed and their decisions batched. Either way the
environment owns the state, drives the flow, masks events per seat, keeps each
deal's event stream in ``records``, and -- as the only consumer of the
randomness source -- guarantees that a seed reproduces a game.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

from jansou.core.tiles import full_tile_set
from jansou.game.events import GameEnd, GameStart
from jansou.game.flow import deal_steps, new_deal
from jansou.game.progression import advance, rank, settle_deposits, starting_position
from jansou.game.wall import Wall

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

    from jansou.core.rules import Rules
    from jansou.core.tiles import Tile
    from jansou.game.actions import Action
    from jansou.game.agents import Agent
    from jansou.game.events import Event
    from jansou.game.flow import DealOutcome, DecisionKind
    from jansou.game.state import GameState


class IllegalActionError(ValueError):
    """An agent returned an action outside the offered set (§14.10, §20)."""


class GameConfigError(ValueError):
    """A misconfigured game: wrong agent count, or walls run out."""


@dataclass(frozen=True)
class GameResult:
    """A finished game: each seat's final score and the seats in rank order."""

    scores: tuple[int, ...]
    ranking: tuple[int, ...]


@dataclass(frozen=True)
class Decision:
    """One recorded decision point: the offer, the choice, and its anchor.

    ``event_index`` is the length of the deal's event record at decision time,
    so ``records[deal][:event_index]`` is exactly what had been emitted when
    the decision was requested.
    """

    seat: int
    kind: DecisionKind
    actions: tuple[Action, ...]
    chosen: Action
    event_index: int


@dataclass(frozen=True)
class DecisionRequest:
    """One pending decision, yielded by ``Environment.play`` to its driver.

    Attributes:
        actions: The offered legal actions; the answer must be one of these.
        events: The events newly emitted since the previous request, masked
            for the deciding seat (whole for a seat in ``unmasked_seats``).
    """

    seat: int
    kind: DecisionKind
    actions: tuple[Action, ...]
    events: tuple[Event, ...]


class Environment:
    """The referee and engine for one game.

    For training tooling that may see hidden information, ``state`` -- the live
    game state of the deal in progress, complete and unmasked, wall included --
    is the sanctioned way to inspect hands and the remaining wall mid-game;
    read it, never mutate it. ``unmasked_seats`` complements it by delivering
    events unmasked to chosen seats.
    """

    def __init__(
        self,
        rules: Rules,
        *,
        seed: int | None = None,
        walls: list[tuple[Tile, ...]] | None = None,
        record_decisions: bool = False,
        unmasked_seats: frozenset[int] = frozenset(),
    ) -> None:
        """Configure a game before it is run.

        Args:
            rules: The rule set governing the game.
            seed: The seed for wall shuffling; ``None`` for unseeded shuffles.
                It covers only the environment's own randomness: randomizing
                agents (e.g. ``RandomAgent``) draw from their own sources and
                must be seeded separately for a reproducible game.
            walls: Predefined tile sequences, one per deal, used in order instead
                of shuffling; ``None`` to shuffle a fresh wall each deal.
            record_decisions: Whether to keep every decision point in
                ``decisions`` (one list per deal, parallel to ``records``).
            unmasked_seats: Seats exempt from event masking -- each observes
                every event whole (all dealt hands, every draw's tile), for
                perfect-information "oracle" training. Empty for normal play.
        """
        self.rules = rules
        self._rng = random.Random(seed)
        self._walls = list(walls) if walls is not None else None
        self.records: list[list[Event]] = []
        self.record_decisions = record_decisions
        self.decisions: list[list[Decision]] = []
        self.unmasked_seats = frozenset(unmasked_seats)
        self.state: GameState | None = None

    def run(self, agents: list[Agent], names: tuple[str, ...] | None = None) -> GameResult:
        """Play the whole game with one agent per seat, returning the result.

        A thin driver over ``play``: every event is delivered to each agent's
        ``observe`` (masked per seat) as it happens, and every decision is
        answered by the deciding seat's ``act``.

        Args:
            agents: One agent per seat, in seat order.
            names: Display names in seat order; defaulted to ``Player N`` when omitted.

        Returns:
            The finished game's final scores and ranking.

        Raises:
            GameConfigError: If the number of agents does not match the rules'
                player count, or a predefined wall is missing for a deal.
        """
        if len(agents) != self.rules.player_count:
            raise GameConfigError(f"expected {self.rules.player_count} agents, got {len(agents)}")

        def fan_out(event: Event) -> None:
            for seat, agent in enumerate(agents):
                agent.observe(self._view(event, seat))

        game = self.play(names, observe=fan_out)
        request = next(game)
        while True:
            action = agents[request.seat].act(request.seat, request.kind, list(request.actions))
            try:
                request = game.send(action)
            except StopIteration as stop:
                return stop.value

    def play(
        self,
        names: tuple[str, ...] | None = None,
        *,
        observe: Callable[[Event], None] | None = None,
    ) -> Generator[DecisionRequest, Action, GameResult]:
        """Play the game step by step, yielding each decision to the caller.

        The inversion of ``run``, for drivers that supply actions themselves
        -- e.g. multiplexing many games and batching their pending decisions:
        the generator yields a ``DecisionRequest`` per decision and is resumed
        with the chosen action; the finished game's ``GameResult`` is the
        generator's return value (``StopIteration.value``).

        Each request carries the events newly emitted since the previous
        request, masked for the deciding seat. Events emitted after a game's
        last decision (the final win or draw, the game end) reach no request;
        a driver that needs every event -- or every seat's view -- should pass
        ``observe``, which is fed each unmasked event as it is emitted,
        game-level events included, and may mask per seat with
        ``Event.mask_for``.

        Args:
            names: Display names in seat order; defaulted to ``Player N`` when omitted.
            observe: An optional callback fed every unmasked event as it is emitted.

        Yields:
            Each decision request, in game order.

        Returns:
            The finished game's final scores and ranking.

        Raises:
            GameConfigError: If a predefined wall is missing for a deal.
            IllegalActionError: If the driver sends an action outside the
                offered set.
        """
        names = names or tuple(f"Player {seat + 1}" for seat in range(self.rules.player_count))
        scores = [self.rules.starting_points] * self.rules.player_count
        pending: list[Event] = []

        def deliver(event: Event) -> None:
            pending.append(event)
            if observe is not None:
                observe(event)

        deliver(GameStart(self.rules.player_count, names, tuple(scores)))
        position = starting_position()
        in_extension = False
        pool = 0
        deal_index = 0
        while True:
            state = new_deal(self.rules, self._wall(deal_index), position, scores, pool)
            self.state = state
            outcome = yield from self._deal(state, pending, deliver)
            scores, pool = state.scores, state.deposit_pool
            step = advance(position, outcome, scores, self.rules, in_extension=in_extension)
            deal_index += 1
            if step.position is None:
                break
            position, in_extension = step.position, step.in_extension
        scores = settle_deposits(scores, pool, self.rules)
        ranking = rank(scores)
        deliver(GameEnd(tuple(scores), ranking))
        return GameResult(tuple(scores), ranking)

    def _deal(
        self, state: GameState, pending: list[Event], deliver: Callable[[Event], None]
    ) -> Generator[DecisionRequest, Action, DealOutcome]:
        """Run one deal's steps, wrapping decision points into requests."""
        deal_events: list[Event] = []
        self.records.append(deal_events)
        deal_decisions: list[Decision] | None = None
        if self.record_decisions:
            deal_decisions = []
            self.decisions.append(deal_decisions)

        def emit(event: Event) -> None:
            deal_events.append(event)
            deliver(event)

        steps = deal_steps(state, emit)
        point = next(steps)
        while True:
            events = tuple(self._view(event, point.seat) for event in pending)
            pending.clear()
            choice = yield DecisionRequest(point.seat, point.kind, point.actions, events)
            if choice not in point.actions:
                raise IllegalActionError(f"seat {point.seat} returned {choice!r}, not among the offered actions")
            if deal_decisions is not None:
                deal_decisions.append(Decision(point.seat, point.kind, point.actions, choice, len(deal_events)))
            try:
                point = steps.send(choice)
            except StopIteration as stop:
                return stop.value

    def _view(self, event: Event, seat: int) -> Event:
        """The event as a seat observes it: whole for oracle seats, else masked."""
        return event if seat in self.unmasked_seats else event.mask_for(seat)

    def _wall(self, deal_index: int) -> Wall:
        """The wall for a deal: a predefined one, or a fresh shuffle."""
        if self._walls is not None:
            if deal_index >= len(self._walls):
                raise GameConfigError(
                    f"the game needs deal {deal_index + 1} but only {len(self._walls)} walls were supplied"
                )
            return Wall(self._walls[deal_index])
        tiles = full_tile_set(self.rules.player_count, aka_dora=self.rules.aka_dora)
        self._rng.shuffle(tiles)
        return Wall(tuple(tiles))
