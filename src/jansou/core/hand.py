"""Hand representation: melds, the concealed part, and structural validity."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum, auto, unique
from typing import TYPE_CHECKING

from jansou.core.tiles import TILES_PER_KIND, Tile

if TYPE_CHECKING:
    from jansou.core.rules import Rules

#: A hand's four group slots.
MAX_MELDS = 4

#: Concealed tiles of a meldless hand at rest.
FULL_HAND_SIZE = 13

_TILES_PER_MELD_SLOT = 3
_MELD_SIZE = {"CHII": 3, "PON": 3, "DAIMINKAN": 4, "ANKAN": 4, "SHOUMINKAN": 4}
_RUN_LENGTH = 3


class InvalidHandError(ValueError):
    """A hand or meld that violates the structural hand constraints."""


@unique
class MeldType(Enum):
    """The five meld kinds."""

    CHII = auto()
    PON = auto()
    DAIMINKAN = auto()
    ANKAN = auto()
    SHOUMINKAN = auto()


@unique
class CallSource(Enum):
    """The opponent a claimed tile came from, relative to the holder."""

    KAMICHA = auto()  # left
    TOIMEN = auto()  # across
    SHIMOCHA = auto()  # right


@dataclass(frozen=True)
class Meld:
    """A group set aside from the concealed part.

    ``called`` is the claimed tile for a chii, pon, open kan, or added kan
    (``None`` for a closed kan); ``source`` is the opponent it came from;
    ``added`` is the fourth tile of an added kan. Well-formedness is enforced
    at construction.

    Attributes:
        type: Which of the five meld kinds this is.
        tiles: The meld's tiles.
        called: The claimed tile among ``tiles``, or ``None`` for a closed kan.
        source: The opponent the claimed tile came from, or ``None`` for a
            closed kan.
        added: The tile added when upgrading a pon to a kan, set only for an
            added kan.
    """

    type: MeldType
    tiles: tuple[Tile, ...]
    called: Tile | None = None
    source: CallSource | None = None
    added: Tile | None = None

    def __post_init__(self) -> None:
        """Normalize ``tiles`` to a tuple and enforce meld well-formedness.

        Raises:
            InvalidHandError: If the tile count, composition, or claim data do
                not match the meld kind.
        """
        object.__setattr__(self, "tiles", tuple(self.tiles))
        expected = _MELD_SIZE[self.type.name]
        if len(self.tiles) != expected:
            raise InvalidHandError(f"{self.type.name} must have {expected} tiles, got {len(self.tiles)}")
        if self.type is MeldType.CHII:
            self._check_chii_tiles()
        elif len({tile.kind for tile in self.tiles}) != 1:
            raise InvalidHandError(f"{self.type.name} tiles must be identical in kind")
        self._check_claim()

    def _check_chii_tiles(self) -> None:
        """A chii is three consecutive tiles of one suit."""
        suits = {tile.suit for tile in self.tiles}
        if None in suits or len(suits) != 1:
            raise InvalidHandError("chii tiles must be number tiles of one suit")
        ranks = sorted(tile.rank for tile in self.tiles)  # type: ignore[type-var]
        if ranks != list(range(ranks[0], ranks[0] + _RUN_LENGTH)):
            raise InvalidHandError(f"chii tiles must be consecutive ranks, got {ranks}")

    def _check_claim(self) -> None:
        """The claimed tile, source, and added tile match the meld kind."""
        if self.type is MeldType.ANKAN:
            if self.called is not None or self.source is not None or self.added is not None:
                raise InvalidHandError("a closed kan has no claimed tile, no source, and no added tile")
            return
        if self.called is None or self.called not in self.tiles:
            raise InvalidHandError(f"{self.type.name} must distinguish a claimed tile among its tiles")
        if self.source is None:
            raise InvalidHandError(f"{self.type.name} must record the opponent the claimed tile came from")
        if self.type is MeldType.CHII and self.source is not CallSource.KAMICHA:
            raise InvalidHandError("a chii may come only from kamicha")
        if self.type is MeldType.SHOUMINKAN:
            if self.added is None or self.added not in self.tiles:
                raise InvalidHandError("an added kan must distinguish the added tile among its tiles")
        elif self.added is not None:
            raise InvalidHandError(f"{self.type.name} has no added tile")

    @property
    def is_open(self) -> bool:
        """Whether the meld breaks concealment: every kind but the closed kan."""
        return self.type is not MeldType.ANKAN

    @property
    def is_kan(self) -> bool:
        """Whether the meld is a quad of any kind."""
        return self.type in (MeldType.DAIMINKAN, MeldType.ANKAN, MeldType.SHOUMINKAN)


@dataclass(frozen=True)
class Hand:
    """One player's tiles: the concealed part plus melds in the order made.

    Attributes:
        concealed: The concealed tiles, including any tile just drawn.
        melds: The melds, in the order they were made.
    """

    concealed: tuple[Tile, ...]
    melds: tuple[Meld, ...] = ()

    def __post_init__(self) -> None:
        """Normalize ``concealed`` and ``melds`` to tuples."""
        object.__setattr__(self, "concealed", tuple(self.concealed))
        object.__setattr__(self, "melds", tuple(self.melds))

    @property
    def all_tiles(self) -> tuple[Tile, ...]:
        """Every tile of the hand: the concealed part and each meld's tiles."""
        meld_tiles = tuple(tile for meld in self.melds for tile in meld.tiles)
        return self.concealed + meld_tiles

    @property
    def is_concealed(self) -> bool:
        """Concealed (menzen): no open call; closed kans do not break this."""
        return all(not meld.is_open for meld in self.melds)

    @property
    def has_melds(self) -> bool:
        """The coarser notion: any meld at all, closed kans included."""
        return bool(self.melds)

    @property
    def rest_size(self) -> int:
        """The concealed-tile count this hand has at rest, given its melds."""
        return FULL_HAND_SIZE - _TILES_PER_MELD_SLOT * len(self.melds)

    def validate(self, rules: Rules | None = None) -> None:
        """Check the structural hand constraints.

        Validity is structural only: completeness, readiness, and positional
        legality are separate concerns. Without ``rules``, one red five per
        suit is allowed (the 136-tile arrangement); with ``rules``, the
        red-five flag decides.

        Args:
            rules: The rules whose ``aka_dora`` flag governs how many red fives
                are allowed, or ``None`` to allow one red five per suit.

        Raises:
            InvalidHandError: If the meld count, concealed-tile count, per-kind
                copies, or red-five count violate the structural constraints.
        """
        if len(self.melds) > MAX_MELDS:
            raise InvalidHandError(f"a hand has at most {MAX_MELDS} melds, got {len(self.melds)}")
        if len(self.concealed) not in (self.rest_size, self.rest_size + 1):
            raise InvalidHandError(
                f"with {len(self.melds)} melds the concealed part must hold "
                f"{self.rest_size} or {self.rest_size + 1} tiles, got {len(self.concealed)}"
            )
        kind_counts = Counter(tile.kind for tile in self.all_tiles)
        for kind, count in kind_counts.items():
            if count > TILES_PER_KIND:
                raise InvalidHandError(f"kind {kind.name} appears {count} times; at most {TILES_PER_KIND} allowed")
        reds_allowed = 1 if rules is None or rules.aka_dora else 0
        red_counts = Counter(tile.kind for tile in self.all_tiles if tile.red)
        for kind, count in red_counts.items():
            if count > reds_allowed:
                raise InvalidHandError(f"{count} red fives of {kind.name}; at most {reds_allowed} allowed")

    def is_valid(self, rules: Rules | None = None) -> bool:
        """Whether ``validate`` passes.

        Args:
            rules: The rules passed through to ``validate``.

        Returns:
            ``True`` if the hand satisfies the structural constraints.
        """
        try:
            self.validate(rules)
        except InvalidHandError:
            return False
        return True
