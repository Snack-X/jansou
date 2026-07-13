"""Reading MJAI JSONL into the neutral `Paifu`.

One JSON object per line, in play order. A win (`hora`) here carries neither
the winning hand nor the score breakdown, so both are recovered from the
stream: the hand by replaying the winner's draws, discards, and calls, the
winning tile from the last draw or discard, and the win value from the winner
side of the recorded score deltas.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

from jansou.core.hand import FULL_HAND_SIZE, CallSource, Meld, MeldType
from jansou.core.notation import dump_mjai as _dump_tiles
from jansou.core.notation import parse_mjai as _parse_tiles
from jansou.core.rules import Rules, preset
from jansou.core.tiles import Tile, TileKind, Wind, full_tile_set
from jansou.game.wall import DEAD_WALL_SIZE
from jansou.io.paifu import (
    Agari,
    Call,
    Discard,
    DoraReveal,
    Draw,
    Event,
    Kita,
    Paifu,
    RoundLog,
    Ryuukyoku,
)

_WIND_OF_NAME = {"E": Wind.EAST, "S": Wind.SOUTH, "W": Wind.WEST, "N": Wind.NORTH}
_NAME_OF_WIND = {wind: name for name, wind in _WIND_OF_NAME.items()}
_SOURCE_OF_RELATIVE = {1: CallSource.KAMICHA, 2: CallSource.TOIMEN, 3: CallSource.SHIMOCHA}
#: Seats to step forward from the caller to reach each source. In sanma the three
#: directions collapse onto two opponents, but every step still lands on one of them.
_STEPS_OF_SOURCE = {CallSource.KAMICHA: -1, CallSource.TOIMEN: 2, CallSource.SHIMOCHA: 1}
#: Honba a single non-winner pays: two shares in sanma, three in yonma.
_HONBA_SHARE = 100
#: Kans that, spread across seats, abort the round.
_KAN_CAP = 4


class MjaiError(ValueError):
    """A malformed MJAI stream. Subclasses ``ValueError``."""


def parse_mjai(source: str | Path | bytes) -> Paifu:
    """Parse an MJAI JSONL document into a neutral game record.

    Args:
        source: The MJAI stream. A ``str`` containing a newline is treated as the
            JSONL text itself; any other ``str`` or a ``Path`` is read as a file
            path; ``bytes`` are decoded as the JSONL text.

    Returns:
        The parsed game.

    Raises:
        MjaiError: If the stream has no ``start_kyoku`` object.
    """
    text = source if isinstance(source, str) and "\n" in source else _read(source)
    events = [json.loads(line) for line in text.splitlines() if line.strip()]
    start = next((event for event in events if event["type"] == "start_kyoku"), None)
    if start is None:
        raise MjaiError("MJAI stream has no start_kyoku")
    player_count = len(start["tehais"])
    rules = preset("tenhou-3p") if player_count == 3 else preset("tenhou")
    return Paifu(rules=rules, player_count=player_count, rounds=tuple(_parse_rounds(events, rules)))


def _read(source: str | Path | bytes) -> str:
    """The document text from a path or bytes, decompressing when gzipped."""
    data = source if isinstance(source, bytes) else Path(source).read_bytes()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return data.decode()


def _parse_rounds(events: list[dict], rules: Rules) -> list[RoundLog]:
    """Split the event stream into rounds on each start_kyoku."""
    rounds: list[RoundLog] = []
    current: list[dict] = []
    for event in events:
        if event["type"] == "start_kyoku":
            current = [event]
        elif event["type"] == "end_kyoku":
            rounds.append(_build_round(current, rules))
        elif current:
            current.append(event)
    return rounds


def _build_round(block: list[dict], rules: Rules) -> RoundLog:
    """One round from its start_kyoku header and following events."""
    start = block[0]
    player_count = len(start["tehais"])
    hands = tuple(tuple(_parse_tiles(tile)[0] for tile in seat) for seat in start["tehais"])
    events, outcome = _parse_body(block[1:], start, rules, player_count)
    return RoundLog(
        round_wind=_WIND_OF_NAME[start["bakaze"]],
        dealer=start["oya"],
        honba=start["honba"],
        riichi_sticks=start["kyotaku"],
        initial_dora=_parse_tiles(start["dora_marker"])[0],
        scores=tuple(start["scores"]),
        hands=hands,
        events=tuple(events),
        outcome=outcome,
    )


def _parse_body(
    body: list[dict],
    start: dict,
    rules: Rules,
    player_count: int,
) -> tuple[list[Event], tuple[Agari, ...] | Ryuukyoku]:
    """The normalized events and outcome of one round."""
    events: list[Event] = []
    agari: list[Agari] = []
    ryuukyoku: Ryuukyoku | None = None
    last_draw: list[Tile | None] = [None] * player_count
    last_discard: Tile | None = None
    robbed: Tile | None = None  # the added-kan or nuki tile a chankan ron wins on, until play continues
    reach_seat: int | None = None
    drawn = 0
    for event in body:
        kind = event["type"]
        if kind == "tsumo":
            tile = _parse_tiles(event["pai"])[0]
            last_draw[event["actor"]] = tile
            robbed = None
            drawn += 1
            events.append(Draw(event["actor"], tile))
        elif kind == "dahai":
            last_discard = _parse_tiles(event["pai"])[0]
            robbed = None
            declare = reach_seat == event["actor"]
            reach_seat = None
            events.append(
                Discard(event["actor"], last_discard, riichi=declare, tsumogiri=event.get("tsumogiri", False))
            )
        elif kind in ("chi", "pon", "daiminkan", "ankan", "kakan"):
            if kind == "kakan":
                # A promoted kan is robbable: a chankan ron wins on the added tile.
                robbed = _parse_tiles(event["pai"])[0]
            events.append(Call(event["actor"], _meld(event, kind, player_count)))
        elif kind == "kita":
            # A nuki is robbable like an added kan: a chankan ron wins on the North.
            robbed = Tile(TileKind.NORTH)
            events.append(Kita(event["actor"]))
        elif kind == "reach":
            reach_seat = event["actor"]
        elif kind == "dora":
            events.append(DoraReveal(_parse_tiles(event["dora_marker"])[0]))
        elif kind == "hora":
            won_on = robbed if robbed is not None else last_discard
            agari.append(_agari(event, start, rules, player_count, last_draw, won_on, first=not agari))
        elif kind == "ryukyoku":
            ryuukyoku = _ryuukyoku(
                event,
                player_count,
                events=events,
                wall_spent=drawn >= _live_wall_size(player_count),
            )
    return events, tuple(agari) if agari else (ryuukyoku or Ryuukyoku())


def _meld(event: dict, kind: str, player_count: int) -> Meld:
    """The meld an MJAI call event describes."""
    consumed = tuple(_parse_tiles(tile)[0] for tile in event["consumed"])
    if kind == "ankan":
        return Meld(MeldType.ANKAN, consumed)
    called = _parse_tiles(event["pai"])[0]
    if kind == "kakan":
        # A promotion's source does not bear on scoring; kamicha is a valid stand-in.
        return Meld(MeldType.SHOUMINKAN, (*consumed, called), called=called, source=CallSource.KAMICHA, added=called)
    source = _SOURCE_OF_RELATIVE[(event["actor"] - event["target"]) % player_count]
    meld_type = {"chi": MeldType.CHII, "pon": MeldType.PON, "daiminkan": MeldType.DAIMINKAN}[kind]
    return Meld(meld_type, (*consumed, called), called=called, source=source)


def _agari(
    event: dict,
    start: dict,
    rules: Rules,
    player_count: int,
    last_draw: list[Tile | None],
    last_discard: Tile | None,
    *,
    first: bool,
) -> Agari:
    """One MJAI win, with its value recovered from the winner's delta."""
    actor, target = event["actor"], event["target"]
    is_tsumo = actor == target
    winning_tile = last_draw[actor] if is_tsumo else last_discard
    if winning_tile is None:
        raise MjaiError("hora without a preceding draw or discard")
    deltas = tuple(event.get("deltas", (0,) * player_count))
    markers = event.get("uradora_markers") or event.get("ura_markers") or ()
    honba = start["honba"] if first else 0
    return Agari(
        winner=actor,
        from_seat=target,
        winning_tile=winning_tile,
        ura_indicators=tuple(_parse_tiles(marker)[0] for marker in markers),
        honba=honba,
        riichi_sticks=start["kyotaku"] if first else 0,
        deltas=deltas,
        value=_value_from_deltas(deltas, actor, honba=honba, rules=rules),
    )


def _value_from_deltas(deltas: tuple[int, ...], winner: int, *, honba: int, rules: Rules) -> int | None:
    """The win value (before honba and deposits) implied by the winner's delta.

    Every point a payer loses the winner gains, so the deltas sum to just the
    deposits swept off the table. Subtracting that sum and the honba from the
    winner's own gain leaves the value, for a tsumo and a ron alike -- and,
    unlike reading a single payer, it still holds when a pao liability splits
    the payment between the discarder and the responsible player.
    """
    if not deltas:
        return None
    honba_total = (rules.player_count - 1) * _HONBA_SHARE * honba
    return deltas[winner] - sum(deltas) - honba_total


def _live_wall_size(player_count: int) -> int:
    """The draws a deal affords before the live wall runs out.

    A kan or a North trades a live tile away for its replacement, so the number
    of draws a round can take is the same however many are called.

    Args:
        player_count: The number of seats at the table.

    Returns:
        The number of draws that spend the live wall exactly.
    """
    tiles = len(full_tile_set(player_count, aka_dora=False))
    return tiles - DEAD_WALL_SIZE - FULL_HAND_SIZE * player_count


def _abort_kind(events: list[Event], player_count: int) -> str:
    """The abort a reason-less draw that fell short of the wall records.

    Each accumulating abort leaves its own count behind in the round -- four
    kans, four riichi, four winds -- so it can be read back from the events.
    What no count explains is a triple ron, whose three declarations MJAI drops
    from the stream entirely.

    Args:
        events: The round's events up to the draw.
        player_count: The number of seats at the table.

    Returns:
        The Tenhou name of the abort.
    """
    if sum(1 for event in events if isinstance(event, Call) and event.meld.is_kan) >= _KAN_CAP:
        return "kan4"
    if len({event.seat for event in events if isinstance(event, Discard) and event.riichi}) >= player_count:
        return "reach4"
    if _four_winds(events, player_count):
        return "kaze4"
    return "ron3"


def _four_winds(events: list[Event], player_count: int) -> bool:
    """Whether every seat's uninterrupted first discard was the same wind."""
    if any(isinstance(event, Call) for event in events):
        return False
    discards = [event for event in events if isinstance(event, Discard)]
    if len(discards) != player_count:
        return False
    kinds = {discard.tile.kind for discard in discards}
    return len(kinds) == 1 and next(iter(kinds)).is_wind


def _ryuukyoku(event: dict, player_count: int, *, events: list[Event], wall_spent: bool) -> Ryuukyoku:
    """An MJAI drawn round.

    A Tenhou-sourced stream names no reason, so the draws it records are told
    apart by where they fall. One taken in place of a discard is a nine-terminals
    abort; one taken with the wall spent is the wall running out; and one that
    falls short of the wall is the abort its own events account for. An explicit
    ``reason`` is kept as the stream gives it.

    Args:
        event: The MJAI ``ryukyoku`` object.
        player_count: The number of seats at the table.
        events: The round's events up to the draw.
        wall_spent: Whether every live draw the wall affords was taken.
    """
    reason = event.get("reason")
    if reason is None:
        if events and isinstance(events[-1], Draw):
            reason = "yao9"
        elif wall_spent:
            reason = "exhaustive"
        else:
            reason = _abort_kind(events, player_count)
    tehais = event.get("tehais")
    tenpai = tuple(tehais[seat] is not None for seat in range(player_count)) if tehais else ()
    return Ryuukyoku(kind=reason, deltas=tuple(event.get("deltas", ())), tenpai=tenpai)


# --- Writing ----------------------------------------------------------------


def dump_mjai(paifu: Paifu) -> str:
    """Serialize a game to MJAI JSONL, for three- and four-player games alike.

    A three-player game's North bonus is written as the ``kita`` event MJAI
    defines for it, followed by the replacement draw the stream already carries.

    Args:
        paifu: The game to serialize.

    Returns:
        The game as MJAI JSONL text (one JSON object per line).
    """
    objects: list[dict] = [{"type": "start_game"}]
    for round_log in paifu.rounds:
        objects.extend(_dump_round(round_log, paifu.player_count))
    objects.append({"type": "end_game"})
    return "\n".join(json.dumps(obj, separators=(",", ":")) for obj in objects)


def _dump_round(round_log: RoundLog, player_count: int) -> list[dict]:
    """The MJAI objects of one round, from start_kyoku to end_kyoku."""
    objects: list[dict] = [
        {
            "type": "start_kyoku",
            "bakaze": _NAME_OF_WIND[round_log.round_wind],
            "dora_marker": _token(round_log.initial_dora),
            "kyoku": round_log.dealer + 1,
            "honba": round_log.honba,
            "kyotaku": round_log.riichi_sticks,
            "oya": round_log.dealer,
            "scores": list(round_log.scores),
            "tehais": [[_token(tile) for tile in hand] for hand in round_log.hands],
        }
    ]
    for event in round_log.events:
        objects.extend(_dump_event(event, player_count))
    objects.extend(_dump_outcome(round_log.outcome))
    objects.append({"type": "end_kyoku"})
    return objects


def _dump_event(event: Event, player_count: int) -> list[dict]:
    """The MJAI object(s) that encode one normalized event."""
    if isinstance(event, Draw):
        return [{"type": "tsumo", "actor": event.seat, "pai": _token(event.tile)}]
    if isinstance(event, Discard):
        dahai = {"type": "dahai", "actor": event.seat, "pai": _token(event.tile), "tsumogiri": event.tsumogiri}
        if event.riichi:
            return [{"type": "reach", "actor": event.seat}, dahai]
        return [dahai]
    if isinstance(event, Call):
        return [_dump_call(event, player_count)]
    if isinstance(event, Kita):
        return [{"type": "kita", "actor": event.seat, "pai": _token(Tile(TileKind.NORTH))}]
    return [{"type": "dora", "dora_marker": _token(event.indicator)}]  # DoraReveal


def _dump_call(event: Call, player_count: int) -> dict:
    """The MJAI call object for a claimed or promoted meld."""
    meld = event.meld
    actor = event.seat
    if meld.type is MeldType.ANKAN:
        return {"type": "ankan", "actor": actor, "consumed": [_token(tile) for tile in meld.tiles]}
    if meld.type is MeldType.SHOUMINKAN:
        consumed = _without(meld.tiles, meld.added)
        return {"type": "kakan", "actor": actor, "pai": _token(meld.added), "consumed": [_token(t) for t in consumed]}
    kind = {MeldType.CHII: "chi", MeldType.PON: "pon", MeldType.DAIMINKAN: "daiminkan"}[meld.type]
    target = (actor + _STEPS_OF_SOURCE[meld.source]) % player_count
    consumed = _without(meld.tiles, meld.called)
    return {
        "type": kind,
        "actor": actor,
        "target": target,
        "pai": _token(meld.called),
        "consumed": [_token(tile) for tile in consumed],
    }


def _dump_outcome(outcome: tuple[Agari, ...] | Ryuukyoku) -> list[dict]:
    """The MJAI object(s) that encode a round's outcome."""
    if isinstance(outcome, Ryuukyoku):
        event = {"type": "ryukyoku", "reason": outcome.kind, "deltas": list(outcome.deltas)}
        if outcome.tenpai:
            event["tehais"] = [[] if ready else None for ready in outcome.tenpai]
        return [event]
    objects: list[dict] = []
    for agari in outcome:
        hora: dict = {"type": "hora", "actor": agari.winner, "target": agari.from_seat, "deltas": list(agari.deltas)}
        if agari.ura_indicators:
            hora["uradora_markers"] = [_token(tile) for tile in agari.ura_indicators]
        objects.append(hora)
    return objects


def _token(tile: Tile) -> str:
    """The MJAI token naming one tile."""
    return _dump_tiles([tile])


def _without(tiles: tuple[Tile, ...], removed: Tile) -> list[Tile]:
    """The meld's tiles with one copy of the claimed or added tile taken out."""
    rest = list(tiles)
    rest.remove(removed)
    return rest
