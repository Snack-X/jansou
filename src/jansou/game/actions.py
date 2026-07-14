"""Actions and their positional legality.

An action is one choice at a decision point. The environment enumerates the
complete legal set at every point and offers it; a player picks one, and there
is no legal action outside the offered set. Legality is positional -- it layers
rule flags, riichi locks, the kan cap and undrawn-live-tile requirement,
swap-calling restrictions, furiten, and yaku presence on top of structural
validity. The win-context builder that yaku checks and resolution share lives
here too, since legality is its first consumer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from jansou.analysis.shanten import shanten_counts
from jansou.analysis.waits import waits_counts
from jansou.core.hand import Hand, Meld, MeldType
from jansou.core.rules import RIICHI_DEPOSIT
from jansou.core.tiles import Tile, TileKind, counts_by_kind, suited_kind
from jansou.scoring.context import WinContext
from jansou.scoring.score import ScoringError, score
from jansou.scoring.yaku import Yaku

if TYPE_CHECKING:
    from jansou.game.state import GameState, PlayerState
    from jansou.scoring.score import ScoreResult

_KAN_CAP = 4
_KYUUSHU_MIN = 9
_PON_SIZE = 2
_KAN_HAND_TILES = 3
_SUIT_MAX = 9
_RUN_SIZE = 3
_CHII_SPAN = 3
_LOW_EDGE = 6
_HIGH_EDGE = 4


@dataclass(frozen=True)
class Action:
    """Base class for the choices a player can make."""


@dataclass(frozen=True)
class Discard(Action):
    """Place one tile into the discard pile.

    ``tsumogiri`` marks the drawn-tile candidate, offered separately from an
    identical copy held in hand: which tile leaves the hand is public.
    """

    tile: Tile
    tsumogiri: bool = False


@dataclass(frozen=True)
class Riichi(Action):
    """Declare riichi together with the accompanying discard.

    ``tsumogiri`` marks the drawn-tile candidate, as for ``Discard``.
    """

    tile: Tile
    tsumogiri: bool = False


@dataclass(frozen=True)
class Tsumo(Action):
    """Declare a self-draw win."""


@dataclass(frozen=True)
class Ron(Action):
    """Declare a win on another player's tile."""


@dataclass(frozen=True)
class Chii(Action):
    """Claim the left player's discard into a run, naming the two hand tiles."""

    tiles: tuple[Tile, Tile]


@dataclass(frozen=True)
class Pon(Action):
    """Claim any opponent's discard into a triplet, naming the two hand tiles."""

    tiles: tuple[Tile, Tile]


@dataclass(frozen=True)
class OpenKan(Action):
    """Claim any opponent's discard into an open quad with three hand tiles."""


@dataclass(frozen=True)
class ClosedKan(Action):
    """Declare a closed quad of a kind held in full."""

    kind: TileKind


@dataclass(frozen=True)
class AddedKan(Action):
    """Add the fourth tile from hand to an existing pon."""

    tile: Tile


@dataclass(frozen=True)
class Nuki(Action):
    """Set aside a North as a bonus tile (three-player only)."""


@dataclass(frozen=True)
class NineTerminals(Action):
    """Declare the nine-terminals abort on the first draw."""


@dataclass(frozen=True)
class Pass(Action):
    """Decline every reaction to a tile."""


@dataclass(frozen=True)
class DeclareTenpai(Action):
    """State readiness (or not) at an exhaustive draw."""

    declare: bool


# --- Win context and detection ------------------------------------------------


def win_context(
    state: GameState,
    seat: int,
    *,
    is_tsumo: bool,
    rinshan: bool = False,
    chankan: bool = False,
) -> WinContext:
    """The scoring context for a win by this seat, from the current state.

    Args:
        state: The current game state.
        seat: The winning seat.
        is_tsumo: Whether the win is a self-draw rather than a ron.
        rinshan: Whether the winning tile is a post-kan replacement draw.
        chankan: Whether the win robs a kan.

    Returns:
        The context capturing every scoring condition this win qualifies for.
    """
    player = state.players[seat]
    remaining = state.wall.live_draws_remaining
    blessing = is_tsumo and state.first_go_around and not player.discards and not player.melds
    return WinContext(
        rules=state.rules,
        round_wind=state.round_wind,
        seat_wind=state.seat_wind(seat),
        is_tsumo=is_tsumo,
        riichi=player.riichi,
        double_riichi=player.double_riichi,
        ippatsu=player.ippatsu,
        haitei=is_tsumo and not rinshan and remaining == 0,
        houtei=not is_tsumo and not chankan and remaining == 0,
        rinshan=rinshan,
        chankan=chankan,
        tenhou=blessing and state.is_dealer(seat),
        chiihou=blessing and not state.is_dealer(seat),
        dora_indicators=state.wall.dora_indicators,
        ura_indicators=state.wall.ura_indicators,
        nuki_count=player.nuki_count,
        honba=state.honba,
        riichi_sticks=state.deposit_pool // RIICHI_DEPOSIT,
    )


def _win_hand(player: PlayerState, winning_tile: Tile, *, is_tsumo: bool) -> Hand:
    """The completed hand: tsumo already holds the tile, ron adds it."""
    if is_tsumo:
        return player.as_hand()
    return Hand((*player.concealed, winning_tile), tuple(player.melds))


def win_result(state: GameState, seat: int, winning_tile: Tile, context: WinContext) -> ScoreResult | None:
    """The score of a hypothetical win under a context, or None when it does not win.

    Args:
        state: The current game state.
        seat: The winning seat.
        winning_tile: The tile completing the hand.
        context: The scoring context to evaluate under.

    Returns:
        The scored result, or ``None`` when the hand does not form a valid win.
    """
    hand = _win_hand(state.players[seat], winning_tile, is_tsumo=context.is_tsumo)
    try:
        return score(hand, winning_tile, context)
    except ScoringError:
        return None


# --- Furiten ------------------------------------------------------------------


def current_waits(state: GameState, seat: int) -> set[TileKind]:
    """The wait set of a seat's resting concealed hand.

    Args:
        state: The current game state.
        seat: The seat whose waits to compute.

    Returns:
        The tile kinds that would complete the hand.
    """
    player = state.players[seat]
    return waits_counts(counts_by_kind(player.concealed), tuple(player.melds), player_count=state.player_count)


def is_furiten(state: GameState, seat: int) -> bool:
    """Whether the seat is barred from ron by any furiten form (§17).

    Args:
        state: The current game state.
        seat: The seat to test.

    Returns:
        ``True`` when any furiten form bars the seat from ron.
    """
    player = state.players[seat]
    if player.riichi_furiten or player.temporary_furiten:
        return True
    waits = current_waits(state, seat)
    if not waits:
        return False
    return any(discard.tile.kind in waits for discard in player.discards)


# --- Small tile helpers -------------------------------------------------------


def _distinct_by_red(tiles: list[Tile]) -> list[Tile]:
    """One representative tile per distinct kind-and-red, in first-seen order."""
    result: list[Tile] = []
    seen: set[tuple[TileKind, bool]] = set()
    for tile in tiles:
        key = (tile.kind, tile.red)
        if key not in seen:
            seen.add(key)
            result.append(tile)
    return result


def _pair_combos(tiles: list[Tile]) -> list[tuple[Tile, Tile]]:
    """The distinct two-tile selections of one kind (at most one copy is red)."""
    reds = [tile for tile in tiles if tile.red]
    ordinaries = [tile for tile in tiles if not tile.red]
    combos: list[tuple[Tile, Tile]] = []
    if len(ordinaries) >= _PON_SIZE:
        combos.append((ordinaries[0], ordinaries[1]))
    if reds and ordinaries:
        chosen = sorted((reds[0], ordinaries[0]))
        combos.append((chosen[0], chosen[1]))
    return combos


def _can_kan(state: GameState) -> bool:
    """Whether a kan may be declared: under the cap and with a live tile left."""
    return state.kans < _KAN_CAP and state.wall.live_draws_remaining >= 1


def _can_extract(state: GameState) -> bool:
    """Whether a North extraction may be declared."""
    return state.rules.is_sanma and state.wall.live_draws_remaining >= 1


def _hand_tiles(player: PlayerState) -> list[Tile]:
    """The concealed tiles plus the drawn tile.

    Every caller sits in the self menu after a draw, so ``drawn`` is present.
    """
    return [*player.concealed, player.drawn]  # type: ignore[list-item]


# --- Self-turn actions --------------------------------------------------------


def self_actions(state: GameState, *, rinshan: bool = False) -> list[Action]:
    """Every legal action at the current player's own decision point.

    Args:
        state: The current game state.
        rinshan: Whether the current draw is a post-kan replacement, which a
            tsumo here would score as rinshan.

    Returns:
        The complete legal action set for the seat on turn.
    """
    seat = state.current_player
    player = state.players[seat]
    if player.drawn is None:
        return _discards(state, seat)
    drawn = player.drawn
    actions: list[Action] = []
    if drawn.kind in current_waits(state, seat):
        tsumo_context = win_context(state, seat, is_tsumo=True, rinshan=rinshan)
        if win_result(state, seat, drawn, tsumo_context) is not None:
            actions.append(Tsumo())
    if player.is_riichi:
        actions.extend(_riichi_locked_options(state, seat, drawn))
    else:
        actions.extend(_open_self_options(state, seat))
    return actions


def _riichi_locked_options(state: GameState, seat: int, drawn: Tile) -> list[Action]:
    """The narrow menu a riichi hand has: allowed kan, North, forced tsumogiri."""
    options: list[Action] = list(_riichi_closed_kans(state, seat))
    if _can_extract(state) and drawn.kind is TileKind.NORTH:
        options.append(Nuki())
    options.append(Discard(drawn, tsumogiri=True))
    return options


def _riichi_closed_kans(state: GameState, seat: int) -> list[Action]:
    """A closed kan on the just-drawn fourth copy that leaves the waits intact."""
    player = state.players[seat]
    if not state.rules.closed_kan_after_riichi or not _can_kan(state) or player.drawn is None:
        return []
    kind = player.drawn.kind
    if counts_by_kind(player.concealed)[kind] != _KAN_HAND_TILES:
        return []
    if not _kan_preserves_waits(state, seat, kind):
        return []
    return [ClosedKan(kind)]


def _kan_preserves_waits(state: GameState, seat: int, kind: TileKind) -> bool:
    """Whether an ankan of a kind leaves the resting wait set exactly unchanged."""
    player = state.players[seat]
    before = waits_counts(counts_by_kind(player.concealed), tuple(player.melds), player_count=state.player_count)
    after_concealed = [tile for tile in player.concealed if tile.kind is not kind]
    after_melds = (*player.melds, Meld(MeldType.ANKAN, (Tile(kind),) * _KAN_CAP))
    after = waits_counts(counts_by_kind(after_concealed), after_melds, player_count=state.player_count)
    return before == after


def _open_self_options(state: GameState, seat: int) -> list[Action]:
    """The full self-menu of a concealed, non-riichi hand after a draw."""
    options: list[Action] = []
    options.extend(_concealed_kans(state, seat))
    options.extend(_added_kans(state, seat))
    if _can_extract(state) and any(tile.kind is TileKind.NORTH for tile in _hand_tiles(state.players[seat])):
        options.append(Nuki())
    options.extend(_nine_terminals(state, seat))
    options.extend(_riichi_options(state, seat))
    options.extend(_discards(state, seat))
    return options


def _concealed_kans(state: GameState, seat: int) -> list[Action]:
    """A closed kan for each kind the hand holds in all four copies."""
    if not _can_kan(state):
        return []
    counts = counts_by_kind(_hand_tiles(state.players[seat]))
    return [ClosedKan(TileKind(kind)) for kind, count in enumerate(counts) if count == _KAN_CAP]


def _added_kans(state: GameState, seat: int) -> list[Action]:
    """An added kan for each pon whose fourth copy sits in hand."""
    if not _can_kan(state):
        return []
    player = state.players[seat]
    pon_kinds = {meld.tiles[0].kind for meld in player.melds if meld.type is MeldType.PON}
    return [AddedKan(tile) for tile in _distinct_by_red(_hand_tiles(player)) if tile.kind in pon_kinds]


def _nine_terminals(state: GameState, seat: int) -> list[Action]:
    """The nine-terminals abort, on an uninterrupted first draw."""
    player = state.players[seat]
    if not state.rules.abort_kyuushu_kyuuhai or not state.first_go_around or player.discards or player.melds:
        return []
    distinct = {tile.kind for tile in _hand_tiles(player) if tile.kind.is_yaochuu}
    return [NineTerminals()] if len(distinct) >= _KYUUSHU_MIN else []


def _riichi_options(state: GameState, seat: int) -> list[Action]:
    """Riichi declarations for each discard that leaves the hand ready."""
    player = state.players[seat]
    rules = state.rules
    if not player.is_concealed or player.is_riichi or state.scores[seat] < RIICHI_DEPOSIT:
        return []
    remaining = state.wall.live_draws_remaining
    # Riichi is never allowed on the final discard (houtei).
    if remaining == 0 or (remaining < state.player_count and not rules.riichi_without_draw):
        return []
    # The self menu follows a draw, so the drawn tile is always present here.
    if not rules.riichi_without_tenpai and shanten_counts(counts_by_kind(_hand_tiles(player)), len(player.melds)) > 0:
        return []
    candidates: list[Action] = [Riichi(tile) for tile in _distinct_by_red(player.concealed)]
    candidates.append(Riichi(player.drawn, tsumogiri=True))  # type: ignore[arg-type]
    if not rules.riichi_without_tenpai:
        candidates = [option for option in candidates if _ready_after_discard(state, seat, option.tile)]
    return candidates


def _ready_after_discard(state: GameState, seat: int, tile: Tile) -> bool:
    """Whether discarding a tile leaves the hand at ready (shanten zero)."""
    player = state.players[seat]
    counts = counts_by_kind(_hand_tiles(player))
    counts[tile.kind] -= 1
    return shanten_counts(counts, len(player.melds)) == 0


def _discards(state: GameState, seat: int) -> list[Action]:
    """A discard per distinct concealed kind plus the draw, minus swap-banned kinds."""
    player = state.players[seat]
    restriction = state.post_call_restriction
    options: list[Action] = [
        Discard(tile) for tile in _distinct_by_red(player.concealed) if tile.kind not in restriction
    ]
    if player.drawn is not None:
        options.append(Discard(player.drawn, tsumogiri=True))
    return options


def kuikae_banned_kinds(choice: Action, tile: Tile) -> frozenset[TileKind]:
    """The kinds the swap-calling ban forbids discarding after a call.

    Args:
        choice: The chii or pon claiming the tile.
        tile: The claimed tile.

    Returns:
        The claimed kind, plus the suji kind when a chii claims a run's edge.
    """
    banned = {tile.kind}
    if isinstance(choice, Chii) and tile.kind.rank is not None:
        ranks = sorted(used.kind for used in (*choice.tiles, tile))
        if ranks[0] == tile.kind and tile.kind.rank <= _LOW_EDGE:
            banned.add(TileKind(tile.kind + _CHII_SPAN))
        elif ranks[-1] == tile.kind and tile.kind.rank >= _HIGH_EDGE:
            banned.add(TileKind(tile.kind - _CHII_SPAN))
    return frozenset(banned)


# --- Reactions ----------------------------------------------------------------


def discard_reactions(state: GameState, seat: int, *, final: bool) -> list[Action]:
    """The reactions a seat may make to the current discard.

    Args:
        state: The current game state.
        seat: The reacting seat.
        final: Whether this is the wall's last discard, when only ron is allowed.

    Returns:
        The legal reactions, including ``Pass`` whenever any reaction is offered.
    """
    _, tile = _require_discard(state)
    player = state.players[seat]
    actions: list[Action] = []
    if tile.kind in current_waits(state, seat) and not is_furiten(state, seat):
        ron_context = win_context(state, seat, is_tsumo=False)
        if win_result(state, seat, tile, ron_context) is not None:
            actions.append(Ron())
    if not final and not player.is_riichi:
        actions.extend(_pon_options(state, seat, tile))
        actions.extend(_open_kan_option(state, seat, tile))
        if state.last_discard[0] == (seat - 1) % state.player_count:
            actions.extend(_chii_options(state, seat, tile))
    if actions:
        actions.append(Pass())
    return actions


def _require_discard(state: GameState) -> tuple[int, Tile]:
    """The current discard, which a reaction presupposes."""
    if state.last_discard is None:
        raise RuntimeError("no discard to react to")
    return state.last_discard


def _pon_options(state: GameState, seat: int, tile: Tile) -> list[Action]:
    """Pon selections when the hand holds two or more of the kind."""
    held = [held_tile for held_tile in state.players[seat].concealed if held_tile.kind is tile.kind]
    if len(held) < _PON_SIZE:
        return []
    return [Pon(combo) for combo in _pair_combos(held)]


def _open_kan_option(state: GameState, seat: int, tile: Tile) -> list[Action]:
    """An open kan when the hand holds the other three copies."""
    if not _can_kan(state):
        return []
    held = [held_tile for held_tile in state.players[seat].concealed if held_tile.kind is tile.kind]
    return [OpenKan()] if len(held) >= _KAN_HAND_TILES else []


def _chii_options(state: GameState, seat: int, tile: Tile) -> list[Action]:
    """Every run pairing that claims the tile and leaves a discard, red copies distinguished."""
    if state.rules.is_sanma or not tile.kind.is_suited:
        return []
    player = state.players[seat]
    rank = tile.kind.rank
    options: list[Action] = []
    for low in (rank - 2, rank - 1, rank):
        ranks = (low, low + 1, low + 2)
        if not all(1 <= value <= _SUIT_MAX for value in ranks):
            continue
        needed = [suited_kind(tile.suit, value) for value in ranks if value != rank]
        picks = [_distinct_by_red([held for held in player.concealed if held.kind is kind]) for kind in needed]
        if all(picks):
            pairings = (Chii(tuple(sorted((first, second)))) for first in picks[0] for second in picks[1])
            options.extend(option for option in pairings if _chii_leaves_discard(state, seat, option, tile))
    return options


def _chii_leaves_discard(state: GameState, seat: int, choice: Chii, tile: Tile) -> bool:
    """Whether some discard stays legal after the chii's swap-calling ban."""
    if not state.rules.kuikae_ban:
        return True
    banned = kuikae_banned_kinds(choice, tile)
    remaining = list(state.players[seat].concealed)
    for used in choice.tiles:
        remaining.remove(used)
    return any(held.kind not in banned for held in remaining)


def robbed_kan_reactions(state: GameState, seat: int, robbed_tile: Tile, *, added_kan: bool) -> list[Action]:
    """Ron on a robbed kan tile (chankan or the thirteen-orphans closed-kan rob).

    Args:
        state: The current game state.
        seat: The reacting seat.
        robbed_tile: The tile the kan would consume.
        added_kan: Whether the kan is an added kan rather than a closed kan.

    Returns:
        ``[Ron(), Pass()]`` when the seat may rob the tile, otherwise empty.
    """
    return [Ron(), Pass()] if _can_rob(state, seat, robbed_tile, added_kan=added_kan) else []


def _can_rob(state: GameState, seat: int, tile: Tile, *, added_kan: bool) -> bool:
    """Whether the seat may rob a kan's tile for a win."""
    if is_furiten(state, seat):
        return False
    context = win_context(state, seat, is_tsumo=False, chankan=True)
    result = win_result(state, seat, tile, context)
    if result is None:
        return False
    if added_kan:
        return True
    if not state.rules.kokushi_ankan_chankan:
        return False
    return any(value.yaku is Yaku.KOKUSHI for value in result.yaku)


def north_reactions(state: GameState, seat: int, north_tile: Tile) -> list[Action]:
    """Ron on an extracted North, scored as an ordinary ron off that tile.

    Args:
        state: The current game state.
        seat: The reacting seat.
        north_tile: The extracted North tile.

    Returns:
        ``[Ron(), Pass()]`` when the seat can win on the North, otherwise empty.
    """
    context = win_context(state, seat, is_tsumo=False)
    if is_furiten(state, seat) or win_result(state, seat, north_tile, context) is None:
        return []
    return [Ron(), Pass()]


def tenpai_declaration_options() -> list[Action]:
    """The declare-or-not choice at an exhaustive draw.

    Returns:
        Both ``DeclareTenpai`` options, to declare readiness or not.
    """
    return [DeclareTenpai(declare=True), DeclareTenpai(declare=False)]
