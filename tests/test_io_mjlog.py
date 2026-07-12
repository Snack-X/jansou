"""Tests for the mjlog parser and its validation against real logs."""

from __future__ import annotations

import gzip
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from jansou.io.mjlog import MjlogError, parse_mjlog
from jansou.validation.check import check_paifu


def _sample(dataset: Path, pattern: str, limit: int) -> list[Path]:
    files = sorted(dataset.glob(pattern))
    if not files:
        pytest.skip(f"no files match {pattern}")
    return files[:limit]


class TestRealLogs:
    def test_four_player_wins_score_as_recorded(self, dataset: Path) -> None:
        self._check_all(_sample(dataset, "mjlog/data/*/*gm-00a9-*.xml", 40))

    def test_three_player_wins_score_as_recorded(self, dataset: Path) -> None:
        self._check_all(_sample(dataset, "mjlog/data/*/*gm-00b9-*.xml", 40))

    @staticmethod
    def _check_all(files: list[Path]) -> None:
        total = 0
        for path in files:
            verdicts = check_paifu(parse_mjlog(path))
            total += len(verdicts)
            failures = [(path.name, v.detail) for v in verdicts if not v.passed]
            assert not failures, failures
        assert total > 0


class TestStructure:
    def test_bytes_source(self) -> None:
        assert parse_mjlog(_MINIMAL.encode()).player_count == 4

    def test_gzip_source(self) -> None:
        assert parse_mjlog(gzip.compress(_MINIMAL.encode())).player_count == 4

    def test_path_source(self, tmp_path: Path) -> None:
        file = tmp_path / "game.mjlog"
        file.write_bytes(gzip.compress(_MINIMAL.encode()))
        assert parse_mjlog(file).player_count == 4

    def test_missing_go_rejected(self) -> None:
        with pytest.raises(MjlogError, match="GO"):
            parse_mjlog(b"<mjloggm ver='2.3'></mjloggm>")

    def test_exhaustive_draw_has_no_wins(self) -> None:
        paifu = parse_mjlog(_MINIMAL.encode())
        assert len(paifu.rounds) == 1
        assert check_paifu(paifu) == []

    def test_unhandled_body_element_is_skipped(self) -> None:
        # A REACH acceptance (step 2) matches no event branch and is passed over.
        doc = (
            '<mjloggm ver="2.3"><GO type="1" lobby="0"/><TAIKYOKU oya="0"/>'
            f'<INIT seed="0,0,0,0,0,134" ten="250,250,250,250" oya="0" '
            f'hai0="{_HAND0}" hai1="{_HAND1}" hai2="{_HAND2}" hai3="{_HAND3}"/>'
            '<REACH who="0" step="2"/>'
            '<RYUUKYOKU ba="0,0" sc="250,0,250,0,250,0,250,0" hai0="0,1,2,3,4,5,6,7,8,9,10,11,12" />'
            "</mjloggm>"
        )
        assert check_paifu(parse_mjlog(doc.encode())) == []


_HAND0 = ",".join(str(i) for i in range(13))
_HAND1 = ",".join(str(i) for i in range(13, 26))
_HAND2 = ",".join(str(i) for i in range(26, 39))
_HAND3 = ",".join(str(i) for i in range(39, 52))
_MINIMAL = (
    '<mjloggm ver="2.3"><GO type="1" lobby="0"/><TAIKYOKU oya="0"/>'
    f'<INIT seed="0,0,0,0,0,134" ten="250,250,250,250" oya="0" '
    f'hai0="{_HAND0}" hai1="{_HAND1}" hai2="{_HAND2}" hai3="{_HAND3}"/>'
    '<RYUUKYOKU ba="0,0" sc="250,0,250,0,250,0,250,0" hai0="0,1,2,3,4,5,6,7,8,9,10,11,12" />'
    "</mjloggm>"
)


class TestFinalStandings:
    def test_owari_parsed_to_the_standing(self) -> None:
        doc = _MINIMAL.replace('hai0="0,1,2', 'owari="387,48.7,311,11.1,118,-38.2,184,-21.6" hai0="0,1,2')
        paifu = parse_mjlog(doc.encode())
        assert paifu.final_scores == (38700, 31100, 11800, 18400)
        assert paifu.final_points == (48.7, 11.1, -38.2, -21.6)

    def test_missing_owari_leaves_none(self) -> None:
        paifu = parse_mjlog(_MINIMAL.encode())
        assert paifu.final_scores is None
        assert paifu.final_points is None

    def test_real_logs_state_their_standing(self, dataset: Path) -> None:
        for path in _sample(dataset, "mjlog/data/*/*gm-00a9-*.xml", 10):
            paifu = parse_mjlog(path)
            assert paifu.final_scores is not None
            assert paifu.final_points is not None
            assert len(paifu.final_scores) == paifu.player_count
            assert len(paifu.final_points) == paifu.player_count
            assert all(score % 100 == 0 for score in paifu.final_scores)
