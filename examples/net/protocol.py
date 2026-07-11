"""The wire protocol: newline-delimited JSON, and its codecs.

Every message is one JSON object per line with a ``type`` field. Tiles travel
as MJAI tokens (``"5m"``, ``"5mr"``, ``"E"``), enums as their lowercased names.
Actions and events are encoded symmetrically so the reference client can
rebuild jansou objects; outcome summaries (wins, ryuukyoku) decode to ``None``
because their full scoring detail is display-only.

A client answers a decision by index into the offered action list, so the
server never has to trust a client-built action object.
"""

from __future__ import annotations

import json
from typing import IO

from jansou.core.hand import Hand, MeldType
from jansou.core.notation import dump_mjai, parse_mjai
from jansou.core.tiles import Tile, Wind
from jansou.game import actions as act
from jansou.game import events as ev
from jansou.game.flow import DecisionKind


class ProtocolError(Exception):
    """A malformed, unexpected, or truncated message."""


# --- Line-delimited JSON over a socket ------------------------------------


def send_message(sock, message: dict) -> None:
    """Send one message as a single JSON line."""
    sock.sendall((json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8"))


def read_message(reader: IO[str] | IO[bytes]) -> dict:
    """Read one JSON-line message, requiring a dict with a ``type`` field.

    Accepts a text or binary line reader; ``json.loads`` takes either.
    """
    line = reader.readline()
    if not line:
        raise ProtocolError("connection closed")
    try:
        message = json.loads(line)
    except json.JSONDecodeError as error:
        raise ProtocolError(f"invalid JSON: {line!r}") from error
    if not isinstance(message, dict) or not isinstance(message.get("type"), str):
        raise ProtocolError(f"message must be an object with a 'type': {line!r}")
    return message


# --- Tiles -----------------------------------------------------------------


def tile_to_wire(tile: Tile) -> str:
    """One tile as its MJAI token."""
    return dump_mjai([tile])


def tile_from_wire(token: str) -> Tile:
    """The tile named by one MJAI token."""
    tiles = parse_mjai(token)
    if len(tiles) != 1:
        raise ProtocolError(f"expected one tile, got {token!r}")
    return tiles[0]


def tiles_to_wire(tiles) -> list[str]:
    """A tile sequence as MJAI tokens."""
    return [tile_to_wire(tile) for tile in tiles]


def tiles_from_wire(tokens) -> tuple[Tile, ...]:
    """MJAI tokens back into tiles."""
    return tuple(tile_from_wire(token) for token in tokens)


# --- Decisions and actions --------------------------------------------------


def decision_kind_to_wire(kind: DecisionKind) -> str:
    """A decision kind as its lowercased name (e.g. ``"self"``)."""
    return kind.name.lower()


def decision_kind_from_wire(name: str) -> DecisionKind:
    """The decision kind named on the wire."""
    try:
        return DecisionKind[name.upper()]
    except KeyError:
        raise ProtocolError(f"unknown decision kind {name!r}") from None


def action_to_wire(action: act.Action) -> dict:
    """One legal action as a JSON object tagged by ``type``."""
    if isinstance(action, act.Discard):
        return {
            "type": "discard",
            "tile": tile_to_wire(action.tile),
            "tsumogiri": action.tsumogiri,
        }
    if isinstance(action, act.Riichi):
        return {
            "type": "riichi",
            "tile": tile_to_wire(action.tile),
            "tsumogiri": action.tsumogiri,
        }
    if isinstance(action, act.Tsumo):
        return {"type": "tsumo"}
    if isinstance(action, act.Ron):
        return {"type": "ron"}
    if isinstance(action, act.Chii):
        return {"type": "chii", "tiles": tiles_to_wire(action.tiles)}
    if isinstance(action, act.Pon):
        return {"type": "pon", "tiles": tiles_to_wire(action.tiles)}
    if isinstance(action, act.OpenKan):
        return {"type": "open_kan"}
    if isinstance(action, act.ClosedKan):
        return {"type": "closed_kan", "kind": tile_to_wire(Tile(action.kind))}
    if isinstance(action, act.AddedKan):
        return {"type": "added_kan", "tile": tile_to_wire(action.tile)}
    if isinstance(action, act.Nuki):
        return {"type": "nuki"}
    if isinstance(action, act.NineTerminals):
        return {"type": "nine_terminals"}
    if isinstance(action, act.Pass):
        return {"type": "pass"}
    if isinstance(action, act.DeclareTenpai):
        return {"type": "declare_tenpai", "declare": action.declare}
    raise ProtocolError(f"unencodable action {action!r}")


def action_from_wire(data: dict) -> act.Action:
    """The action described by a wire object."""
    tag = data.get("type")
    if tag == "discard":
        return act.Discard(tile_from_wire(data["tile"]), tsumogiri=data["tsumogiri"])
    if tag == "riichi":
        return act.Riichi(tile_from_wire(data["tile"]), tsumogiri=data["tsumogiri"])
    if tag == "tsumo":
        return act.Tsumo()
    if tag == "ron":
        return act.Ron()
    if tag == "chii":
        first, second = tiles_from_wire(data["tiles"])
        return act.Chii((first, second))
    if tag == "pon":
        first, second = tiles_from_wire(data["tiles"])
        return act.Pon((first, second))
    if tag == "open_kan":
        return act.OpenKan()
    if tag == "closed_kan":
        return act.ClosedKan(tile_from_wire(data["kind"]).kind)
    if tag == "added_kan":
        return act.AddedKan(tile_from_wire(data["tile"]))
    if tag == "nuki":
        return act.Nuki()
    if tag == "nine_terminals":
        return act.NineTerminals()
    if tag == "pass":
        return act.Pass()
    if tag == "declare_tenpai":
        return act.DeclareTenpai(declare=bool(data["declare"]))
    raise ProtocolError(f"unknown action type {tag!r}")


# --- Events ------------------------------------------------------------------


def _hand_to_wire(hand: Hand) -> dict:
    """A revealed hand: concealed tiles plus melds."""
    melds = [
        {"type": meld.type.name.lower(), "tiles": tiles_to_wire(meld.tiles)}
        for meld in hand.melds
    ]
    return {"concealed": tiles_to_wire(hand.concealed), "melds": melds}


def encode_event(event: ev.Event) -> dict:
    """One event (already masked for its recipient) as a JSON object."""
    if isinstance(event, ev.GameStart):
        return {
            "type": "game_start",
            "player_count": event.player_count,
            "names": list(event.names),
            "scores": list(event.starting_scores),
        }
    if isinstance(event, ev.DealStart):
        hands = [None if hand is None else tiles_to_wire(hand) for hand in event.hands]
        return {
            "type": "deal_start",
            "dealer": event.dealer,
            "round_wind": event.round_wind.name.lower(),
            "round_number": event.round_number,
            "honba": event.honba,
            "deposits": event.deposits,
            "scores": list(event.scores),
            "hands": hands,
            "dora_indicator": tile_to_wire(event.dora_indicator),
        }
    if isinstance(event, ev.Draw):
        tile = None if event.tile is None else tile_to_wire(event.tile)
        return {
            "type": "draw",
            "seat": event.seat,
            "tile": tile,
            "replacement": event.replacement,
        }
    if isinstance(event, ev.Discard):
        return {
            "type": "discard",
            "seat": event.seat,
            "tile": tile_to_wire(event.tile),
            "tsumogiri": event.tsumogiri,
            "riichi": event.riichi,
        }
    if isinstance(event, ev.Call):
        return {
            "type": "call",
            "meld_type": event.meld_type.name.lower(),
            "caller": event.caller,
            "source": event.source,
            "tiles": tiles_to_wire(event.tiles),
        }
    if isinstance(event, ev.IndicatorReveal):
        return {"type": "indicator_reveal", "tile": tile_to_wire(event.tile)}
    if isinstance(event, ev.NorthExtraction):
        return {
            "type": "north_extraction",
            "seat": event.seat,
            "tile": tile_to_wire(event.tile),
        }
    if isinstance(event, ev.RiichiAccepted):
        return {"type": "riichi_accepted", "seat": event.seat}
    if isinstance(event, ev.Win):
        result = event.result
        return {
            "type": "win",
            "seat": event.seat,
            "from_seat": event.from_seat,
            "winning_tile": tile_to_wire(event.winning_tile),
            "hand": _hand_to_wire(event.hand),
            "yaku": [
                {"name": value.yaku.name.lower(), "value": value.value}
                for value in result.yaku
            ],
            "is_yakuman": result.is_yakuman,
            "han": result.han,
            "fu": result.fu.total,
            "dora": {
                "dora": result.dora.dora,
                "ura": result.dora.ura,
                "aka": result.dora.aka,
                "nuki": result.dora.nuki,
            },
            "limit": result.limit.name.lower(),
            "points": result.payment.total,
            "ura_indicators": tiles_to_wire(event.ura_indicators),
        }
    if isinstance(event, ev.Ryuukyoku):
        return {
            "type": "ryuukyoku",
            "kind": event.kind.name.lower(),
            "revealed": [
                {"seat": seat, "hand": _hand_to_wire(hand)}
                for seat, hand in event.revealed
            ],
            "counted_ready": sorted(event.counted_ready),
        }
    if isinstance(event, ev.ScoreChange):
        return {
            "type": "score_change",
            "deltas": list(event.deltas),
            "scores": list(event.scores),
        }
    if isinstance(event, ev.GameEnd):
        return {
            "type": "game_end",
            "final_scores": list(event.final_scores),
            "ranking": list(event.ranking),
        }
    raise ProtocolError(f"unencodable event {event!r}")


def decode_event(data: dict) -> ev.Event | None:
    """The event described by a wire object, or ``None`` for outcome summaries.

    ``win`` and ``ryuukyoku`` carry display-only scoring detail that cannot be
    rebuilt into full jansou objects; agents that only track their own hand
    (all the built-in ones) never read them, so they decode to ``None``.
    """
    tag = data.get("type")
    if tag == "game_start":
        return ev.GameStart(
            data["player_count"], tuple(data["names"]), tuple(data["scores"])
        )
    if tag == "deal_start":
        hands = tuple(
            None if hand is None else tiles_from_wire(hand) for hand in data["hands"]
        )
        return ev.DealStart(
            dealer=data["dealer"],
            round_wind=Wind[data["round_wind"].upper()],
            round_number=data["round_number"],
            honba=data["honba"],
            deposits=data["deposits"],
            scores=tuple(data["scores"]),
            hands=hands,
            dora_indicator=tile_from_wire(data["dora_indicator"]),
        )
    if tag == "draw":
        tile = None if data["tile"] is None else tile_from_wire(data["tile"])
        return ev.Draw(data["seat"], tile, replacement=data["replacement"])
    if tag == "discard":
        return ev.Discard(
            data["seat"],
            tile_from_wire(data["tile"]),
            tsumogiri=data["tsumogiri"],
            riichi=data["riichi"],
        )
    if tag == "call":
        return ev.Call(
            meld_type=MeldType[data["meld_type"].upper()],
            caller=data["caller"],
            source=data["source"],
            tiles=tiles_from_wire(data["tiles"]),
        )
    if tag == "indicator_reveal":
        return ev.IndicatorReveal(tile_from_wire(data["tile"]))
    if tag == "north_extraction":
        return ev.NorthExtraction(data["seat"], tile_from_wire(data["tile"]))
    if tag == "riichi_accepted":
        return ev.RiichiAccepted(data["seat"])
    if tag == "score_change":
        return ev.ScoreChange(tuple(data["deltas"]), tuple(data["scores"]))
    if tag == "game_end":
        return ev.GameEnd(tuple(data["final_scores"]), tuple(data["ranking"]))
    if tag in ("win", "ryuukyoku"):
        return None
    raise ProtocolError(f"unknown event type {tag!r}")
