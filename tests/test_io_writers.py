"""Round-trip tests for the log writers: a written game re-parses to its scores."""

from __future__ import annotations

import json
import urllib.parse
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from jansou.core.hand import Hand
from jansou.core.notation import parse_mpsz
from jansou.core.rules import preset
from jansou.core.tiles import Tile, Wind
from jansou.io.mjai import dump_mjai, parse_mjai
from jansou.io.mjlog import dump_mjlog, parse_mjlog
from jansou.io.paifu import (
    Agari,
    Discard,
    DoraReveal,
    Draw,
    Event,
    Kita,
    Paifu,
    RoundLog,
    Ryuukyoku,
    replay_round,
)
from jansou.io.tenhou_json import (
    TenhouJsonError,
    dump_tenhou_json,
    dump_tenhou_json_url,
    parse_tenhou_json,
)
from jansou.scoring.score import score
from jansou.validation.check import check_paifu

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


def _tiles(text: str) -> tuple[Tile, ...]:
    return tuple(parse_mpsz(text))


def _round_with(
    events: tuple[Event, ...],
    winning_hand: str,
    winning_tile: str,
    *,
    from_seat: int,
    ura: tuple[Tile, ...] = (),
) -> RoundLog:
    """A dealer-seat win, its value and deltas filled in by scoring the replay.

    The events reach a winning hand for seat 0; the round is first built with a
    placeholder win so the replay can supply the exact context, then rebuilt
    with the value, fu, and deltas that context scores to.
    """
    hand = _hand(winning_hand)
    win_tile = parse_mpsz(winning_tile)[0]
    placeholder = Agari(winner=0, from_seat=from_seat, winning_tile=win_tile, hand=hand, ura_indicators=ura)
    scaffold = _round(events, (placeholder,))
    record = replay_round(scaffold, preset("tenhou"), 4)[0]
    result = score(record.hand, record.winning_tile, record.context)
    value = result.payment.total - result.payment.honba - result.payment.sticks
    agari = Agari(
        winner=0,
        from_seat=from_seat,
        winning_tile=win_tile,
        hand=hand,
        ura_indicators=ura,
        deltas=_deltas(result, from_seat),
        fu=result.fu.total,
        value=value,
    )
    return _round(events, (agari,))


def _hand(text: str) -> Hand:
    return Hand(_tiles(text), ())


def _deltas(result: object, from_seat: int) -> tuple[int, ...]:
    payment = result.payment  # type: ignore[attr-defined]
    deltas = [0, 0, 0, 0]
    if from_seat == 0:  # tsumo by the dealer
        for seat in range(1, 4):
            deltas[seat] = -payment.tsumo_non_dealer
            deltas[0] += payment.tsumo_non_dealer
    else:
        deltas[0] = payment.ron
        deltas[from_seat] = -payment.ron
    return tuple(deltas)


_FILLER_HAND = "123456789m1234p"


def _go_around() -> tuple[Event, ...]:
    """One turn each for seats 1-3 (draw then discard an honor) so rotation returns to the dealer."""
    events: list[Event] = []
    for seat, honor in ((1, "1z"), (2, "2z"), (3, "3z")):
        events.append(Draw(seat, parse_mpsz(honor)[0]))
        events.append(Discard(seat, parse_mpsz(honor)[0]))
    return tuple(events)


def _round(events: tuple[Event, ...], outcome: object) -> RoundLog:
    return RoundLog(
        round_wind=Wind.EAST,
        dealer=0,
        honba=0,
        riichi_sticks=0,
        initial_dora=parse_mpsz("1z")[0],
        scores=(25000, 25000, 25000, 25000),
        hands=(_tiles("123m456m789m11p23s"), _tiles(_FILLER_HAND), _tiles(_FILLER_HAND), _tiles(_FILLER_HAND)),
        events=events,
        outcome=outcome,
    )


def _game(round_log: RoundLog) -> Paifu:
    return Paifu(rules=preset("tenhou"), player_count=4, rounds=(round_log,))


_WRITERS: list[tuple[str, Callable, Callable]] = [
    ("mjlog", dump_mjlog, lambda text: parse_mjlog(text.encode())),
    ("tenhou", dump_tenhou_json, parse_tenhou_json),
    ("mjai", dump_mjai, parse_mjai),
]


def _round_trips(paifu: Paifu) -> None:
    """Assert every writer re-parses to a game whose wins all validate."""
    for _name, dump, parse in _WRITERS:
        reparsed = parse(dump(paifu))
        verdicts = check_paifu(reparsed)
        assert verdicts, "expected at least one win"
        assert all(v.passed for v in verdicts), [v.detail for v in verdicts if not v.passed]


class TestConstructedRoundTrips:
    def test_dealer_tsumo_with_a_discard_and_reveal(self) -> None:
        events = (
            Draw(0, parse_mpsz("3p")[0]),
            Discard(0, parse_mpsz("3p")[0]),
            *_go_around(),
            DoraReveal(parse_mpsz("9m")[0]),
            Draw(0, parse_mpsz("4s")[0]),
        )
        self_draw = _round_with(events, "123m456m789m11p234s", "4s", from_seat=0)
        _round_trips(_game(self_draw))

    def test_riichi_discard_reveals_ura(self) -> None:
        events = (
            Draw(0, parse_mpsz("3p")[0]),
            Discard(0, parse_mpsz("3p")[0], riichi=True),
            *_go_around(),
            Draw(0, parse_mpsz("4s")[0]),
        )
        won = _round_with(events, "123m456m789m11p234s", "4s", from_seat=0, ura=_tiles("8p"))
        _round_trips(_game(won))

    def test_ron_from_another_seat(self) -> None:
        # Seats 1 and 2 pass; seat 3 discards the winning 4s and seat 0 rons it.
        events = (
            Draw(0, parse_mpsz("3p")[0]),
            Discard(0, parse_mpsz("3p")[0]),
            Draw(1, parse_mpsz("1z")[0]),
            Discard(1, parse_mpsz("1z")[0]),
            Draw(2, parse_mpsz("2z")[0]),
            Discard(2, parse_mpsz("2z")[0]),
            Draw(3, parse_mpsz("4s")[0]),
            Discard(3, parse_mpsz("4s")[0]),
        )
        won = _round_with(events, "123m456m789m11p234s", "4s", from_seat=3)
        _round_trips(_game(won))

    def test_tenhou_viewer_url_round_trips(self) -> None:
        events = (
            Draw(0, parse_mpsz("3p")[0]),
            Discard(0, parse_mpsz("3p")[0]),
            *_go_around(),
            Draw(0, parse_mpsz("4s")[0]),
        )
        paifu = _game(_round_with(events, "123m456m789m11p234s", "4s", from_seat=0))
        url = dump_tenhou_json_url(paifu)
        assert url.startswith("https://tenhou.net/6/#json=")
        assert url.endswith("&ts=0")
        # The fragment decodes to exactly the object dump_tenhou_json writes.
        fragment = url.split("#json=", 1)[1].split("&", 1)[0]
        assert json.loads(urllib.parse.unquote(fragment)) == dump_tenhou_json(paifu)
        # And the parser reads the URL straight back.
        verdicts = check_paifu(parse_tenhou_json(url))
        assert verdicts
        assert all(verdict.passed for verdict in verdicts)


class TestDrawRoundTrips:
    def _draw_game(self, outcome: Ryuukyoku) -> Paifu:
        events = (Draw(0, parse_mpsz("3p")[0]), Discard(0, parse_mpsz("3p")[0]))
        return _game(_round(events, outcome))

    def test_exhaustive_draw_with_tenpai(self) -> None:
        outcome = Ryuukyoku(kind="exhaustive", deltas=(1500, -1500, 1500, -1500), tenpai=(True, False, True, False))
        for _name, dump, parse in _WRITERS:
            reparsed = parse(dump(self._draw_game(outcome)))
            assert reparsed.rounds[0].outcome  # a Ryuukyoku is truthy; no wins to check

    def test_abortive_draw_without_payments(self) -> None:
        outcome = Ryuukyoku(kind="nine_terminals", deltas=(), tenpai=())
        for _name, dump, parse in _WRITERS:
            reparsed = parse(dump(self._draw_game(outcome)))
            assert isinstance(reparsed.rounds[0].outcome, Ryuukyoku)


class TestFinalStandings:
    def test_mjlog_carries_a_standing_on_a_final_win(self) -> None:
        events = (*_go_around(), Draw(0, parse_mpsz("4s")[0]))
        base = _game(_round_with(events, "123m456m789m11p234s", "4s", from_seat=0))
        paifu = replace(base, final_scores=(38700, 31100, 11800, 18400), final_points=(48.7, 11.1, -38.2, -21.6))
        document = dump_mjlog(paifu)
        assert 'owari="387,48.7,311,11.1,118,-38.2,184,-21.6"' in document
        reparsed = parse_mjlog(document.encode())
        assert reparsed.final_scores == paifu.final_scores
        assert reparsed.final_points == paifu.final_points

    def test_mjlog_carries_a_negative_standing_on_a_final_draw(self) -> None:
        outcome = Ryuukyoku(kind="exhaustive", deltas=(0, 0, 0, 0), tenpai=(False, False, False, False))
        base = _game(_round((*_go_around(), Draw(0, parse_mpsz("1z")[0]), Discard(0, parse_mpsz("1z")[0])), outcome))
        paifu = replace(base, final_scores=(24500, -2000, 22500, 55000), final_points=(4.5, -52.0, -17.5, 65.0))
        document = dump_mjlog(paifu)
        assert 'owari="245,4.5,-20,-52.0,225,-17.5,550,65.0"' in document
        reparsed = parse_mjlog(document.encode())
        assert reparsed.final_scores == paifu.final_scores
        assert reparsed.final_points == paifu.final_points

    def test_absent_standing_writes_no_owari(self) -> None:
        outcome = Ryuukyoku(kind="exhaustive", deltas=(0, 0, 0, 0), tenpai=(False, False, False, False))
        base = _game(_round((*_go_around(), Draw(0, parse_mpsz("1z")[0]), Discard(0, parse_mpsz("1z")[0])), outcome))
        assert "owari" not in dump_mjlog(base)

    def test_real_standings_survive_the_mjlog_writer(self, dataset: Path) -> None:
        files = sorted(dataset.glob("mjlog/data/*/*.xml"))[:5]
        if not files:
            pytest.skip("no mjlog files present")
        for path in files:
            paifu = parse_mjlog(path)
            reparsed = parse_mjlog(dump_mjlog(paifu).encode())
            assert reparsed.final_scores == paifu.final_scores
            assert reparsed.final_points == paifu.final_points


class TestTsumogiriMarks:
    def _marked_game(self) -> Paifu:
        events = (
            Draw(0, parse_mpsz("3p")[0]),
            Discard(0, parse_mpsz("3p")[0], tsumogiri=True),  # the draw, given up unchanged
            Draw(1, parse_mpsz("1z")[0]),
            Discard(1, parse_mpsz("1z")[0]),  # a tedashi of a copy identical to the draw
            Draw(2, parse_mpsz("2z")[0]),
            Discard(2, parse_mpsz("5m")[0]),  # an ordinary tedashi
            Draw(3, parse_mpsz("3z")[0]),
            Discard(3, parse_mpsz("3z")[0], riichi=True, tsumogiri=True),  # a riichi tsumogiri
        )
        outcome = Ryuukyoku(kind="exhaustive", deltas=(0, 0, 0, 0), tenpai=(False, False, False, False))
        return _game(_round(events, outcome))

    def test_marks_round_trip_through_every_writer(self) -> None:
        for name, dump, parse in _WRITERS:
            reparsed = parse(dump(self._marked_game()))
            discards = [event for event in reparsed.rounds[0].events if isinstance(event, Discard)]
            flags = [(event.tsumogiri, event.riichi) for event in discards]
            assert flags == [(True, False), (False, False), (False, False), (True, True)], name

    def test_mjlog_conveys_the_mark_by_tile_index(self) -> None:
        text = dump_mjlog(self._marked_game())
        assert "<T44/>" in text  # seat 0 draws 3p (index 44)...
        assert "<D44/>" in text  # ...and its tsumogiri reuses the same copy
        assert "<U108/>" in text  # seat 1 draws 1z (index 108)...
        assert "<E109/>" in text  # ...and the identical tedashi takes another copy

    def test_mjlog_tsumogiri_without_a_draw_falls_back_to_the_canonical_copy(self) -> None:
        round_log = _round(
            (Discard(0, parse_mpsz("3p")[0], tsumogiri=True),),
            Ryuukyoku(kind="exhaustive", deltas=(0, 0, 0, 0), tenpai=()),
        )
        assert "<D44/>" in dump_mjlog(_game(round_log))


class TestGuardsAndFlags:
    def _sanma_game(self) -> Paifu:
        outcome = Ryuukyoku(kind="exhaustive", deltas=(0, 0, 0), tenpai=())
        round_log = RoundLog(
            round_wind=Wind.EAST,
            dealer=0,
            honba=0,
            riichi_sticks=0,
            initial_dora=parse_mpsz("1z")[0],
            scores=(35000, 35000, 35000),
            hands=(_tiles("123m"), _tiles("456m"), _tiles("789m")),
            events=(),
            outcome=outcome,
        )
        return Paifu(rules=preset("tenhou-3p"), player_count=3, rounds=(round_log,))

    def test_mjai_writes_three_player_games_with_the_north_bonus(self) -> None:
        # MJAI represents the sanma nuki dora as a `kita` event; it must survive the writer.
        events = (Draw(0, _tiles("1m")[0]), Kita(0), Draw(0, _tiles("9p")[0]))
        round_log = RoundLog(
            round_wind=Wind.EAST,
            dealer=0,
            honba=0,
            riichi_sticks=0,
            initial_dora=_tiles("1z")[0],
            scores=(35000, 35000, 35000),
            hands=(_tiles("19m"), _tiles("19p"), _tiles("19s")),
            events=events,
            outcome=Ryuukyoku(kind="exhaustive", deltas=(0, 0, 0), tenpai=()),
        )
        paifu = Paifu(rules=preset("tenhou-3p"), player_count=3, rounds=(round_log,))
        reparsed = parse_mjai(dump_mjai(paifu))
        assert reparsed.player_count == 3
        assert [event for event in reparsed.rounds[0].events if isinstance(event, Kita)] == [Kita(0)]

    def test_tenhou_rejects_three_player_games(self) -> None:
        with pytest.raises(TenhouJsonError, match="four-player"):
            dump_tenhou_json(self._sanma_game())

    def test_tenhou_url_rejects_three_player_games(self) -> None:
        with pytest.raises(TenhouJsonError, match="four-player"):
            dump_tenhou_json_url(self._sanma_game())

    def test_mjlog_preserves_the_no_aka_flag(self) -> None:
        events = (Draw(0, parse_mpsz("3p")[0]), Discard(0, parse_mpsz("3p")[0]), Draw(0, parse_mpsz("4s")[0]))
        won = _round_with(events, "123m456m789m11p234s", "4s", from_seat=0)
        paifu = Paifu(rules=replace(preset("tenhou"), aka_dora=False), player_count=4, rounds=(won,))
        assert not parse_mjlog(dump_mjlog(paifu).encode()).rules.aka_dora


class TestDatasetRoundTrips:
    """Every writer re-emits real games that re-parse to their recorded scores."""

    def _mjlog_files(self, dataset: Path, limit: int) -> list[Path]:
        files = sorted(dataset.glob("mjlog/data/*/*.xml"))
        if not files:
            pytest.skip("no mjlog files present")
        return files[:limit]

    def test_mjlog_writer_round_trips_real_games(self, dataset: Path) -> None:
        total = 0
        for path in self._mjlog_files(dataset, 40):
            paifu = parse_mjlog(path)
            reparsed = parse_mjlog(dump_mjlog(paifu).encode())
            verdicts = check_paifu(reparsed)
            total += len(verdicts)
            assert all(v.passed for v in verdicts), [v.detail for v in verdicts if not v.passed]
        assert total > 0

    def test_four_player_games_survive_the_tenhou_and_mjai_writers(self, dataset: Path) -> None:
        checked = 0
        for path in self._mjlog_files(dataset, 60):
            paifu = parse_mjlog(path)
            if paifu.player_count != 4:
                continue
            for dump, parse in ((dump_tenhou_json, parse_tenhou_json), (dump_mjai, parse_mjai)):
                verdicts = check_paifu(parse(dump(paifu)))
                checked += len(verdicts)
                assert all(v.passed for v in verdicts), [v.detail for v in verdicts if not v.passed]
        assert checked > 0

    def test_real_mjai_logs_round_trip(self, dataset: Path) -> None:
        files = sorted(dataset.glob("mjai/data/4p/*.jsonl"))
        if not files:
            pytest.skip("no mjai files present")
        for path in files[:10]:
            paifu = parse_mjai(path)
            verdicts = check_paifu(parse_mjai(dump_mjai(paifu)))
            assert all(v.passed for v in verdicts), [v.detail for v in verdicts if not v.passed]

    def test_real_three_player_mjai_logs_round_trip(self, dataset: Path) -> None:
        files = sorted(dataset.glob("mjai/data/3p/*.jsonl.gz"))
        if not files:
            pytest.skip("no three-player mjai files present")
        total = 0
        for path in files:
            paifu = parse_mjai(path)
            reparsed = parse_mjai(dump_mjai(paifu))
            assert reparsed.player_count == 3
            verdicts = check_paifu(reparsed)
            total += len(verdicts)
            assert all(v.passed for v in verdicts), [(path.name, v.detail) for v in verdicts if not v.passed]
        assert total > 0

    def test_real_tenhou_logs_round_trip(self, dataset: Path) -> None:
        listing = dataset / "tenhou_json" / "list.txt"
        if not listing.is_file():
            pytest.skip("no tenhou_json listing present")
        urls = [line for line in listing.read_text().splitlines() if line.strip() and not line.startswith("#")]
        for url in urls[:10]:
            paifu = parse_tenhou_json(url)
            verdicts = check_paifu(parse_tenhou_json(dump_tenhou_json(paifu)))
            assert all(v.passed for v in verdicts), [v.detail for v in verdicts if not v.passed]
