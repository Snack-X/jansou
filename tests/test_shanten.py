"""Tests for shanten, cross-checked against an independent brute-force oracle."""

from __future__ import annotations

import random

import pytest

from jansou.analysis.decompose import is_complete
from jansou.analysis.shanten import _block_options, discard_shantens, draw_shantens, is_tenpai, shanten, shanten_counts
from jansou.analysis.shanten import is_complete as hand_complete
from jansou.core.hand import CallSource, Hand, Meld, MeldType
from jansou.core.notation import parse_mpsz
from jansou.core.tiles import NUM_KINDS, TILES_PER_KIND, YAOCHUU_KINDS, counts_by_kind

_INF = 99


def counts(mpsz: str) -> list[int]:
    return counts_by_kind(parse_mpsz(mpsz))


def chii_123m() -> Meld:
    return Meld(MeldType.CHII, tuple(parse_mpsz("123m")), called=parse_mpsz("1m")[0], source=CallSource.KAMICHA)


def _place_set(c: list[int], rng: random.Random) -> bool:
    """Add one random run or triplet to the counts if it fits, reporting success."""
    for _ in range(20):
        if rng.random() < 0.4:  # triplet two-fifths of the time
            k = rng.randrange(NUM_KINDS)
            if c[k] <= TILES_PER_KIND - 3:
                c[k] += 3
                return True
        else:
            k = rng.randrange(3) * 9 + rng.randrange(7)  # a run start within a suit
            if all(c[k + d] < TILES_PER_KIND for d in range(3)):
                for d in range(3):
                    c[k + d] += 1
                return True
    return False


def _place_pair(c: list[int], rng: random.Random) -> bool:
    """Add one random pair to the counts if it fits, reporting success."""
    for _ in range(20):
        k = rng.randrange(NUM_KINDS)
        if c[k] <= TILES_PER_KIND - 2:
            c[k] += 2
            return True
    return False


def _relevant_draws(c: list[int]) -> set[int]:
    """Kinds worth drawing: those present, ranks within one, and all terminals-and-honors."""
    kinds: set[int] = set(YAOCHUU_KINDS)
    for k in range(NUM_KINDS):
        if not c[k]:
            continue
        kinds.add(k)
        if k < 27:  # a suited kind
            rank = k % 9
            for delta, low in ((-1, 1), (-2, 2), (1, 8), (2, 7)):
                if (rank >= low) if delta < 0 else (rank <= low):
                    kinds.add(k + delta)
    return kinds


def _min_draws(c: list[int], num_melds: int, limit: int, memo: dict[tuple[int, ...], int]) -> int:
    """Fewest draws (each paired with a free discard) to reach agari, capped by limit."""
    if limit <= 0:
        return _INF
    key = (*c, limit)
    if key in memo:
        return memo[key]
    best = _INF
    for k in _relevant_draws(c):
        if c[k] >= TILES_PER_KIND:
            continue
        c[k] += 1
        if is_complete(c, num_melds):
            best = 1
        elif limit > 1:
            for d in range(NUM_KINDS):
                if c[d]:
                    c[d] -= 1
                    best = min(best, 1 + _min_draws(c, num_melds, limit - 1, memo))
                    c[d] += 1
        c[k] -= 1
        if best == 1:
            break
    memo[key] = best
    return best


def brute_shanten(c: list[int], num_melds: int, cap: int = 3) -> int | None:
    """Shanten via minimum draws to agari, or None if it exceeds the cap."""
    if sum(c) == 14 - 3 * num_melds and is_complete(c, num_melds):
        return -1
    draws = _min_draws(list(c), num_melds, cap + 1, {})
    return None if draws > cap + 1 else draws - 1


class TestKnownValues:
    @pytest.mark.parametrize(
        ("mpsz", "expected"),
        [
            ("123m456m789m234p55s", -1),  # complete standard
            ("11223344556677p", -1),  # complete seven pairs
            ("19m19p19s12345677z", -1),  # complete thirteen orphans
            ("123m456m789m234p5s", 0),  # tanki tenpai
            ("123m456m789m13p55s", 0),  # kanchan tenpai
            ("19m19p19s1234567z", 0),  # thirteen orphans, thirteen-way wait
            ("19m19p1s11234567z", 0),  # thirteen orphans, single wait on 9s
            ("1188m2299p3355s6z", 0),  # seven pairs tenpai
            ("123m456m789m1357p", 1),
            ("123m456m78m24p679s", 2),
        ],
    )
    def test_known(self, mpsz: str, expected: int) -> None:
        c = counts(mpsz)
        assert shanten_counts(c, 0) == expected
        # The brute oracle is exponential in search depth, so cross-check it only
        # in the shallow regime; higher values are anchored by the property tests.
        if expected <= 1:
            assert brute_shanten(c, 0, cap=max(expected, 0)) == expected

    def test_with_melds(self) -> None:
        # A chii plus three concealed sets and a lone tile: a tanki tenpai.
        hand = Hand(tuple(parse_mpsz("456m789m234p1p")), (chii_123m(),))
        assert shanten(hand) == 0
        assert is_tenpai(hand)

    def test_complete_hand_helpers(self) -> None:
        hand = Hand(tuple(parse_mpsz("123m456m789m234p55s")))
        assert hand_complete(hand)
        assert shanten(hand) == -1


class TestSizeValidation:
    def test_rejects_illegal_size(self) -> None:
        with pytest.raises(ValueError, match="concealed tiles"):
            shanten_counts(counts("123m"), 0)

    def test_accepts_rest_and_holding(self) -> None:
        assert shanten_counts(counts("123456789m1234z"), 0) >= 0  # 13, rest
        assert shanten_counts(counts("123456789m12345z"), 0) >= -1  # 14, holding


class TestAgainstBruteForce:
    """Random cross-checks tying shanten to the independent decomposition logic."""

    def _random_rest_hand(self, rng: random.Random) -> list[int]:
        pool = [k for k in range(NUM_KINDS) for _ in range(TILES_PER_KIND)]
        rng.shuffle(pool)
        c = [0] * NUM_KINDS
        for k in pool[:13]:
            c[k] += 1
        return c

    def _near_ready_hand(self, rng: random.Random) -> list[int]:
        """A hand biased toward low shanten: a complete hand, lightly perturbed."""
        while True:
            c = [0] * NUM_KINDS
            if not all(_place_set(c, rng) for _ in range(4)) or not _place_pair(c, rng):
                continue
            for _ in range(rng.randint(1, 3)):  # remove a few tiles
                present = [k for k in range(NUM_KINDS) if c[k]]
                c[rng.choice(present)] -= 1
            while sum(c) < 13:  # refill to resting size with random tiles
                k = rng.randrange(NUM_KINDS)
                if c[k] < TILES_PER_KIND:
                    c[k] += 1
            if sum(c) == 13:
                return c

    def test_tenpai_anchor(self) -> None:
        # A 13-tile hand is tenpai exactly when some kind completes it.
        rng = random.Random(1)
        for _ in range(1500):
            c = self._random_rest_hand(rng)
            has_wait = any(
                c[k] < TILES_PER_KIND and is_complete([*c[:k], c[k] + 1, *c[k + 1 :]], 0) for k in range(NUM_KINDS)
            )
            assert (shanten_counts(c, 0) == 0) == has_wait

    def test_complete_anchor(self) -> None:
        # A 14-tile hand is shanten -1 exactly when it decomposes.
        rng = random.Random(2)
        for _ in range(1500):
            c = self._random_rest_hand(rng)
            k = rng.randrange(NUM_KINDS)
            while c[k] >= TILES_PER_KIND:
                k = rng.randrange(NUM_KINDS)
            c[k] += 1
            assert (shanten_counts(c, 0) == -1) == is_complete(c, 0)

    def test_draw_changes_shanten_by_at_most_one(self) -> None:
        rng = random.Random(3)
        for _ in range(300):
            c = self._random_rest_hand(rng)
            base = shanten_counts(c, 0)
            for k in range(NUM_KINDS):
                if c[k] >= TILES_PER_KIND:
                    continue
                c[k] += 1
                drawn = shanten_counts(c, 0)
                c[k] -= 1
                assert base - 1 <= drawn <= base

    def test_matches_brute_force_near_ready(self) -> None:
        rng = random.Random(4)
        checked = 0
        for _ in range(300):
            c = self._near_ready_hand(rng)
            fast = shanten_counts(c, 0)
            if fast > 1:
                continue
            assert fast == brute_shanten(c, 0, cap=1)
            checked += 1
        assert checked > 100


def _reference_shanten_counts(c: list[int], num_melds: int) -> int:
    """The pre-table algorithm, kept verbatim as the equivalence oracle."""
    states = {(0, 0, 0)}
    for start, stop in ((0, 9), (9, 18), (18, 27), (27, 34)):
        options = _block_options(tuple(c[start:stop]), is_honor=start == 27)
        states = {(s + bs, p + bp, h + bh) for s, p, h in states for bs, bp, bh in options if h + bh <= 1}
    best = 8
    for s, p, h in states:
        total_sets = s + num_melds
        best = min(best, 8 - 2 * total_sets - min(p, 4 - total_sets) - h)
    if num_melds == 0 and sum(c) in (13, 14):
        pairs = sum(1 for x in c if x >= 2)
        kinds = sum(1 for x in c if x)
        present = sum(1 for k in YAOCHUU_KINDS if c[k])
        has_pair = any(c[k] >= 2 for k in YAOCHUU_KINDS)
        best = min(best, 6 - pairs + max(0, 7 - kinds), 13 - present - (1 if has_pair else 0))
    return best


def _random_hands(rng: random.Random, n: int) -> list[tuple[list[int], int]]:
    """Random legal hands across meld counts, sizes, and skewed tile families."""
    pools = (
        list(range(NUM_KINDS)),  # anything
        [*range(9), *range(27, 34)],  # one suit plus honors: run-heavy and chuuren-like
        [k for k in range(NUM_KINDS) if k in YAOCHUU_KINDS],  # terminals and honors: kokushi-like
    )
    hands = []
    for _ in range(n):
        num_melds = rng.randrange(5)
        total = 13 - 3 * num_melds + rng.randrange(2)
        pool = rng.choice(pools)
        c = [0] * NUM_KINDS
        if rng.randrange(2):  # pair-heavy start toward chiitoi shapes
            for k in rng.sample(pool, min(total // 2, len(pool))):
                c[k] = 2
        while sum(c) > total:
            k = rng.choice([k for k in range(NUM_KINDS) if c[k]])
            c[k] -= 1
        while sum(c) < total:
            k = rng.choice(pool)
            if c[k] < TILES_PER_KIND:
                c[k] += 1
        hands.append((c, num_melds))
    return hands


class TestPackedEquivalence:
    """The packed-table combination reproduces the original search, bit for bit."""

    def test_matches_the_reference_on_random_hands(self) -> None:
        for c, num_melds in _random_hands(random.Random(5), 4000):
            assert shanten_counts(c, num_melds) == _reference_shanten_counts(c, num_melds), (c, num_melds)


class TestDrawShantens:
    def test_matches_per_kind_probes(self) -> None:
        rng = random.Random(6)
        for c, num_melds in _random_hands(rng, 600):
            if sum(c) != 13 - 3 * num_melds:  # probes draw onto a resting hand
                continue
            kinds = [k for k in range(NUM_KINDS) if c[k] < TILES_PER_KIND]
            probed = draw_shantens(c, num_melds, kinds)
            for k in kinds:
                c[k] += 1
                expected = shanten_counts(c, num_melds)
                c[k] -= 1
                assert probed[k] == expected, (c, num_melds, k)

    def test_probing_nothing_returns_nothing(self) -> None:
        assert draw_shantens(counts("123456789m1234z"), 0, ()) == {}

    def test_rejects_a_probe_past_the_holding_size(self) -> None:
        with pytest.raises(ValueError, match="concealed tiles"):
            draw_shantens(counts("123456789m12345z"), 0, [0])

    def test_chiitoi_probe_completes_the_seventh_pair(self) -> None:
        c = counts("1188m2299p3355s6z")
        assert draw_shantens(c, 0, [32]) == {32: -1}  # the 6z pair completes seven pairs

    def test_kokushi_probes_cover_both_kind_families(self) -> None:
        c = counts("19m19p1s11234567z")
        probed = draw_shantens(c, 0, [26, 4])
        assert probed[26] == -1  # 9s fills the missing orphan
        assert probed[4] == 0  # 5m does not help: still one exchange from thirteen orphans


class TestDiscardShantens:
    def test_matches_per_kind_probes(self) -> None:
        rng = random.Random(7)
        for c, num_melds in _random_hands(rng, 600):
            if sum(c) != 14 - 3 * num_melds:  # probes discard from a holding hand
                continue
            kinds = [k for k in range(NUM_KINDS) if c[k]]
            probed = discard_shantens(c, num_melds, kinds)
            for k in kinds:
                c[k] -= 1
                expected = shanten_counts(c, num_melds)
                c[k] += 1
                assert probed[k] == expected, (c, num_melds, k)

    def test_rejects_a_kind_the_hand_does_not_hold(self) -> None:
        with pytest.raises(ValueError, match="holds none"):
            discard_shantens(counts("123456789m12345z"), 0, [26])

    def test_rejects_a_probe_below_the_resting_size(self) -> None:
        with pytest.raises(ValueError, match="concealed tiles"):
            discard_shantens(counts("123456789m1234z"), 0, [0])

    def test_probing_nothing_returns_nothing(self) -> None:
        assert discard_shantens(counts("123456789m12345z"), 0, ()) == {}
