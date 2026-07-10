"""Tests for the batch validation CLI."""

from __future__ import annotations

import gzip
import io
import json
import sys
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from jansou.validation import cli


class _Tty(io.StringIO):
    """A stderr stand-in that reports itself as an interactive terminal."""

    def isatty(self) -> bool:
        return True


_MJAI_LINES = (
    '{"type":"start_game"}',
    '{"type":"start_kyoku","bakaze":"E","dora_marker":"9s","kyoku":1,"honba":0,"kyotaku":0,"oya":0,'
    '"scores":[25000,25000,25000,25000],"tehais":['
    '["2m","3m","4m","5p","6p","7p","2s","3s","4s","C","C","W","W"],'
    '["1m","2m","3m","4m","5m","6m","7m","8m","9m","1p","2p","3p","4p"],'
    '["5p","6p","7p","8p","9p","1s","2s","3s","4s","5s","6s","7s","8s"],'
    '["9s","E","S","N","P","F","1m","9m","1p","9p","1s","9s","E"]]}',
    '{"type":"tsumo","actor":0,"pai":"C"}',
    '{"type":"hora","actor":0,"target":0,"deltas":[48000,-16000,-16000,-16000]}',
    '{"type":"end_kyoku"}',
)
_MJAI = "\n".join(_MJAI_LINES)

_TENHOU = {
    "title": ["", ""],
    "name": ["A", "B", "C", "D"],
    "rule": {"aka": 1},
    "log": [
        [
            [0, 0, 0],
            [25000, 25000, 25000, 25000],
            [39],
            [],
            [12, 13, 14, 25, 26, 27, 32, 33, 34, 47, 47, 43, 43],
            [43],
            [],
            [11, 15, 16, 18, 21, 23, 28, 31, 35, 38, 41, 44, 45],
            [],
            [],
            [19, 17, 22, 24, 29, 36, 37, 42, 46, 11, 15, 18, 21],
            [],
            [],
            [13, 16, 14, 25, 28, 31, 34, 37, 39, 42, 45, 46, 12],
            [],
            [],
            ["和了", [48000, -16000, -16000, -16000], [0, 0, 0, "役満16000点∀", "天和(役満)"]],
        ]
    ],
}

_MJLOG_DRAW = (
    '<mjloggm ver="2.3"><GO type="1"/><INIT seed="0,0,0,0,0,134" ten="250,250,250,250" oya="0" '
    f'hai0="{",".join(str(i) for i in range(13))}" hai1="{",".join(str(i) for i in range(13, 26))}" '
    f'hai2="{",".join(str(i) for i in range(26, 39))}" hai3="{",".join(str(i) for i in range(39, 52))}"/>'
    '<RYUUKYOKU ba="0,0" sc="250,0,250,0,250,0,250,0"/></mjloggm>'
)


@pytest.fixture
def mixed_dir(tmp_path: Path) -> Path:
    (tmp_path / "game.jsonl").write_text(_MJAI)
    (tmp_path / "game.json").write_text(json.dumps(_TENHOU))
    (tmp_path / "urls.txt").write_text(f"https://tenhou.net/6/#json={json.dumps(_TENHOU)}&ts=0\n")
    (tmp_path / "game.mjlog").write_bytes(gzip.compress(_MJLOG_DRAW.encode()))
    (tmp_path / "plain.xml").write_text(_MJLOG_DRAW)
    return tmp_path


class TestFormatDetection:
    def test_each_format_is_recognized(self, mixed_dir: Path) -> None:
        for name in ("game.jsonl", "game.json", "urls.txt", "game.mjlog", "plain.xml"):
            games = list(cli.iter_games(mixed_dir / name))
            assert games
            assert all(game.rounds for game in games)


class TestRun:
    def test_all_pass(self, mixed_dir: Path) -> None:
        summary = cli.run([str(mixed_dir / "*")])
        assert summary.ok
        assert summary.passed >= 3
        assert summary.failed == 0

    def test_parallel_matches_serial(self, mixed_dir: Path) -> None:
        serial = cli.run([str(mixed_dir / "*")], jobs=1)
        parallel = cli.run([str(mixed_dir / "*")], jobs=2)
        assert (serial.passed, serial.failed) == (parallel.passed, parallel.failed)

    def test_recursive_glob(self, mixed_dir: Path) -> None:
        assert cli.run([str(mixed_dir / "**" / "*.jsonl")]).passed >= 1

    def test_reports_progress_per_file(self, mixed_dir: Path) -> None:
        updates: list[tuple[int, int]] = []
        summary = cli.run([str(mixed_dir / "*")], on_progress=lambda done, total: updates.append((done, total)))
        assert [done for done, _ in updates] == list(range(1, summary.files + 1))
        assert updates[-1] == (summary.files, summary.files)

    def test_failure_is_reported(self, tmp_path: Path) -> None:
        bad = json.loads(json.dumps(_TENHOU))
        bad["log"][0][-1] = ["和了", [1, -1, 0, 0], [0, 0, 0, "役満16000点∀", "天和"]]
        (tmp_path / "bad.json").write_text(json.dumps(bad))
        summary = cli.run([str(tmp_path / "*.json")])
        assert not summary.ok
        assert summary.failed == 1
        assert summary.failure_samples

    def test_unparseable_file_becomes_an_error(self, tmp_path: Path) -> None:
        (tmp_path / "broken.jsonl").write_text('{"type":"start_kyoku"}')  # missing required fields
        summary = cli.run([str(tmp_path / "*.jsonl")])
        assert summary.errors == 1
        assert not summary.ok

    def test_error_sample_respects_the_cap(self, tmp_path: Path) -> None:
        (tmp_path / "broken.jsonl").write_text('{"type":"start_kyoku"}')
        summary = cli.run([str(tmp_path / "*.jsonl")], sample_cap=0)
        assert summary.errors == 1
        assert summary.failure_samples == []

    def test_two_wins_in_one_file(self, tmp_path: Path) -> None:
        two_rounds = json.loads(json.dumps(_TENHOU))
        two_rounds["log"].append(json.loads(json.dumps(_TENHOU["log"][0])))
        (tmp_path / "two.json").write_text(json.dumps(two_rounds))
        assert cli.run([str(tmp_path / "*.json")]).passed == 2

    def test_url_listing_ignores_blank_lines(self, tmp_path: Path) -> None:
        url = f"https://tenhou.net/6/#json={json.dumps(_TENHOU)}&ts=0"
        (tmp_path / "urls.txt").write_text(f"\n# a comment, not a url\n{url}\n")
        assert cli.run([str(tmp_path / "*.txt")]).passed == 1

    def test_per_file_failure_detail_is_capped(self, tmp_path: Path) -> None:
        two_failing = json.loads(json.dumps(_TENHOU))
        two_failing["log"][0][-1] = ["和了", [1, -1, 0, 0], [0, 0, 0, "役満16000点∀", "天和"]]
        two_failing["log"].append(json.loads(json.dumps(two_failing["log"][0])))
        (tmp_path / "fails.json").write_text(json.dumps(two_failing))
        summary = cli.run([str(tmp_path / "*.json")], max_failures_per_file=1)
        assert summary.failed == 2
        assert len(summary.failure_samples) == 1

    def test_failure_sample_is_capped(self, tmp_path: Path) -> None:
        two_failing = json.loads(json.dumps(_TENHOU))
        two_failing["log"][0][-1] = ["和了", [1, -1, 0, 0], [0, 0, 0, "役満16000点∀", "天和"]]
        two_failing["log"].append(json.loads(json.dumps(two_failing["log"][0])))
        (tmp_path / "fails.json").write_text(json.dumps(two_failing))
        summary = cli.run([str(tmp_path / "*.json")], sample_cap=1)
        assert summary.failed == 2
        assert len(summary.failure_samples) == 1


class TestMain:
    def test_success_exit_code(self, mixed_dir: Path, capsys: pytest.CaptureFixture) -> None:
        assert cli.main([str(mixed_dir / "*")]) == 0
        assert "OK" in capsys.readouterr().out

    def test_no_match_exit_code(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        assert cli.main([str(tmp_path / "nothing-*.xml")]) == 2
        assert "no files matched" in capsys.readouterr().out

    def test_failure_exit_code(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        (tmp_path / "broken.jsonl").write_text('{"type":"start_kyoku"}')
        assert cli.main([str(tmp_path / "*.jsonl")]) == 1
        assert "FAILED" in capsys.readouterr().out

    def test_rejects_zero_jobs(self, mixed_dir: Path) -> None:
        with pytest.raises(SystemExit):
            cli.main([str(mixed_dir / "*"), "--jobs", "0"])

    def test_progress_shown_on_a_terminal(self, mixed_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        stderr = _Tty()
        monkeypatch.setattr(sys, "stderr", stderr)
        cli.main([str(mixed_dir / "*")])
        assert "checked" in stderr.getvalue()
        assert stderr.getvalue().rstrip().endswith("files")

    def test_quiet_suppresses_progress(self, mixed_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        stderr = _Tty()
        monkeypatch.setattr(sys, "stderr", stderr)
        cli.main([str(mixed_dir / "*"), "--quiet"])
        assert stderr.getvalue() == ""
