"""Authoritative game state: the wall, table position, and per-player holdings.

State is omniscient -- it holds hidden tiles as plainly as visible ones. What a
player may see of it is a separate concern, handled by event masking (events).
The state here is mutable: a deal mutates it in place as play advances, and the
per-deal parts reset each deal while scores, sticks, and the table position
carry across.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from jansou.core.hand import CallSource, Hand
from jansou.core.tiles import Wind

if TYPE_CHECKING:
    from jansou.core.hand import Meld
    from jansou.core.rules import Rules
    from jansou.core.tiles import Tile, TileKind
    from jansou.game.wall import Wall
    from jansou.scoring.yaku import Yaku


@dataclass
class Discard:
    """One tile in a player's discard pile, with the marks rules consume."""

    tile: Tile
    tsumogiri: bool = False
    riichi: bool = False
    called_away: bool = False


@dataclass(frozen=True)
class Liability:
    """A pao mark: one player answers for another's named yakuman shape.

    Attributes:
        beneficiary: The player whose yakuman the mark covers.
        payer: The player who must answer for it.
        shape: The yakuman shape the liability is for.
    """

    beneficiary: int
    payer: int
    shape: Yaku


@dataclass
class PlayerState:
    """One seat's holdings and per-deal status."""

    concealed: list[Tile] = field(default_factory=list)
    melds: list[Meld] = field(default_factory=list)
    drawn: Tile | None = None
    discards: list[Discard] = field(default_factory=list)
    riichi: bool = False
    double_riichi: bool = False
    ippatsu: bool = False
    riichi_furiten: bool = False
    temporary_furiten: bool = False
    nuki_count: int = 0

    @property
    def is_concealed(self) -> bool:
        """Concealed (menzen): no open call; closed kans do not break this."""
        return all(not meld.is_open for meld in self.melds)

    @property
    def is_riichi(self) -> bool:
        """Whether the player has declared riichi, single or double."""
        return self.riichi or self.double_riichi

    def as_hand(self, *, include_drawn: bool = True) -> Hand:
        """The hand as a Hand value, optionally including the drawn tile.

        Args:
            include_drawn: Whether to include the just-drawn tile, when present.

        Returns:
            The concealed tiles and melds as a ``Hand``.
        """
        concealed = list(self.concealed)
        if include_drawn and self.drawn is not None:
            concealed.append(self.drawn)
        return Hand(tuple(concealed), tuple(self.melds))


@dataclass
class GameState:
    """The complete state of a deal in progress, within a running game."""

    rules: Rules
    scores: list[int]
    wall: Wall
    dealer: int
    round_wind: Wind
    round_number: int
    honba: int
    deposit_pool: int
    players: list[PlayerState]
    current_player: int
    last_discard: tuple[int, Tile] | None = None
    first_go_around: bool = True
    riichi_declarations: int = 0
    kans: int = 0
    pending_riichi: int | None = None
    liabilities: list[Liability] = field(default_factory=list)
    counted_ready: frozenset[int] | None = None
    post_call_restriction: frozenset[TileKind] = frozenset()
    deferred_reveal: bool = False

    @property
    def player_count(self) -> int:
        """The number of seats in this game."""
        return len(self.players)

    def seat_wind(self, seat: int) -> Wind:
        """The seat wind of a player, by position relative to the dealer."""
        return Wind((seat - self.dealer) % self.player_count)

    def is_dealer(self, seat: int) -> bool:
        """Whether the seat holds East this deal."""
        return seat == self.dealer

    def next_seat(self, seat: int) -> int:
        """The seat that follows in turn order, wrapping."""
        return (seat + 1) % self.player_count

    def relative_source(self, discarder: int, caller: int) -> CallSource:
        """Where a claimed tile came from, as seen by the caller (§5.2).

        Args:
            discarder: The seat that discarded the claimed tile.
            caller: The seat making the claim.

        Returns:
            The source direction relative to the caller.
        """
        if discarder == (caller - 1) % self.player_count:
            return CallSource.KAMICHA
        if discarder == (caller + 1) % self.player_count:
            return CallSource.SHIMOCHA
        return CallSource.TOIMEN
