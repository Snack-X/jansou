"""Tests for the top-level package."""

from __future__ import annotations

from importlib.metadata import version

import jansou


def test_version_is_exported_from_the_package_metadata() -> None:
    assert jansou.__version__ == version("jansou")
    assert jansou.__version__[0].isdigit()
