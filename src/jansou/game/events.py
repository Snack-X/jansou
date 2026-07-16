"""The typed event stream, shared by live play and records.

Everything that happens in a game is emitted as an event, in order. Agents
observe events to build their own view of the world (§20); the same vocabulary
is what a record stores. Events carrying seat-private information are masked per
recipient: a player sees the tiles of its own draws and its own dealt hand, and
only that a draw or a deal happened for anyone else. Public events -- discards,
calls, reveals, wins, scores -- are identical for every seat.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum, auto, unique
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jansou.core.hand import Hand, MeldType
    from jansou.core.tiles import Tile, Wind
    from jansou.scoring.score import ScoreResult


@unique
class RyuukyokuKind(Enum):
    """The reason a deal ended without a win."""

    EXHAUSTIVE = auto()
    NAGASHI = auto()
    NINE_TERMINALS = auto()
    FOUR_WINDS = auto()
    FOUR_RIICHI = auto()
    FOUR_KANS = auto()
    TRIPLE_RON = auto()


@dataclass(frozen=True)
class Event:
    """Base class for events; masking is the identity unless overridden."""

    def mask_for(self, seat: int) -> Event:
        """The view of this event a given seat is allowed to observe.

        Args:
            seat: The seat the event is being masked for.

        Returns:
            The event as that seat may see it; the event itself when nothing is hidden.
        """
        _ = seat
        return self


@dataclass(frozen=True)
class GameStart(Event):
    """The game begins: seating, names, and starting scores."""

    player_count: int
    names: tuple[str, ...]
    starting_scores: tuple[int, ...]


@dataclass(frozen=True)
class DealStart(Event):
    """A deal begins: table position, dealt hands, and the first indicator.

    Each seat sees only its own dealt hand; the others are masked to None.

    Attributes:
        deposits: Riichi deposit points carried on the table into this deal
            (a points amount, not a stick count).
        hands: Each seat's dealt tiles, masked to ``None`` for the other seats.
        dora_indicator: The single dora indicator turned up at the start.
    """

    dealer: int
    round_wind: Wind
    round_number: int
    honba: int
    deposits: int
    scores: tuple[int, ...]
    hands: tuple[tuple[Tile, ...] | None, ...]
    dora_indicator: Tile

    def mask_for(self, seat: int) -> DealStart:
        """Mask the dealt hands so the seat sees only its own.

        Args:
            seat: The seat the deal is being masked for.

        Returns:
            The deal with every other seat's hand replaced by ``None``.
        """
        masked = tuple(hand if owner == seat else None for owner, hand in enumerate(self.hands))
        return replace(self, hands=masked)


@dataclass(frozen=True)
class Draw(Event):
    """A player draws a tile, from the live wall or a replacement.

    Only the drawing seat sees the tile; others see a masked draw.

    Attributes:
        tile: The tile drawn, or ``None`` when masked from another seat.
        replacement: Whether this is a post-kan or post-North replacement draw.
    """

    seat: int
    tile: Tile | None
    replacement: bool = False

    def mask_for(self, seat: int) -> Draw:
        """Hide the drawn tile from every seat but the drawer.

        Args:
            seat: The seat the draw is being masked for.

        Returns:
            The draw unchanged for the drawing seat, otherwise with its tile hidden.
        """
        if seat == self.seat:
            return self
        return replace(self, tile=None)


@dataclass(frozen=True)
class Discard(Event):
    """A player discards a tile, opening the claim window."""

    seat: int
    tile: Tile
    tsumogiri: bool = False
    riichi: bool = False


@dataclass(frozen=True)
class Call(Event):
    """A claimed meld: its kind, the caller, the source seat, and the tiles.

    Attributes:
        caller: The seat that claimed the tile.
        source: The seat the tile came from (the caller itself for a closed or added kan).
    """

    meld_type: MeldType
    caller: int
    source: int
    tiles: tuple[Tile, ...]


@dataclass(frozen=True)
class IndicatorReveal(Event):
    """A kan turns up the next dora indicator."""

    tile: Tile


@dataclass(frozen=True)
class NorthExtraction(Event):
    """A player sets aside a just-drawn North as a bonus tile (three-player)."""

    seat: int
    tile: Tile


@dataclass(frozen=True)
class RiichiAccepted(Event):
    """A riichi declaration's discard survived the window; the deposit is banked."""

    seat: int


@dataclass(frozen=True)
class Win(Event):
    """A win, with the winner, the source, the completing tile, and the score.

    The ura indicators are the ones this win revealed -- empty unless it was a
    riichi win -- so a record keeps the concealed reveal that public play hides.

    Attributes:
        from_seat: The discarder on a ron, or ``None`` on a tsumo.
        winning_tile: The tile that completed the hand.
        liable_seat: The seat answering for the win under pao, or ``None``.
    """

    seat: int
    from_seat: int | None
    winning_tile: Tile
    hand: Hand
    result: ScoreResult
    ura_indicators: tuple[Tile, ...] = ()
    liable_seat: int | None = None


@dataclass(frozen=True)
class Ryuukyoku(Event):
    """A deal ends with no winner, with its kind and any revealed hands.

    Attributes:
        revealed: The ready hands shown at an exhaustive draw, as ``(seat, hand)`` pairs.
        counted_ready: The seats counted as ready for the noten settlement.
    """

    kind: RyuukyokuKind
    revealed: tuple[tuple[int, Hand], ...] = ()
    counted_ready: frozenset[int] = frozenset()


@dataclass(frozen=True)
class ScoreChange(Event):
    """A settlement: per-seat deltas and the resulting scores."""

    deltas: tuple[int, ...]
    scores: tuple[int, ...]


@dataclass(frozen=True)
class GameEnd(Event):
    """The game ends: final scores and the seats in ranking order."""

    final_scores: tuple[int, ...]
    ranking: tuple[int, ...]
