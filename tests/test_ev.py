"""Tests for the single-player outlook, cross-checked against a brute oracle."""

from __future__ import annotations

from itertools import pairwise

import pytest

from jansou.analysis import _native
from jansou.analysis.ev import Outlook, Valuation, _build, _Graph, _GraphCache, discard_outlooks, hand_outlook, outlook
from jansou.analysis.shanten import discard_shantens, shanten_counts
from jansou.core.hand import CallSource, Hand, Meld, MeldType
from jansou.core.notation import parse_mpsz
from jansou.core.rules import Rules, preset
from jansou.core.tiles import NUM_KINDS, TILES_PER_KIND, Tile, TileKind, Wind, counts_by_kind, kinds_in_play
from jansou.scoring.context import WinContext
from jansou.scoring.score import ScoringError, score

TOLERANCE = 1e-12


@pytest.fixture(autouse=True)
def _pure_solver(monkeypatch: pytest.MonkeyPatch) -> None:
    # These tests pin the pure reference; the native paths are covered elsewhere.
    monkeypatch.setattr(_native, "SOLVER", None)


def counts(mpsz: str) -> list[int]:
    """The kind counts of a hand written in MPSZ notation."""
    return counts_by_kind(parse_mpsz(mpsz))


def kind_of(mpsz: str) -> TileKind:
    """The single kind named by a one-tile MPSZ string."""
    return parse_mpsz(mpsz)[0].kind


def tiles_of(counts: list[int]) -> tuple[Tile, ...]:
    """Plain tiles for a count vector, in canonical order."""
    return tuple(Tile(TileKind(kind)) for kind, copies in enumerate(counts) for _ in range(copies))


def brute_outlook(
    hand: list[int],
    num_melds: int,
    draws: int,
    *,
    visible: list[int] | None = None,
    player_count: int = 4,
    winning: bool,
) -> float:
    """The measure by exhaustive expectimax over every draw and every discard.

    The independent oracle: it shares the solver's draw model -- every draw sees
    the same pool of what the player cannot see -- but searches every discard
    rather than only the ones that advance the hand, so it bounds the solver from
    above.
    """
    seen = visible or [0] * NUM_KINDS
    kinds = kinds_in_play(player_count)
    total = sum(TILES_PER_KIND - seen[k] - hand[k] for k in kinds)

    def walk(state: list[int], left: int) -> float:
        if not winning and shanten_counts(state, num_melds) <= 0:
            return 1.0
        if left == 0:
            return 0.0
        value = 0.0
        for kind in kinds:
            copies = TILES_PER_KIND - seen[kind] - state[kind]
            if not copies:
                continue
            state[kind] += 1
            if winning and shanten_counts(state, num_melds) == -1:
                best = 1.0
            else:
                best = max(walk([*state[:j], state[j] - 1, *state[j + 1 :]], left - 1) for j in kinds if state[j])
            state[kind] -= 1
            value += copies * best
        return value / total

    return walk(list(hand), draws)


class TestBruteOracle:
    """The solver against exhaustive expectimax at horizons the oracle can reach."""

    @pytest.mark.parametrize(
        "mpsz",
        [
            "123m456m789m234p5s",  # tanki that can be traded for a ryanmen
            "123m456m789m13p55s",  # kanchan tenpai
            "123m456m789m12p55s",  # penchan tenpai
            "111m456m789m345p5s",  # tanki behind a breakable run
        ],
    )
    @pytest.mark.parametrize("draws", [1, 2])
    def test_never_exceeds_the_oracle(self, mpsz: str, draws: int) -> None:
        # Only advancing draws are searched, so the solver reports a floor on the
        # full search: it declines the sideways trades the oracle is free to make.
        result = hand_outlook(counts(mpsz), 0, draws)
        for winning, curve in ((True, result.win), (False, result.ready)):
            assert curve[draws] <= brute_outlook(counts(mpsz), 0, draws, winning=winning) + TOLERANCE

    @pytest.mark.parametrize("draws", [1, 2])
    def test_matches_oracle_without_upgrades(self, draws: int) -> None:
        # A penchan behind a pair and three finished sets has no sideways trade
        # worth making, so the restricted search and the full one agree exactly.
        hand = counts("123m456m789m12p55s")
        result = hand_outlook(hand, 0, draws)
        assert result.win[draws] == pytest.approx(brute_outlook(hand, 0, draws, winning=True))
        assert result.ready[draws] == pytest.approx(brute_outlook(hand, 0, draws, winning=False))

    def test_sideways_trades_are_the_whole_gap(self) -> None:
        # The one hand where the two disagree most is the one where breaking a
        # finished run buys a two-sided wait -- the trade the solver will not make.
        hand = counts("111m456m789m345p5s")
        assert hand_outlook(hand, 0, 2).win[2] < brute_outlook(hand, 0, 2, winning=True)


class TestClosedForm:
    def test_kept_wait_is_geometric(self) -> None:
        # Every draw sees the same pool, so a hand that keeps a fixed wait fails
        # independently each time and its win curve is the geometric complement.
        hand = counts("123m456m789m234p5s")
        unseen = sum(TILES_PER_KIND - hand[k] for k in kinds_in_play(4))
        live = TILES_PER_KIND - hand[kind_of("5s")]
        result = hand_outlook(hand, 0, 8)
        for draws in range(9):
            assert result.win[draws] == pytest.approx(1 - (1 - live / unseen) ** draws)

    def test_win_never_falls_below_keeping_the_wait(self) -> None:
        # A ready hand can always simply sit on the wait it holds, so its win
        # curve is floored by that wait's own drawing odds.
        hand = counts("123m456m789m13p55s")
        unseen = sum(TILES_PER_KIND - hand[k] for k in kinds_in_play(4))
        live = TILES_PER_KIND - hand[kind_of("2p")]
        result = hand_outlook(hand, 0, 12)
        for draws in range(13):
            assert result.win[draws] >= 1 - (1 - live / unseen) ** draws - TOLERANCE


class TestMonotonicity:
    @pytest.mark.parametrize(
        "mpsz",
        ["123m456m789m13p55s", "123m456m78m134p55s", "19m19p19s1234567z", "1188m2299p3355s6z"],
    )
    def test_curves_rise_with_draws(self, mpsz: str) -> None:
        result = hand_outlook(counts(mpsz), 0, 10)
        for curve in (result.ready, result.win):
            assert all(earlier <= later + TOLERANCE for earlier, later in pairwise(curve))

    def test_visible_waits_only_hurt(self) -> None:
        hand = counts("123m456m789m13p55s")
        wait = kind_of("2p")
        previous = 1.0
        for shown in range(TILES_PER_KIND + 1):
            visible = [0] * NUM_KINDS
            visible[wait] = shown
            current = hand_outlook(hand, 0, 10, visible=visible).win[-1]
            assert current <= previous + TOLERANCE
            previous = current

    def test_fully_visible_wait_never_wins(self) -> None:
        hand = counts("123m456m789m234p5s")
        visible = [0] * NUM_KINDS
        visible[kind_of("5s")] = TILES_PER_KIND - hand[kind_of("5s")]
        assert hand_outlook(hand, 0, 12, visible=visible).win[-1] == 0.0

    def test_no_draws_never_wins(self) -> None:
        assert hand_outlook(counts("123m456m789m234p5s"), 0, 0).win == (0.0,)


class TestShantenConsistency:
    @pytest.mark.parametrize(
        "mpsz",
        ["123m456m789m13p55s", "123m456m78m134p55s", "123m456m78m14p556s", "19m19p19s1234567z"],
    )
    def test_ready_and_win_start_at_shanten(self, mpsz: str) -> None:
        hand = counts(mpsz)
        distance = shanten_counts(hand, 0)
        result = hand_outlook(hand, 0, 8)
        assert (result.ready[0] == 1.0) is (distance == 0)
        assert all(result.ready[d] == 0.0 for d in range(distance))
        assert all(result.win[d] == 0.0 for d in range(distance + 1))

    def test_ready_at_zero_draws_matches_tenpai(self) -> None:
        hand = counts("123m456m789m234p55s")
        for kind, result in discard_outlooks(hand, 0, 4).items():
            rest = [*hand]
            rest[kind] -= 1
            assert (result.ready[0] == 1.0) is (shanten_counts(rest, 0) == 0)


class TestEntryPoints:
    def test_discard_matches_hand_outlook(self) -> None:
        hand = counts("123m456m789m234p55s")
        results = discard_outlooks(hand, 0, 8)
        for kind, result in results.items():
            rest = [*hand]
            rest[kind] -= 1
            assert result == hand_outlook(rest, 0, 8)

    def test_hand_wrapper_matches_counts(self) -> None:
        tiles = tuple(parse_mpsz("123m456m789m13p55s"))
        assert outlook(Hand(tiles), 6) == hand_outlook(counts("123m456m789m13p55s"), 0, 6)

    def test_melds_shift_the_target(self) -> None:
        # Ten concealed tiles behind a called set are ready, and say so.
        hand = Hand(tuple(parse_mpsz("456m789m234p1p")), (_chii(),))
        assert outlook(hand, 5).ready[0] == 1.0


class TestDeterminism:
    def test_repeat_calls_agree(self) -> None:
        hand = counts("123m456m78m134p55s")
        assert hand_outlook(hand, 0, 9) == hand_outlook(hand, 0, 9)

    def test_visible_is_not_cached_into_the_graph(self) -> None:
        # The reachable-hand graph is shared across game positions, so a warm
        # cache from one set of visible tiles must not colour the next answer.
        hand = counts("123m456m789m13p55s")
        visible = [0] * NUM_KINDS
        visible[kind_of("2p")] = 3
        blind = hand_outlook(hand, 0, 10)
        seen = hand_outlook(hand, 0, 10, visible=visible)
        assert hand_outlook(hand, 0, 10) == blind
        assert seen.win[-1] < blind.win[-1]


class TestBeam:
    def test_beam_never_exceeds_the_full_search(self) -> None:
        hand = counts("123m456m78m134p55s")
        full = hand_outlook(hand, 0, 10)
        for beam in (1, 2, 3):
            capped = hand_outlook(hand, 0, 10, beam=beam)
            assert capped.win[-1] <= full.win[-1] + TOLERANCE
            assert capped.ready[-1] <= full.ready[-1] + TOLERANCE

    def test_generous_beam_is_the_full_search(self) -> None:
        hand = counts("123m456m78m134p55s")
        assert hand_outlook(hand, 0, 8, beam=NUM_KINDS) == hand_outlook(hand, 0, 8)


class TestSanma:
    def test_middle_manzu_are_not_drawn(self) -> None:
        resting = counts("111m999m456p789p1z")
        assert hand_outlook(resting, 0, 6, player_count=3).ready[0] == 1.0
        # Seven manzu kinds are out of play, so the unseen pool this tanki is
        # drawn from is smaller and the same wait comes in sooner than at four.
        sanma = hand_outlook(resting, 0, 6, player_count=3).win[-1]
        yonma = hand_outlook(resting, 0, 6).win[-1]
        assert sanma > yonma
        assert set(discard_outlooks(counts("119m456p789p123s11z"), 0, 4, player_count=3)) <= set(kinds_in_play(3))


class TestRejections:
    def test_rejects_negative_draws(self) -> None:
        with pytest.raises(ValueError, match="must not be negative"):
            hand_outlook(counts("123m456m789m13p55s"), 0, -1)

    def test_rejects_zero_beam(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            hand_outlook(counts("123m456m789m13p55s"), 0, 4, beam=0)

    def test_rejects_illegal_size(self) -> None:
        with pytest.raises(ValueError, match="concealed tiles"):
            hand_outlook(counts("123m"), 0, 4)

    def test_rejects_impossible_visible(self) -> None:
        visible = [0] * NUM_KINDS
        visible[kind_of("1m")] = TILES_PER_KIND
        with pytest.raises(ValueError, match="visible and held"):
            hand_outlook(counts("123m456m789m13p55s"), 0, 4, visible=visible)

    def test_rejects_horizon_beyond_the_wall(self) -> None:
        visible = [0] * NUM_KINDS
        for kind in kinds_in_play(4):
            visible[kind] = TILES_PER_KIND
        for tile in (counts("123m456m789m13p55s"),):
            for kind, held in enumerate(tile):
                visible[kind] -= held
        with pytest.raises(ValueError, match="tiles are unseen"):
            hand_outlook(counts("123m456m789m13p55s"), 0, 4, visible=visible)


def test_outlook_is_a_pair_of_curves() -> None:
    result = hand_outlook(counts("123m456m789m13p55s"), 0, 3)
    assert isinstance(result, Outlook)
    assert len(result.ready) == len(result.win) == 4


def _chii() -> Meld:
    """A called 123m run, for the meld-bearing hand test."""
    return Meld(MeldType.CHII, tuple(parse_mpsz("123m")), called=parse_mpsz("1m")[0], source=CallSource.KAMICHA)


class TestCandidateRestriction:
    def test_only_distance_holding_discards_are_reported(self) -> None:
        # Breaking a finished set gives ground, so those discards are left out;
        # what remains is exactly the set that holds the hand's distance.
        hand = counts("123m456m789m234p55s")
        keeps = min(discard_shantens(hand, 0, [k for k, n in enumerate(hand) if n]).values())
        reported = discard_outlooks(hand, 0, 6)
        for kind, copies in enumerate(hand):
            if not copies:
                continue
            rest = [*hand]
            rest[kind] -= 1
            assert (TileKind(kind) in reported) is (shanten_counts(rest, 0) == keeps)

    def test_a_ready_hand_reports_only_its_ready_discards(self) -> None:
        hand = counts("123m456m789m234p55s")
        assert all(result.ready[0] == 1.0 for result in discard_outlooks(hand, 0, 6).values())


class TestGraphCache:
    def test_bound_is_counted_in_hands_not_entries(self) -> None:
        # A pool of workers cares about residency, and entries differ hugely in
        # size, so the bound counts the hands held rather than the entries.
        cache = _GraphCache(limit=1)
        shapes = ("123m456m78m134p55s", "123m456m78m134p56s")
        for mpsz in shapes:
            graph = cache.get((mpsz,), lambda mpsz=mpsz: _build(tuple(counts(mpsz)), 0, 4, 6, None))
            assert len(graph.counts) > 1
        # Each entry alone is over the bound, so the older one has been dropped
        # and the newest is kept rather than evicting itself.
        rebuilt: list[str] = []

        def build(mpsz: str) -> object:
            rebuilt.append(mpsz)
            return _build(tuple(counts(mpsz)), 0, 4, 6, None)

        cache.get((shapes[0],), lambda: build(shapes[0]))
        cache.get((shapes[1],), lambda: build(shapes[1]))
        assert rebuilt == [shapes[0], shapes[1]]

    def test_repeated_lookups_reuse_the_entry(self) -> None:
        cache = _GraphCache(limit=10_000)
        key = ("only",)
        built = cache.get(key, lambda: _build(tuple(counts("123m456m789m13p55s")), 0, 4, 6, None))
        assert cache.get(key, lambda: pytest.fail("should not rebuild")) is built

    def test_a_key_stored_during_its_build_keeps_the_first_graph(self) -> None:
        # Two threads can build one key at once; the later store must not count its hands twice.
        cache = _GraphCache(limit=10_000)
        key = ("raced",)

        def build() -> _Graph:
            return _build(tuple(counts("123m456m789m13p55s")), 0, 4, 6, None)

        first: list[_Graph] = []

        def build_after_a_rival() -> _Graph:
            first.append(cache.get(key, build))
            return build()

        assert cache.get(key, build_after_a_rival) is first[0]
        assert cache._states == len(first[0].counts)


class TestVisibleBlindGraph:
    def test_unreachable_continuations_are_simply_dead(self) -> None:
        # The graph is built without visible tiles, so it can hold hands needing
        # a copy that is already accounted for. Those must weigh nothing, not raise.
        hand = counts("123m456m78m134p55s")
        visible = [0] * NUM_KINDS
        for mpsz in ("2p", "5p", "6m", "9m", "5s"):
            visible[kind_of(mpsz)] = TILES_PER_KIND - hand[kind_of(mpsz)]
        starved = hand_outlook(hand, 0, 10, visible=visible)
        assert all(0.0 <= value <= 1.0 for value in starved.win + starved.ready)
        assert starved.win[-1] <= hand_outlook(hand, 0, 10).win[-1]


class TestDistanceGate:
    def test_hands_beyond_the_gate_report_flat_zero(self) -> None:
        far = counts("129m458m134p256s9s")
        assert shanten_counts(far, 0) > 2
        gated = hand_outlook(far, 0, 6)
        assert gated.ready == (0.0,) * 7
        assert gated.win == (0.0,) * 7

    def test_hands_inside_the_gate_are_untouched(self) -> None:
        near = counts("123m456m78m134p55s")
        assert shanten_counts(near, 0) <= 2
        assert hand_outlook(near, 0, 6) == hand_outlook(near, 0, 6, max_distance=None)

    def test_the_gate_is_where_the_caller_puts_it(self) -> None:
        hand = counts("129m458m134p256s9s")
        distance = shanten_counts(hand, 0)
        assert hand_outlook(hand, 0, 5, max_distance=distance).win[-1] > 0.0
        assert hand_outlook(hand, 0, 5, max_distance=distance - 1).win[-1] == 0.0

    def test_none_solves_at_any_distance(self) -> None:
        hand = counts("129m458m134p256s9s")
        assert hand_outlook(hand, 0, 5, max_distance=None).win[-1] > 0.0

    def test_the_gate_reaches_the_per_discard_entry_point(self) -> None:
        hand = counts("129m458m134p256s99s")
        assert all(result.win[-1] == 0.0 for result in discard_outlooks(hand, 0, 5).values())
        assert any(result.win[-1] > 0.0 for result in discard_outlooks(hand, 0, 5, max_distance=None).values())

    def test_rejects_negative_distance(self) -> None:
        with pytest.raises(ValueError, match="max distance"):
            hand_outlook(counts("123m456m789m13p55s"), 0, 4, max_distance=-1)


def brute_points(
    hand: list[int],
    num_melds: int,
    draws: int,
    valuation: Valuation,
    *,
    player_count: int = 4,
) -> float:
    """What a win is worth given it happens, by exhaustive points-maximising search.

    The independent oracle for the points curve: it searches every discard rather
    than only the advancing ones, choosing at each draw the line with the greatest
    points expected, and divides that by the win odds of the same line.
    """
    seen = [0] * NUM_KINDS
    kinds = kinds_in_play(player_count)
    total = sum(TILES_PER_KIND - seen[k] - hand[k] for k in kinds)
    context = WinContext(
        rules=valuation.rules,
        round_wind=valuation.round_wind,
        seat_wind=valuation.seat_wind,
        is_tsumo=True,
        dora_indicators=valuation.dora_indicators,
    )

    def worth(state: list[int], drawn: int) -> float:
        tiles: list[Tile] = []
        for kind, copies in enumerate(state):
            reds = min(valuation.red_fives[kind], copies) if valuation.red_fives else 0
            tiles.extend(Tile(TileKind(kind), red=True) for _ in range(reds))
            tiles.extend(Tile(TileKind(kind)) for _ in range(copies - reds))
        tiles.append(Tile(TileKind(drawn)))
        try:
            return float(score(Hand(tuple(tiles), valuation.melds), Tile(TileKind(drawn)), context).payment.total)
        except ScoringError:
            return 0.0

    def walk(state: list[int], left: int) -> tuple[float, float]:
        if left == 0:
            return 0.0, 0.0
        earned = won = 0.0
        for kind in kinds:
            copies = TILES_PER_KIND - seen[kind] - state[kind]
            if not copies:
                continue
            state[kind] += 1
            if shanten_counts(state, num_melds) == -1:
                state[kind] -= 1
                earned += copies * worth(state, kind)
                won += copies
                continue
            best = max(
                (walk([*state[:j], state[j] - 1, *state[j + 1 :]], left - 1) for j in kinds if state[j]),
                key=lambda pair: pair[0],
            )
            state[kind] -= 1
            earned += copies * best[0]
            won += copies * best[1]
        return earned / total, won / total

    expected, chance = walk(list(hand), draws)
    return expected / chance if chance else 0.0


class TestPointsClosedForm:
    def test_a_dead_wait_prices_its_only_completion(self) -> None:
        # One completion and no way to improve, so the conditional expectation is
        # simply that hand's score.
        hand = counts("123m456m789m234p5s")
        valuation = Valuation(rules=Rules(), dora_indicators=(Tile(TileKind.M1),))
        context = WinContext(rules=Rules(), is_tsumo=True, dora_indicators=(Tile(TileKind.M1),))
        alone = score(Hand(tuple(parse_mpsz("123m456m789m234p55s"))), Tile(TileKind.S5), context).payment.total
        result = hand_outlook(hand, 0, 8, valuation=valuation)
        assert all(value == pytest.approx(float(alone)) for value in result.points[1:])

    def test_a_multi_wait_is_the_copy_weighted_average(self) -> None:
        # Three finished sets, a pair, and a two-sided wait: no advancing draw
        # exists, so the answer is each completion weighted by its live copies.
        hand = counts("123m456m789m11p34s")
        valuation = Valuation(rules=Rules())
        context = WinContext(rules=Rules(), is_tsumo=True)
        earned = chance = 0.0
        for mpsz in ("2s", "5s"):
            kind = kind_of(mpsz)
            copies = TILES_PER_KIND - hand[kind]
            whole = [*hand]
            whole[kind] += 1
            paid = score(Hand(tiles_of(whole)), Tile(kind), context).payment.total
            earned += copies * paid
            chance += copies
        result = hand_outlook(hand, 0, 10, valuation=valuation)
        assert result.points[-1] == pytest.approx(earned / chance)


class TestPointsOracle:
    @pytest.mark.parametrize("draws", [1, 2])
    def test_matches_oracle_without_upgrades(self, draws: int) -> None:
        # Same idiom as the probability curves: compare where the restricted and
        # the full search cannot diverge.
        hand = counts("123m456m789m12p55s")
        valuation = Valuation(rules=Rules(), dora_indicators=(Tile(TileKind.M1),))
        result = hand_outlook(hand, 0, draws, valuation=valuation)
        assert result.points[draws] == pytest.approx(brute_points(hand, 0, draws, valuation))

    def test_matches_oracle_with_a_red_five(self) -> None:
        # The penchan again, so the restricted and full searches cannot diverge,
        # this time with the pair's five marked red.
        hand = counts("123m456m789m12p55s")
        reds = [0] * NUM_KINDS
        reds[TileKind.S5] = 1
        valuation = Valuation(rules=Rules(), red_fives=tuple(reds))
        assert hand_outlook(hand, 0, 2, valuation=valuation).points[2] == pytest.approx(
            brute_points(hand, 0, 2, valuation)
        )


class TestPointsContext:
    def test_a_dora_indicator_raises_the_price(self) -> None:
        hand = counts("123m456m789m234p5s")
        blind = hand_outlook(hand, 0, 6, valuation=Valuation(rules=Rules()))
        dora = hand_outlook(hand, 0, 6, valuation=Valuation(rules=Rules(), dora_indicators=(Tile(TileKind.S4),)))
        assert dora.points[-1] > blind.points[-1]

    def test_a_red_five_raises_the_price(self) -> None:
        hand = counts("123m456m789m345p5s")
        reds = [0] * NUM_KINDS
        reds[TileKind.P5] = 1
        plain = hand_outlook(hand, 0, 6, valuation=Valuation(rules=Rules()))
        aka = hand_outlook(hand, 0, 6, valuation=Valuation(rules=Rules(), red_fives=tuple(reds)))
        assert aka.points[-1] > plain.points[-1]

    def test_presets_price_differently(self) -> None:
        # Tenhou plays red fives, the association ruleset does not, so the same
        # marked hand is worth more under one than the other.
        hand = counts("123m456m789m345p5s")
        reds = [0] * NUM_KINDS
        reds[TileKind.P5] = 1
        with_aka = hand_outlook(hand, 0, 6, valuation=Valuation(rules=preset("tenhou"), red_fives=tuple(reds)))
        without = hand_outlook(hand, 0, 6, valuation=Valuation(rules=preset("renmei"), red_fives=tuple(reds)))
        assert with_aka.points[-1] > without.points[-1]

    def test_an_open_hand_with_no_yaku_is_worth_nothing(self) -> None:
        # All simples behind a called triplet scores only on open tanyao, so
        # barring it leaves a hand that can complete but cannot legally win.
        pon = Meld(MeldType.PON, tuple(parse_mpsz("222m")), called=parse_mpsz("2m")[0], source=CallSource.KAMICHA)
        hand = counts("345m345p678p3s")
        allowed = hand_outlook(hand, 1, 6, valuation=Valuation(rules=Rules(kuitan=True), melds=(pon,)))
        barred = hand_outlook(hand, 1, 6, valuation=Valuation(rules=Rules(kuitan=False), melds=(pon,)))
        assert allowed.points[-1] > 0.0
        assert barred.points[-1] == 0.0
        # The shape is unchanged either way: only the price moves.
        assert allowed.win == barred.win

    def test_the_dealer_is_paid_more(self) -> None:
        hand = counts("123m456m789m234p5s")
        non_dealer = hand_outlook(hand, 0, 6, valuation=Valuation(rules=Rules(), seat_wind=Wind.SOUTH))
        dealer = hand_outlook(hand, 0, 6, valuation=Valuation(rules=Rules(), seat_wind=Wind.EAST))
        assert dealer.points[-1] > non_dealer.points[-1]


class TestPointsShape:
    def test_points_are_absent_without_a_valuation(self) -> None:
        assert hand_outlook(counts("123m456m789m13p55s"), 0, 5).points == ()

    def test_points_are_indexed_like_the_other_curves(self) -> None:
        result = hand_outlook(counts("123m456m789m13p55s"), 0, 5, valuation=Valuation(rules=Rules()))
        assert len(result.points) == len(result.win) == len(result.ready) == 6
        assert result.points[0] == 0.0

    def test_beyond_the_gate_points_are_flat_zero(self) -> None:
        far = counts("129m458m134p256s9s")
        assert hand_outlook(far, 0, 5, valuation=Valuation(rules=Rules())).points == (0.0,) * 6

    def test_the_discard_entry_point_agrees(self) -> None:
        hand = counts("123m456m789m234p55s")
        valuation = Valuation(rules=Rules(), dora_indicators=(Tile(TileKind.M1),))
        for kind, result in discard_outlooks(hand, 0, 6, valuation=valuation).items():
            rest = [*hand]
            rest[kind] -= 1
            assert result == hand_outlook(rest, 0, 6, valuation=valuation)

    def test_repeat_calls_agree(self) -> None:
        hand = counts("123m456m78m134p55s")
        valuation = Valuation(rules=Rules(), dora_indicators=(Tile(TileKind.M1),))
        assert hand_outlook(hand, 0, 6, valuation=valuation) == hand_outlook(hand, 0, 6, valuation=valuation)


class TestPointsRejections:
    def test_rejects_a_meld_count_mismatch(self) -> None:
        with pytest.raises(ValueError, match="valuation holds"):
            hand_outlook(counts("456m789m134p12s"), 1, 4, valuation=Valuation(rules=Rules()))

    def test_rejects_a_short_red_vector(self) -> None:
        with pytest.raises(ValueError, match="count per kind"):
            hand_outlook(counts("123m456m789m13p55s"), 0, 4, valuation=Valuation(rules=Rules(), red_fives=(1, 0)))

    def test_rejects_a_red_that_is_not_a_five(self) -> None:
        reds = [0] * NUM_KINDS
        reds[TileKind.M1] = 1
        with pytest.raises(ValueError, match="cannot be red"):
            hand_outlook(counts("123m456m789m13p55s"), 0, 4, valuation=Valuation(rules=Rules(), red_fives=tuple(reds)))

    def test_rejects_more_reds_than_the_hand_holds(self) -> None:
        reds = [0] * NUM_KINDS
        reds[TileKind.P5] = 2
        with pytest.raises(ValueError, match="red"):
            hand_outlook(counts("123m456m789m345p5s"), 0, 4, valuation=Valuation(rules=Rules(), red_fives=tuple(reds)))


class TestBareRed:
    """A hand whose only copy of a marked five can itself be the discard."""

    HAND = "123m456m789m5p678s9s"  # red 5p and 9s both give a tanki tenpai

    def marks(self) -> tuple[int, ...]:
        reds = [0] * NUM_KINDS
        reds[TileKind.P5] = 1
        return tuple(reds)

    def test_cutting_the_marked_five_is_priced_not_rejected(self) -> None:
        hand = counts(self.HAND)
        results = discard_outlooks(hand, 0, 8, valuation=Valuation(rules=Rules(), red_fives=self.marks()))
        assert TileKind.P5 in results
        assert results[TileKind.P5].points[-1] > 0.0

    def test_the_cut_red_is_in_the_pond_for_that_candidate(self) -> None:
        # The candidate that cuts the marked five prices as if never marked; the
        # candidates that keep it price the aka. Their conditional values differ
        # by at least the aka han's doubling on this hand.
        hand = counts(self.HAND)
        marked = discard_outlooks(hand, 0, 8, valuation=Valuation(rules=Rules(), red_fives=self.marks()))
        plain = discard_outlooks(hand, 0, 8, valuation=Valuation(rules=Rules()))
        assert marked[TileKind.P5].points == plain[TileKind.P5].points
        keeper = next(kind for kind in marked if kind != TileKind.P5)
        assert marked[keeper].points[-1] > plain[keeper].points[-1]

    def test_the_direct_entry_point_still_rejects_an_absent_red(self) -> None:
        # Only a discard sweep may legitimately fall below the mark; a 13-tile
        # hand passed directly with a mark it does not hold is a caller error.
        hand = counts(self.HAND)
        hand[TileKind.P5] -= 1
        with pytest.raises(ValueError, match="red"):
            hand_outlook(hand, 0, 8, valuation=Valuation(rules=Rules(), red_fives=self.marks()))
