"""Tests for exporting a played engine game as a Paifu."""

from __future__ import annotations

from itertools import pairwise

import pytest

from jansou.core.hand import Hand
from jansou.core.notation import parse_mpsz
from jansou.core.rules import RIICHI_DEPOSIT, Rules, preset
from jansou.core.tiles import Wind
from jansou.game.agents import RandomAgent, SmartEfficiencyAgent
from jansou.game.environment import Environment
from jansou.game.events import (
    DealStart,
    RyuukyokuKind,
    ScoreChange,
)
from jansou.game.events import (
    Discard as GameDiscard,
)
from jansou.game.events import (
    Draw as GameDraw,
)
from jansou.game.events import (
    Ryuukyoku as GameRyuukyoku,
)
from jansou.game.events import (
    Win as GameWin,
)
from jansou.io.from_game import paifu_from_game, paifu_from_records
from jansou.io.mjai import dump_mjai, parse_mjai
from jansou.io.mjlog import dump_mjlog, parse_mjlog
from jansou.io.paifu import Discard as PaifuDiscard
from jansou.io.paifu import Ryuukyoku, leftover_deposits, settled_scores
from jansou.io.tenhou_json import dump_tenhou_json, parse_tenhou_json
from jansou.scoring.context import WinContext
from jansou.scoring.score import score
from jansou.validation.check import check_paifu

_HONBA_RON_BONUS = 300
_FOUR_PLAYER = 4


class TestEngineExport:
    @pytest.mark.parametrize(("preset_name", "count"), [("tenhou", 4), ("tenhou-3p", 3)])
    def test_recorded_games_round_trip_through_every_writer(self, preset_name: str, count: int) -> None:
        checked = 0
        for seed in range(60):
            env = Environment(preset(preset_name), seed=seed)
            env.run([RandomAgent(seed + offset) for offset in range(count)])
            paifu = paifu_from_game(env)
            writers = [(dump_mjlog, lambda text: parse_mjlog(text.encode()))]
            if count == _FOUR_PLAYER:
                writers += [(dump_tenhou_json, parse_tenhou_json), (dump_mjai, parse_mjai)]
            for dump, parse in writers:
                for verdict in check_paifu(parse(dump(paifu))):
                    checked += 1
                    assert verdict.passed, verdict.detail
        assert checked > 0

    def test_engine_records_carry_the_game_identity_and_standing(self) -> None:
        env = Environment(preset("tenhou"), seed=1)
        result = env.run([RandomAgent(seat) for seat in range(4)], names=("N", "E", "S", "W"))
        paifu = paifu_from_game(env)
        # The settled standing reproduces the environment's own result.
        assert paifu.final_scores == result.scores
        assert paifu.final_points is None
        assert paifu.names == ("N", "E", "S", "W")
        assert paifu.preset == "tenhou"

    def test_round_scores_chain_through_recorded_games(self) -> None:
        # Seeds picked to exercise the two spots the bridge once got wrong:
        # deposits carried across rounds (1) and a double ron sweeping the pot (6).
        saw_carried = saw_multi_ron = False
        for seed in (1, 6):
            env = Environment(preset("tenhou"), seed=seed)
            env.run([SmartEfficiencyAgent(seed * 10 + offset) for offset in range(4)])
            paifu = paifu_from_game(env)
            saw_carried = saw_carried or any(round_log.riichi_sticks for round_log in paifu.rounds)
            saw_multi_ron = saw_multi_ron or any(
                not isinstance(round_log.outcome, Ryuukyoku) and len(round_log.outcome) > 1
                for round_log in paifu.rounds
            )
            for current, following in pairwise(paifu.rounds):
                assert settled_scores(current) == following.scores
                assert leftover_deposits(current) == following.riichi_sticks
        assert saw_carried
        assert saw_multi_ron

    def test_engine_game_reparses_whole_through_mjai(self) -> None:
        env = Environment(preset("tenhou"), seed=6)
        env.run([SmartEfficiencyAgent(60 + offset) for offset in range(4)], names=("a", "b", "c", "d"))
        paifu = paifu_from_game(env)
        reparsed = parse_mjai(dump_mjai(paifu))
        assert reparsed.rules == paifu.rules
        assert reparsed.names == paifu.names
        assert reparsed.preset == paifu.preset
        assert reparsed.final_scores == paifu.final_scores
        for ours, theirs in zip(paifu.rounds, reparsed.rounds, strict=True):
            assert theirs.scores == ours.scores
            assert theirs.riichi_sticks == ours.riichi_sticks
            if isinstance(ours.outcome, Ryuukyoku):
                assert isinstance(theirs.outcome, Ryuukyoku)
                assert theirs.outcome.kind == ours.outcome.kind
            else:
                assert not isinstance(theirs.outcome, Ryuukyoku)
                assert [agari.deltas for agari in theirs.outcome] == [agari.deltas for agari in ours.outcome]


def _win(
    concealed: str,
    winning: str,
    *,
    seat: int,
    dealer: int,
    sticks: int = 0,
    from_seat: int | None = 2,
    liable: int | None = None,
) -> GameWin:
    """A recorded win (ron from seat 2 unless told otherwise) scored in its replay context."""
    hand = Hand(tuple(parse_mpsz(concealed)), ())
    tile = parse_mpsz(winning)[0]
    context = WinContext(
        rules=preset("tenhou"),
        round_wind=Wind.EAST,
        seat_wind=Wind((seat - dealer) % 4),
        is_tsumo=from_seat is None,
        dora_indicators=(parse_mpsz("1z")[0],),
        riichi_sticks=sticks,
    )
    result = score(hand, tile, context)
    return GameWin(
        seat=seat,
        from_seat=from_seat,
        winning_tile=tile,
        hand=hand,
        result=result,
        ura_indicators=(),
        liable_seat=liable,
    )


class TestBridgeInternals:
    def _deal_start(self, honba: int, deposits: int) -> DealStart:
        thirteen = parse_mpsz("34m345p567p234s88s")  # each winner's hand before the ron
        return DealStart(
            dealer=0,
            round_wind=Wind.EAST,
            round_number=0,
            honba=honba,
            deposits=deposits,
            scores=(25000, 25000, 25000, 25000),
            hands=(
                tuple(thirteen),
                tuple(thirteen),
                tuple(parse_mpsz("119m119p119s1z")),
                tuple(parse_mpsz("119m119p119s1z")),
            ),
            dora_indicator=parse_mpsz("1z")[0],
        )

    def test_double_ron_splits_value_and_bonus_across_winners(self) -> None:
        won_tile = parse_mpsz("2m")[0]
        records = [
            [
                self._deal_start(honba=1, deposits=1000),
                GameDraw(2, won_tile),
                GameDiscard(2, won_tile),
                _win("234m345p567p234s88s", "2m", seat=0, dealer=0, sticks=1),
                _win("234m345p567p234s88s", "2m", seat=1, dealer=0, sticks=1),
                ScoreChange((3900, 3900, -7800, 0), (28900, 28900, 17200, 25000)),
            ]
        ]
        paifu = paifu_from_records(records, preset("tenhou"))
        outcome = paifu.rounds[0].outcome
        assert isinstance(outcome, tuple)
        assert len(outcome) == 2
        # The bonus and deposit ride the first winner only, counted in sticks.
        assert (outcome[0].honba, outcome[0].riichi_sticks) == (1, 1)
        assert (outcome[1].honba, outcome[1].riichi_sticks) == (0, 0)
        # The first winner's own delta carries the swept pot on top of value and bonus.
        assert outcome[0].deltas[0] == outcome[0].value + _HONBA_RON_BONUS + RIICHI_DEPOSIT
        # Each win's reconstructed deltas recover its value under the reader's honba rule.
        for agari in outcome:
            assert -agari.deltas[agari.from_seat] - _HONBA_RON_BONUS * agari.honba == agari.value
        # The written game re-parses to the same two validated wins.
        for verdict in check_paifu(parse_mjlog(dump_mjlog(paifu).encode())):
            assert verdict.passed, verdict.detail

    def test_carried_deposits_become_a_stick_count(self) -> None:
        # The engine's deposit pool is points; the record's riichi_sticks counts sticks.
        records = [[self._deal_start(honba=0, deposits=2000), GameRyuukyoku(kind=RyuukyokuKind.FOUR_WINDS)]]
        paifu = paifu_from_records(records, preset("tenhou"))
        assert paifu.rounds[0].riichi_sticks == 2

    def test_discard_marks_survive_into_the_record(self) -> None:
        tile = parse_mpsz("2m")[0]
        records = [
            [
                self._deal_start(honba=0, deposits=0),
                GameDraw(0, tile),
                GameDiscard(0, tile, tsumogiri=True),
                GameDraw(1, tile),
                GameDiscard(1, tile, riichi=True),
                GameRyuukyoku(kind=RyuukyokuKind.FOUR_WINDS),
            ]
        ]
        paifu = paifu_from_records(records, preset("tenhou"))
        discards = [event for event in paifu.rounds[0].events if isinstance(event, PaifuDiscard)]
        assert [(event.tsumogiri, event.riichi) for event in discards] == [(True, False), (False, True)]

    def test_abortive_draw_keeps_its_reason_and_moves_no_points(self) -> None:
        records = [[self._deal_start(honba=0, deposits=0), GameRyuukyoku(kind=RyuukyokuKind.FOUR_WINDS)]]
        paifu = paifu_from_records(records, preset("tenhou"))
        outcome = paifu.rounds[0].outcome
        assert isinstance(outcome, Ryuukyoku)
        assert outcome.kind == "kaze4"  # the canonical record name, not the engine's
        assert outcome.deltas == ()
        assert outcome.tenpai == (False, False, False, False)

    def test_nagashi_draw_gets_the_nagashi_record_kind(self) -> None:
        records = [[self._deal_start(honba=0, deposits=0), GameRyuukyoku(kind=RyuukyokuKind.NAGASHI)]]
        outcome = paifu_from_records(records, preset("tenhou")).rounds[0].outcome
        assert isinstance(outcome, Ryuukyoku)
        assert outcome.kind == "nm"

    def test_custom_rules_match_no_preset(self) -> None:
        records = [[self._deal_start(honba=0, deposits=0), GameRyuukyoku(kind=RyuukyokuKind.FOUR_WINDS)]]
        paifu = paifu_from_records(records, Rules(kiriage_mangan=True))
        assert paifu.preset is None

    def test_no_deals_settle_no_standing(self) -> None:
        assert paifu_from_records([], preset("tenhou")).final_scores is None

    def _pao_double_ron_records(self) -> list[list]:
        won_tile = parse_mpsz("2m")[0]
        return [
            [
                self._deal_start(honba=1, deposits=0),
                GameDraw(2, won_tile),
                GameDiscard(2, won_tile),
                _win("234m345p567p234s88s", "2m", seat=0, dealer=0, liable=3),
                _win("234m345p567p234s88s", "2m", seat=1, dealer=0),
                ScoreChange((3900, 3900, -7800, 0), (28900, 28900, 17200, 25000)),
            ]
        ]

    def test_pao_ron_reconstruction_splits_base_and_honba_to_liable(self) -> None:
        first = paifu_from_records(self._pao_double_ron_records(), preset("tenhou")).rounds[0].outcome[0]
        assert first.liable_seat == 3
        value = first.value
        assert first.deltas[0] == value + _HONBA_RON_BONUS
        # Tenhou rules: the liable seat pays half the base and the whole honba.
        assert first.deltas[3] == -(value // 2) - _HONBA_RON_BONUS
        assert first.deltas[2] == -(value - value // 2)

    def test_pao_ron_reconstruction_gives_honba_to_the_discarder_when_ruled(self) -> None:
        rules = Rules(pao_honba_to_liable=False)
        first = paifu_from_records(self._pao_double_ron_records(), rules).rounds[0].outcome[0]
        value = first.value
        assert first.deltas[3] == -(value // 2)
        assert first.deltas[2] == -(value - value // 2) - _HONBA_RON_BONUS

    def test_pao_tsumo_reconstruction_charges_the_liable_alone(self) -> None:
        won_tile = parse_mpsz("2m")[0]
        records = [
            [
                self._deal_start(honba=0, deposits=0),
                GameDraw(0, won_tile),
                _win("234m345p567p234s88s", "2m", seat=0, dealer=0, from_seat=None, liable=2),
            ]
        ]
        agari = paifu_from_records(records, preset("tenhou")).rounds[0].outcome[0]
        assert agari.liable_seat == 2
        assert agari.deltas[1] == 0
        assert agari.deltas[3] == 0
        assert agari.deltas[2] == -agari.deltas[0]
