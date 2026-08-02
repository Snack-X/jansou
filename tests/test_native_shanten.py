"""Differential tests: the native shanten kernel against the pure kernel.

The pure Python kernel is the reference. Every case runs both implementations
on the same counts and requires identical answers, including the probe sweeps'
``None`` entries. Hands are drawn from a four-copy pool under a fixed seed, so
the sample never changes between runs.
"""

from __future__ import annotations

import random
import zlib

import jansou_native
import pytest

from jansou.analysis.shanten import _table_bytes, probe_shantens, shanten_counts
from jansou.core.notation import parse_mpsz
from jansou.core.tiles import NUM_KINDS, counts_by_kind

_HANDS_PER_CASE = 80
_SEED = 20260802


@pytest.fixture(scope="module")
def kernel() -> jansou_native.ShantenKernel:
    return jansou_native.ShantenKernel(zlib.decompress(_table_bytes()))


def _random_counts(rng: random.Random, size: int) -> list[int]:
    """Counts for ``size`` tiles drawn without replacement from the full pool."""
    pool = [kind for kind in range(NUM_KINDS) for _ in range(4)]
    rng.shuffle(pool)
    result = [0] * NUM_KINDS
    for kind in pool[:size]:
        result[kind] += 1
    return result


def _sizes(num_melds: int) -> tuple[int, int]:
    rest = 13 - 3 * num_melds
    return rest, rest + 1


@pytest.mark.parametrize("num_melds", range(5))
def test_shanten_counts_matches_pure(kernel, num_melds: int) -> None:
    rng = random.Random(_SEED + num_melds)
    for size in _sizes(num_melds):
        for _ in range(_HANDS_PER_CASE):
            counts = _random_counts(rng, size)
            assert kernel.shanten_counts(counts, num_melds) == shanten_counts(counts, num_melds)


@pytest.mark.parametrize("num_melds", range(5))
def test_probe_shantens_matches_pure(kernel, num_melds: int) -> None:
    rng = random.Random(_SEED * 2 + num_melds)
    resting, holding = _sizes(num_melds)
    for size, delta in ((resting, 1), (holding, -1)):
        for _ in range(_HANDS_PER_CASE):
            counts = _random_counts(rng, size)
            assert kernel.probe_shantens(counts, num_melds, delta) == probe_shantens(counts, num_melds, delta)


@pytest.mark.parametrize(
    ("mpsz", "expected"),
    [
        ("123m456p789s111z22z", -1),  # standard complete
        ("11m22m33p44p55s66s7z", 0),  # seven pairs tenpai
        ("19m19p19s12345677z", -1),  # thirteen orphans complete
        ("19m19p19s1234567z", 0),  # thirteen orphans thirteen-wait
        ("1111m2222p3333s5z", 2),  # four-of-a-kind walls: no fifth copy completes them
    ],
)
def test_known_shapes(kernel, mpsz: str, expected: int) -> None:
    counts = counts_by_kind(parse_mpsz(mpsz))
    assert kernel.shanten_counts(counts, 0) == expected
    assert shanten_counts(counts, 0) == expected


def test_probe_none_where_probe_leaves_radix(kernel) -> None:
    counts = counts_by_kind(parse_mpsz("1111m2233445p66s"))
    probes = kernel.probe_shantens(counts, 0, 1)
    assert probes[0] is None  # a fifth 1m does not exist
    assert probes == probe_shantens(counts, 0, 1)


def test_input_validation(kernel) -> None:
    with pytest.raises(ValueError, match="34 entries"):
        kernel.shanten_counts([0] * 33, 0)
    with pytest.raises(ValueError, match="cannot have"):
        kernel.shanten_counts([0] * 34, 0)
    with pytest.raises(ValueError, match="melds must be zero to four"):
        kernel.shanten_counts([1] * 13 + [0] * 21, -1)
    with pytest.raises(ValueError, match="four-copy radix"):
        kernel.shanten_counts([5, 4, 4] + [0] * 31, 0)
    with pytest.raises(ValueError, match="four copies"):
        kernel.probe_shantens([5, 4, 4, 1] + [0] * 30, 0, -1)
    with pytest.raises(ValueError, match="cannot have"):
        kernel.probe_shantens([1] * 13 + [0] * 21, 0, 2)


def test_payload_validation() -> None:
    with pytest.raises(ValueError, match="magic"):
        jansou_native.ShantenKernel(b"nope")
    with pytest.raises(ValueError, match="wrong length"):
        jansou_native.ShantenKernel(b"JSBT\x00")
