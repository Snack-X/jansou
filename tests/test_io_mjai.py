"""Tests for the MJAI parser and its validation against real logs."""

from __future__ import annotations

import gzip
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

import json
from itertools import pairwise

from jansou.core.rules import Rules, preset
from jansou.core.tiles import Tile, TileKind
from jansou.game.actions import NineTerminals
from jansou.io.mjai import MjaiError, _dump_rules, parse_mjai
from jansou.io.paifu import Kita, Ryuukyoku, leftover_deposits, settled_scores
from jansou.io.replay import replay_round_decisions
from jansou.validation.check import check_paifu


class TestRealLogs:
    def test_four_player_wins_score_as_recorded(self, dataset: Path) -> None:
        self._check_all(sorted(dataset.glob("mjai/data/4p/*.jsonl")))

    def test_three_player_wins_score_as_recorded(self, dataset: Path) -> None:
        self._check_all(sorted(dataset.glob("mjai/data/3p/*.jsonl.gz")))

    def test_round_scores_chain_through_every_game(self, dataset: Path) -> None:
        # Each round's recorded start scores and deposit count must equal the
        # previous round's settlement -- the invariant the final standing rides on.
        files = sorted(dataset.glob("mjai/data/4p/*.jsonl")) + sorted(dataset.glob("mjai/data/3p/*.jsonl.gz"))
        if not files:
            pytest.skip("no mjai files present")
        for path in files:
            for current, following in pairwise(parse_mjai(path).rounds):
                assert settled_scores(current) == following.scores, path.name
                assert leftover_deposits(current) == following.riichi_sticks, path.name

    @staticmethod
    def _check_all(files: list[Path]) -> None:
        if not files:
            pytest.skip("no mjai files present")
        total = 0
        for path in files:
            verdicts = check_paifu(parse_mjai(path))
            total += len(verdicts)
            assert all(v.passed for v in verdicts), [(path.name, v.detail) for v in verdicts if not v.passed]
        assert total > 0


def _stream(*lines: str) -> str:
    """Join event lines into an MJAI JSONL document."""
    return "\n".join(lines)


_START = '{"type":"start_game"}'
_TSUMO = '{"type":"tsumo","actor":0,"pai":"C"}'
_END_KYOKU = '{"type":"end_kyoku"}'
_END_GAME = '{"type":"end_game"}'
#: Draws a four-player wall affords: 136 tiles, less the 14-tile dead wall and four dealt hands.
_LIVE_WALL_4P = 70


class TestParsing:
    def test_accepts_raw_text(self) -> None:
        text = _stream(_START, _START_KYOKU, _TSUMO, _HORA_TSUMO, _END_KYOKU, _END_GAME)
        paifu = parse_mjai(text)
        assert paifu.player_count == 4
        assert len(paifu.rounds) == 1

    def test_accepts_bytes_and_path(self, tmp_path: Path) -> None:
        text = _stream(_START, _START_KYOKU, _TSUMO, _HORA_TSUMO, _END_KYOKU)
        assert parse_mjai(text.encode()).player_count == 4
        file = tmp_path / "game.jsonl"
        file.write_text(text)
        assert parse_mjai(file).player_count == 4

    def test_accepts_gzip(self, tmp_path: Path) -> None:
        text = _stream(_START, _START_KYOKU, _TSUMO, _HORA_TSUMO, _END_KYOKU)
        assert parse_mjai(gzip.compress(text.encode())).player_count == 4
        file = tmp_path / "game.jsonl.gz"
        file.write_bytes(gzip.compress(text.encode()))
        assert parse_mjai(file).player_count == 4

    def test_blank_lines_ignored(self) -> None:
        text = _stream(_START, "", _START_KYOKU, "", _END_KYOKU)
        assert len(parse_mjai(text).rounds) == 1

    def test_stream_without_start_kyoku_rejected(self) -> None:
        with pytest.raises(MjaiError, match="start_kyoku"):
            parse_mjai(_stream(_START, _END_GAME))

    def test_final_scores_settled_from_the_rounds(self) -> None:
        text = _stream(_START, _START_KYOKU, _TSUMO, _HORA_TSUMO, _END_KYOKU, _END_GAME)
        paifu = parse_mjai(text)
        assert paifu.final_scores == (26500, 24500, 24500, 24500)
        assert paifu.final_points is None

    def test_final_scores_award_leftover_deposits_to_first(self) -> None:
        # Seat 0's riichi deposit is still on the table at game end; Tenhou
        # rules send it to the first place, seat 1 on the tie-break.
        reach = '{"type":"reach","actor":0}'
        dahai = '{"type":"dahai","actor":0,"pai":"2m","tsumogiri":false}'
        draw = '{"type":"ryukyoku","reason":"exhaustive_draw","deltas":[0,0,0,0]}'
        text = _stream(_START, _START_KYOKU, _TSUMO, reach, dahai, draw, _END_KYOKU, _END_GAME)
        assert parse_mjai(text).final_scores == (24000, 26000, 25000, 25000)

    def test_truncated_stream_settles_no_final_scores(self) -> None:
        # A start_kyoku without its end_kyoku parses to no rounds, and no standing.
        assert parse_mjai(_stream(_START, _START_KYOKU, _TSUMO)).final_scores is None

    def test_plain_stream_infers_the_tenhou_preset(self) -> None:
        paifu = parse_mjai(_stream(_START, _START_KYOKU, _TSUMO, _HORA_TSUMO, _END_KYOKU))
        assert paifu.rules == preset("tenhou")
        assert paifu.preset == "tenhou"
        assert paifu.names is None

    def test_start_game_rules_and_names_are_read_back(self) -> None:
        header = json.dumps(
            {"type": "start_game", "names": ["a", "b", "c", "d"], "rules": _dump_rules(preset("m-league"))}
        )
        paifu = parse_mjai(_stream(header, _START_KYOKU, _TSUMO, _HORA_TSUMO, _END_KYOKU))
        assert paifu.rules == preset("m-league")
        assert paifu.preset == "m-league"
        assert paifu.names == ("a", "b", "c", "d")

    def test_start_game_preset_name_alone_selects_the_rules(self) -> None:
        header = '{"type":"start_game","preset":"mahjong-soul"}'
        paifu = parse_mjai(_stream(header, _START_KYOKU, _TSUMO, _HORA_TSUMO, _END_KYOKU))
        assert paifu.rules == preset("mahjong-soul")
        assert paifu.preset == "mahjong-soul"

    def test_unknown_rule_keys_are_dropped(self) -> None:
        # A log written by a newer library parses; its extra flags are ignored.
        header = '{"type":"start_game","rules":{"kuitan":false,"a_future_flag":true}}'
        paifu = parse_mjai(_stream(header, _START_KYOKU, _TSUMO, _HORA_TSUMO, _END_KYOKU))
        assert paifu.rules == Rules(kuitan=False)

    def test_rules_player_count_must_match_the_dealt_hands(self) -> None:
        header = '{"type":"start_game","preset":"tenhou-3p"}'
        with pytest.raises(MjaiError, match="players"):
            parse_mjai(_stream(header, _START_KYOKU, _TSUMO, _HORA_TSUMO, _END_KYOKU))

    def test_end_game_scores_state_the_standing_outright(self) -> None:
        end = '{"type":"end_game","scores":[30000,20000,25000,25000]}'
        paifu = parse_mjai(_stream(_START, _START_KYOKU, _TSUMO, _HORA_TSUMO, _END_KYOKU, end))
        assert paifu.final_scores == (30000, 20000, 25000, 25000)

    def test_foreign_draw_reasons_normalize_to_canonical_kinds(self) -> None:
        draw = '{"type":"ryukyoku","reason":"sanchaho","deltas":[0,0,0,0]}'
        outcome = parse_mjai(_stream(_START_KYOKU, _TSUMO, draw, _END_KYOKU)).rounds[0].outcome
        assert isinstance(outcome, Ryuukyoku)
        assert outcome.kind == "ron3"

    def test_ryukyoku_round_has_no_wins(self) -> None:
        draw = '{"type":"ryukyoku","reason":"exhaustive_draw","deltas":[0,0,0,0],"tehais":[["1m"],null,null,null]}'
        assert check_paifu(parse_mjai(_stream(_START_KYOKU, draw, _END_KYOKU))) == []

    def test_reasonless_draw_with_the_wall_spent_is_exhaustive(self) -> None:
        # A Tenhou-sourced stream omits the reason. Seventy draws spend a
        # four-player wall, so this one is the wall running out.
        turns: list[str] = []
        for turn in range(_LIVE_WALL_4P):
            actor = turn % 4
            turns.append(f'{{"type":"tsumo","actor":{actor},"pai":"1m"}}')
            turns.append(f'{{"type":"dahai","actor":{actor},"pai":"1m","tsumogiri":true}}')
        draw = '{"type":"ryukyoku","deltas":[0,0,0,0]}'
        outcome = parse_mjai(_stream(_START_KYOKU, *turns, draw, _END_KYOKU)).rounds[0].outcome
        assert isinstance(outcome, Ryuukyoku)
        assert outcome.kind == "exhaustive"

    def test_reasonless_draw_with_the_wall_still_live_is_a_triple_ron(self) -> None:
        # Three seats ron the discard and the round is abandoned. MJAI records
        # neither the declarations nor a reason, and no count in the round
        # accounts for the draw, so a triple ron is what is left.
        dahai = '{"type":"dahai","actor":0,"pai":"2m","tsumogiri":false}'
        draw = '{"type":"ryukyoku","deltas":[0,0,0,0]}'
        outcome = parse_mjai(_stream(_START_KYOKU, _TSUMO, dahai, draw, _END_KYOKU)).rounds[0].outcome
        assert isinstance(outcome, Ryuukyoku)
        assert outcome.kind == "ron3"

    def test_reasonless_draw_after_four_kans_is_a_four_kan_abort(self) -> None:
        kans = [
            f'{{"type":"ankan","actor":{seat},"consumed":["{tile}","{tile}","{tile}","{tile}"]}}'
            for seat, tile in enumerate(("1m", "2m", "3m", "4m"))
        ]
        dahai = '{"type":"dahai","actor":3,"pai":"1p","tsumogiri":false}'
        draw = '{"type":"ryukyoku","deltas":[0,0,0,0]}'
        outcome = parse_mjai(_stream(_START_KYOKU, _TSUMO, *kans, dahai, draw, _END_KYOKU)).rounds[0].outcome
        assert isinstance(outcome, Ryuukyoku)
        assert outcome.kind == "kan4"

    def test_reasonless_draw_after_four_riichi_is_a_four_riichi_abort(self) -> None:
        reaches: list[str] = []
        for seat in range(4):
            reaches.append(f'{{"type":"reach","actor":{seat}}}')
            reaches.append(f'{{"type":"dahai","actor":{seat},"pai":"1p","tsumogiri":false}}')
        draw = '{"type":"ryukyoku","deltas":[0,0,0,0]}'
        outcome = parse_mjai(_stream(_START_KYOKU, _TSUMO, *reaches, draw, _END_KYOKU)).rounds[0].outcome
        assert isinstance(outcome, Ryuukyoku)
        assert outcome.kind == "reach4"

    def test_reasonless_draw_after_four_wind_discards_is_a_four_wind_abort(self) -> None:
        winds = [f'{{"type":"dahai","actor":{seat},"pai":"E","tsumogiri":false}}' for seat in range(4)]
        draw = '{"type":"ryukyoku","deltas":[0,0,0,0]}'
        outcome = parse_mjai(_stream(_START_KYOKU, _TSUMO, *winds, draw, _END_KYOKU)).rounds[0].outcome
        assert isinstance(outcome, Ryuukyoku)
        assert outcome.kind == "kaze4"

    def test_four_differing_first_discards_are_not_a_four_wind_abort(self) -> None:
        # The seats must all discard the *same* wind for the round to abort on it.
        discards = [
            f'{{"type":"dahai","actor":{seat},"pai":"{pai}","tsumogiri":false}}'
            for seat, pai in enumerate(("E", "S", "W", "N"))
        ]
        draw = '{"type":"ryukyoku","deltas":[0,0,0,0]}'
        outcome = parse_mjai(_stream(_START_KYOKU, _TSUMO, *discards, draw, _END_KYOKU)).rounds[0].outcome
        assert isinstance(outcome, Ryuukyoku)
        assert outcome.kind == "ron3"

    def test_a_call_rules_out_a_four_wind_abort(self) -> None:
        # A claimed tile interrupts the first go-around, so the winds cannot abort.
        winds = [f'{{"type":"dahai","actor":{seat},"pai":"E","tsumogiri":false}}' for seat in range(3)]
        pon = '{"type":"pon","actor":3,"target":2,"pai":"E","consumed":["E","E"]}'
        dahai = '{"type":"dahai","actor":3,"pai":"E","tsumogiri":false}'
        draw = '{"type":"ryukyoku","deltas":[0,0,0,0]}'
        stream = _stream(_START_KYOKU, _TSUMO, *winds, pon, dahai, draw, _END_KYOKU)
        outcome = parse_mjai(stream).rounds[0].outcome
        assert isinstance(outcome, Ryuukyoku)
        assert outcome.kind == "ron3"

    def test_reasonless_draw_before_a_discard_is_nine_terminals(self) -> None:
        # The same reason-less object, but falling on a first draw before its
        # discard, is a nine-terminals abort -- and replays as one.
        draw = '{"type":"ryukyoku","deltas":[0,0,0,0]}'
        paifu = parse_mjai(_stream(_NINE_TERMINALS_START, _DEALER_FIRST_DRAW, draw, _END_KYOKU))
        outcome = paifu.rounds[0].outcome
        assert isinstance(outcome, Ryuukyoku)
        assert outcome.kind == "yao9"
        replayed = list(replay_round_decisions(paifu, 0))
        assert isinstance(replayed[-1].chosen, NineTerminals)

    def test_kita_becomes_a_north_extraction(self) -> None:
        # A three-player nukidora: the kita event sets a North aside as a bonus tile.
        kita = '{"type":"kita","actor":0,"pai":"N"}'
        events = parse_mjai(_stream(_SANMA_START_KYOKU, kita, _END_KYOKU)).rounds[0].events
        assert [event for event in events if isinstance(event, Kita)] == [Kita(0)]

    def test_chankan_ron_wins_on_a_robbed_nuki(self) -> None:
        # Seat 1 extracts a North; seat 0 robs it, so the win is on the North and
        # not on seat 0's own earlier discard.
        stream = _stream(
            _SANMA_START_KYOKU,
            '{"type":"dahai","actor":0,"pai":"1s","tsumogiri":false}',
            '{"type":"tsumo","actor":1,"pai":"N"}',
            '{"type":"kita","actor":1,"pai":"N"}',
            '{"type":"hora","actor":0,"target":1,"deltas":[8000,-8000,0]}',
            _END_KYOKU,
        )
        agari = parse_mjai(stream).rounds[0].outcome[0]
        assert agari.winning_tile == Tile(TileKind.NORTH)
        assert (agari.winner, agari.from_seat) == (0, 1)

    def test_chankan_ron_wins_on_the_added_kan_tile(self) -> None:
        # Seat 2 promotes its 6m pon; seat 0 robs the added tile rather than a discard.
        stream = _stream(
            _START_KYOKU,
            '{"type":"pon","actor":2,"target":1,"pai":"6m","consumed":["6m","6m"]}',
            '{"type":"dahai","actor":2,"pai":"1p","tsumogiri":false}',
            '{"type":"tsumo","actor":2,"pai":"6m"}',
            '{"type":"kakan","actor":2,"pai":"6m","consumed":["6m","6m","6m"]}',
            '{"type":"hora","actor":0,"target":2,"deltas":[8000,0,-8000,0]}',
            _END_KYOKU,
        )
        agari = parse_mjai(stream).rounds[0].outcome[0]
        assert agari.winning_tile == Tile(TileKind.M6)
        assert (agari.winner, agari.from_seat) == (0, 2)

    def test_value_survives_a_split_payment(self) -> None:
        # Under pao the discarder and the liable player share the 32000; reading either
        # payer alone would halve it, so the value comes off the winner's own delta.
        start = (
            '{"type":"start_kyoku","bakaze":"E","dora_marker":"9s","kyoku":1,"honba":1,"kyotaku":1,"oya":0,'
            '"scores":[25000,25000,25000,25000],"tehais":['
            '["1m","9m","1p","9p","1s","9s","E","S","W","N","P","F","C"],'
            '["1m","9m","1p","9p","1s","9s","E","S","W","N","P","F","C"],'
            '["1m","9m","1p","9p","1s","9s","E","S","W","N","P","F","C"],'
            '["1m","9m","1p","9p","1s","9s","E","S","W","N","P","F","C"]]}'
        )
        dahai = '{"type":"dahai","actor":1,"pai":"1m","tsumogiri":false}'
        # 32000 + 300 honba + 1000 deposit reaches the winner; seat 2 is the liable player.
        hora = '{"type":"hora","actor":0,"target":1,"deltas":[33300,-16000,-16300,0]}'
        agari = parse_mjai(_stream(start, dahai, hora, _END_KYOKU)).rounds[0].outcome[0]
        assert agari.value == 32000


_SANMA_START_KYOKU = (
    '{"type":"start_kyoku","bakaze":"E","dora_marker":"1p","kyoku":1,"honba":0,"kyotaku":0,"oya":0,'
    '"scores":[35000,35000,35000],"tehais":['
    '["1m","9m","1p","2p","3p","4p","5p","6p","7p","8p","9p","1s","N"],'
    '["1m","9m","1p","2p","3p","4p","5p","6p","7p","8p","9p","1s","2s"],'
    '["1m","9m","1p","2p","3p","4p","5p","6p","7p","8p","9p","1s","2s"]]}'
)

_START_KYOKU = (
    '{"type":"start_kyoku","bakaze":"E","dora_marker":"9s","kyoku":1,"honba":0,"kyotaku":0,"oya":0,'
    '"scores":[25000,25000,25000,25000],"tehais":['
    '["2m","3m","4m","5p","6p","7p","2s","3s","4s","C","C","W","W"],'
    '["1m","1m","1m","2m","2m","2m","3m","3m","3m","5m","5m","6m","6m"],'
    '["1p","1p","1p","2p","2p","2p","3p","3p","3p","5s","5s","6s","6s"],'
    '["9m","9m","9m","8p","8p","8p","7s","7s","7s","E","E","S","S"]]}'
)

# The dealer holds all thirteen distinct terminals and honors, so a first draw
# lets them declare nine terminals.
_NINE_TERMINALS_START = (
    '{"type":"start_kyoku","bakaze":"E","dora_marker":"9s","kyoku":1,"honba":0,"kyotaku":0,"oya":0,'
    '"scores":[25000,25000,25000,25000],"tehais":['
    '["1m","9m","1p","9p","1s","9s","E","S","W","N","P","F","C"],'
    '["2m","3m","4m","5m","6m","7m","8m","2p","3p","4p","5p","6p","7p"],'
    '["2s","3s","4s","5s","6s","7s","8s","2m","3m","4m","5m","6m","7m"],'
    '["2p","3p","4p","5p","6p","7p","8p","2s","3s","4s","5s","6s","7s"]]}'
)
_DEALER_FIRST_DRAW = '{"type":"tsumo","actor":0,"pai":"8s"}'

# Seat 0 (dealer) draws a third W and tsumos a shanpon W/C wait: the only yaku is
# menzen tsumo (1 han), 30 fu with the concealed honor triplet, so a dealer tsumo
# collects 500 from each -- the deltas the fixture records.
_HORA_TSUMO = '{"type":"hora","actor":0,"target":0,"deltas":[1500,-500,-500,-500]}'
