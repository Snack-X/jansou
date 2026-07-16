"""A format-neutral game record and the replay that turns wins into scorables.

Every parser (mjlog, Tenhou JSON, MJAI) produces the same `Paifu`: a rules
configuration and a sequence of rounds, each a normalized event stream plus an
outcome. `replay_round` walks that stream once, tracking each seat's hand and
the situational state a win depends on -- riichi and its one-shot window, the
live-wall depth behind haitei and houtei, the robbing window behind chankan,
and the untouched-first-turn state behind blessings -- and emits one
`AgariRecord` per win: a hand, its winning tile, the win context to score it
against, and the values the log recorded to check that score against.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from jansou.core.hand import FULL_HAND_SIZE, Hand, Meld, MeldType
from jansou.core.rules import RIICHI_DEPOSIT
from jansou.core.tiles import Tile, TileKind, Wind
from jansou.game.progression import settle_deposits
from jansou.game.wall import DEAD_WALL_SIZE
from jansou.scoring.context import WinContext

if TYPE_CHECKING:
    from jansou.core.rules import Rules


# --- Normalized events ------------------------------------------------------


@dataclass(frozen=True)
class Draw:
    """A tile taken from the wall or, after a kan or kita, from the dead wall.

    Attributes:
        seat: The seat that drew.
        tile: The tile drawn.
    """

    seat: int
    tile: Tile


@dataclass(frozen=True)
class Discard:
    """A tile put out; ``riichi`` marks the sideways riichi-declaring discard.

    Attributes:
        seat: The seat that discarded.
        tile: The tile discarded.
        riichi: Whether this discard declared riichi (the sideways tile).
        tsumogiri: Whether the discard gave up the drawn tile unchanged.
    """

    seat: int
    tile: Tile
    riichi: bool = False
    tsumogiri: bool = False


@dataclass(frozen=True)
class Call:
    """A meld claimed or promoted: the formed meld carries its kind and tiles.

    Attributes:
        seat: The seat that made the call.
        meld: The meld formed, carrying its type and tiles.
    """

    seat: int
    meld: Meld


@dataclass(frozen=True)
class Kita:
    """A three-player North set aside for a bonus, drawing a replacement.

    Attributes:
        seat: The seat that set the North aside.
    """

    seat: int


@dataclass(frozen=True)
class DoraReveal:
    """A freshly turned kan-dora indicator.

    Attributes:
        indicator: The newly revealed dora indicator tile.
    """

    indicator: Tile


Event = Draw | Discard | Call | Kita | DoraReveal


# --- Outcomes ---------------------------------------------------------------


@dataclass(frozen=True)
class Agari:
    """One win in a round; several share a round on a multiple ron.

    Attributes:
        winner: The seat that won.
        from_seat: The seat the winning tile came from (the winner itself on a tsumo).
        winning_tile: The tile that completed the hand.
        ura_indicators: The ura-dora indicators revealed to this winner, if any.
        honba: The honba count credited to this win.
        riichi_sticks: The riichi deposits collected by this win.
        deltas: The per-seat score changes the log recorded for this win.
        fu: The fu the log recorded, or None when not stated (e.g. a limit hand).
        value: The point value the log recorded, or None when not stated.
        hand: The winner's hand when the log carries it outright, else None.
    """

    winner: int
    from_seat: int
    winning_tile: Tile
    ura_indicators: tuple[Tile, ...] = ()
    honba: int = 0
    riichi_sticks: int = 0
    deltas: tuple[int, ...] = ()
    fu: int | None = None
    value: int | None = None
    hand: Hand | None = None

    @property
    def is_tsumo(self) -> bool:
        """A self-draw win: the winning tile came from the winner's own draw."""
        return self.winner == self.from_seat


@dataclass(frozen=True)
class Ryuukyoku:
    """A round that ended without a win.

    Attributes:
        kind: The draw kind (``"exhaustive"`` or an abortive-draw name).
        deltas: The per-seat score changes settled at the draw.
        tenpai: Per-seat readiness, marking who was counted tenpai.
    """

    kind: str = "exhaustive"
    deltas: tuple[int, ...] = ()
    tenpai: tuple[bool, ...] = ()


Outcome = tuple[Agari, ...] | Ryuukyoku


# --- Records ----------------------------------------------------------------


@dataclass(frozen=True)
class RoundLog:
    """One round: its opening state, its events, and how it ended.

    Consecutive rounds of one game chain: each round's ``scores`` equals the
    previous round's settled scores (``settled_scores``), and its
    ``riichi_sticks`` the deposits the previous round left on the table
    (``leftover_deposits``) -- across wins, draws, multiple ron, and the
    three-player game alike.

    Attributes:
        round_wind: The prevailing wind of the round.
        dealer: The seat sitting as dealer.
        honba: The honba (repeat) count.
        riichi_sticks: The riichi deposits carried into the round.
        initial_dora: The dora indicator turned at the start.
        scores: Each seat's score at the start of the round.
        hands: Each seat's dealt starting hand.
        events: The normalized event stream, in play order.
        outcome: How the round ended: the wins, or the draw.
    """

    round_wind: Wind
    dealer: int
    honba: int
    riichi_sticks: int
    initial_dora: Tile
    scores: tuple[int, ...]
    hands: tuple[tuple[Tile, ...], ...]
    events: tuple[Event, ...]
    outcome: Outcome


@dataclass(frozen=True)
class Paifu:
    """A whole game: its rules and its rounds in order.

    Attributes:
        rules: The rules configuration the game was played under.
        player_count: The number of seats (three or four).
        rounds: The rounds played, in order.
        final_scores: The final standing in points per seat: the standing the
            log states outright, or one settled from the rounds when the
            format carries none (MJAI); ``None`` when neither is available.
        final_points: The platform's adjusted result per seat (Tenhou's uma
            column), or ``None`` when the source carries no standing.
    """

    rules: Rules
    player_count: int
    rounds: tuple[RoundLog, ...]
    final_scores: tuple[int, ...] | None = None
    final_points: tuple[float, ...] | None = None


@dataclass(frozen=True)
class AgariRecord:
    """A win rebuilt for scoring, with the values the log expects it to reach.

    Attributes:
        hand: The winner's completed 14-tile hand.
        winning_tile: The tile that completed the hand.
        context: The win context to score the hand against.
        winner: The seat that won.
        from_seat: The seat the winning tile came from (the winner on a tsumo).
        expected_fu: The fu the log recorded, or None when not stated.
        expected_value: The point value the log recorded, or None when not stated.
        expected_deltas: The per-seat score changes the log recorded.
    """

    hand: Hand
    winning_tile: Tile
    context: WinContext
    winner: int
    from_seat: int
    expected_fu: int | None
    expected_value: int | None
    expected_deltas: tuple[int, ...]


# --- Score chain --------------------------------------------------------------

#: The names the formats give a triple-ron abort.
_TRIPLE_RON_KINDS = frozenset({"ron3", "triple_ron", "sanchahou", "三家和", "三家和了"})


def _banked_riichi_seats(round_log: RoundLog) -> list[int]:
    """Seats whose riichi deposit was banked during the round.

    Every riichi-declaring discard banks its deposit once it survives the ron
    window; one the round ends on -- taken by a winner, or by the three rons
    of a triple-ron abort -- never completes, and never pays.
    """
    declared = [event.seat for event in round_log.events if isinstance(event, Discard) and event.riichi]
    outcome = round_log.outcome
    ends_on_ron = not isinstance(outcome, Ryuukyoku) or outcome.kind in _TRIPLE_RON_KINDS
    last = round_log.events[-1] if round_log.events else None
    if ends_on_ron and isinstance(last, Discard) and last.riichi:
        declared.remove(last.seat)
    return declared


def settled_scores(round_log: RoundLog) -> tuple[int, ...]:
    """Each seat's score when the round ends.

    The start-of-round ``scores``, less each riichi deposit banked during the
    round, plus the outcome's recorded deltas (which already carry the honba
    and the deposits a win sweeps). Within one game this equals the next
    round's ``scores``.

    Args:
        round_log: The round to settle.

    Returns:
        Each seat's score after the round.
    """
    scores = list(round_log.scores)
    for seat in _banked_riichi_seats(round_log):
        scores[seat] -= RIICHI_DEPOSIT
    outcome = round_log.outcome
    for deltas in [outcome.deltas] if isinstance(outcome, Ryuukyoku) else [agari.deltas for agari in outcome]:
        for seat, delta in enumerate(deltas):
            scores[seat] += delta
    return tuple(scores)


def leftover_deposits(round_log: RoundLog) -> int:
    """The riichi deposits still on the table when the round ends.

    A win sweeps every deposit; a draw carries the pool forward, grown by the
    riichi banked during the round. Within one game this equals the next
    round's ``riichi_sticks``.

    Args:
        round_log: The round to settle.

    Returns:
        The number of deposits left on the table.
    """
    if isinstance(round_log.outcome, Ryuukyoku):
        return round_log.riichi_sticks + len(_banked_riichi_seats(round_log))
    return 0


def settled_final_scores(rounds: tuple[RoundLog, ...], rules: Rules) -> tuple[int, ...]:
    """The final standing settled from a game's rounds.

    The last round's settled scores, plus the end-of-game deposit settlement
    the rule set prescribes (leftover deposits to first place where the rule
    says so). For a source whose log states no standing outright, this is the
    game's ``final_scores``.

    Args:
        rounds: The game's rounds, in order; must be non-empty.
        rules: The rule set governing the end-of-game settlement.

    Returns:
        Each seat's final score.
    """
    last = rounds[-1]
    pool = leftover_deposits(last) * RIICHI_DEPOSIT
    return tuple(settle_deposits(list(settled_scores(last)), pool, rules))


# --- Replay -----------------------------------------------------------------


@dataclass
class _SeatState:
    """The concealed tiles and melds one seat holds as a round is replayed."""

    concealed: list[Tile] = field(default_factory=list)
    melds: list[Meld] = field(default_factory=list)


@dataclass
class _Situation:
    """The round-wide state a win reads: riichi, ippatsu, the wall, and robbing."""

    player_count: int
    live_remaining: int
    riichi: list[bool]
    ippatsu: list[bool]
    riichi_pending: list[bool]
    double_riichi: list[bool]
    has_discarded: list[bool]
    draw_count: list[int]
    nuki: list[int]
    dora_indicators: list[Tile]
    any_call: bool = False
    last_was_kan_draw: bool = False
    last_draw_was_rinshan: bool = False
    ippatsu_break_pending: bool = False
    pending_rob: str | None = None


def _blank_situation(player_count: int, initial_dora: Tile) -> _Situation:
    """The situation at the start of a round, before any event."""
    live = (136 if player_count == 4 else 108) - DEAD_WALL_SIZE - FULL_HAND_SIZE * player_count
    return _Situation(
        player_count=player_count,
        live_remaining=live,
        riichi=[False] * player_count,
        ippatsu=[False] * player_count,
        riichi_pending=[False] * player_count,
        double_riichi=[False] * player_count,
        has_discarded=[False] * player_count,
        draw_count=[0] * player_count,
        nuki=[0] * player_count,
        dora_indicators=[initial_dora],
    )


def _apply_draw(state: _SeatState, sit: _Situation, event: Draw) -> None:
    """Add a drawn tile and advance the wall and one-shot state."""
    state.concealed.append(event.tile)
    sit.draw_count[event.seat] += 1
    if sit.ippatsu_break_pending:
        sit.ippatsu = [False] * sit.player_count
        sit.ippatsu_break_pending = False
    sit.pending_rob = None
    if sit.last_was_kan_draw:
        sit.last_was_kan_draw = False
        sit.last_draw_was_rinshan = True
    else:
        sit.last_draw_was_rinshan = False
        sit.live_remaining -= 1


def _apply_discard(state: _SeatState, sit: _Situation, event: Discard) -> None:
    """Remove a discarded tile and update the riichi and one-shot windows."""
    state.concealed.remove(event.tile)
    if event.riichi:
        sit.riichi[event.seat] = True
        sit.riichi_pending[event.seat] = True
        sit.double_riichi[event.seat] = not sit.has_discarded[event.seat] and not sit.any_call
    if sit.riichi_pending[event.seat]:
        sit.ippatsu[event.seat] = True
        sit.riichi_pending[event.seat] = False
    elif sit.ippatsu[event.seat]:
        sit.ippatsu[event.seat] = False
    sit.has_discarded[event.seat] = True
    sit.last_draw_was_rinshan = False


def _apply_call(state: _SeatState, sit: _Situation, event: Call) -> None:
    """Fold a claimed meld into a seat and break the interrupted windows."""
    meld = event.meld
    sit.any_call = True
    if meld.type is MeldType.SHOUMINKAN:
        # A promoted kan is robbable: defer the ippatsu break for a chankan win.
        sit.ippatsu_break_pending = True
        sit.pending_rob = "kakan"
        _promote_shouminkan(state, meld)
        sit.live_remaining -= 1
        sit.last_was_kan_draw = True
        return
    sit.ippatsu = [False] * sit.player_count
    _remove_meld_tiles(state, meld)
    state.melds.append(meld)
    if meld.is_kan:
        sit.live_remaining -= 1
        sit.last_was_kan_draw = True


def _promote_shouminkan(state: _SeatState, meld: Meld) -> None:
    """Upgrade the matching pon to the added kan and retire the added tile."""
    for index, existing in enumerate(state.melds):
        if existing.type is MeldType.PON and existing.tiles[0].kind is meld.tiles[0].kind:
            state.melds[index] = meld
            break
    state.concealed.remove(meld.added)  # type: ignore[arg-type]


def _remove_meld_tiles(state: _SeatState, meld: Meld) -> None:
    """Take a meld's non-claimed tiles out of the concealed part."""
    from_hand = list(meld.tiles)
    if meld.called is not None:
        from_hand.remove(meld.called)
    for tile in from_hand:
        state.concealed.remove(tile)


def _apply_kita(state: _SeatState, sit: _Situation, event: Kita) -> None:
    """Set a North aside for its bonus and draw a replacement, robbable in turn."""
    state.concealed.remove(Tile(TileKind.NORTH))
    sit.nuki[event.seat] += 1
    sit.any_call = True
    # A kita interrupts the untouched go-around but keeps a live ippatsu until
    # play continues; it is robbable but does not carry the chankan yaku.
    sit.ippatsu_break_pending = True
    sit.pending_rob = "kita"
    sit.live_remaining -= 1
    sit.last_was_kan_draw = True


def _apply_event(seats: list[_SeatState], sit: _Situation, event: Event) -> None:
    """Advance the replay by one event."""
    if isinstance(event, Draw):
        _apply_draw(seats[event.seat], sit, event)
    elif isinstance(event, Discard):
        _apply_discard(seats[event.seat], sit, event)
    elif isinstance(event, Call):
        _apply_call(seats[event.seat], sit, event)
    elif isinstance(event, Kita):
        _apply_kita(seats[event.seat], sit, event)
    else:
        sit.dora_indicators.append(event.indicator)


def _win_context(rules: Rules, round_log: RoundLog, sit: _Situation, agari: Agari) -> WinContext:
    """The context to score one win against, from the replayed situation."""
    who = agari.winner
    seat_wind = Wind((who - round_log.dealer) % sit.player_count)
    first_draw = agari.is_tsumo and sit.draw_count[who] == 1 and not sit.any_call
    double = sit.double_riichi[who]
    return WinContext(
        rules=rules,
        round_wind=round_log.round_wind,
        seat_wind=seat_wind,
        is_tsumo=agari.is_tsumo,
        riichi=sit.riichi[who] and not double,
        double_riichi=double,
        ippatsu=sit.ippatsu[who],
        haitei=agari.is_tsumo and sit.live_remaining <= 0 and not sit.last_draw_was_rinshan,
        houtei=not agari.is_tsumo and sit.live_remaining <= 0 and sit.pending_rob is None,
        rinshan=agari.is_tsumo and sit.last_draw_was_rinshan,
        chankan=not agari.is_tsumo and sit.pending_rob == "kakan",
        tenhou=first_draw and who == round_log.dealer,
        chiihou=first_draw and who != round_log.dealer,
        dora_indicators=tuple(sit.dora_indicators),
        ura_indicators=agari.ura_indicators,
        nuki_count=sit.nuki[who],
        honba=agari.honba,
        riichi_sticks=agari.riichi_sticks,
    )


def _win_hand(seats: list[_SeatState], agari: Agari) -> Hand:
    """The winner's 14-tile hand, from the log if given or the replayed state."""
    if agari.hand is not None:
        return agari.hand
    state = seats[agari.winner]
    concealed = list(state.concealed)
    if not agari.is_tsumo:
        concealed.append(agari.winning_tile)
    return Hand(tuple(concealed), tuple(state.melds))


def replay_round(round_log: RoundLog, rules: Rules, player_count: int) -> list[AgariRecord]:
    """Replay a round and return one scorable record per win (empty on a draw).

    Walks the round's event stream once to rebuild each winner's hand and the
    situational state a win depends on -- riichi and its one-shot window, the
    live-wall depth behind haitei and houtei, the robbing window behind chankan,
    and the untouched-first-turn state behind blessings.

    Args:
        round_log: The round to replay.
        rules: The rules configuration to score the wins under.
        player_count: The number of seats (three or four).

    Returns:
        One ``AgariRecord`` per win in the round, or an empty list when the round
        ended in a draw.
    """
    if isinstance(round_log.outcome, Ryuukyoku):
        return []
    seats = [_SeatState(concealed=list(hand)) for hand in round_log.hands]
    sit = _blank_situation(player_count, round_log.initial_dora)
    for event in round_log.events:
        _apply_event(seats, sit, event)
    return [
        AgariRecord(
            hand=_win_hand(seats, agari),
            winning_tile=agari.winning_tile,
            context=_win_context(rules, round_log, sit, agari),
            winner=agari.winner,
            from_seat=agari.from_seat,
            expected_fu=agari.fu,
            expected_value=agari.value,
            expected_deltas=agari.deltas,
        )
        for agari in round_log.outcome
    ]
