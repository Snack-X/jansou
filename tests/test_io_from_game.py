"""Tests for exporting a played engine game as a Paifu."""

from __future__ import annotations

import pytest

from jansou.core.hand import Hand
from jansou.core.notation import parse_mpsz
from jansou.core.rules import preset
from jansou.core.tiles import Wind
from jansou.game.agents import RandomAgent
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
from jansou.io.paifu import Ryuukyoku
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

    def test_engine_records_carry_no_final_standing(self) -> None:
        env = Environment(preset("tenhou"), seed=1)
        env.run([RandomAgent(seat) for seat in range(4)])
        paifu = paifu_from_game(env)
        assert paifu.final_scores is None
        assert paifu.final_points is None


def _win(concealed: str, winning: str, *, seat: int, dealer: int) -> GameWin:
    """A recorded ron win scored in the context its replay will reproduce."""
    hand = Hand(tuple(parse_mpsz(concealed)), ())
    tile = parse_mpsz(winning)[0]
    context = WinContext(
        rules=preset("tenhou"),
        round_wind=Wind.EAST,
        seat_wind=Wind((seat - dealer) % 4),
        is_tsumo=False,
        dora_indicators=(parse_mpsz("1z")[0],),
    )
    result = score(hand, tile, context)
    return GameWin(seat=seat, from_seat=2, winning_tile=tile, hand=hand, result=result, ura_indicators=())


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
                _win("234m345p567p234s88s", "2m", seat=0, dealer=0),
                _win("234m345p567p234s88s", "2m", seat=1, dealer=0),
                ScoreChange((3900, 3900, -7800, 0), (28900, 28900, 17200, 25000)),
            ]
        ]
        paifu = paifu_from_records(records, preset("tenhou"))
        outcome = paifu.rounds[0].outcome
        assert isinstance(outcome, tuple)
        assert len(outcome) == 2
        # The bonus and deposit ride the first winner only.
        assert (outcome[0].honba, outcome[0].riichi_sticks) == (1, 1000)
        assert (outcome[1].honba, outcome[1].riichi_sticks) == (0, 0)
        # Each win's reconstructed deltas recover its value under the reader's honba rule.
        for agari in outcome:
            assert -agari.deltas[agari.from_seat] - _HONBA_RON_BONUS * agari.honba == agari.value
        # The written game re-parses to the same two validated wins.
        for verdict in check_paifu(parse_mjlog(dump_mjlog(paifu).encode())):
            assert verdict.passed, verdict.detail

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
        assert outcome.kind == "four_winds"
        assert outcome.deltas == ()
        assert outcome.tenpai == (False, False, False, False)
