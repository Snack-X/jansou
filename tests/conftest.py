"""Shared fixtures: locating the provided real-log dataset."""

from __future__ import annotations

from pathlib import Path

import pytest

_DATASET = Path(__file__).resolve().parent.parent / "dataset"


@pytest.fixture
def dataset() -> Path:
    """The root of the provided real-log dataset, skipping if it is absent."""
    if not _DATASET.is_dir():
        pytest.skip("real-log dataset not present")
    return _DATASET


def sample_mjlog(dataset: Path, limit: int) -> list[Path]:
    """A deterministic sample of mjlog files."""
    files = sorted(dataset.glob("mjlog/data/*/*.xml"))
    if not files:
        pytest.skip("no mjlog files present")
    return files[:limit]
