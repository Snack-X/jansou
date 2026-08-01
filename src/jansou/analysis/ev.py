"""Single-player outlook: a hand's own chances of reaching ready and of winning.

A draw-only abstraction of the rest of the hand. Only this player draws, taking
tiles it cannot see; nobody calls, nobody discards into a win, and there are no
kans or interruptions, so the only win is a tsumo. The player is assumed to play
the abstraction perfectly, choosing at every draw the discard that maximises the
measure being reported.

Draws are taken **with** replacement: every draw sees the same pool, so a kind
already drawn is no less likely to come again. That keeps a hand's value
independent of how many draws have gone before, which is what lets one pass
compute the whole curve. It understates a little -- a wait truly thins the pool
as the hand goes on -- in the same direction as the search restriction below.

This is a hand-potential measure, not a game model: defence, calls, and the
opponents are outside it. It reports two probabilities as curves over the draws
still to come, both cumulative and both absorbing -- the chance of having reached
ready, and the chance of having won, on or before each draw.

Each measure is maximised by its own line of play, so the ready curve is the best
achievable readiness and the win curve the best achievable win rate; neither is
the other's by-product. Winning is judged on shape alone, so a hand that could
not legally win for want of a yaku still counts as won here.

Only draws that shorten the distance to ready are searched, and by default only
the single most promising hand is kept per such draw. Sideways exchanges that
trade one wait for a wider one of the same distance are therefore declined, so
every figure reported is a floor on what perfect play would achieve, not an
estimate of it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from jansou.analysis.shanten import discard_shantens, draw_shantens, shanten_counts
from jansou.core.hand import Hand
from jansou.core.tiles import FIVE_KINDS, NUM_KINDS, TILES_PER_KIND, Tile, TileKind, Wind, counts_by_kind, kinds_in_play
from jansou.scoring.context import WinContext
from jansou.scoring.score import ScoringError, score

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from jansou.core.hand import Meld
    from jansou.core.rules import Rules

_GRAPH_CACHE_STATES = 200_000
"""Hands the reachable-hand graph cache holds, summed over its entries.

Counted in hands rather than in entries because entries differ hugely in size: a
ready hand's graph is a single hand, one far from ready can be hundreds. Bounding
the count of entries would leave residency free to vary by orders of magnitude,
which matters when a caller runs a pool of worker processes.

The graph itself depends on the hand alone, never on which tiles are visible, so
one entry serves every game position that reaches the same shape.
"""

_COMPLETE = -1
_READY = 0


@dataclass(frozen=True)
class Valuation:
    """The table as it stands, for pricing a hand's winning completions.

    The context is frozen at the moment of asking: the indicators are the ones
    already turned, and no later reveal, no ura, and no riichi is supposed. A
    completion is priced as a self-draw, which is the only win the abstraction
    admits, so a concealed hand always carries at least the self-draw yaku.
    Table bonuses -- the repeat counter and the deposits on the table -- are left
    out, since they belong to the table rather than to the hand.

    Attributes:
        rules: The rules the completion is scored under.
        melds: The called sets the hand holds, which fix what it can still score.
        dora_indicators: The indicators turned so far.
        round_wind: The wind of the round.
        seat_wind: The wind of the seat.
        red_fives: Red copies held per kind, since a count of tiles by kind
            cannot carry the marking on its own. Only fives may be marked.
    """

    rules: Rules
    melds: tuple[Meld, ...] = ()
    dora_indicators: tuple[Tile, ...] = ()
    round_wind: Wind = Wind.EAST
    seat_wind: Wind = Wind.EAST
    red_fives: tuple[int, ...] = ()


@dataclass(frozen=True)
class Outlook:
    """How a hand is expected to fare on its own draws, by draws remaining.

    Every curve is indexed by the number of draws still to come, from zero up to
    the horizon the caller asked for. The two probabilities are cumulative: entry
    ``d`` is the chance of having reached that outcome on or before the ``d``-th
    draw.

    Attributes:
        ready: The chance of having reached ready, playing to maximise it.
        win: The chance of having won, playing to maximise it.
        points: What a win is worth on average, given that it happens, playing to
            maximise the points expected. Empty when no valuation was supplied,
            and zero where no win is possible at all.
    """

    ready: tuple[float, ...]
    win: tuple[float, ...]
    points: tuple[float, ...] = ()


@dataclass(frozen=True)
class _Graph:
    """The hands reachable from a starting hand, and how each is reached.

    States are held by index so the evaluation sweep can address them by
    position. A draw that improves nothing leaves no edge: the tile is discarded
    again and the hand stays where it is, which the sweep models as staying put.

    Attributes:
        counts: Each state's concealed counts by kind.
        ready: Whether each state is already ready.
        wins: Each state's winning draws, by kind.
        moves: Each state's improving draws, as a kind with the states it can
            leave behind.
    """

    counts: tuple[tuple[int, ...], ...]
    ready: tuple[bool, ...]
    wins: tuple[tuple[int, ...], ...]
    moves: tuple[tuple[tuple[int, tuple[int, ...]], ...], ...]


def _acceptance_breadth(counts: list[int], num_melds: int, kinds: tuple[TileKind, ...], base: int) -> int:
    """How many draws would lower this hand's shanten, ignoring visible tiles."""
    drawable = [kind for kind in kinds if counts[kind] < TILES_PER_KIND]
    after = draw_shantens(counts, num_melds, drawable)
    return sum(TILES_PER_KIND - counts[kind] for kind in drawable if after[kind] < base)


def _landings(
    counts: list[int],
    num_melds: int,
    kinds: tuple[TileKind, ...],
    drawn: TileKind,
    improved: int,
    beam: int | None,
) -> list[tuple[int, ...]]:
    """The hands a shanten-lowering draw can leave behind, best first if capped."""
    held = [kind for kind in kinds if counts[kind]]
    after = discard_shantens(counts, num_melds, held)
    landed: list[tuple[int, ...]] = []
    for kind, shanten in after.items():
        if shanten != improved or kind == drawn:
            continue
        counts[kind] -= 1
        landed.append(tuple(counts))
        counts[kind] += 1
    if beam is None or len(landed) <= beam:
        return landed
    landed.sort(key=lambda state: -_acceptance_breadth(list(state), num_melds, kinds, improved))
    return landed[:beam]


def _expand(
    state: tuple[int, ...],
    num_melds: int,
    kinds: tuple[TileKind, ...],
    beam: int | None,
    *,
    deepen: bool,
) -> tuple[int, list[int], list[tuple[int, list[tuple[int, ...]]]]]:
    """One hand's shanten, its winning draws, and its improving draws' landings.

    With ``deepen`` unset the landings are skipped: the hand sits at the horizon,
    where no further improvement could be drawn on in time to matter.
    """
    counts = list(state)
    base = shanten_counts(counts, num_melds)
    drawable = [kind for kind in kinds if counts[kind] < TILES_PER_KIND]
    after = draw_shantens(counts, num_melds, drawable)
    wins: list[int] = []
    moves: list[tuple[int, list[tuple[int, ...]]]] = []
    for kind in drawable:
        improved = after[kind]
        if improved == _COMPLETE:
            wins.append(int(kind))
            continue
        if improved >= base or not deepen:
            continue
        counts[kind] += 1
        landed = _landings(counts, num_melds, kinds, kind, improved, beam)
        counts[kind] -= 1
        if landed:
            moves.append((int(kind), landed))
    return base, wins, moves


def _build(state: tuple[int, ...], num_melds: int, player_count: int, depth: int, beam: int | None) -> _Graph:
    """Every hand reachable from this one by ``depth`` shanten-lowering draws.

    Built without reference to any visible tiles, so the same graph serves every
    game position holding this shape; the tiles actually left to draw enter only
    as weights when the graph is evaluated.
    """
    kinds = kinds_in_play(player_count)
    index = {state: 0}
    order = [state]
    levels = [0]
    ready: list[bool] = []
    wins: list[tuple[int, ...]] = []
    moves: list[tuple[tuple[int, tuple[int, ...]], ...]] = []
    cursor = 0
    while cursor < len(order):
        level = levels[cursor]
        base, won, stepped = _expand(order[cursor], num_melds, kinds, beam, deepen=level < depth)
        ready.append(base <= _READY)
        wins.append(tuple(won))
        edges: list[tuple[int, tuple[int, ...]]] = []
        for kind, landed in stepped:
            targets = []
            for hand in landed:
                if hand not in index:
                    index[hand] = len(order)
                    order.append(hand)
                    levels.append(level + 1)
                targets.append(index[hand])
            edges.append((kind, tuple(targets)))
        moves.append(tuple(edges))
        cursor += 1
    return _Graph(tuple(order), tuple(ready), tuple(wins), tuple(moves))


class _GraphCache:
    """The built graphs, bounded by how many hands they hold between them.

    Oldest entries go first once the bound is passed, and a single entry larger
    than the whole bound is simply kept alone rather than evicting itself.
    """

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._held: dict[tuple[object, ...], _Graph] = {}
        self._states = 0

    def get(self, key: tuple[object, ...], build: Callable[[], _Graph]) -> _Graph:
        """The cached graph for this key, building and storing it if absent."""
        found = self._held.get(key)
        if found is not None:
            return found
        built = build()
        self._held[key] = built
        self._states += len(built.counts)
        while self._states > self._limit and len(self._held) > 1:
            self._states -= len(self._held.pop(next(iter(self._held))).counts)
        return built


_CACHE = _GraphCache(_GRAPH_CACHE_STATES)


def _graph(state: tuple[int, ...], num_melds: int, player_count: int, depth: int, beam: int | None) -> _Graph:
    """The reachable-hand graph for this hand, from the cache where possible."""
    key = (state, num_melds, player_count, depth, beam)
    return _CACHE.get(key, lambda: _build(state, num_melds, player_count, depth, beam))


def _unseen(graph: _Graph, visible: Sequence[int], kinds: tuple[TileKind, ...]) -> tuple[list[list[int]], int]:
    """Copies of each kind still unseen from every hand, and the starting total.

    Only the hand actually held has to square with the visible counts. The graph
    is built without them, so it can hold hands that would need a fifth copy of
    something already accounted for; those simply run dry, and the draw that
    would reach them carries no weight.

    Raises:
        ValueError: If a kind's visible copies plus the held hand's own exceed four.
    """
    pools: list[list[int]] = []
    for index, counts in enumerate(graph.counts):
        pool = [0] * len(counts)
        for kind in kinds:
            left = TILES_PER_KIND - visible[kind] - counts[kind]
            if left < 0 and not index:
                raise ValueError(f"kind {TileKind(kind)} has more copies visible and held than exist")
            pool[kind] = max(left, 0)
        pools.append(pool)
    return pools, sum(pools[0])


def _sweep(graph: _Graph, pools: list[list[int]], total: int, later: list[float], *, winning: bool) -> list[float]:
    """One draw's worth of value, given the value of every state one draw on.

    Every draw that neither wins nor improves is thrown straight back, leaving the
    hand where it was, so the whole residual mass carries the staying value. The
    same staying value also floors each improving draw, because keeping the drawn
    tile is never forced.

    Every hand here holds the same number of tiles, so every hand leaves the same
    number unseen and the divisor is the one pool total throughout.
    """
    values = [0.0] * len(graph.counts)
    for index, pool in enumerate(pools):
        if not winning and graph.ready[index]:
            values[index] = 1.0
            continue
        held = later[index]
        drawn = 0
        value = 0.0
        if winning:
            # Only a ready hand has a winning draw, and the ready measure has
            # already answered for those, so this mass belongs to the win curve.
            for kind in graph.wins[index]:
                drawn += pool[kind]
                value += pool[kind]
        for kind, targets in graph.moves[index]:
            drawn += pool[kind]
            value += pool[kind] * max(held, *(later[target] for target in targets))
        values[index] = (value + (total - drawn) * held) / total
    return values


def _completed(state: tuple[int, ...], drawn: int, valuation: Valuation) -> Hand:
    """The hand this state becomes when the drawn kind completes it.

    Red copies are placed among the tiles already held, never among the draw:
    the pool is counted by kind, so which copy arrives is not modelled. That
    also guarantees a plain copy of the drawn kind for the win to point at.
    """
    tiles: list[Tile] = []
    for kind, copies in enumerate(state):
        reds = min(valuation.red_fives[kind], copies) if valuation.red_fives else 0
        tiles.extend(Tile(TileKind(kind), red=True) for _ in range(reds))
        tiles.extend(Tile(TileKind(kind)) for _ in range(copies - reds))
    tiles.append(Tile(TileKind(drawn)))
    return Hand(tuple(tiles), valuation.melds)


def _value(state: tuple[int, ...], drawn: int, valuation: Valuation, memo: dict[tuple, float]) -> float:
    """What this completion pays, or nothing when it carries no yaku to win on."""
    key = (state, drawn)
    if key in memo:
        return memo[key]
    context = WinContext(
        rules=valuation.rules,
        round_wind=valuation.round_wind,
        seat_wind=valuation.seat_wind,
        is_tsumo=True,
        dora_indicators=valuation.dora_indicators,
    )
    try:
        worth = float(score(_completed(state, drawn, valuation), Tile(TileKind(drawn)), context).payment.total)
    except ScoringError:
        worth = 0.0
    memo[key] = worth
    return worth


def _points_sweep(
    graph: _Graph,
    pools: list[list[int]],
    total: int,
    later: tuple[list[float], list[float]],
    worth: list[dict[int, float]],
) -> tuple[list[float], list[float]]:
    """One draw of expected points, carrying the win odds of the line that earns it.

    The choice at each improving draw is the one that maximises points expected,
    which is a third optimum alongside the two the probability curves report. The
    win odds ride along under that same choice, so dividing one by the other gives
    what a win is worth given that it happens.
    """
    later_points, later_wins = later
    points = [0.0] * len(graph.counts)
    wins = [0.0] * len(graph.counts)
    for index, pool in enumerate(pools):
        held_points, held_wins = later_points[index], later_wins[index]
        drawn = 0
        earned = 0.0
        won = 0.0
        for kind in graph.wins[index]:
            drawn += pool[kind]
            earned += pool[kind] * worth[index][kind]
            won += pool[kind]
        for kind, targets in graph.moves[index]:
            drawn += pool[kind]
            best_points, best_wins = held_points, held_wins
            for target in targets:
                if later_points[target] > best_points:
                    best_points, best_wins = later_points[target], later_wins[target]
            earned += pool[kind] * best_points
            won += pool[kind] * best_wins
        points[index] = (earned + (total - drawn) * held_points) / total
        wins[index] = (won + (total - drawn) * held_wins) / total
    return points, wins


def _points_curve(
    graph: _Graph,
    pools: list[list[int]],
    total: int,
    horizon: int,
    valuation: Valuation,
    memo: dict[tuple, float],
) -> tuple[float, ...]:
    """What a win is worth given it happens, over the whole draws-remaining curve."""
    worth = [
        {kind: _value(counts, kind, valuation, memo) for kind in graph.wins[index]}
        for index, counts in enumerate(graph.counts)
    ]
    later = ([0.0] * len(graph.counts), [0.0] * len(graph.counts))
    curve = [0.0]
    for _ in range(horizon):
        later = _points_sweep(graph, pools, total, later, worth)
        curve.append(later[0][0] / later[1][0] if later[1][0] else 0.0)
    return tuple(curve)


def _curve(graph: _Graph, pools: list[list[int]], total: int, horizon: int, *, winning: bool) -> tuple[float, ...]:
    """The whole draws-remaining curve for one measure.

    Because every draw sees the same pool, a hand's value turns only on how many
    draws are left and not on how many have gone. One sweep per draw therefore
    walks the entire curve, each entry being the value one draw deeper than the
    last.
    """
    values = [1.0 if not winning and state else 0.0 for state in graph.ready]
    curve = [values[0]]
    for _ in range(horizon):
        values = _sweep(graph, pools, total, values, winning=winning)
        curve.append(values[0])
    return tuple(curve)


def _check_valuation(concealed: list[int], num_melds: int, valuation: Valuation) -> None:
    """Reject a valuation that does not describe the hand it is asked about.

    Raises:
        ValueError: If the called sets do not match the meld count, or a red
            marking names a kind that is not a five or is not held.
    """
    if len(valuation.melds) != num_melds:
        raise ValueError(f"valuation holds {len(valuation.melds)} melds, but the hand has {num_melds}")
    if not valuation.red_fives:
        return
    if len(valuation.red_fives) != NUM_KINDS:
        raise ValueError(f"red fives must be a count per kind, got {len(valuation.red_fives)}")
    for kind, reds in enumerate(valuation.red_fives):
        if reds and TileKind(kind) not in FIVE_KINDS:
            raise ValueError(f"kind {TileKind(kind)} cannot be red")
        if reds > concealed[kind]:
            raise ValueError(f"{reds} red {TileKind(kind)} marked, but the hand holds {concealed[kind]}")


def _outlook(
    concealed: list[int],
    num_melds: int,
    max_draws: int,
    visible: Sequence[int] | None,
    player_count: int,
    beam: int | None,
    max_distance: int | None,
    valuation: Valuation | None = None,
    memo: dict[tuple, float] | None = None,
) -> Outlook:
    """The shared body behind both entry points."""
    if max_draws < 0:
        raise ValueError(f"max draws must not be negative, got {max_draws}")
    if beam is not None and beam < 1:
        raise ValueError(f"beam must be at least one, got {beam}")
    if max_distance is not None and max_distance < 0:
        raise ValueError(f"max distance must not be negative, got {max_distance}")
    kinds = kinds_in_play(player_count)
    distance = shanten_counts(concealed, num_melds)
    if valuation is not None:
        _check_valuation(concealed, num_melds, valuation)
    if max_distance is not None and distance > max_distance:
        blank = (0.0,) * (max_draws + 1)
        return Outlook(ready=blank, win=blank, points=blank if valuation else ())
    graph = _graph(tuple(concealed), num_melds, player_count, max_draws, beam)
    pools, total = _unseen(graph, visible or [0] * len(concealed), kinds)
    if total < max_draws:
        raise ValueError(f"only {total} tiles are unseen, fewer than the {max_draws} draws asked for")
    return Outlook(
        ready=_curve(graph, pools, total, max_draws, winning=False),
        win=_curve(graph, pools, total, max_draws, winning=True),
        points=(
            _points_curve(graph, pools, total, max_draws, valuation, memo if memo is not None else {})
            if valuation is not None
            else ()
        ),
    )


def hand_outlook(
    concealed: list[int],
    num_melds: int,
    max_draws: int,
    *,
    visible: Sequence[int] | None = None,
    player_count: int = 4,
    beam: int | None = 1,
    max_distance: int | None = 2,
    valuation: Valuation | None = None,
) -> Outlook:
    """The outlook for a resting hand played on as it stands.

    ``visible`` counts the tiles known to be outside the hand -- every pond, every
    called meld, and the revealed indicators -- and must not include the hand
    itself, which is deducted from the unseen pool separately.

    Args:
        concealed: Concealed tile counts indexed by kind, at resting size.
        num_melds: The number of called melds the hand holds.
        max_draws: The furthest draw to report, the horizon of the curves.
        visible: Counts by kind of tiles known to be outside the hand. Defaults
            to none visible.
        player_count: The number of players, which fixes the kinds in play.
        beam: The most hands to keep per improving draw, widest acceptance
            first, or ``None`` to keep every one. Keeping only the best is the
            default; keeping all is exact but far costlier away from ready.
        max_distance: The furthest from ready a hand may be and still be solved;
            beyond it both curves are flat zero. ``None`` solves at any distance.
            Cost rises about tenfold per step away from ready, so this is the
            control that bounds it.
        valuation: The table to price winning completions against. Given one, the
            ``points`` curve is filled; without one it stays empty.

    Returns:
        The ready and win curves over zero to ``max_draws`` draws.

    Raises:
        ValueError: If the hand is not of a legal resting size, if ``max_draws``
            is negative or exceeds the unseen tiles, if ``beam`` is below one, if
            ``max_distance`` is negative, if a kind has more copies visible and
            held than exist, or if the valuation does not describe the hand.
    """
    return _outlook(concealed, num_melds, max_draws, visible, player_count, beam, max_distance, valuation)


def discard_outlooks(
    concealed: list[int],
    num_melds: int,
    max_draws: int,
    *,
    visible: Sequence[int] | None = None,
    player_count: int = 4,
    beam: int | None = 1,
    max_distance: int | None = 2,
    valuation: Valuation | None = None,
) -> dict[TileKind, Outlook]:
    """The outlook left behind by each discard from a hand holding a fresh draw.

    Only the discards that leave the hand no further from ready are reported: a
    discard that gives ground is never the one being weighed, and solving it
    costs as much as solving a real candidate. Each result is exactly what
    ``hand_outlook`` reports for the hand that discard leaves.

    Args:
        concealed: Concealed tile counts indexed by kind, at post-draw size.
        num_melds: The number of called melds the hand holds.
        max_draws: The furthest draw to report, the horizon of the curves.
        visible: Counts by kind of tiles known to be outside the hand. Defaults
            to none visible.
        player_count: The number of players, which fixes the kinds in play.
        beam: The most hands to keep per improving draw, widest acceptance
            first, or ``None`` to keep every one.
        max_distance: The furthest from ready a hand may be and still be solved;
            beyond it every curve returned is flat zero. ``None`` solves at any
            distance.
        valuation: The table to price winning completions against. Given one, the
            ``points`` curve is filled; without one it stays empty.

    Returns:
        Each discard that holds the hand's distance to ready, mapped to the
        outlook of the hand it leaves.

    Raises:
        ValueError: If the hand is not of a legal post-draw size, if ``max_draws``
            is negative or exceeds the unseen tiles, if ``beam`` is below one, if
            ``max_distance`` is negative, if a kind has more copies visible and
            held than exist, or if the valuation does not describe the hand.
    """
    counts = list(concealed)
    memo: dict[tuple, float] = {}
    held = [kind for kind in kinds_in_play(player_count) if counts[kind]]
    after = discard_shantens(counts, num_melds, held)
    keeps = min(after.values())
    outlooks = {}
    for kind in held:
        if after[kind] != keeps:
            continue
        counts[kind] -= 1
        outlooks[kind] = _outlook(
            counts, num_melds, max_draws, visible, player_count, beam, max_distance, valuation, memo
        )
        counts[kind] += 1
    return outlooks


def outlook(
    hand: Hand,
    max_draws: int,
    *,
    visible: Sequence[int] | None = None,
    player_count: int = 4,
    beam: int | None = 1,
    max_distance: int | None = 2,
    valuation: Valuation | None = None,
) -> Outlook:
    """The outlook for a resting hand.

    Args:
        hand: The resting hand to analyse.
        max_draws: The furthest draw to report, the horizon of the curves.
        visible: Counts by kind of tiles known to be outside the hand. Defaults
            to none visible.
        player_count: The number of players, which fixes the kinds in play.
        beam: The most hands to keep per improving draw, widest acceptance
            first, or ``None`` to keep every one.
        max_distance: The furthest from ready a hand may be and still be solved;
            beyond it both curves are flat zero. ``None`` solves at any distance.
        valuation: The table to price winning completions against. Given one, the
            ``points`` curve is filled; without one it stays empty.

    Returns:
        The curves over zero to ``max_draws`` draws.
    """
    return _outlook(
        counts_by_kind(hand.concealed),
        len(hand.melds),
        max_draws,
        visible,
        player_count,
        beam,
        max_distance,
        valuation,
    )
