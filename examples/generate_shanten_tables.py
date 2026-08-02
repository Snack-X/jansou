"""Regenerate the packed shanten block tables shipped with the package.

Enumerates every legal suit block (nine ranks) and honor block (seven kinds),
prices each with the analysis layer's own distance-vector search, and writes the
lot as one compressed file of vector-class bytes. The runtime loads this file
instead of pricing hundreds of thousands of blocks lazily, which is what makes
the table-driven probe path fast from the first decision.

Semantics cannot drift: the same `_distance_vector` that answers a lazy fill
also fills this file, so regenerating is only ever needed if that search itself
changes. Takes a couple of minutes single-core.

    uv run python examples/generate_shanten_tables.py
"""

from __future__ import annotations

import time
import zlib
from pathlib import Path

from jansou.analysis.shanten import _distance_vector

RADIX = 5
SUIT_WIDTH = 9
HONOR_WIDTH = 7
MAX_BLOCK_TILES = 14
MAGIC = b"JSBT"
SENTINEL = 255
TARGET = Path(__file__).resolve().parent.parent / "src" / "jansou" / "analysis" / "shanten_blocks.bin"


def fill(width: int, classes: dict[tuple[int, ...], int], vectors: list[tuple[int, ...]]) -> bytearray:
    """One table's bytes: each legal block's vector class, sentinel elsewhere."""
    table = bytearray([SENTINEL]) * RADIX**width
    block = [0] * width

    def walk(position: int, index: int, left: int) -> None:
        if position == width:
            vector = _distance_vector(tuple(block), is_honor=width == HONOR_WIDTH)
            cls = classes.get(vector)
            if cls is None:
                cls = classes[vector] = len(vectors)
                vectors.append(vector)
            table[index] = cls
            return
        step = RADIX**position
        for copies in range(min(RADIX - 1, left) + 1):
            block[position] = copies
            walk(position + 1, index + copies * step, left - copies)
        block[position] = 0

    walk(0, 0, MAX_BLOCK_TILES)
    return table


def main() -> None:
    """Build both tables and write the compressed payload."""
    started = time.perf_counter()
    classes: dict[tuple[int, ...], int] = {}
    vectors: list[tuple[int, ...]] = []
    suit = fill(SUIT_WIDTH, classes, vectors)
    honor = fill(HONOR_WIDTH, classes, vectors)
    assert len(vectors) < SENTINEL, "vector classes must fit a byte with the sentinel spare"

    payload = bytearray(MAGIC)
    payload.append(len(vectors))
    for vector in vectors:
        payload.extend(vector)
    payload.extend(suit)
    payload.extend(honor)
    TARGET.write_bytes(zlib.compress(bytes(payload), 9))

    print(
        f"{len(vectors)} vectors over {sum(1 for b in suit if b != SENTINEL)} suit and "
        f"{sum(1 for b in honor if b != SENTINEL)} honor blocks -> "
        f"{TARGET.stat().st_size / 1024:.0f} KiB in {time.perf_counter() - started:.0f}s"
    )


if __name__ == "__main__":
    main()
