"""The game environment: the referee that runs a whole game.

Constructed once per game with its configuration, an optional seed, and
optionally predefined walls. Running is a single call: hand it one agent per
seat and it plays to the end, returning the final scores and rankings. It owns
the state, drives the flow, masks events per seat, keeps each deal's event
stream in ``records``, and -- as the only consumer of the randomness source --
guarantees that a seed reproduces a game.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

from jansou.core.tiles import full_tile_set
from jansou.game.events import GameEnd, GameStart
from jansou.game.flow import new_deal, play_deal
from jansou.game.progression import advance, rank, settle_deposits, starting_position
from jansou.game.wall import Wall

if TYPE_CHECKING:
    from jansou.core.rules import Rules
    from jansou.core.tiles import Tile
    from jansou.game.actions import Action
    from jansou.game.agents import Agent
    from jansou.game.events import Event
    from jansou.game.flow import DecisionKind
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


class Environment:
    """The referee and engine for one game."""

    def __init__(
        self,
        rules: Rules,
        *,
        seed: int | None = None,
        walls: list[tuple[Tile, ...]] | None = None,
    ) -> None:
        """Configure a game before it is run.

        Args:
            rules: The rule set governing the game.
            seed: The seed for wall shuffling; ``None`` for unseeded shuffles.
            walls: Predefined tile sequences, one per deal, used in order instead
                of shuffling; ``None`` to shuffle a fresh wall each deal.
        """
        self.rules = rules
        self._rng = random.Random(seed)
        self._walls = list(walls) if walls is not None else None
        self.records: list[list[Event]] = []
        self.state: GameState | None = None

    def run(self, agents: list[Agent], names: tuple[str, ...] | None = None) -> GameResult:
        """Play the whole game with one agent per seat, returning the result.

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
        names = names or tuple(f"Player {seat + 1}" for seat in range(self.rules.player_count))
        scores = [self.rules.starting_points] * self.rules.player_count
        self._notify(agents, GameStart(self.rules.player_count, names, tuple(scores)))
        scores, pool = self._play(agents, scores)
        scores = settle_deposits(scores, pool, self.rules)
        ranking = rank(scores)
        self._notify(agents, GameEnd(tuple(scores), ranking))
        return GameResult(tuple(scores), ranking)

    def _play(self, agents: list[Agent], scores: list[int]) -> tuple[list[int], int]:
        """Run deals until an ending condition, returning final scores and pool."""
        position = starting_position()
        in_extension = False
        pool = 0
        deal_index = 0
        while True:
            state = new_deal(self.rules, self._wall(deal_index), position, scores, pool)
            self.state = state
            outcome = play_deal(state, self._decider(agents), self._emitter(agents))
            scores, pool = state.scores, state.deposit_pool
            step = advance(position, outcome, scores, self.rules, in_extension=in_extension)
            deal_index += 1
            if step.position is None:
                return scores, pool
            position, in_extension = step.position, step.in_extension

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

    def _decider(self, agents: list[Agent]):  # noqa: ANN202 - a decide callback for the flow
        def decide(seat: int, kind: DecisionKind, actions: list[Action]) -> Action:
            action = agents[seat].act(seat, kind, list(actions))
            if action not in actions:
                raise IllegalActionError(f"seat {seat} returned {action!r}, not among the offered actions")
            return action

        return decide

    def _emitter(self, agents: list[Agent]):  # noqa: ANN202 - an emit callback for the flow
        deal_events: list[Event] = []
        self.records.append(deal_events)

        def emit(event: Event) -> None:
            deal_events.append(event)
            for seat, agent in enumerate(agents):
                agent.observe(event.mask_for(seat))

        return emit

    def _notify(self, agents: list[Agent], event: Event) -> None:
        """Deliver a game-level event to every agent (never recorded per deal)."""
        for seat, agent in enumerate(agents):
            agent.observe(event.mask_for(seat))
