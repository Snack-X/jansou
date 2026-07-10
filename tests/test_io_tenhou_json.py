"""Tests for the Tenhou JSON parser and its validation against real logs."""

from __future__ import annotations

import json
import urllib.parse
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from jansou.core.hand import MeldType
from jansou.io.paifu import Call, Discard, Draw
from jansou.io.tenhou_json import TenhouJsonError, parse_tenhou_json
from jansou.validation.check import check_paifu


def _game(result: list) -> dict:
    """A one-round four-player game whose dealer tsumos a shanpon wait."""
    dealer_tsumo_round = [
        [0, 0, 0],  # E1, no honba, no deposits
        [25000, 25000, 25000, 25000],
        [39],  # dora indicator 9s -> dora 1s, held by nobody
        [],
        [12, 13, 14, 25, 26, 27, 32, 33, 34, 47, 47, 43, 43],  # 234m 567p 234s CC WW
        [43],  # draws the third W
        [],  # ... and tsumos, so no discard
        [11, 15, 16, 18, 21, 23, 28, 31, 35, 38, 41, 44, 45],
        [],
        [],
        [19, 17, 22, 24, 29, 36, 37, 42, 46, 11, 15, 18, 21],
        [],
        [],
        [13, 16, 14, 25, 28, 31, 34, 37, 39, 42, 45, 46, 12],
        [],
        [],
        result,
    ]
    return {"title": ["", ""], "name": ["A", "B", "C", "D"], "rule": {"aka": 1}, "log": [dealer_tsumo_round]}


# The dealer draws once and tsumos untouched: this is Tenhou (blessing of heaven),
# a dealer yakuman worth 16000 from each.
_WIN = ["和了", [48000, -16000, -16000, -16000], [0, 0, 0, "役満16000点∀", "天和(役満)"]]


class TestRealLogs:
    def test_every_win_scores_as_recorded(self, dataset: Path) -> None:
        listing = dataset / "tenhou_json" / "list.txt"
        if not listing.is_file():
            pytest.skip("no tenhou_json listing present")
        total = 0
        for line in listing.read_text().splitlines():
            if not line.strip():
                continue
            verdicts = check_paifu(parse_tenhou_json(line.strip()))
            total += len(verdicts)
            assert all(verdict.passed for verdict in verdicts), [v.detail for v in verdicts if not v.passed]
        assert total > 0


class TestSources:
    def test_dict_source(self) -> None:
        verdicts = check_paifu(parse_tenhou_json(_game(_WIN)))
        assert len(verdicts) == 1
        assert verdicts[0].passed, verdicts[0].detail

    def test_json_text_source(self) -> None:
        assert check_paifu(parse_tenhou_json(json.dumps(_game(_WIN))))[0].passed

    def test_bytes_source(self) -> None:
        assert check_paifu(parse_tenhou_json(json.dumps(_game(_WIN)).encode()))[0].passed

    def test_url_source(self) -> None:
        encoded = urllib.parse.quote(json.dumps(_game(_WIN)))
        url = f"https://tenhou.net/6/#json={encoded}&ts=0"
        assert check_paifu(parse_tenhou_json(url))[0].passed

    def test_path_source(self, tmp_path: Path) -> None:
        file = tmp_path / "game.json"
        file.write_text(json.dumps(_game(_WIN)))
        assert check_paifu(parse_tenhou_json(str(file)))[0].passed


class TestOutcomes:
    def test_exhaustive_draw_has_no_wins(self) -> None:
        assert check_paifu(parse_tenhou_json(_game(["流局", [0, 0, 0, 0]]))) == []

    def test_abortive_draw_without_payments(self) -> None:
        assert check_paifu(parse_tenhou_json(_game(["九種九牌"]))) == []


class TestValue:
    def test_value_survives_a_split_payment(self) -> None:
        # Under pao the discarder and the liable player share the 32000; reading either
        # payer alone would halve it, so the value comes off the winner's own delta.
        # Seat 0 discards, seat 1 rons, seat 2 is liable. The winner's 33300 carries
        # 300 honba and one 1000-point deposit on top of the value.
        win = ["和了", [-16000, 33300, -16300, 0], [1, 0, 1, "役満32000点", "大三元(役満)"]]
        game = {
            "name": ["A", "B", "C", "D"],
            "log": [
                [
                    [0, 1, 1],  # E1, one honba, one deposit
                    [25000, 25000, 25000, 25000],
                    [39],
                    [],
                    [11, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 23, 24],
                    [25],  # the dealer draws 5p
                    [25],  # ... and discards it into the ron
                    [31, 32, 33, 34, 35, 36, 37, 38, 39, 41, 42, 43, 44],
                    [],
                    [],
                    [45, 46, 47, 11, 12, 13, 14, 15, 16, 17, 18, 19, 21],
                    [],
                    [],
                    [22, 23, 24, 25, 26, 27, 28, 29, 31, 32, 33, 34, 35],
                    [],
                    [],
                    win,
                ]
            ],
        }
        assert parse_tenhou_json(game).rounds[0].outcome[0].value == 32000


class TestCalls:
    def test_pon_claims_the_earlier_of_two_identical_discards(self) -> None:
        # The dealer discards 8p twice: seat 3 pons the first, seat 1 chiis the second.
        # Both calls sit at their seat's next slot when the first 8p lands, so the
        # reader must take the pon or it hands seat 1 a tile it never called.
        game = {
            "name": ["A", "B", "C", "D"],
            "log": [
                [
                    [0, 0, 0],
                    [25000, 25000, 25000, 25000],
                    [39],
                    [],
                    [11, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 23, 24],
                    [11, 12],
                    [28, 28],  # discards 8p, then 8p again
                    [26, 27, 31, 32, 33, 34, 35, 36, 37, 38, 39, 41, 42],
                    ["c282627"],  # chii of the second 8p
                    [45],
                    [43, 44, 45, 46, 47, 11, 12, 13, 14, 15, 16, 17, 18],
                    [13],
                    [46],
                    [28, 28, 19, 21, 22, 23, 24, 25, 26, 27, 29, 31, 32],
                    ["2828p28"],  # pon of the first 8p
                    [41],
                    ["流局", [0, 0, 0, 0]],
                ]
            ],
        }
        calls = [event for event in parse_tenhou_json(game).rounds[0].events if isinstance(event, Call)]
        assert [(call.seat, call.meld.type) for call in calls] == [(3, MeldType.PON), (1, MeldType.CHII)]

    def test_a_declined_call_is_left_for_the_later_discard(self) -> None:
        # Seat 2 discards East twice. Seat 0 holds a pon of it but passes on the first,
        # and seat 1's pon of South then skips seat 0's turn, so seat 0's pon still waits
        # when the second East lands. Claiming the first East strands seat 1's pon.
        game = {
            "name": ["A", "B", "C", "D"],
            "log": [
                [
                    [0, 0, 0],
                    [25000, 25000, 25000, 25000],
                    [39],
                    [],
                    [41, 41, 11, 12, 13, 14, 15, 16, 17, 18, 19, 21, 35],
                    [27, "41p4141"],  # pons the second East
                    [29, 35],
                    [42, 42, 22, 23, 24, 25, 26, 27, 28, 29, 31, 32, 33],
                    [38, "42p4242"],  # pons South, skipping seat 0
                    [19, 43],
                    [34, 36, 37, 44, 45, 46, 47, 11, 12, 13, 14, 15, 16],
                    [22, 41],
                    [41, 41],  # discards East, then East again
                    [17, 18, 19, 21, 22, 23, 24, 25, 26, 27, 28, 29, 31],
                    [34],
                    [42],
                    ["流局", [0, 0, 0, 0]],
                ]
            ],
        }
        events = parse_tenhou_json(game).rounds[0].events
        calls = [event for event in events if isinstance(event, Call)]
        assert [(call.seat, call.meld.type) for call in calls] == [(1, MeldType.PON), (0, MeldType.PON)]
        # Seat 0's pon lands after the second East, not the first.
        easts = [index for index, event in enumerate(events) if isinstance(event, Discard) and event.seat == 2]
        assert events.index(calls[1]) > easts[1]

    def test_round_whose_calls_no_turn_order_explains_is_rejected(self) -> None:
        game = {
            "name": ["A", "B", "C", "D"],
            "log": [
                [
                    [0, 0, 0],
                    [25000, 25000, 25000, 25000],
                    [39],
                    [],
                    [11, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 23, 24],
                    [11],
                    [19],  # nobody can chii a 9m
                    [26, 27, 31, 32, 33, 34, 35, 36, 37, 38, 39, 41, 42],
                    ["c282627"],  # a chii of an 8p that is never discarded
                    [45],
                    [43, 44, 45, 46, 47, 11, 12, 13, 14, 15, 16, 17, 18],
                    [],
                    [],
                    [28, 28, 19, 21, 22, 23, 24, 25, 26, 27, 29, 31, 32],
                    [],
                    [],
                    ["流局", [0, 0, 0, 0]],
                ]
            ],
        }
        with pytest.raises(TenhouJsonError, match="no turn order"):
            parse_tenhou_json(game)


class TestDiscards:
    def test_tsumogiri_stands_for_the_drawn_tile(self) -> None:
        # Discard code 60 is a tsumogiri: it names whatever the seat just drew.
        game = {
            "name": ["A", "B", "C", "D"],
            "log": [
                [
                    [0, 0, 0],
                    [25000, 25000, 25000, 25000],
                    [39],
                    [],
                    [11, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 23, 24],
                    [25],  # draws 5p
                    [60],  # ... and tsumogiris it
                    [31, 32, 33, 34, 35, 36, 37, 38, 39, 41, 42, 43, 44],
                    [],
                    [],
                    [45, 46, 47, 11, 12, 13, 14, 15, 16, 17, 18, 19, 21],
                    [],
                    [],
                    [22, 23, 24, 25, 26, 27, 28, 29, 31, 32, 33, 34, 35],
                    [],
                    [],
                    ["流局", [0, 0, 0, 0]],
                ]
            ],
        }
        events = parse_tenhou_json(game).rounds[0].events
        drawn = next(event for event in events if isinstance(event, Draw))
        discarded = next(event for event in events if isinstance(event, Discard))
        assert discarded.tile == drawn.tile
