"""Tests for replaying parsed records as live decision streams."""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import replace

import pytest

from jansou.core.hand import CallSource, Meld, MeldType
from jansou.core.notation import parse_mpsz
from jansou.core.rules import Rules, preset
from jansou.core.tiles import Tile, TileKind, Wind, full_tile_set
from jansou.game.actions import (
    AddedKan,
    Chii,
    ClosedKan,
    Discard,
    NineTerminals,
    Nuki,
    OpenKan,
    Pass,
    Pon,
    Ron,
)
from jansou.game.agents import Agent, RandomAgent
from jansou.game.environment import Environment
from jansou.game.events import DealStart
from jansou.game.events import Draw as GameDraw
from jansou.game.flow import DecisionKind, play_deal
from jansou.game.state import GameState, PlayerState
from jansou.game.wall import Wall
from jansou.io.from_game import paifu_from_game, paifu_from_records
from jansou.io.paifu import Agari, DoraReveal, Draw, Paifu, RoundLog, Ryuukyoku
from jansou.io.paifu import Call as RecordCall
from jansou.io.paifu import Discard as RecordDiscard
from jansou.io.replay import ReplayError, replay_paifu, replay_round_decisions

_CLAIMS = (Pon, Chii, OpenKan, Ron)
_REACTION_KINDS = (DecisionKind.DISCARD_REACTION, DecisionKind.ROBBED_KAN, DecisionKind.NORTH_REACTION)


def _compare(recorded: list[tuple], replayed: list) -> None:
    """Require the replayed stream to mirror the recorded decisions."""
    assert len(recorded) == len(replayed)
    for old, new in zip(recorded, replayed, strict=True):
        seat, kind, actions, chosen = old
        assert seat == new.request.seat
        assert kind is new.request.kind
        assert actions == new.request.actions
        assert any(new.chosen is action for action in new.request.actions)
        if chosen != new.chosen:
            # A claim preempted by a higher-priority one is invisible to the
            # record, so the replay resolves that seat to a pass.
            assert kind in _REACTION_KINDS
            assert isinstance(chosen, _CLAIMS)
            assert new.chosen == Pass()


def _run_game(rules: Rules, seed: int, agents: list[Agent] | None = None) -> Environment:
    env = Environment(rules, seed=seed, record_decisions=True)
    env.run(agents or [RandomAgent(seed * 4 + offset) for offset in range(rules.player_count)])
    return env


def _game_round_trip(env: Environment) -> list:
    recorded = [
        (decision.seat, decision.kind, decision.actions, decision.chosen) for deal in env.decisions for decision in deal
    ]
    replayed = list(replay_paifu(paifu_from_game(env)))
    _compare(recorded, replayed)
    return replayed


@pytest.fixture(scope="module")
def draw_game() -> Environment:
    return _run_game(Rules(), 0)


@pytest.fixture(scope="module")
def win_game() -> Environment:
    return _run_game(preset("tenhou"), 2)  # ends deals with tsumo and ura reveals


@pytest.fixture(scope="module")
def tenpai_game() -> Environment:
    return _run_game(preset("renmei"), 2)  # reaches declared-tenpai draws


def _pinned_sequence(player_count: int, pins: dict[str, str]) -> tuple[Tile, ...]:
    """The unshuffled full set with chosen indices pinned to chosen tiles."""
    sequence = list(full_tile_set(player_count, aka_dora=False))
    for key, text in pins.items():
        sequence[int(key[1:])] = parse_mpsz(text)[0]
    return tuple(sequence)


def _scripted_round(
    hands: list[str], decide, *, rules: Rules | None = None, pins: dict[str, str] | None = None
) -> tuple[Paifu, list]:
    """Play one scripted deal, then require its record to replay identically."""
    # The scripted wall carries no red fives, so the rules must not expect them.
    rules = rules or Rules(player_count=len(hands), aka_dora=False)
    state = GameState(
        rules=rules,
        scores=[rules.starting_points] * rules.player_count,
        wall=Wall(_pinned_sequence(rules.player_count, pins or {})),
        dealer=0,
        round_wind=Wind.EAST,
        round_number=1,
        honba=0,
        deposit_pool=0,
        players=[PlayerState(concealed=list(parse_mpsz(hand))) for hand in hands],
        current_player=0,
    )
    recorded: list[tuple] = []
    events: list = []

    def deciding(seat: int, kind: DecisionKind, actions: list) -> object:
        chosen = decide(seat, kind, actions)
        recorded.append((seat, kind, tuple(actions), chosen))
        return chosen

    play_deal(state, deciding, events.append)
    paifu = paifu_from_records([events], rules)
    replayed = list(replay_paifu(paifu))
    _compare(recorded, replayed)
    return paifu, replayed


def _first(actions: list, wanted: type) -> object | None:
    return next((action for action in actions if isinstance(action, wanted)), None)


def _tsumogiri(actions: list) -> Discard:
    return next(action for action in actions if isinstance(action, Discard) and action.tsumogiri)


def _discard_of(actions: list, tile: Tile) -> Discard | None:
    return next(
        (action for action in actions if isinstance(action, Discard) and action.tile == tile and not action.tsumogiri),
        None,
    )


class _GreedyCaller(Agent):
    """Calls every chii and pon offered, picking randomly otherwise."""

    def __init__(self, seed: int) -> None:
        self._rng = random.Random(seed)

    def act(self, seat: int, kind: DecisionKind, actions: list) -> object:
        _ = (seat, kind)
        call = next((action for action in actions if isinstance(action, (Chii, Pon))), None)
        return call if call is not None else self._rng.choice(actions)


class TestGameRoundTrip:
    def test_draws_and_kans(self, draw_game: Environment) -> None:
        _game_round_trip(draw_game)

    def test_tsumo_and_ura(self, win_game: Environment) -> None:
        _game_round_trip(win_game)

    def test_ron(self) -> None:
        env = _run_game(preset("tenhou"), 17)
        replayed = _game_round_trip(env)
        assert any(isinstance(decision.chosen, Ron) for decision in replayed)

    def test_tenpai_declarations(self, tenpai_game: Environment) -> None:
        replayed = _game_round_trip(tenpai_game)
        assert any(decision.request.kind is DecisionKind.TENPAI for decision in replayed)

    def test_sanma_extractions(self) -> None:
        rules = preset("tenhou-3p")
        env = _run_game(rules, 2)
        replayed = _game_round_trip(env)
        assert any(isinstance(decision.chosen, Nuki) for decision in replayed)

    def test_call_greedy_swap_banned_menus(self) -> None:
        rules = preset("tenhou")
        env = _run_game(rules, 221, [_GreedyCaller(884 + offset) for offset in range(4)])
        replayed = _game_round_trip(env)
        assert any(isinstance(decision.chosen, Chii) for decision in replayed)
        assert any(isinstance(decision.chosen, Pon) for decision in replayed)


# --- Scripted rare shapes -------------------------------------------------------

_CHANKAN_HANDS = [
    "55s888m999m777z11p",  # claims the pon, then adds the drawn fourth 5s
    "5s234m234p234s678p",  # feeds the 5s
    "123m456p789p22p34s",  # waits on 2s/5s: robs the added kan
    "123m456p789s22s67s",  # waits on 5s/8s: declines the rob
]


def _chankan_decide(seat: int, kind: DecisionKind, actions: list) -> object:
    if kind is DecisionKind.SELF:
        kan = _first(actions, AddedKan)
        if kan is not None:
            return kan
        if seat == 0:
            return _discard_of(actions, Tile(TileKind.P1)) or _tsumogiri(actions)
        if seat == 1:
            return _discard_of(actions, Tile(TileKind.S5)) or _tsumogiri(actions)
        return _tsumogiri(actions)
    if kind is DecisionKind.DISCARD_REACTION and seat == 0:
        return _first(actions, Pon) or Pass()
    if kind is DecisionKind.ROBBED_KAN and seat == 2:
        return Ron()
    return Pass()


class TestScriptedRounds:
    def _chankan_paifu(self) -> tuple[Paifu, list]:
        return _scripted_round(_CHANKAN_HANDS, _chankan_decide, pins={"i19": "5s"})

    def test_chankan_robs_and_declines(self) -> None:
        _, replayed = self._chankan_paifu()
        robbed = [decision for decision in replayed if decision.request.kind is DecisionKind.ROBBED_KAN]
        assert [type(decision.chosen) for decision in robbed] == [Ron, Pass]

    def test_chankan_with_the_call_recorded(self) -> None:
        # An mjlog-style record keeps the robbed kan's call; the engine's own
        # record stops at the attempt. Both must replay to the same decisions.
        paifu, replayed = self._chankan_paifu()
        meld = Meld(
            MeldType.SHOUMINKAN,
            tuple(parse_mpsz("5555s")),
            called=Tile(TileKind.S5),
            source=CallSource.KAMICHA,
            added=Tile(TileKind.S5),
        )
        round_log = replace(paifu.rounds[0], events=(*paifu.rounds[0].events, RecordCall(0, meld)))
        recorded_style = replace(paifu, rounds=(round_log,))
        again = list(replay_paifu(recorded_style))
        assert [(d.request.seat, d.request.kind, d.request.actions, d.chosen) for d in again] == [
            (d.request.seat, d.request.kind, d.request.actions, d.chosen) for d in replayed
        ]

    def test_declined_chankan_completes_the_kan(self) -> None:
        hands = [
            "55s888m999m777z11p",  # adds the fourth 5s after the rob is declined
            "5s234m234p234s678p",  # feeds the 5s
            "123m456p789p22p34s",  # declines the rob, then sits furiten
            "123m123p13s555z66z",  # wins on the completed kan's replacement discard
        ]

        def decide(seat: int, kind: DecisionKind, actions: list) -> object:
            if kind is DecisionKind.SELF:
                kan = _first(actions, AddedKan)
                if kan is not None:
                    return kan
                if seat == 0:
                    return _discard_of(actions, Tile(TileKind.P1)) or _tsumogiri(actions)
                if seat == 1:
                    return _discard_of(actions, Tile(TileKind.S5)) or _tsumogiri(actions)
                return _tsumogiri(actions)
            if kind is DecisionKind.DISCARD_REACTION and seat == 0:
                return _first(actions, Pon) or Pass()
            if kind is DecisionKind.DISCARD_REACTION and seat == 3:
                return _first(actions, Ron) or Pass()
            return Pass()

        pins = {"i19": "5s", "i0": "2s", "i6": "8s"}  # i6: the kan indicator, kept off the crowded 2m
        _, replayed = _scripted_round(hands, decide, pins=pins)
        robbed = [decision for decision in replayed if decision.request.kind is DecisionKind.ROBBED_KAN]
        assert [type(decision.chosen) for decision in robbed] == [Pass]
        assert any(isinstance(decision.chosen, Ron) for decision in replayed)

    def test_open_kan_claim(self) -> None:
        hands = [
            "555s888m999m11p77z",  # open-kans the fed 5s
            "5s234m234p234s678p",  # feeds the 5s
            "19m19p19s1234z678m",  # bystander, holding no fifth 5s
            "123m123p13s555z66z",  # wins on the replacement discard
        ]

        def decide(seat: int, kind: DecisionKind, actions: list) -> object:
            if kind is DecisionKind.SELF:
                if seat == 1:
                    return _discard_of(actions, Tile(TileKind.S5)) or _tsumogiri(actions)
                return _tsumogiri(actions)
            if kind is DecisionKind.DISCARD_REACTION and seat == 0:
                return _first(actions, OpenKan) or Pass()
            if kind is DecisionKind.DISCARD_REACTION and seat == 3:
                return _first(actions, Ron) or Pass()
            return Pass()

        _, replayed = _scripted_round(hands, decide, pins={"i0": "2s", "i6": "8s"})
        assert any(isinstance(decision.chosen, OpenKan) for decision in replayed)

    def test_triple_ron_abort(self) -> None:
        hands = [
            "159m159p159s1234z",  # discards the 1m all three others win on
            "23m456p789p234s99s",
            "23m456s789s345p88p",
            "23m345m678p678s66p",
        ]

        def decide(seat: int, kind: DecisionKind, actions: list) -> object:
            _ = seat
            if kind is DecisionKind.SELF:
                return _discard_of(actions, Tile(TileKind.M1)) or _tsumogiri(actions)
            return _first(actions, Ron) or Pass()

        paifu, replayed = _scripted_round(hands, decide)
        assert isinstance(paifu.rounds[0].outcome, Ryuukyoku)
        assert sum(1 for decision in replayed if isinstance(decision.chosen, Ron)) == 3

    def test_nine_terminals(self) -> None:
        hands = ["19m19p19s1234567z", "159m159p159s1234z", "159m159p159s1234z", "159m159p159s1234z"]

        def decide(seat: int, kind: DecisionKind, actions: list) -> object:
            _ = seat
            if kind is DecisionKind.SELF:
                return _first(actions, NineTerminals) or _tsumogiri(actions)
            return Pass()

        paifu, replayed = _scripted_round(hands, decide)
        assert isinstance(paifu.rounds[0].outcome, Ryuukyoku)
        assert isinstance(replayed[-1].chosen, NineTerminals)

    def test_sanma_kita_streak_and_late_replacements(self) -> None:
        hands = [
            "44447777z888p22p",  # extracts four Norths, then kans the 7z
            "111999p111s666z2s",  # waits on the 2s the fifth replacement feeds
            "199m19p19s112233z",
        ]

        def decide(seat: int, kind: DecisionKind, actions: list) -> object:
            _ = seat
            if kind is DecisionKind.SELF:
                nuki = _first(actions, Nuki)
                if nuki is not None:
                    return nuki
                kan = _first(actions, ClosedKan)
                if kan is not None:
                    return kan
                return _tsumogiri(actions)
            return _first(actions, Ron) or Pass()

        pins = {"i0": "5p", "i1": "6p", "i2": "7p", "i3": "5s", "i53": "4p", "i107": "2s"}
        rules = replace(preset("tenhou-3p"), aka_dora=False)
        _, replayed = _scripted_round(hands, decide, rules=rules, pins=pins)
        assert sum(1 for decision in replayed if isinstance(decision.chosen, Nuki)) == 4
        assert any(isinstance(decision.chosen, ClosedKan) for decision in replayed)
        assert isinstance(replayed[-1].chosen, Ron)

    def test_sanma_robbed_north(self) -> None:
        hands = [
            "4z234p234s678s88s7p",  # extracts the North another seat wins on
            "111p999p111s555z4z",  # waits on the 4z alone
            "199m19p19s112233z",
        ]

        def decide(seat: int, kind: DecisionKind, actions: list) -> object:
            _ = seat
            if kind is DecisionKind.SELF:
                return _first(actions, Nuki) or _tsumogiri(actions)
            return _first(actions, Ron) or Pass()

        rules = replace(preset("tenhou-3p"), aka_dora=False)
        _, replayed = _scripted_round(hands, decide, rules=rules)
        # The robbed extraction never completes, so the record ends at the
        # attempt and the replay rebuilds it from the winning tile.
        assert isinstance(replayed[-2].chosen, Nuki)
        assert replayed[-1].request.kind is DecisionKind.NORTH_REACTION
        assert isinstance(replayed[-1].chosen, Ron)


# --- Masking and observation ----------------------------------------------------


class TestMaskingAndObservation:
    def test_requests_mask_events_for_the_deciding_seat(self, win_game: Environment) -> None:
        # A draw is always followed by the drawer's own decision, so the draws a
        # request carries are the deciding seat's; the deal starts prove the
        # masking, hiding every other seat's dealt hand.
        saw_own = saw_start = False
        for decision in replay_paifu(paifu_from_game(win_game)):
            for event in decision.request.events:
                if isinstance(event, GameDraw):
                    assert event.seat == decision.request.seat
                    assert event.tile is not None
                    saw_own = True
                elif isinstance(event, DealStart):
                    for owner, hand in enumerate(event.hands):
                        assert (hand is not None) == (owner == decision.request.seat)
                    saw_start = True
        assert saw_own
        assert saw_start

    def test_observe_receives_unmasked_events(self, win_game: Environment) -> None:
        events: list = []
        for _ in replay_paifu(paifu_from_game(win_game), observe=events.append):
            pass
        draws = [event for event in events if isinstance(event, GameDraw)]
        assert draws
        assert all(draw.tile is not None for draw in draws)
        start = next(event for event in events if isinstance(event, DealStart))
        assert all(hand is not None for hand in start.hands)


# --- Dirty records --------------------------------------------------------------


def _tampered(paifu: Paifu, index: int, **changes: object) -> Paifu:
    rounds = list(paifu.rounds)
    rounds[index] = replace(rounds[index], **changes)
    return replace(paifu, rounds=tuple(rounds))


def _win_round(paifu: Paifu) -> int:
    """The index of a round that ended in a win, leaving the wall unfinished."""
    return next(index for index, r in enumerate(paifu.rounds) if not isinstance(r.outcome, Ryuukyoku))


def _four_winds_paifu() -> Paifu:
    """A scripted round every seat opens by discarding East, aborting the deal."""

    def decide(seat: int, kind: DecisionKind, actions: list) -> object:
        _ = seat
        if kind is DecisionKind.SELF:
            return _discard_of(actions, Tile(TileKind.EAST)) or _tsumogiri(actions)
        return Pass()  # pragma: no cover - no reaction can arise on the four winds

    # The four junk hands already hold every 5m; keep the draws off that kind.
    paifu, _ = _scripted_round(["159m159p159s1234z"] * 4, decide, pins={"i16": "6m", "i17": "7m"})
    return paifu


def _spare_tile(round_log: RoundLog, rules: Rules) -> Tile:
    """A tile kind the round's record never shows a full count of."""
    used: Counter = Counter(tile.kind for hand in round_log.hands for tile in hand)
    used[round_log.initial_dora.kind] += 1
    for event in round_log.events:
        if isinstance(event, Draw):
            used[event.tile.kind] += 1
        elif isinstance(event, DoraReveal):
            used[event.indicator.kind] += 1
    kind = next(k for k in (t.kind for t in full_tile_set(rules.player_count, aka_dora=False)) if used[k] == 0)
    return Tile(kind)


class TestDirtyRecords:
    def test_truncated_after_a_discard(self, draw_game: Environment) -> None:
        paifu = paifu_from_game(draw_game)
        events = paifu.rounds[0].events
        cut = next(index for index, event in enumerate(events) if isinstance(event, RecordDiscard)) + 1
        tampered = _tampered(paifu, 0, events=events[:cut])
        with pytest.raises(ReplayError, match="beyond the record's end") as caught:
            list(replay_round_decisions(tampered, 0))
        assert caught.value.round_index == 0

    def test_truncated_after_a_draw(self, draw_game: Environment) -> None:
        paifu = paifu_from_game(draw_game)
        tampered = _tampered(paifu, 0, events=paifu.rounds[0].events[:1])
        with pytest.raises(ReplayError, match="still to act"):
            list(replay_round_decisions(tampered, 0))

    def test_desynchronized_draws(self, draw_game: Environment) -> None:
        paifu = paifu_from_game(draw_game)
        tampered = _tampered(paifu, 0, events=paifu.rounds[0].events[1:])  # drop the first draw
        with pytest.raises(ReplayError, match="where the record holds") as caught:
            list(replay_round_decisions(tampered, 0))
        assert caught.value.event_index is not None

    def test_short_hand(self, draw_game: Environment) -> None:
        paifu = paifu_from_game(draw_game)
        hands = list(paifu.rounds[0].hands)
        hands[0] = hands[0][:12]
        with pytest.raises(ReplayError, match="thirteen"):
            list(replay_round_decisions(_tampered(paifu, 0, hands=tuple(hands)), 0))

    def test_overused_tiles(self, draw_game: Environment) -> None:
        paifu = paifu_from_game(draw_game)
        hands = list(paifu.rounds[0].hands)
        hands[0] = tuple(parse_mpsz("1m") * 13)
        with pytest.raises(ReplayError, match="more copies"):
            list(replay_round_decisions(_tampered(paifu, 0, hands=tuple(hands)), 0))

    def test_more_draws_than_the_wall(self, win_game: Environment) -> None:
        paifu = paifu_from_game(win_game)
        index = _win_round(paifu)
        spare = _spare_tile(paifu.rounds[index], paifu.rules)
        padding = tuple(Draw(0, spare) for _ in range(140))
        tampered = _tampered(paifu, index, events=(*paifu.rounds[index].events, *padding))
        with pytest.raises(ReplayError, match="do not fit the wall"):
            list(replay_round_decisions(tampered, index))

    def test_unplayed_trailing_draw(self) -> None:
        # An aborted deal ends on the engine's initiative, so a trailing draw
        # is never consulted for any decision and survives to the end check.
        paifu = _four_winds_paifu()
        spare = _spare_tile(paifu.rounds[0], paifu.rules)
        tampered = _tampered(paifu, 0, events=(*paifu.rounds[0].events, Draw(0, spare)))
        with pytest.raises(ReplayError, match="still unplayed"):
            list(replay_round_decisions(tampered, 0))

    def test_unplayed_trailing_reveal(self, win_game: Environment) -> None:
        paifu = paifu_from_game(win_game)
        index = _win_round(paifu)
        spare = _spare_tile(paifu.rounds[index], paifu.rules)
        tampered = _tampered(paifu, index, events=(*paifu.rounds[index].events, DoraReveal(spare)))
        with pytest.raises(ReplayError, match="still unplayed"):
            list(replay_round_decisions(tampered, index))

    def test_missing_reveal(self, draw_game: Environment) -> None:
        paifu = paifu_from_game(draw_game)
        index = next(i for i, r in enumerate(paifu.rounds) if any(isinstance(event, DoraReveal) for event in r.events))
        stripped = tuple(event for event in paifu.rounds[index].events if not isinstance(event, DoraReveal))
        tampered = _tampered(paifu, index, events=stripped)
        with pytest.raises(ReplayError, match="the record does not have"):
            list(replay_round_decisions(tampered, index))

    def test_too_many_reveals(self, draw_game: Environment) -> None:
        paifu = paifu_from_game(draw_game)
        extra = tuple(DoraReveal(Tile(TileKind.M1)) for _ in range(5))
        tampered = _tampered(paifu, 0, events=(*paifu.rounds[0].events, *extra))
        with pytest.raises(ReplayError, match="more dora indicators"):
            list(replay_round_decisions(tampered, 0))

    def test_win_outcome_at_a_declared_draw(self, tenpai_game: Environment) -> None:
        paifu = paifu_from_game(tenpai_game)
        index = next(
            i
            for i, deal in enumerate(tenpai_game.decisions)
            if any(decision.kind is DecisionKind.TENPAI for decision in deal)
        )
        # The fake winning tile is never placed in the wall, so any tile works.
        fake = (Agari(winner=0, from_seat=0, winning_tile=Tile(TileKind.M1)),)
        with pytest.raises(ReplayError, match="readiness declaration"):
            list(replay_round_decisions(_tampered(paifu, index, outcome=fake), index))

    def test_impossible_recorded_choice(self, draw_game: Environment) -> None:
        paifu = paifu_from_game(draw_game)
        events = list(paifu.rounds[0].events)
        index = next(i for i, event in enumerate(events) if isinstance(event, RecordDiscard))
        events[index] = replace(events[index], riichi=True)  # a riichi the opening hand cannot declare
        with pytest.raises(ReplayError, match="not among the offered") as caught:
            list(replay_round_decisions(_tampered(paifu, 0, events=tuple(events)), 0))
        assert caught.value.event_index == index

    def test_foreign_failures_are_wrapped(self, draw_game: Environment) -> None:
        paifu = paifu_from_game(draw_game)
        tampered = _tampered(paifu, 0, riichi_sticks=None)  # not even a number
        with pytest.raises(ReplayError) as caught:
            list(replay_round_decisions(tampered, 0))
        assert caught.value.round_index == 0
        assert caught.value.__cause__ is not None
