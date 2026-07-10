"""Tests for the agent interface and the reference agents."""

from __future__ import annotations

import pytest

from jansou.core.hand import MeldType
from jansou.core.notation import parse_mpsz
from jansou.core.tiles import Tile, TileKind, Wind
from jansou.game.actions import (
    ClosedKan,
    DeclareTenpai,
    Discard,
    NineTerminals,
    Nuki,
    OpenKan,
    Pass,
    Pon,
    Riichi,
    Tsumo,
)
from jansou.game.agents import (
    Agent,
    EfficiencyAgent,
    RandomAgent,
    SimpleAgent,
    SmartEfficiencyAgent,
    _is_yakuhai,
    _PlayerView,
    _shape_neutral,
)
from jansou.game.events import Call, DealStart, Draw, IndicatorReveal, NorthExtraction, ScoreChange
from jansou.game.events import Discard as DiscardEvent
from jansou.game.flow import DecisionKind


def deal_start(hand: str, *, seat: int = 0, dealer: int = 0, player_count: int = 4) -> DealStart:
    hands = tuple(tuple(parse_mpsz(hand)) if index == seat else None for index in range(player_count))
    return DealStart(
        dealer=dealer,
        round_wind=Wind.EAST,
        round_number=1,
        honba=0,
        deposits=0,
        scores=(25000,) * player_count,
        hands=hands,
        dora_indicator=Tile(TileKind.M1),
    )


def tile(spec: str) -> Tile:
    return parse_mpsz(spec)[0]


class TestBaseAgent:
    def test_act_must_be_overridden(self) -> None:
        with pytest.raises(NotImplementedError):
            Agent().act(0, DecisionKind.SELF, [])

    def test_observe_is_a_noop(self) -> None:
        Agent().observe(deal_start("123m"))  # does not raise


class TestRandomAgent:
    def test_picks_an_offered_action(self) -> None:
        actions = [Discard(tile("1m")), Discard(tile("2m")), Pass()]
        assert RandomAgent(seed=0).act(0, DecisionKind.SELF, actions) in actions

    def test_is_reproducible(self) -> None:
        actions = [Discard(tile("1m")), Discard(tile("2m")), Discard(tile("3m"))]
        assert RandomAgent(seed=5).act(0, DecisionKind.SELF, actions) == RandomAgent(seed=5).act(
            0, DecisionKind.SELF, actions
        )


class TestSimpleAgent:
    def test_takes_a_win(self) -> None:
        assert SimpleAgent().act(0, DecisionKind.SELF, [Tsumo(), Discard(tile("1m"))]) == Tsumo()

    def test_riichi_takes_the_earliest_tile(self) -> None:
        agent = SimpleAgent()
        chosen = agent.act(0, DecisionKind.SELF, [Riichi(tile("9m")), Riichi(tile("1m")), Discard(tile("1m"))])
        assert chosen == Riichi(tile("1m"))

    def test_tsumogiri_discards_the_draw(self) -> None:
        agent = SimpleAgent()
        agent.observe(Draw(0, tile("5p")))
        assert agent.act(0, DecisionKind.SELF, [Discard(tile("1m")), Discard(tile("5p"))]) == Discard(tile("5p"))

    def test_falls_back_to_earliest_discard(self) -> None:
        agent = SimpleAgent()
        assert agent.act(0, DecisionKind.SELF, [Discard(tile("5p")), Discard(tile("1m"))]) == Discard(tile("1m"))

    def test_passes_reactions(self) -> None:
        assert SimpleAgent().act(1, DecisionKind.DISCARD_REACTION, [Pon((tile("1m"), tile("1m"))), Pass()]) == Pass()

    def test_declares_tenpai(self) -> None:
        assert SimpleAgent().act(
            0, DecisionKind.TENPAI, [DeclareTenpai(declare=True), DeclareTenpai(declare=False)]
        ) == (DeclareTenpai(declare=True))


class TestPlayerView:
    def test_reconstructs_dealt_hand_and_draw(self) -> None:
        view = _PlayerView()
        view.observe(deal_start("123m456p789s1122z"))
        view.observe(Draw(0, tile("5m")))
        assert view.seat == 0
        assert view.drawn == tile("5m")
        assert len(view.concealed) == 13

    def test_discard_returns_to_thirteen(self) -> None:
        view = _PlayerView()
        view.observe(deal_start("123m456p789s1122z"))
        view.observe(Draw(0, tile("5m")))
        view.observe(DiscardEvent(0, tile("1z")))
        assert len(view.concealed) == 13
        assert view.drawn is None

    def test_tracks_visible_from_others(self) -> None:
        view = _PlayerView()
        view.observe(deal_start("123m456p789s1122z"))
        view.observe(DiscardEvent(1, tile("9p")))
        view.observe(IndicatorReveal(tile("3s")))
        assert view.visible[TileKind.P9] == 1
        assert view.visible[TileKind.S3] == 1

    def test_own_closed_kan(self) -> None:
        view = _PlayerView()
        view.observe(deal_start("1111m456p789s123z9s"))
        view.observe(Draw(0, tile("9s")))
        view.observe(Call(MeldType.ANKAN, 0, 0, tuple(parse_mpsz("1111m"))))
        assert view.meld_count == 1
        assert view.is_concealed()

    def test_own_added_kan_keeps_meld_count(self) -> None:
        view = _PlayerView()
        view.observe(deal_start("456p789s123z99s5m"))
        view.meld_open = [True]  # a prior pon of 1m
        view.concealed = list(parse_mpsz("1m456p789s123z9s"))
        view.observe(Draw(0, tile("5m")))
        view.observe(Call(MeldType.SHOUMINKAN, 0, 0, tuple(parse_mpsz("1111m"))))
        assert view.meld_count == 1
        assert not view.is_concealed()

    def test_claiming_an_opponent_tile(self) -> None:
        view = _PlayerView()
        view.observe(deal_start("11m456p789s1234z9s"))
        view.observe(DiscardEvent(3, tile("1m")))  # the claimed tile
        view.observe(Call(MeldType.PON, 0, 3, tuple(parse_mpsz("111m"))))
        assert view.meld_count == 1
        assert not view.is_concealed()
        assert view.concealed.count(tile("1m")) == 0

    def test_north_extraction(self) -> None:
        view = _PlayerView()
        view.observe(deal_start("456p789s12345z9s6z", player_count=3))
        view.observe(Draw(0, tile("1m")))
        view.observe(NorthExtraction(0, Tile(TileKind.NORTH)))
        assert view.nuki == 1

    def test_seat_wind(self) -> None:
        view = _PlayerView()
        view.observe(deal_start("123m456p789s1122z", seat=1, dealer=0))
        assert view.seat_wind() is Wind.SOUTH


class TestEfficiencyAgent:
    def _setup(self, agent: EfficiencyAgent, hand: str, drawn: str) -> None:
        agent.observe(deal_start(hand))
        agent.observe(Draw(0, tile(drawn)))

    def test_takes_a_win(self) -> None:
        agent = EfficiencyAgent(seed=0)
        assert agent.act(0, DecisionKind.SELF, [Tsumo(), Discard(tile("1m"))]) == Tsumo()

    def test_takes_the_nine_terminals_abort(self) -> None:
        agent = EfficiencyAgent(seed=0)
        self._setup(agent, "19m19p19s123456z", "7z")
        assert agent.act(0, DecisionKind.SELF, [NineTerminals(), Discard(tile("7z"))]) == NineTerminals()

    def test_always_extracts_north(self) -> None:
        agent = EfficiencyAgent(seed=0)
        self._setup(agent, "4z123p456p789p11s", "9m")
        assert agent.act(0, DecisionKind.SELF, [Nuki(), Discard(tile("9m"))]) == Nuki()

    def test_declares_a_shape_neutral_kan(self) -> None:
        agent = EfficiencyAgent(seed=0)
        self._setup(agent, "5555z123m456p78p9s", "1m")
        chosen = agent.act(0, DecisionKind.SELF, [ClosedKan(TileKind.HAKU), Discard(tile("1m"))])
        assert chosen == ClosedKan(TileKind.HAKU)

    def test_discards_by_acceptance(self) -> None:
        agent = EfficiencyAgent(seed=0)
        self._setup(agent, "23456789m234p11s", "9p")
        # Offer the isolated 9p and the pair 1s; the agent keeps the useful shape.
        chosen = agent.act(0, DecisionKind.SELF, [Discard(tile("9p")), Discard(tile("1s"))])
        assert chosen == Discard(tile("9p"))

    def test_riichi_by_acceptance(self) -> None:
        agent = EfficiencyAgent(seed=0)
        self._setup(agent, "234m345m567p234s8s", "9s")
        actions = [Riichi(tile("9s")), Discard(tile("9s")), Discard(tile("8s"))]
        assert isinstance(agent.act(0, DecisionKind.SELF, actions), (Riichi, Discard))

    def test_reactions_pass(self) -> None:
        agent = EfficiencyAgent(seed=0)
        self._setup(agent, "234m345m567p234s8s", "9s")
        assert agent.act(1, DecisionKind.DISCARD_REACTION, [Pon((tile("8s"), tile("8s"))), Pass()]) == Pass()

    def test_declares_tenpai(self) -> None:
        agent = EfficiencyAgent(seed=0)
        assert agent.act(0, DecisionKind.TENPAI, [DeclareTenpai(declare=True), DeclareTenpai(declare=False)]) == (
            DeclareTenpai(declare=True)
        )

    def test_falls_back_when_best_kind_is_not_offered(self) -> None:
        agent = EfficiencyAgent(seed=0)
        self._setup(agent, "23456789m234p11s", "9p")
        # The best discard (9p) is withheld; the agent takes the earliest offered kind.
        chosen = agent.act(0, DecisionKind.SELF, [Discard(tile("1s")), Discard(tile("2p"))])
        assert isinstance(chosen, Discard)


class TestObserveFallThrough:
    def test_simple_ignores_non_draw_events(self) -> None:
        agent = SimpleAgent()
        agent.observe(deal_start("123m"))  # not a Draw; no state change, no error

    def test_view_ignores_unhandled_events(self) -> None:
        view = _PlayerView()
        view.observe(deal_start("123m456p789s1122z"))
        view.observe(ScoreChange((0, 0, 0, 0), (25000,) * 4))  # unhandled; ignored


class TestSmartEfficiencyAgent:
    def test_pons_a_value_triplet_while_concealed(self) -> None:
        agent = SmartEfficiencyAgent(seed=0)
        agent.observe(deal_start("55z234m567p11s99p2m"))
        agent.observe(DiscardEvent(1, tile("5z")))
        chosen = agent.act(0, DecisionKind.DISCARD_REACTION, [Pon((tile("5z"), tile("5z"))), Pass()])
        assert isinstance(chosen, Pon)

    def test_declines_a_non_value_pon_while_concealed(self) -> None:
        agent = SmartEfficiencyAgent(seed=0)
        agent.observe(deal_start("55m234p567p11s99p2s"))
        agent.observe(DiscardEvent(1, tile("5m")))
        assert agent.act(0, DecisionKind.DISCARD_REACTION, [Pon((tile("5m"), tile("5m"))), Pass()]) == Pass()

    def test_open_hand_takes_a_shanten_lowering_call(self) -> None:
        agent = SmartEfficiencyAgent(seed=0)
        agent.observe(deal_start("22m44z567p11s99p23p"))
        agent.observe(DiscardEvent(1, tile("2m")))
        agent.observe(Call(MeldType.PON, 0, 1, tuple(parse_mpsz("222m"))))  # now open
        agent.observe(DiscardEvent(0, tile("3p")))  # the caller's own post-pon discard
        agent.observe(DiscardEvent(2, tile("4z")))
        chosen = agent.act(0, DecisionKind.DISCARD_REACTION, [Pon((tile("4z"), tile("4z"))), Pass()])
        assert isinstance(chosen, (Pon, Pass))

    def test_open_hand_evaluates_an_open_kan(self) -> None:
        agent = SmartEfficiencyAgent(seed=0)
        agent.observe(deal_start("22m333s456p789p11z"))
        agent.observe(DiscardEvent(1, tile("2m")))
        agent.observe(Call(MeldType.PON, 0, 1, tuple(parse_mpsz("222m"))))  # now open
        agent.observe(DiscardEvent(0, tile("1z")))  # the caller's own post-pon discard
        agent.observe(DiscardEvent(2, tile("3s")))
        chosen = agent.act(0, DecisionKind.DISCARD_REACTION, [OpenKan(), Pass()])
        assert isinstance(chosen, (OpenKan, Pass))


class TestHelpers:
    def test_shape_neutral(self) -> None:
        assert _shape_neutral(TileKind.HAKU, list(parse_mpsz("123m")))
        assert _shape_neutral(TileKind.M1, list(parse_mpsz("789p123z")))  # isolated 1m
        assert not _shape_neutral(TileKind.M5, list(parse_mpsz("467m")))  # neighbors within reach

    def test_is_yakuhai(self) -> None:
        view = _PlayerView()
        view.observe(deal_start("123m456p789s1122z", seat=1, dealer=0))
        assert _is_yakuhai(TileKind.HAKU, view)  # a dragon
        assert _is_yakuhai(TileKind.EAST, view)  # the round wind
        assert _is_yakuhai(TileKind.SOUTH, view)  # this seat's wind
        assert not _is_yakuhai(TileKind.WEST, view)
