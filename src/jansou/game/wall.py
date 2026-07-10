"""The wall: a deal's shuffled tile sequence and its fixed positional mapping.

A wall is a full shuffled sequence of tiles. Everything positional -- which
tiles are dealt, which are ordinary live draws, which serve kan and North
replacement draws, and which are dora and ura indicators -- follows from the
sequence alone, so two walls with the same sequence deal identically.

Writing the sequence as s1 .. sN, the mapping is: the dead wall is s1..s14 --
replacement tiles s1..s4 drawn in order, dora indicators s5, s7, s9, s11, s13
revealed in order, ura indicators s6, s8, s10, s12, s14 beneath them -- and the
live wall is s15..sN, consumed in order for the deal and then ordinary draws.
Each replacement draw shortens the live wall by one from its tail; the fifth
through eighth replacement draws (three-player only) take those cut tail tiles
in descending order.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jansou.core.tiles import Tile

#: Tiles set aside as the dead wall, ahead of the live wall.
DEAD_WALL_SIZE = 14

#: Concealed tiles dealt to each player before the first turn.
DEAL_SIZE = 13

#: The 0-based dead-wall slots for the dora and ura indicators.
_DORA_SLOTS = (4, 6, 8, 10, 12)
_URA_SLOTS = (5, 7, 9, 11, 13)

#: How many indicators the five slots can ever show.
_MAX_INDICATORS = 5

#: Replacement draws taken from the dead wall proper, before the cut tail.
_DEAD_WALL_REPLACEMENTS = 4

#: The traditional deal: three rounds of four tiles, then one of a single tile.
_DEAL_ROUNDS = (4, 4, 4, 1)


class WallError(RuntimeError):
    """A draw or reveal the wall cannot satisfy."""


class Wall:
    """A single deal's tiles, drawn through the positional mapping."""

    def __init__(self, sequence: tuple[Tile, ...]) -> None:
        """Set up a wall from a full shuffled sequence.

        Args:
            sequence: The full shuffled tile sequence ``s1 .. sN``, dead wall first.

        Raises:
            WallError: If the sequence holds fewer tiles than the dead wall needs.
        """
        self._sequence = tuple(sequence)
        if len(self._sequence) < DEAD_WALL_SIZE:
            raise WallError(f"a wall needs at least {DEAD_WALL_SIZE} tiles, got {len(self._sequence)}")
        self._front = DEAD_WALL_SIZE
        self._replacements = 0
        self._revealed = 1

    @property
    def sequence(self) -> tuple[Tile, ...]:
        """The full shuffled sequence, s1 .. sN."""
        return self._sequence

    @property
    def live_draws_remaining(self) -> int:
        """Undrawn live tiles: those neither dealt, drawn, nor cut for replacements."""
        return len(self._sequence) - self._replacements - self._front

    @property
    def replacements_taken(self) -> int:
        """How many replacement draws (kan or North) have been made."""
        return self._replacements

    @property
    def indicators_revealed(self) -> int:
        """How many dora indicators are face up."""
        return self._revealed

    @property
    def dora_indicators(self) -> tuple[Tile, ...]:
        """The face-up dora indicators, in reveal order."""
        return tuple(self._sequence[_DORA_SLOTS[i]] for i in range(self._revealed))

    @property
    def ura_indicators(self) -> tuple[Tile, ...]:
        """The ura indicators beneath the face-up dora indicators, in order."""
        return tuple(self._sequence[_URA_SLOTS[i]] for i in range(self._revealed))

    def deal(self, player_count: int) -> tuple[tuple[Tile, ...], ...]:
        """Draw each player's thirteen starting tiles in the 4-4-4-1 pattern.

        Args:
            player_count: The number of seats to deal to.

        Returns:
            Each seat's thirteen dealt tiles, indexed by seat.
        """
        hands: list[list[Tile]] = [[] for _ in range(player_count)]
        for count in _DEAL_ROUNDS:
            for seat in range(player_count):
                for _ in range(count):
                    hands[seat].append(self._take_front())
        return tuple(tuple(hand) for hand in hands)

    def draw_live(self) -> Tile:
        """Take the next ordinary draw from the live wall's front.

        Returns:
            The tile drawn.

        Raises:
            WallError: If the live wall is exhausted.
        """
        return self._take_front()

    def draw_replacement(self) -> Tile:
        """Take a replacement tile for a kan or North extraction.

        Requires at least one undrawn live tile to remain, and shortens the
        live wall by one. The first four replacements come from the dead wall
        proper; later ones take the cut tail in descending order.

        Returns:
            The replacement tile drawn.

        Raises:
            WallError: If no undrawn live tile remains to shorten.
        """
        if self.live_draws_remaining < 1:
            raise WallError("no undrawn live tile remains for a replacement draw")
        self._replacements += 1
        if self._replacements <= _DEAD_WALL_REPLACEMENTS:
            return self._sequence[self._replacements - 1]
        return self._sequence[len(self._sequence) - self._replacements + _DEAD_WALL_REPLACEMENTS]

    def reveal_indicator(self) -> Tile:
        """Turn up the next dora indicator and return it.

        Returns:
            The newly revealed dora indicator.

        Raises:
            WallError: If all five dora indicators are already revealed.
        """
        if self._revealed >= _MAX_INDICATORS:
            raise WallError("all five dora indicators are already revealed")
        self._revealed += 1
        return self._sequence[_DORA_SLOTS[self._revealed - 1]]

    def _take_front(self) -> Tile:
        """Consume one tile from the live wall's front."""
        if self.live_draws_remaining < 1:
            raise WallError("the live wall is exhausted")
        tile = self._sequence[self._front]
        self._front += 1
        return tile
