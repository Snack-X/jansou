"""Win context: the surrounding situation a completed hand is scored against.

The winning tile and its source belong to the hand; everything else scoring
draws on -- seat and round winds, riichi and its one-shot and situational
flags, the active bonus indicators, and the carried bonus -- is gathered here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from jansou.core.tiles import Wind

if TYPE_CHECKING:
    from jansou.core.rules import Rules
    from jansou.core.tiles import Tile


@dataclass(frozen=True)
class WinContext:
    """The situation of a win, beyond the hand and its winning tile.

    Attributes:
        rules: The ruleset the win is scored under.
        round_wind: The prevailing wind of the round.
        seat_wind: The winner's seat wind; ``Wind.EAST`` marks the dealer.
        is_tsumo: Whether the win was self-drawn rather than claimed.
        riichi: Whether the winner declared riichi.
        double_riichi: Whether riichi was declared on the first uninterrupted discard.
        ippatsu: Whether the win came within one go-around of a riichi declaration.
        haitei: Whether the win was the self-draw of the final wall tile.
        houtei: Whether the win claimed the final discard.
        rinshan: Whether the win was the self-draw off a kan replacement.
        chankan: Whether the win robbed an opponent's added kan.
        tenhou: Whether the dealer won on the opening draw.
        chiihou: Whether a non-dealer won on the first self-draw.
        dora_indicators: The face-up dora indicators.
        ura_indicators: The under-riichi dora indicators.
        nuki_count: The number of set-aside North tiles in three-player play.
        honba: The repeat-counter bonus carried into the deal.
        riichi_sticks: Deposit sticks on the table, in thousand-point units, collected by the winner.
    """

    rules: Rules
    round_wind: Wind = Wind.EAST
    seat_wind: Wind = Wind.EAST
    is_tsumo: bool = False

    # Situational yaku flags, read rather than derived from the tiles.
    riichi: bool = False
    double_riichi: bool = False
    ippatsu: bool = False
    haitei: bool = False
    houtei: bool = False
    rinshan: bool = False
    chankan: bool = False
    tenhou: bool = False
    chiihou: bool = False

    # Bonus indicators and set-aside North tiles.
    dora_indicators: tuple[Tile, ...] = ()
    ura_indicators: tuple[Tile, ...] = ()
    nuki_count: int = 0

    # Table bonus carried into the deal.
    honba: int = 0

    # Deposit sticks on the table, in thousand-point units, collected by the winner.
    riichi_sticks: int = 0

    @property
    def is_dealer(self) -> bool:
        """Whether the winner holds the East seat."""
        return self.seat_wind is Wind.EAST

    @property
    def is_riichi(self) -> bool:
        """Whether the hand won under riichi, single or double."""
        return self.riichi or self.double_riichi
