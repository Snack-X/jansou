"""The optional native kernels, loaded once and shared, with a pure fallback.

The maturin package ``jansou-native`` ships table-driven rewrites of the
analysis hot paths. When it is installed and the shipped block tables load,
``KERNEL`` and ``SOLVER`` hold ready instances; otherwise both stay ``None``
and every caller keeps to the pure path. The pure implementation remains the
reference either way.
"""

from __future__ import annotations

import zlib
from importlib import resources

try:
    from jansou_native import OutlookSolver, ShantenKernel
except ImportError:  # pragma: no cover -- exercised only without the extra installed
    OutlookSolver = ShantenKernel = None

_TABLE_RESOURCE = "shanten_blocks.bin"


def _payload() -> bytes:
    """The decompressed table payload the native kernels parse."""
    return zlib.decompress((resources.files("jansou.analysis") / _TABLE_RESOURCE).read_bytes())


def build() -> tuple[ShantenKernel | None, OutlookSolver | None]:
    """One kernel and one solver over the shipped tables, or nothing.

    Returns:
        The pair ``(kernel, solver)``: ready instances when the native module
        is installed and the tables load, both ``None`` otherwise.
    """
    if ShantenKernel is None:
        return None, None
    try:
        payload = _payload()
    except (OSError, ValueError, zlib.error):
        return None, None
    return ShantenKernel(payload), OutlookSolver(payload)


KERNEL, SOLVER = build()
