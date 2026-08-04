"""Tests for the native wiring inside jansou.analysis.

The deep pure suites (test_shanten, test_ev) force the pure path, and the
differential suites test the native module directly. This file covers the seam
between them: the loader, the routing branches, and wrapper-level parity of
the native-backed results against the pure reference.
"""

from __future__ import annotations

import pytest

from jansou.analysis import _native
from jansou.analysis import shanten as shanten_module
from jansou.analysis.ev import Valuation, hand_outlook
from jansou.analysis.shanten import probe_shantens, shanten_counts
from jansou.core.notation import parse_mpsz
from jansou.core.rules import Rules
from jansou.core.tiles import Tile, TileKind, counts_by_kind

_VALUATION = Valuation(rules=Rules(), dora_indicators=(Tile(TileKind.M1),))


def counts(mpsz: str) -> list[int]:
    return counts_by_kind(parse_mpsz(mpsz))


def _clear_memos() -> None:
    shanten_module._PROBE_MEMO.clear()
    shanten_module._COUNT_MEMO.clear()


@pytest.mark.skipif(_native.ShantenKernel is None, reason="jansou-native is not installed")
def test_native_engines_are_loaded() -> None:
    # The dev environment always ships the extra; without it, the wiring tests
    # below still pass by exercising the pure fallback against itself.
    assert _native.KERNEL is not None
    assert _native.SOLVER is not None


def test_build_without_the_module(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_native, "ShantenKernel", None)
    assert _native.build() == (None, None)


def test_build_without_the_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> bytes:
        raise ValueError("no tables")

    monkeypatch.setattr(_native, "_payload", broken)
    assert _native.build() == (None, None)


def test_kernel_wrappers_match_pure(monkeypatch: pytest.MonkeyPatch) -> None:
    resting = counts("123m456p78s11z234s")
    post_draw = counts("123m456p789s11z234s")
    _clear_memos()
    native = (
        probe_shantens(resting, 0, 1),
        probe_shantens(post_draw, 0, -1),
        shanten_counts(resting, 0),
    )
    _clear_memos()
    monkeypatch.setattr(_native, "KERNEL", None)
    pure = (
        probe_shantens(resting, 0, 1),
        probe_shantens(post_draw, 0, -1),
        shanten_counts(resting, 0),
    )
    _clear_memos()
    assert native == pure


def test_outlook_matches_pure(monkeypatch: pytest.MonkeyPatch) -> None:
    hand = counts("123m456p78s11z234s")
    visible = [1] * 27 + [0] * 7
    native = hand_outlook(hand, 0, 8, visible=visible, valuation=_VALUATION)
    monkeypatch.setattr(_native, "SOLVER", None)
    pure = hand_outlook(hand, 0, 8, visible=visible, valuation=_VALUATION)
    assert native == pure
    assert native.points


def test_outlook_without_valuation_skips_points() -> None:
    assert hand_outlook(counts("123m456p78s11z234s"), 0, 5).points == ()


def test_beyond_radix_counts_fall_to_the_pure_path() -> None:
    # Five copies of a kind pass the size check but not the tables' radix; the
    # pure path then rejects the probe sweep exactly as it did before.
    hand = [5, 4, 4] + [0] * 31
    with pytest.raises(ValueError, match="four copies"):
        hand_outlook(hand, 0, 5, max_distance=None)


def test_junk_visible_falls_to_the_pure_path() -> None:
    visible = [5] + [0] * 33
    with pytest.raises(ValueError, match="visible and held"):
        hand_outlook(counts("123m456p78s11z234s"), 0, 5, visible=visible)


@pytest.mark.parametrize("num_melds", [-1, 5])
def test_meld_counts_outside_the_domain_are_rejected(num_melds: int) -> None:
    with pytest.raises(ValueError, match="melds must be zero to four"):
        shanten_counts(counts("123m456p78s11z234s"), num_melds)
    with pytest.raises(ValueError, match="melds must be zero to four"):
        probe_shantens(counts("123m456p78s11z234s"), num_melds, 1)
