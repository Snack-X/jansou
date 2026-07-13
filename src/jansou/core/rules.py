"""Rules configuration: game-setup parameters, rule flags, and presets."""

from __future__ import annotations

from dataclasses import dataclass, replace
from types import MappingProxyType

from jansou.core.tiles import Wind

#: The points one riichi declaration deposits on the table.
RIICHI_DEPOSIT = 1000

_PLAYER_COUNTS = (3, 4)
_HONBA_VALUES = (0, 100)
_DOUBLE_WIND_FU_VALUES = (2, 4)
_NOTEN_POOL_VALUES = (0, 3000)


@dataclass(frozen=True)
class Rules:
    """A complete rules configuration, fixed for a whole game.

    Field defaults are the library's baseline rule set, so ``Rules()``
    is the default baseline. A configuration whose settings contradict one
    another is rejected at construction.

    Attributes:
        player_count: The number of players, 3 or 4.
        starting_points: The points each player starts with.
        game_length: The wind the game runs to (east-only or south).
        sudden_death_target: The points a leader needs to end sudden death.
        honba_value: The points each non-winner pays per counter (honba),
            0 or 100.
        double_wind_fu: The fu for a pair of the seat-and-round wind, 2 or 4.
        kiriage_mangan: Whether near-mangan scores round up to mangan.
        kazoe_yakuman: Whether a hand of thirteen or more han scores a yakuman.
        multiple_yakuman: Whether several yakuman in one hand accumulate.
        double_yakuman: Whether the double-yakuman hands score at twice value.
        aka_dora: Whether red fives are in the set and count as dora.
        ura_dora: Whether under-dora are revealed on a riichi win.
        kan_dora: Whether declaring a kan reveals an extra dora indicator.
        nuki_dora: Whether pulled north tiles count as dora (sanma only).
        closed_kan_indicator_immediate: Whether a closed kan reveals its dora
            immediately rather than after the next discard.
        open_kan_indicator_immediate: Whether an open kan reveals its dora
            immediately rather than after the next discard.
        riichi_without_draw: Whether riichi may be declared when too few tiles
            remain in the wall to draw again.
        riichi_without_tenpai: Whether riichi may be declared while not tenpai.
        closed_kan_after_riichi: Whether a closed kan is allowed after riichi.
        kuitan: Whether an open all-simples hand still scores tanyao.
        kuikae_ban: Whether swap-calling (kuikae) is forbidden.
        multiple_ron: Whether more than one player may ron the same discard.
        kokushi_ankan_chankan: Whether thirteen orphans may rob a closed kan.
        abort_kyuushu_kyuuhai: Whether nine terminals and honors in an opening
            hand aborts the hand.
        abort_suufon_renda: Whether four identical wind discards on the first
            go-around aborts the hand.
        abort_suucha_riichi: Whether four riichi declarations abort the hand.
        abort_suukaikan: Whether the fourth kan aborts the hand.
        abort_sanchahou: Whether a triple ron aborts the hand.
        noten_penalty_pool: The no-tenpai payment pool at an exhaustive draw,
            0 or 3000.
        formal_tenpai: Whether formal (shape-only) tenpai counts at a draw.
        tenpai_declaration: Whether players must declare tenpai at a draw.
        nagashi_mangan: Whether an all-terminal-and-honor discard pile scores a
            mangan.
        ippatsu: Whether winning within one uninterrupted go-around after
            riichi scores ippatsu.
        dealer_repeat_on_tenpai: Whether the dealer repeats when tenpai at a
            draw.
        pao_daisangen: Whether the big-three-dragons feeder shares liability.
        pao_daisuushi: Whether the big-four-winds feeder shares liability.
        pao_suukantsu: Whether the four-kan feeder shares liability.
        pao_daiminkan: Whether the open-kan feeder is liable for a rinshan win.
        pao_honba_to_liable: Whether the liable player pays the honba on a
            liable ron; the discarder pays it otherwise.
        allow_negative_scores: Whether play continues once a player is below zero.
        agari_yame: Whether a leading dealer may end the game on a win.
        sudden_death: Whether a tied or below-target game goes to overtime.
        rank_ties_shared: Whether tied players split their placement points.
        leftover_deposits_to_first: Whether leftover riichi deposits go to first.
    """

    # Game-setup parameters.
    player_count: int = 4
    starting_points: int = 25_000
    game_length: Wind = Wind.SOUTH
    sudden_death_target: int = 30_000

    # Score options.
    honba_value: int = 100
    double_wind_fu: int = 4
    kiriage_mangan: bool = False
    kazoe_yakuman: bool = True

    # Yakuman options.
    multiple_yakuman: bool = True
    double_yakuman: bool = True

    # Dora options.
    aka_dora: bool = True
    ura_dora: bool = True
    kan_dora: bool = True
    nuki_dora: bool = False
    closed_kan_indicator_immediate: bool = True
    open_kan_indicator_immediate: bool = True

    # Riichi options.
    riichi_without_draw: bool = False
    riichi_without_tenpai: bool = False
    closed_kan_after_riichi: bool = True

    # Call and win options.
    kuitan: bool = True
    kuikae_ban: bool = False
    multiple_ron: bool = False
    kokushi_ankan_chankan: bool = False

    # Abortive draw options.
    abort_kyuushu_kyuuhai: bool = True
    abort_suufon_renda: bool = True
    abort_suucha_riichi: bool = True
    abort_suukaikan: bool = True
    abort_sanchahou: bool = True

    # Exhaustive draw options.
    noten_penalty_pool: int = 3000
    formal_tenpai: bool = False
    tenpai_declaration: bool = False
    nagashi_mangan: bool = True

    # Riichi yaku and repeat options.
    ippatsu: bool = True
    dealer_repeat_on_tenpai: bool = True

    # Liability options.
    pao_daisangen: bool = True
    pao_daisuushi: bool = True
    pao_suukantsu: bool = False
    pao_daiminkan: bool = False
    pao_honba_to_liable: bool = True

    # Game-end options.
    allow_negative_scores: bool = True
    agari_yame: bool = True
    sudden_death: bool = True
    rank_ties_shared: bool = False
    leftover_deposits_to_first: bool = False

    def __post_init__(self) -> None:
        """Reject a configuration whose settings contradict one another.

        Raises:
            ValueError: If the player count, nuki-dora pairing, honba value,
                double-wind fu, or no-tenpai pool is outside its allowed set.
        """
        if self.player_count not in _PLAYER_COUNTS:
            raise ValueError(f"player count must be 3 or 4, got {self.player_count}")
        if self.nuki_dora and self.player_count != 3:
            raise ValueError("nuki dora requires the three-player game")
        if self.honba_value not in _HONBA_VALUES:
            raise ValueError(f"honba value must be 0 or 100, got {self.honba_value}")
        if self.double_wind_fu not in _DOUBLE_WIND_FU_VALUES:
            raise ValueError(f"double-wind pair fu must be 2 or 4, got {self.double_wind_fu}")
        if self.noten_penalty_pool not in _NOTEN_POOL_VALUES:
            raise ValueError(f"no-tenpai penalty pool must be 0 or 3000, got {self.noten_penalty_pool}")

    @property
    def is_sanma(self) -> bool:
        """Whether this is the three-player game."""
        return self.player_count == 3

    @property
    def honba_per_counter(self) -> int:
        """The points one counter adds to a win: one share per non-winner."""
        return (self.player_count - 1) * self.honba_value


#: The default baseline: every flag at its default.
_DEFAULT = Rules()

#: Association base: the shared configuration of the professional associations,
#: as differences from the default baseline.
_ASSOCIATION_BASE = replace(
    _DEFAULT,
    starting_points=30_000,
    double_wind_fu=2,
    kuikae_ban=True,
    double_yakuman=False,
    aka_dora=False,
    abort_kyuushu_kyuuhai=False,
    abort_suufon_renda=False,
    abort_suucha_riichi=False,
    abort_suukaikan=False,
    abort_sanchahou=False,
    riichi_without_draw=True,
    formal_tenpai=True,
    tenpai_declaration=True,
    nagashi_mangan=False,
    agari_yame=False,
    sudden_death=False,
    rank_ties_shared=True,
)

#: Net-mahjong base: the shared configuration of the online platforms,
#: as differences from the default baseline.
_NET_MAHJONG_BASE = replace(
    _DEFAULT,
    open_kan_indicator_immediate=False,
    multiple_ron=True,
    kuikae_ban=True,
    formal_tenpai=True,
    allow_negative_scores=False,
    leftover_deposits_to_first=True,
)

_TENHOU = replace(_NET_MAHJONG_BASE, double_yakuman=False)

_MAHJONG_SOUL = replace(
    _NET_MAHJONG_BASE,
    kiriage_mangan=True,
    abort_sanchahou=False,
    kokushi_ankan_chankan=True,
)

#: Setup parameters the three-player platform presets pin.
_SANMA_SETUP = {
    "player_count": 3,
    "starting_points": 35_000,
    "sudden_death_target": 40_000,
}

#: Named presets, each a base plus its overrides.
PRESETS: MappingProxyType[str, Rules] = MappingProxyType(
    {
        "renmei": replace(
            _ASSOCIATION_BASE,
            ura_dora=False,
            kan_dora=False,
            ippatsu=False,
            pao_suukantsu=True,
            pao_honba_to_liable=False,
        ),
        "saikouisen": replace(
            _ASSOCIATION_BASE,
            kiriage_mangan=True,
            kazoe_yakuman=False,
            pao_daisangen=False,
            pao_daisuushi=False,
        ),
        "saikouisen-classic": replace(
            _ASSOCIATION_BASE,
            kazoe_yakuman=False,
            pao_daisangen=False,
            pao_daisuushi=False,
            ura_dora=False,
            kan_dora=False,
            riichi_without_tenpai=True,
            closed_kan_after_riichi=False,
            kuikae_ban=False,
            noten_penalty_pool=0,
            formal_tenpai=False,
            tenpai_declaration=False,
            dealer_repeat_on_tenpai=False,
            ippatsu=False,
        ),
        "mu": replace(
            _ASSOCIATION_BASE,
            honba_value=0,
            kazoe_yakuman=False,
            multiple_yakuman=False,
            ura_dora=False,
            kan_dora=False,
            closed_kan_after_riichi=False,
            kuikae_ban=False,
            noten_penalty_pool=0,
            ippatsu=False,
        ),
        "kyokai": replace(
            _ASSOCIATION_BASE,
            starting_points=25_000,
            kiriage_mangan=True,
            kazoe_yakuman=False,
            pao_daisangen=False,
            pao_daisuushi=False,
        ),
        "rmu": replace(
            _ASSOCIATION_BASE,
            kiriage_mangan=True,
            pao_daisangen=False,
            pao_daisuushi=False,
        ),
        "m-league": replace(
            _ASSOCIATION_BASE,
            starting_points=25_000,
            kiriage_mangan=True,
            kazoe_yakuman=False,
            aka_dora=True,
            pao_suukantsu=True,
            leftover_deposits_to_first=True,
        ),
        "saikyosen": replace(
            _ASSOCIATION_BASE,
            kiriage_mangan=True,
            kazoe_yakuman=False,
            pao_suukantsu=True,
            leftover_deposits_to_first=True,
            rank_ties_shared=False,
        ),
        "tenhou": _TENHOU,
        "mahjong-soul": _MAHJONG_SOUL,
        "tenhou-3p": replace(
            _TENHOU,
            abort_sanchahou=False,
            abort_suucha_riichi=False,
            abort_suufon_renda=False,
            nuki_dora=True,
            **_SANMA_SETUP,
        ),
        "mahjong-soul-3p": replace(
            _MAHJONG_SOUL,
            abort_suucha_riichi=False,
            abort_suufon_renda=False,
            nuki_dora=True,
            **_SANMA_SETUP,
        ),
    },
)


def preset(name: str) -> Rules:
    """The named preset configuration.

    Args:
        name: The preset name, one of the keys of ``PRESETS``.

    Returns:
        The ``Rules`` for that preset.

    Raises:
        ValueError: If ``name`` is not a known preset.
    """
    try:
        return PRESETS[name]
    except KeyError:
        known = ", ".join(sorted(PRESETS))
        raise ValueError(f"unknown preset {name!r}; known presets: {known}") from None
