"""Edge-branch tests: rule flags and malformed-input guards."""

from __future__ import annotations

import pytest

from jansou.io.mjai import MjaiError, parse_mjai
from jansou.io.mjlog import parse_mjlog
from jansou.io.tenhou_json import TenhouJsonError, parse_tenhou_json


def test_mjlog_no_aka_bit_disables_red_fives() -> None:
    doc = (
        '<mjloggm ver="2.3"><GO type="3"/>'  # 0x01 | 0x02 == PVP with red fives disabled
        f'<INIT seed="0,0,0,0,0,134" ten="250,250,250,250" oya="0" '
        f'hai0="{",".join(str(i) for i in range(13))}" hai1="{",".join(str(i) for i in range(13, 26))}" '
        f'hai2="{",".join(str(i) for i in range(26, 39))}" hai3="{",".join(str(i) for i in range(39, 52))}"/>'
        '<RYUUKYOKU ba="0,0" sc="250,0,250,0,250,0,250,0"/></mjloggm>'
    )
    assert not parse_mjlog(doc.encode()).rules.aka_dora


def test_mjai_hora_without_a_drawn_or_discarded_tile_is_rejected() -> None:
    start = (
        '{"type":"start_kyoku","bakaze":"E","dora_marker":"9s","kyoku":1,"honba":0,"kyotaku":0,"oya":0,'
        '"scores":[25000,25000,25000,25000],"tehais":[["1m"],["1m"],["1m"],["1m"]]}'
    )
    hora = '{"type":"hora","actor":0,"target":0,"deltas":[0,0,0,0]}'
    with pytest.raises(MjaiError, match="preceding draw"):
        parse_mjai(f'{start}\n{hora}\n{{"type":"end_kyoku"}}')


def test_tenhou_tsumogiri_without_a_draw_is_rejected() -> None:
    # Seat 1 chis the dealer's 1m -- a claim, so it draws no tile from the wall --
    # and then tsumogiris (60), which has no just-drawn tile to stand for.
    game = {
        "name": ["A", "B", "C", "D"],
        "log": [
            [
                [0, 0, 0],
                [25000, 25000, 25000, 25000],
                [39],
                [],
                [12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 23, 24, 25],
                [26],  # the dealer draws 6p
                [11],  # ... and discards 1m
                [12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 23, 24, 25],
                ["c111213"],  # seat 1 chis the 1m
                [60],  # ... then tsumogiris with nothing drawn
                [41, 42, 43, 44, 45, 46, 47, 11, 12, 13, 14, 15, 16],
                [],
                [],
                [17, 18, 19, 21, 22, 23, 24, 25, 26, 27, 28, 29, 31],
                [],
                [],
                ["流局", [0, 0, 0, 0]],
            ]
        ],
    }
    with pytest.raises(TenhouJsonError, match="tsumogiri"):
        parse_tenhou_json(game)
