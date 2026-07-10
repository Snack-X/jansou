# ruff: noqa: T201
# T201 is the "print" rule -- printing the report and progress is this CLI's job.

"""The batch validator: score the wins in many logs against the logs themselves.

Files are matched by glob, their format detected by content, parsed, replayed,
and checked in a process pool. Per-file verdicts are merged in a deterministic
order, a capped sample of failures is shown, and the exit code reports whether
every win reproduced its recorded score.
"""

from __future__ import annotations

import argparse
import glob
import multiprocessing
import sys
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

from jansou.io.mjai import parse_mjai
from jansou.io.mjlog import parse_mjlog
from jansou.io.tenhou_json import parse_tenhou_json
from jansou.validation.check import check_paifu

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from jansou.io.paifu import Paifu

_OK, _FAIL, _USAGE = 0, 1, 2


@dataclass(frozen=True)
class FileReport:
    """The result of checking every win in one file's games.

    Attributes:
        path: The file that was checked.
        passed: How many wins in the file reproduced their recorded score.
        failed: How many wins mismatched their recorded score.
        failures: A capped sample of failure descriptions, one per mismatch.
        error: The error string if the file could not be parsed, else ``None``.
    """

    path: str
    passed: int = 0
    failed: int = 0
    failures: tuple[str, ...] = ()
    error: str | None = None


@dataclass
class Summary:
    """The merged result over every checked file.

    Attributes:
        files: How many files were checked.
        passed: Total wins that reproduced their recorded score.
        failed: Total wins that mismatched their recorded score.
        errors: How many files could not be parsed.
        failure_samples: A capped, ordered sample of failure and error lines.
    """

    files: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    failure_samples: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Whether every win reproduced its score and every file parsed."""
        return self.failed == 0 and self.errors == 0


def iter_games(path: str | Path) -> Iterator[Paifu]:
    """Yield the one or more games a file holds, dispatched on its content.

    The format is detected from the bytes: a gzip magic number or a leading
    ``<`` is mjlog; ``#json=`` lines or a ``{...}`` body with a ``"log"`` key is
    Tenhou JSON; anything else is treated as MJAI.

    Args:
        path: The file to read and parse.

    Yields:
        Each parsed game the file contains.
    """
    data = Path(path).read_bytes()
    head = data[:2]
    if head == b"\x1f\x8b" or data.lstrip()[:1] == b"<":
        yield parse_mjlog(data)
        return
    text = data.decode()
    if "#json=" in text:
        for line in text.splitlines():
            if "#json=" in line:
                yield parse_tenhou_json(line.strip())
        return
    stripped = text.lstrip()
    if stripped.startswith("{") and '"log"' in text:
        yield parse_tenhou_json(text)
        return
    yield parse_mjai(text)


def check_file(path: str, max_failures: int = 5) -> FileReport:
    """Check every win in one file, capping the failure detail it carries back.

    A parse or read error is caught and returned as the report's ``error``
    rather than raised, so one bad file does not abort a batch.

    Args:
        path: The file to check.
        max_failures: The most failure descriptions to keep for the file.

    Returns:
        A ``FileReport`` with the pass and fail counts, a capped failure sample,
        and an ``error`` string if the file could not be parsed.
    """
    passed = failed = 0
    failures: list[str] = []
    try:
        for paifu in iter_games(path):
            for verdict in check_paifu(paifu):
                if verdict.passed:
                    passed += 1
                    continue
                failed += 1
                if len(failures) < max_failures:
                    failures.append(f"{path}: seat {verdict.winner}: {verdict.detail}")
    except (ValueError, KeyError, IndexError, OSError) as error:
        return FileReport(path, error=f"{type(error).__name__}: {error}")
    return FileReport(path, passed=passed, failed=failed, failures=tuple(failures))


def _expand(patterns: list[str]) -> list[str]:
    """Every file matching the given globs, de-duplicated and sorted."""
    matched: set[str] = set()
    for pattern in patterns:
        # glob patterns are user input, not fixed paths, so Path.glob does not apply.
        hits = glob.glob(pattern, recursive=True)  # noqa: PTH207
        matched.update(match for match in hits if Path(match).is_file())
    return sorted(matched)


def _no_progress(done: int, total: int) -> None:
    """Discard progress updates; the default when no indicator is shown."""


def _print_progress(done: int, total: int) -> None:
    """Overwrite one stderr line with the running count of checked files."""
    end = "\n" if done == total else ""
    print(f"\rchecked {done}/{total} files", end=end, file=sys.stderr, flush=True)


def run(
    patterns: list[str],
    *,
    jobs: int = 1,
    max_failures_per_file: int = 5,
    sample_cap: int = 20,
    on_progress: Callable[[int, int], None] = _no_progress,
) -> Summary:
    """Validate every file the patterns match, merging results deterministically.

    Files are matched by glob, checked serially or across a process pool, and
    their per-file reports merged in path order into one summary.

    Args:
        patterns: File globs to expand (recursive ``**`` supported).
        jobs: The number of worker processes; ``1`` runs serially.
        max_failures_per_file: The most failure descriptions kept per file.
        sample_cap: The most failure and error lines kept across all files.
        on_progress: Called with ``(files_done, files_total)`` as each file
            finishes; the default ignores it.

    Returns:
        The merged ``Summary`` over every matched file.
    """
    paths = _expand(patterns)
    reports = _collect(paths, jobs, max_failures_per_file, on_progress)
    summary = Summary()
    for report in reports:
        summary.files += 1
        summary.passed += report.passed
        summary.failed += report.failed
        if report.error is not None:
            summary.errors += 1
            if len(summary.failure_samples) < sample_cap:
                summary.failure_samples.append(f"{report.path}: ERROR {report.error}")
        for failure in report.failures:
            if len(summary.failure_samples) < sample_cap:
                summary.failure_samples.append(failure)
    return summary


def _collect(
    paths: list[str], jobs: int, max_failures: int, on_progress: Callable[[int, int], None]
) -> list[FileReport]:
    """Run the per-file checks serially or across a process pool, in path order.

    Each finished file advances ``on_progress``; the pool yields results as they
    complete, so they are re-sorted into path order before returning.
    """
    total = len(paths)
    reports: list[FileReport] = []
    if jobs <= 1 or total <= 1:
        for path in paths:
            reports.append(check_file(path, max_failures))
            on_progress(len(reports), total)
        return reports
    worker = partial(check_file, max_failures=max_failures)
    with multiprocessing.Pool(jobs) as pool:
        for report in pool.imap_unordered(worker, paths):
            reports.append(report)
            on_progress(len(reports), total)
    reports.sort(key=lambda report: report.path)
    return reports


def _format(summary: Summary) -> str:
    """The human-readable report for a run."""
    lines = [
        f"files: {summary.files}  wins passed: {summary.passed}  "
        f"failed: {summary.failed}  file errors: {summary.errors}",
    ]
    if summary.failure_samples:
        lines.append(f"first {len(summary.failure_samples)} problem(s):")
        lines.extend(f"  {sample}" for sample in summary.failure_samples)
    lines.append("OK" if summary.ok else "FAILED")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Validate the given globs from the console, returning a process exit code.

    Args:
        argv: The argument vector to parse; ``None`` reads ``sys.argv``.

    Returns:
        The process exit code: ``0`` when every win reproduced its score and
        every file parsed, ``1`` on any failure or file error, and ``2`` when no
        files matched.
    """
    parser = argparse.ArgumentParser(prog="jansou-validate", description="Validate mahjong logs against their scores.")
    parser.add_argument("patterns", nargs="+", help="file globs (recursive ** supported)")
    parser.add_argument("-j", "--jobs", type=int, default=1, help="worker processes")
    parser.add_argument("--max-failures", type=int, default=5, help="failure details kept per file")
    parser.add_argument("-q", "--quiet", action="store_true", help="suppress the progress indicator")
    args = parser.parse_args(argv)
    if args.jobs < 1:
        parser.error("jobs must be at least 1")
    show_progress = not args.quiet and sys.stderr.isatty()
    summary = run(
        args.patterns,
        jobs=args.jobs,
        max_failures_per_file=args.max_failures,
        on_progress=_print_progress if show_progress else _no_progress,
    )
    if summary.files == 0:
        print("no files matched")
        return _USAGE
    print(_format(summary))
    return _OK if summary.ok else _FAIL


if __name__ == "__main__":
    raise SystemExit(main())
