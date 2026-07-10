"""Tests for the MJAI parser and its validation against real logs."""

from __future__ import annotations

import gzip
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from jansou.core.tiles import Tile, TileKind
from jansou.io.mjai import MjaiError, parse_mjai
from jansou.io.paifu import Kita
from jansou.validation.check import check_paifu


class TestRealLogs:
    def test_four_player_wins_score_as_recorded(self, dataset: Path) -> None:
        self._check_all(sorted(dataset.glob("mjai/data/4p/*.jsonl")))

    def test_three_player_wins_score_as_recorded(self, dataset: Path) -> None:
        self._check_all(sorted(dataset.glob("mjai/data/3p/*.jsonl.gz")))

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

    def test_ryukyoku_round_has_no_wins(self) -> None:
        draw = '{"type":"ryukyoku","reason":"exhaustive_draw","deltas":[0,0,0,0],"tehais":[["1m"],null,null,null]}'
        assert check_paifu(parse_mjai(_stream(_START_KYOKU, draw, _END_KYOKU))) == []

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

# Seat 0 (dealer) draws a third W and tsumos a shanpon W/C wait: the only yaku is
# menzen tsumo (1 han), 30 fu with the concealed honor triplet, so a dealer tsumo
# collects 500 from each -- the deltas the fixture records.
_HORA_TSUMO = '{"type":"hora","actor":0,"target":0,"deltas":[1500,-500,-500,-500]}'
