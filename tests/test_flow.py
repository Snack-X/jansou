"""Tests for deal flow and round resolution."""

from __future__ import annotations

from jansou.core.hand import CallSource, Hand, Meld, MeldType
from jansou.core.notation import parse_mpsz
from jansou.core.rules import Rules
from jansou.core.tiles import Tile, TileKind, Wind, full_tile_set
from jansou.game.actions import (
    AddedKan,
    Chii,
    ClosedKan,
    DeclareTenpai,
    Discard,
    NineTerminals,
    Nuki,
    OpenKan,
    Pass,
    Pon,
    Riichi,
    Ron,
    Tsumo,
)
from jansou.game.events import (
    Call,
    DealStart,
    Draw,
    IndicatorReveal,
    NorthExtraction,
    RiichiAccepted,
    Ryuukyoku,
    RyuukyokuKind,
    ScoreChange,
    Win,
)
from jansou.game.flow import (
    DecisionKind,
    Position,
    _find_liability,
    _kuikae_ban,
    _record_liability,
    _score_one_win,
    _WinRecord,
    new_deal,
    play_deal,
)
from jansou.game.state import Discard as DiscardMark
from jansou.game.state import GameState, Liability, PlayerState
from jansou.game.wall import Wall
from jansou.scoring.context import WinContext
from jansou.scoring.score import score
from jansou.scoring.yaku import Yaku

_JUNK = "159m159p159s1234z"


def discard_of(actions: list, tile: Tile) -> Discard | None:
    return next((action for action in actions if isinstance(action, Discard) and action.tile == tile), None)


def flow_state(
    hands: list[str],
    *,
    sequence: tuple | None = None,
    dealer: int = 0,
    player_count: int = 4,
    rules: Rules | None = None,
    drain: int = 0,
    **overrides: object,
) -> GameState:
    seq = sequence if sequence is not None else tuple(full_tile_set(player_count, aka_dora=False))
    wall = Wall(seq)
    for _ in range(drain):
        wall.draw_live()
    players = [PlayerState(concealed=list(parse_mpsz(hand))) for hand in hands]
    state = GameState(
        rules=rules or Rules(player_count=player_count),
        scores=[25000] * player_count,
        wall=wall,
        dealer=dealer,
        round_wind=Wind.EAST,
        round_number=1,
        honba=0,
        deposit_pool=0,
        players=players,
        current_player=dealer,
    )
    for name, value in overrides.items():
        setattr(state, name, value)
    return state


def sequence_with(**index_tiles: Tile) -> tuple:
    seq = list(full_tile_set(4, aka_dora=False))
    for key, tile in index_tiles.items():
        seq[int(key[1:])] = tile
    return tuple(seq)


def just_discard(_seat: int, kind: DecisionKind, actions: list) -> object:
    if kind is DecisionKind.SELF:
        return next(action for action in actions if isinstance(action, Discard))
    if kind is DecisionKind.TENPAI:
        return next(action for action in actions if isinstance(action, DeclareTenpai) and action.declare)
    return next(action for action in actions if isinstance(action, Pass))


class Recorder:
    def __init__(self) -> None:
        self.events: list = []

    def __call__(self, event: object) -> None:
        self.events.append(event)

    def of(self, event_type: type) -> list:
        return [event for event in self.events if isinstance(event, event_type)]


def test_full_deal_runs_to_exhaustive_draw() -> None:
    wall = Wall(tuple(full_tile_set(4, aka_dora=False)))
    state = new_deal(Rules(), wall, Position(0, Wind.EAST, 1, 0), [25000] * 4, 0)
    rec = Recorder()
    outcome = play_deal(state, just_discard, rec)
    assert outcome.is_draw
    assert not outcome.is_abortive
    assert rec.of(DealStart)
    assert len(rec.of(Draw)) == 70  # the live wall's ordinary draws
    assert rec.of(Ryuukyoku)[0].kind is RyuukyokuKind.EXHAUSTIVE
    assert rec.of(ScoreChange)


class TestWins:
    def test_dealer_tsumo(self) -> None:
        seq = sequence_with(i14=Tile(TileKind.S8))
        state = flow_state(["234m345m567p234s8s", _JUNK, _JUNK, _JUNK], sequence=seq)
        rec = Recorder()

        def decide(seat: int, kind: DecisionKind, actions: list) -> object:
            if kind is DecisionKind.SELF and any(isinstance(a, Tsumo) for a in actions):
                return Tsumo()
            return just_discard(seat, kind, actions)

        outcome = play_deal(state, decide, rec)
        assert outcome.winners == (0,)
        assert outcome.dealer_repeats
        assert not outcome.is_draw
        win = rec.of(Win)[0]
        assert win.seat == 0
        assert win.from_seat is None
        assert state.scores[0] > 25000

    def test_ron_off_the_dealer(self) -> None:
        seq = sequence_with(i14=Tile(TileKind.S8))
        state = flow_state([_JUNK, "234m345m567p234s8s", _JUNK, _JUNK], sequence=seq)
        state.players[1].riichi = True

        def decide(seat: int, kind: DecisionKind, actions: list) -> object:
            if kind is DecisionKind.DISCARD_REACTION and any(isinstance(a, Ron) for a in actions):
                return Ron()
            return just_discard(seat, kind, actions)

        rec = Recorder()
        outcome = play_deal(state, decide, rec)
        assert outcome.winners == (1,)
        assert not outcome.dealer_repeats
        assert state.scores[1] > 25000
        assert state.scores[0] < 25000


class TestRiichi:
    def test_riichi_is_accepted_and_banked(self) -> None:
        seq = sequence_with(i14=Tile(TileKind.M1))
        state = flow_state(["234m345m567p234s8s", _JUNK, _JUNK, _JUNK], sequence=seq)
        rec = Recorder()

        def decide(seat: int, kind: DecisionKind, actions: list) -> object:
            if seat == 0 and kind is DecisionKind.SELF and any(isinstance(a, Riichi) for a in actions):
                return next(a for a in actions if isinstance(a, Riichi))
            return just_discard(seat, kind, actions)

        outcome = play_deal(state, decide, rec)
        assert rec.of(RiichiAccepted)
        assert outcome.is_draw
        assert state.deposit_pool == 1000  # the banked deposit stays on the table at a draw
        assert sum(state.scores) + state.deposit_pool == 100_000  # points are conserved


class TestCalls:
    def test_pon_redirects_the_turn(self) -> None:
        seq = sequence_with(i14=Tile(TileKind.WEST))
        state = flow_state([_JUNK, "33z123m456p789s11z", _JUNK, _JUNK], sequence=seq)

        def decide(seat: int, kind: DecisionKind, actions: list) -> object:
            if seat == 0 and kind is DecisionKind.SELF and (west := discard_of(actions, Tile(TileKind.WEST))):
                return west
            if seat == 1 and kind is DecisionKind.DISCARD_REACTION and any(isinstance(a, Pon) for a in actions):
                return next(a for a in actions if isinstance(a, Pon))
            return just_discard(seat, kind, actions)

        rec = Recorder()
        play_deal(state, decide, rec)
        calls = rec.of(Call)
        assert calls
        assert calls[0].meld_type is MeldType.PON
        assert any(meld.type is MeldType.PON for meld in state.players[1].melds)
        assert state.players[0].discards[0].called_away  # the discard was claimed

    def test_open_kan_draws_replacement_and_reveals(self) -> None:
        seq = sequence_with(i14=Tile(TileKind.S5))
        state = flow_state([_JUNK, "555s123m456p789p1z", _JUNK, _JUNK], sequence=seq)

        def decide(seat: int, kind: DecisionKind, actions: list) -> object:
            if seat == 0 and kind is DecisionKind.SELF and (five := discard_of(actions, Tile(TileKind.S5))):
                return five
            if seat == 1 and kind is DecisionKind.DISCARD_REACTION and any(isinstance(a, OpenKan) for a in actions):
                return next(a for a in actions if isinstance(a, OpenKan))
            return just_discard(seat, kind, actions)

        rec = Recorder()
        play_deal(state, decide, rec)
        assert any(meld.type is MeldType.DAIMINKAN for meld in state.players[1].melds)
        assert state.kans == 1
        assert rec.of(IndicatorReveal)


class TestKans:
    def test_closed_kan_then_rinshan_tsumo(self) -> None:
        seq = sequence_with(i14=Tile(TileKind.M1))
        seq = list(seq)
        seq[0] = Tile(TileKind.S9)  # the replacement completes the tanki
        state = flow_state(["111m234p567p789p9s", _JUNK, _JUNK, _JUNK], sequence=tuple(seq))

        def decide(seat: int, kind: DecisionKind, actions: list) -> object:
            if kind is DecisionKind.SELF and any(isinstance(a, ClosedKan) for a in actions):
                return next(a for a in actions if isinstance(a, ClosedKan))
            if kind is DecisionKind.SELF and any(isinstance(a, Tsumo) for a in actions):
                return Tsumo()
            return just_discard(seat, kind, actions)

        rec = Recorder()
        outcome = play_deal(state, decide, rec)
        assert outcome.winners == (0,)
        assert Yaku.RINSHAN in {value.yaku for value in rec.of(Win)[0].result.yaku}
        assert any(meld.type is MeldType.ANKAN for meld in state.players[0].melds)

    def test_deferred_reveal_after_discard(self) -> None:
        seq = sequence_with(i14=Tile(TileKind.M1))
        rules = Rules(closed_kan_indicator_immediate=False)
        state = flow_state(["111m234p567p789p9s", _JUNK, _JUNK, _JUNK], sequence=seq, rules=rules)

        def decide(seat: int, kind: DecisionKind, actions: list) -> object:
            if seat == 0 and kind is DecisionKind.SELF and any(isinstance(a, ClosedKan) for a in actions):
                return next(a for a in actions if isinstance(a, ClosedKan))
            return just_discard(seat, kind, actions)

        rec = Recorder()
        play_deal(state, decide, rec)
        assert rec.of(IndicatorReveal)  # the deferred reveal still lands

    def test_chankan_robs_an_added_kan(self) -> None:
        pon = Meld(MeldType.PON, tuple(parse_mpsz("555s")), called=Tile(TileKind.S5), source=CallSource.TOIMEN)
        seq = sequence_with(i14=Tile(TileKind.EAST))
        state = flow_state(["5s123m456m789m", "234m345m678p234s5s", _JUNK, _JUNK], sequence=seq)
        state.players[0].melds = [pon]
        state.players[1].riichi = True

        def decide(seat: int, kind: DecisionKind, actions: list) -> object:
            if seat == 0 and kind is DecisionKind.SELF and any(isinstance(a, AddedKan) for a in actions):
                return next(a for a in actions if isinstance(a, AddedKan))
            if seat == 1 and kind is DecisionKind.ROBBED_KAN:
                return Ron()
            return just_discard(seat, kind, actions)

        rec = Recorder()
        outcome = play_deal(state, decide, rec)
        assert outcome.winners == (1,)
        assert Yaku.CHANKAN in {value.yaku for value in rec.of(Win)[0].result.yaku}
        assert state.kans == 0  # a robbed kan never completes


class TestSanma:
    def test_nuki_extraction(self) -> None:
        seq = sequence_with(i14=Tile(TileKind.M1))
        state = flow_state(
            ["4z123p456p789p111s", _JUNK, _JUNK],
            sequence=tuple(full_tile_set(3, aka_dora=False)[:14]) + seq[14:],
            player_count=3,
            rules=Rules(player_count=3),
        )

        def decide(seat: int, kind: DecisionKind, actions: list) -> object:
            if seat == 0 and kind is DecisionKind.SELF and any(isinstance(a, Nuki) for a in actions):
                return Nuki()
            return just_discard(seat, kind, actions)

        rec = Recorder()
        play_deal(state, decide, rec)
        assert rec.of(NorthExtraction)
        assert state.players[0].nuki_count == len(rec.of(NorthExtraction))

    def test_north_can_be_robbed(self) -> None:
        seq = sequence_with(i14=Tile(TileKind.M1))
        seq = tuple(full_tile_set(3, aka_dora=False)[:14]) + seq[14:]
        state = flow_state(
            ["4z123p456p789p111s", "44z123p456p789p11s", _JUNK],
            sequence=seq,
            player_count=3,
            rules=Rules(player_count=3),
        )
        state.players[1].riichi = True

        def decide(seat: int, kind: DecisionKind, actions: list) -> object:
            if seat == 0 and kind is DecisionKind.SELF and any(isinstance(a, Nuki) for a in actions):
                return Nuki()
            if seat == 1 and kind is DecisionKind.NORTH_REACTION:
                return Ron()
            return just_discard(seat, kind, actions)

        rec = Recorder()
        outcome = play_deal(state, decide, rec)
        assert outcome.winners == (1,)


class TestAborts:
    def test_nine_terminals(self) -> None:
        seq = sequence_with(i14=Tile(TileKind.CHUN))
        state = flow_state(["19m19p19s1234567z", _JUNK, _JUNK, _JUNK], sequence=seq)

        def decide(seat: int, kind: DecisionKind, actions: list) -> object:
            if kind is DecisionKind.SELF and any(isinstance(a, NineTerminals) for a in actions):
                return NineTerminals()
            return just_discard(seat, kind, actions)

        rec = Recorder()
        outcome = play_deal(state, decide, rec)
        assert outcome.is_abortive
        assert rec.of(Ryuukyoku)[0].kind is RyuukyokuKind.NINE_TERMINALS

    def test_four_winds(self) -> None:
        seq = list(full_tile_set(4, aka_dora=False))
        for index in (14, 15, 16, 17):
            seq[index] = Tile(TileKind.EAST)
        state = flow_state([_JUNK, _JUNK, _JUNK, _JUNK], sequence=tuple(seq))

        def decide(seat: int, kind: DecisionKind, actions: list) -> object:
            if kind is DecisionKind.SELF and (east := discard_of(actions, Tile(TileKind.EAST))):
                return east
            return just_discard(seat, kind, actions)

        rec = Recorder()
        outcome = play_deal(state, decide, rec)
        assert outcome.is_abortive
        assert rec.of(Ryuukyoku)[0].kind is RyuukyokuKind.FOUR_WINDS

    def test_four_riichi(self) -> None:
        seq = list(full_tile_set(4, aka_dora=False))
        for index in (14, 15, 16, 17):
            seq[index] = Tile(TileKind.M1)
        state = flow_state(["234m345m567p234s8s"] * 4, sequence=tuple(seq))

        def decide(seat: int, kind: DecisionKind, actions: list) -> object:
            if kind is DecisionKind.SELF and any(isinstance(a, Riichi) for a in actions):
                return next(a for a in actions if isinstance(a, Riichi))
            return just_discard(seat, kind, actions)

        rec = Recorder()
        play_deal(state, decide, rec)
        assert rec.of(Ryuukyoku)[0].kind is RyuukyokuKind.FOUR_RIICHI

    def test_four_kans_across_players(self) -> None:
        seq = sequence_with(i14=Tile(TileKind.M1))
        state = flow_state(["1111m234p567p789p", _JUNK, _JUNK, _JUNK], sequence=seq, kans=3)
        state.players[1].melds = [Meld(MeldType.ANKAN, tuple(parse_mpsz("2222m")))]
        state.players[2].melds = [Meld(MeldType.ANKAN, tuple(parse_mpsz("3333m")))]
        state.players[1].concealed = list(parse_mpsz("456p789p123s1z"))
        state.players[2].concealed = list(parse_mpsz("456p789p123s1z"))

        def decide(seat: int, kind: DecisionKind, actions: list) -> object:
            if seat == 0 and kind is DecisionKind.SELF and any(isinstance(a, ClosedKan) for a in actions):
                return next(a for a in actions if isinstance(a, ClosedKan))
            return just_discard(seat, kind, actions)

        rec = Recorder()
        outcome = play_deal(state, decide, rec)
        assert rec.of(Ryuukyoku)[0].kind is RyuukyokuKind.FOUR_KANS
        assert outcome.is_abortive

    def test_triple_ron_aborts(self) -> None:
        seq = sequence_with(i14=Tile(TileKind.S8))
        state = flow_state([_JUNK, "234m345m567p234s8s", "234m345m567p234s8s", "234m345m567p234s8s"], sequence=seq)
        for seat in (1, 2, 3):
            state.players[seat].riichi = True

        def decide(seat: int, kind: DecisionKind, actions: list) -> object:
            if kind is DecisionKind.DISCARD_REACTION and any(isinstance(a, Ron) for a in actions):
                return Ron()
            return just_discard(seat, kind, actions)

        rec = Recorder()
        outcome = play_deal(state, decide, rec)
        assert outcome.is_abortive
        assert rec.of(Ryuukyoku)[0].kind is RyuukyokuKind.TRIPLE_RON


class TestMultipleRon:
    def test_double_ron_both_win(self) -> None:
        seq = sequence_with(i14=Tile(TileKind.S8))
        rules = Rules(multiple_ron=True)
        state = flow_state([_JUNK, "234m345m567p234s8s", "234m345m567p234s8s", _JUNK], sequence=seq, rules=rules)
        state.players[1].riichi = True
        state.players[2].riichi = True

        def decide(seat: int, kind: DecisionKind, actions: list) -> object:
            if kind is DecisionKind.DISCARD_REACTION and any(isinstance(a, Ron) for a in actions):
                return Ron()
            return just_discard(seat, kind, actions)

        rec = Recorder()
        outcome = play_deal(state, decide, rec)
        assert set(outcome.winners) == {1, 2}
        assert len(rec.of(Win)) == 2

    def test_head_bump_keeps_nearest(self) -> None:
        seq = sequence_with(i14=Tile(TileKind.S8))
        state = flow_state([_JUNK, "234m345m567p234s8s", "234m345m567p234s8s", _JUNK], sequence=seq)
        state.players[1].riichi = True
        state.players[2].riichi = True

        def decide(seat: int, kind: DecisionKind, actions: list) -> object:
            if kind is DecisionKind.DISCARD_REACTION and any(isinstance(a, Ron) for a in actions):
                return Ron()
            return just_discard(seat, kind, actions)

        rec = Recorder()
        outcome = play_deal(state, decide, rec)
        assert outcome.winners == (1,)  # nearest to the discarder in turn order


class TestExhaustiveVariants:
    def test_formal_tenpai_counts_yakuless_shapes(self) -> None:
        # A concealed yakuless tenpai counts as ready only under the formal-tenpai flag.
        rules = Rules(formal_tenpai=True)
        state = flow_state(["123m456p789p123s9s", _JUNK, _JUNK, _JUNK], rules=rules, drain=121)
        seq = list(state.wall.sequence)
        seq[135] = Tile(TileKind.M1)
        state.wall._sequence = tuple(seq)

        rec = Recorder()
        outcome = play_deal(state, just_discard, rec)
        assert outcome.is_draw
        assert 0 in rec.of(Ryuukyoku)[0].counted_ready

    def test_nagashi_mangan(self) -> None:
        state = flow_state([_JUNK, _JUNK, _JUNK, _JUNK], drain=121)
        seq = list(state.wall.sequence)
        seq[135] = Tile(TileKind.EAST)  # a terminal-or-honor final discard
        state.wall._sequence = tuple(seq)
        state.players[0].discards = [DiscardMark(tile=Tile(TileKind.M1))]

        def decide(seat: int, kind: DecisionKind, actions: list) -> object:
            if seat == 0 and kind is DecisionKind.SELF and (east := discard_of(actions, Tile(TileKind.EAST))):
                return east
            return just_discard(seat, kind, actions)

        rec = Recorder()
        play_deal(state, decide, rec)
        assert state.scores[0] == 25000 + 12000  # dealer nagashi mangan

    def test_no_dealer_repeat_when_dealer_noten(self) -> None:
        rules = Rules(dealer_repeat_on_tenpai=True)
        state = flow_state([_JUNK, "234m345m567p234s8s", _JUNK, _JUNK], rules=rules)
        rec = Recorder()
        outcome = play_deal(state, just_discard, rec)
        assert not outcome.dealer_repeats  # the dealer's junk hand is noten


class TestPaoAndKuikae:
    def test_kuikae_ban_kinds(self) -> None:
        assert _kuikae_ban(Chii((Tile(TileKind.M4), Tile(TileKind.M5))), Tile(TileKind.M3)) == frozenset(
            {TileKind.M3, TileKind.M6}
        )
        assert _kuikae_ban(Chii((Tile(TileKind.M5), Tile(TileKind.M6))), Tile(TileKind.M7)) == frozenset(
            {TileKind.M7, TileKind.M4}
        )
        assert _kuikae_ban(Chii((Tile(TileKind.M1), Tile(TileKind.M2))), Tile(TileKind.M3)) == frozenset({TileKind.M3})
        assert _kuikae_ban(Chii((Tile(TileKind.M2), Tile(TileKind.M4))), Tile(TileKind.M3)) == frozenset({TileKind.M3})
        assert _kuikae_ban(Pon((Tile(TileKind.EAST), Tile(TileKind.EAST))), Tile(TileKind.EAST)) == frozenset(
            {TileKind.EAST}
        )

    def test_pao_tsumo_shifts_payment_to_the_liable(self) -> None:
        hand = Hand(tuple(parse_mpsz("555z666z777z789s11p")))
        winning = parse_mpsz("7s")[0]
        result = score(hand, winning, WinContext(rules=Rules(), is_tsumo=True, seat_wind=Wind.SOUTH))
        state = flow_state([_JUNK, _JUNK, _JUNK, _JUNK])
        state.players[0].melds = [
            Meld(MeldType.PON, tuple(parse_mpsz("555z")), called=Tile(TileKind.HAKU), source=CallSource.TOIMEN)
        ]
        state.liabilities = [
            Liability(beneficiary=1, payer=3, shape=Yaku.DAISUUSHI),  # a non-matching mark, skipped first
            Liability(beneficiary=0, payer=2, shape=Yaku.DAISANGEN),
        ]
        record = _WinRecord(seat=0, from_seat=None, tile=winning, result=result)
        deltas = [0, 0, 0, 0]
        _score_one_win(state, record, deltas)
        assert deltas[2] < 0  # the liable player pays
        assert deltas[1] == 0  # the others pay nothing
        assert deltas[3] == 0
        assert deltas[0] == -sum(deltas[i] for i in (1, 2, 3))

    def test_pao_ron_splits_the_base(self) -> None:
        hand = Hand(tuple(parse_mpsz("555z666z777z789s11p")))
        winning = parse_mpsz("7s")[0]
        result = score(hand, winning, WinContext(rules=Rules(), seat_wind=Wind.SOUTH))
        state = flow_state([_JUNK, _JUNK, _JUNK, _JUNK])
        state.liabilities = [Liability(beneficiary=0, payer=2, shape=Yaku.DAISANGEN)]
        record = _WinRecord(seat=0, from_seat=1, tile=winning, result=result)
        deltas = [0, 0, 0, 0]
        _score_one_win(state, record, deltas)
        assert deltas[1] < 0  # the discarder pays half
        assert deltas[2] < 0  # the liable pays half
        assert deltas[0] == 32000


def _dragon_pon(kind: TileKind) -> Meld:
    tile = Tile(kind)
    return Meld(MeldType.PON, (tile, tile, tile), called=tile, source=CallSource.TOIMEN)


class TestCoverageGaps:
    def test_added_kan_completes_unrobbed(self) -> None:
        chii = Meld(MeldType.CHII, tuple(parse_mpsz("123m")), called=Tile(TileKind.M1), source=CallSource.KAMICHA)
        pon = Meld(MeldType.PON, tuple(parse_mpsz("555s")), called=Tile(TileKind.S5), source=CallSource.TOIMEN)
        seq = sequence_with(i14=Tile(TileKind.EAST))
        state = flow_state(["5s456m789m", _JUNK, _JUNK, _JUNK], sequence=seq)
        state.players[0].melds = [chii, pon]  # the chii precedes the pon in _upgrade_pon's search

        def decide(seat: int, kind: DecisionKind, actions: list) -> object:
            if seat == 0 and kind is DecisionKind.SELF and any(isinstance(a, AddedKan) for a in actions):
                return next(a for a in actions if isinstance(a, AddedKan))
            return just_discard(seat, kind, actions)

        rec = Recorder()
        play_deal(state, decide, rec)
        assert any(meld.type is MeldType.SHOUMINKAN for meld in state.players[0].melds)
        assert state.kans == 1

    def test_kan_dora_off_reveals_nothing(self) -> None:
        seq = sequence_with(i14=Tile(TileKind.M1))
        rules = Rules(kan_dora=False)
        state = flow_state(["111m234p567p789p9s", _JUNK, _JUNK, _JUNK], sequence=seq, rules=rules)

        def decide(seat: int, kind: DecisionKind, actions: list) -> object:
            if seat == 0 and kind is DecisionKind.SELF and any(isinstance(a, ClosedKan) for a in actions):
                return next(a for a in actions if isinstance(a, ClosedKan))
            return just_discard(seat, kind, actions)

        rec = Recorder()
        play_deal(state, decide, rec)
        assert not rec.of(IndicatorReveal)

    def test_chii_with_kuikae_ban(self) -> None:
        seq = sequence_with(i14=Tile(TileKind.M3))
        rules = Rules(kuikae_ban=True)
        state = flow_state([_JUNK, "45m123p456p789p11z", _JUNK, _JUNK], sequence=seq, rules=rules)

        def decide(seat: int, kind: DecisionKind, actions: list) -> object:
            if seat == 0 and kind is DecisionKind.SELF and (three := discard_of(actions, Tile(TileKind.M3))):
                return three
            if seat == 1 and kind is DecisionKind.DISCARD_REACTION and any(isinstance(a, Chii) for a in actions):
                return next(a for a in actions if isinstance(a, Chii))
            return just_discard(seat, kind, actions)

        rec = Recorder()
        play_deal(state, decide, rec)
        assert any(call.meld_type is MeldType.CHII for call in rec.of(Call))

    def test_passing_a_robbable_ron_under_riichi_locks_furiten(self) -> None:
        pon = Meld(MeldType.PON, tuple(parse_mpsz("555s")), called=Tile(TileKind.S5), source=CallSource.TOIMEN)
        seq = sequence_with(i14=Tile(TileKind.EAST))
        state = flow_state(["5s123m456m789m", "234m345m678p234s5s", _JUNK, _JUNK], sequence=seq)
        state.players[0].melds = [pon]
        state.players[1].riichi = True

        def decide(seat: int, kind: DecisionKind, actions: list) -> object:
            if seat == 0 and kind is DecisionKind.SELF and any(isinstance(a, AddedKan) for a in actions):
                return next(a for a in actions if isinstance(a, AddedKan))
            if seat == 1 and kind is DecisionKind.ROBBED_KAN:
                return Pass()
            return just_discard(seat, kind, actions)

        rec = Recorder()
        play_deal(state, decide, rec)
        assert state.players[1].riichi_furiten

    def test_single_riichi_when_not_first_discard(self) -> None:
        seq = sequence_with(i14=Tile(TileKind.M1))
        state = flow_state(
            ["234m345m567p234s8s", _JUNK, _JUNK, _JUNK],
            sequence=seq,
            first_go_around=False,
        )
        state.players[0].discards = [DiscardMark(tile=Tile(TileKind.M9))]

        def decide(seat: int, kind: DecisionKind, actions: list) -> object:
            if seat == 0 and kind is DecisionKind.SELF and any(isinstance(a, Riichi) for a in actions):
                return next(a for a in actions if isinstance(a, Riichi))
            return just_discard(seat, kind, actions)

        rec = Recorder()
        play_deal(state, decide, rec)
        assert state.players[0].riichi
        assert not state.players[0].double_riichi

    def test_tenpai_declaration_can_decline(self) -> None:
        rules = Rules(tenpai_declaration=True)
        state = flow_state(["234m345m567p234s8s", _JUNK, _JUNK, _JUNK], rules=rules, drain=121)
        seq = list(state.wall.sequence)
        seq[135] = Tile(TileKind.M1)
        state.wall._sequence = tuple(seq)
        state.players[0].riichi = True

        def decide(seat: int, kind: DecisionKind, actions: list) -> object:
            if kind is DecisionKind.TENPAI:
                return DeclareTenpai(declare=False)
            return just_discard(seat, kind, actions)

        rec = Recorder()
        play_deal(state, decide, rec)
        assert 0 not in rec.of(Ryuukyoku)[0].counted_ready  # declined despite being ready

    def test_tenpai_declaration_can_accept(self) -> None:
        rules = Rules(tenpai_declaration=True)
        state = flow_state(["234m345m567p234s8s", _JUNK, _JUNK, _JUNK], rules=rules, drain=121)
        seq = list(state.wall.sequence)
        seq[135] = Tile(TileKind.M1)
        state.wall._sequence = tuple(seq)
        state.players[0].riichi = True

        rec = Recorder()
        play_deal(state, just_discard, rec)
        assert 0 in rec.of(Ryuukyoku)[0].counted_ready  # declared and ready

    def test_yakuless_tenpai_is_not_counted_ready(self) -> None:
        rules = Rules(dealer_repeat_on_tenpai=False, nagashi_mangan=False)
        state = flow_state(["123m456p789p123s9s", _JUNK, _JUNK, _JUNK], rules=rules, drain=121)
        seq = list(state.wall.sequence)
        seq[135] = Tile(TileKind.M1)
        state.wall._sequence = tuple(seq)

        rec = Recorder()
        outcome = play_deal(state, just_discard, rec)
        assert 0 not in rec.of(Ryuukyoku)[0].counted_ready  # a concealed yakuless tenpai fails the strict test
        assert not outcome.dealer_repeats

    def test_record_liability_shapes(self) -> None:
        state = flow_state([_JUNK, _JUNK, _JUNK, _JUNK])
        daisangen = [_dragon_pon(TileKind.HAKU), _dragon_pon(TileKind.HATSU), _dragon_pon(TileKind.CHUN)]
        state.players[0].melds = daisangen
        _record_liability(state, 0, 2, daisangen[2])
        assert state.liabilities[-1].shape is Yaku.DAISANGEN

        state.liabilities.clear()
        winds = [
            Meld(MeldType.PON, (Tile(w), Tile(w), Tile(w)), called=Tile(w), source=CallSource.TOIMEN)
            for w in (TileKind.EAST, TileKind.SOUTH, TileKind.WEST, TileKind.NORTH)
        ]
        state.players[1].melds = winds
        _record_liability(state, 1, 3, winds[3])
        assert state.liabilities[-1].shape is Yaku.DAISUUSHI

    def test_record_liability_suukantsu(self) -> None:
        rules = Rules(pao_suukantsu=True)
        state = flow_state([_JUNK, _JUNK, _JUNK, _JUNK], rules=rules)
        kans = [Meld(MeldType.ANKAN, (Tile(k),) * 4) for k in (TileKind.M1, TileKind.M2, TileKind.M3, TileKind.M4)]
        state.players[0].melds = kans
        _record_liability(state, 0, 1, kans[3])
        assert state.liabilities[-1].shape is Yaku.SUUKANTSU

    def test_find_liability_ignores_non_yakuman(self) -> None:
        hand = Hand(tuple(parse_mpsz("234m345m567p234s88s")))
        result = score(hand, parse_mpsz("2m")[0], WinContext(rules=Rules(), riichi=True, seat_wind=Wind.SOUTH))
        state = flow_state([_JUNK, _JUNK, _JUNK, _JUNK])
        state.liabilities = [Liability(beneficiary=0, payer=2, shape=Yaku.DAISANGEN)]
        record = _WinRecord(seat=0, from_seat=1, tile=parse_mpsz("2m")[0], result=result)
        assert _find_liability(state, record) is None
