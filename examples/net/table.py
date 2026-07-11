"""A live table view rebuilt from wire events, and its terminal rendering.

``TableView`` consumes the wire encoding of events (the ``data`` of ``event``
messages) and maintains everything a spectator or player can know: seats'
hands (exact tiles when visible, a count when masked), melds, rivers, riichi
status, dora, scores, and the remaining wall. The renderer draws the whole
table as plain text with optional ANSI color, so the same module serves the
server's unmasked live display and the human client's own-seat view.
"""

from __future__ import annotations

import sys

from protocol import tile_from_wire

_RIICHI_DEPOSIT = 1000
_DEAD_WALL = 14
_HAND_SIZE = 13
_TILE_TOTAL = {3: 108, 4: 136}
_SEAT_WINDS = ("E", "S", "W", "N")
_ROUND_NAMES = {"east": "East", "south": "South", "west": "West", "north": "North"}

_SUIT_COLOR = {"m": "31", "p": "34", "s": "32"}
_HONOR_COLOR = "33"
_DIM = "2"
_INVERT = "7"


def _kind_of(token: str) -> str:
    """A token without its red-five marker, naming just the tile kind."""
    return token[:-1] if token.endswith("r") else token


def _sort_key(token: str):
    return tile_from_wire(token).sort_key


class SeatView:
    """One seat's visible state: hand or count, melds, river, flags."""

    def __init__(self, score: int, hand: list[str] | None) -> None:
        self.score = score
        self.hand = hand
        self.hidden_count = 0 if hand is not None else _HAND_SIZE
        self.drawn: str | None = None
        self.melds: list[tuple[str, list[str]]] = []
        self.river: list[tuple[str, bool, bool]] = []  # (token, riichi, tsumogiri)
        self.riichi = False
        self.nuki = 0

    def _pool(self) -> list[str]:
        pool = list(self.hand or [])
        if self.drawn is not None:
            pool.append(self.drawn)
        return pool

    def remove(self, tokens: list[str]) -> None:
        """Take exact tokens out of the hand (drawn tile included)."""
        if self.hand is None:
            self.hidden_count -= len(tokens)
        else:
            pool = self._pool()
            for token in tokens:
                pool.remove(token)
            self.hand = pool
        self.drawn = None

    def draw(self, token: str | None) -> None:
        if self.hand is None:
            self.hidden_count += 1
        else:
            self.drawn = token


class TableView:
    """The whole table, folded from the event stream."""

    def __init__(self) -> None:
        self.player_count = 0
        self.names: list[str] = []
        self.seats: list[SeatView] = []
        self.dealer = 0
        self.round_wind = "east"
        self.round_number = 1
        self.honba = 0
        self.sticks = 0
        self.dora: list[str] = []
        self.wall = 0
        self.turn: int | None = None
        self.last_discard: tuple[int, str] | None = None
        self.outcomes: list[dict] = []
        self.final: dict | None = None

    def apply(self, data: dict) -> None:
        """Fold one wire event into the view."""
        handler = getattr(self, f"_on_{data['type']}", None)
        if handler is not None:
            handler(data)

    def _on_game_start(self, data: dict) -> None:
        self.player_count = data["player_count"]
        self.names = list(data["names"])
        self.seats = [SeatView(score, None) for score in data["scores"]]
        self.final = None

    def _on_deal_start(self, data: dict) -> None:
        self.seats = [
            SeatView(score, hand) for score, hand in zip(data["scores"], data["hands"])
        ]
        self.dealer = data["dealer"]
        self.round_wind = data["round_wind"]
        self.round_number = data["round_number"]
        self.honba = data["honba"]
        self.sticks = data["deposits"] // _RIICHI_DEPOSIT
        self.dora = [data["dora_indicator"]]
        self.wall = (
            _TILE_TOTAL[self.player_count] - _HAND_SIZE * self.player_count - _DEAD_WALL
        )
        self.turn = None
        self.last_discard = None
        self.outcomes = []

    def _on_draw(self, data: dict) -> None:
        self.wall -= 1
        self.turn = data["seat"]
        self.seats[data["seat"]].draw(data["tile"])

    def _on_discard(self, data: dict) -> None:
        seat = self.seats[data["seat"]]
        seat.remove([data["tile"]])
        seat.river.append((data["tile"], data["riichi"], data["tsumogiri"]))
        self.last_discard = (data["seat"], data["tile"])

    def _on_call(self, data: dict) -> None:
        caller = self.seats[data["caller"]]
        tiles = list(data["tiles"])
        if data["source"] == data["caller"]:
            if data["meld_type"] == "shouminkan":
                self._upgrade_pon(caller, tiles)
            else:  # ankan: all four tiles leave the hand
                caller.remove(tiles)
                caller.melds.append((data["meld_type"], tiles))
        else:
            claimed = self.seats[data["source"]].river.pop()[0]
            contributed = list(tiles)
            contributed.remove(claimed)
            caller.remove(contributed)
            caller.melds.append((data["meld_type"], tiles))
        self.turn = data["caller"]

    def _upgrade_pon(self, seat: SeatView, tiles: list[str]) -> None:
        """Turn the matching pon into the kan, removing the added tile from hand."""
        kind = _kind_of(tiles[0])
        index = next(
            i
            for i, (kind_, meld) in enumerate(seat.melds)
            if kind_ == "pon" and _kind_of(meld[0]) == kind
        )
        added = list(tiles)
        for token in seat.melds[index][1]:
            added.remove(token)
        seat.remove(added)
        seat.melds[index] = ("shouminkan", tiles)

    def _on_indicator_reveal(self, data: dict) -> None:
        self.dora.append(data["tile"])

    def _on_north_extraction(self, data: dict) -> None:
        seat = self.seats[data["seat"]]
        seat.remove([data["tile"]])
        seat.nuki += 1

    def _on_riichi_accepted(self, data: dict) -> None:
        seat = self.seats[data["seat"]]
        seat.riichi = True
        seat.score -= _RIICHI_DEPOSIT
        self.sticks += 1

    def _on_score_change(self, data: dict) -> None:
        for seat, score in zip(self.seats, data["scores"]):
            seat.score = score

    def _on_win(self, data: dict) -> None:
        self.outcomes.append(data)

    def _on_ryuukyoku(self, data: dict) -> None:
        self.outcomes.append(data)

    def _on_game_end(self, data: dict) -> None:
        self.final = data

    def seat_wind(self, seat: int) -> str:
        return _SEAT_WINDS[(seat - self.dealer) % self.player_count]


# --- Rendering ---------------------------------------------------------------


def color_enabled() -> bool:
    """Whether stdout is a terminal that can take ANSI colors."""
    return sys.stdout.isatty()


def _paint(token: str, *, color: bool, extra: str = "") -> str:
    if not color:
        return token
    kind = _kind_of(token)
    code = _SUIT_COLOR.get(kind[-1], _HONOR_COLOR)
    codes = f"{extra};{code}" if extra else code
    return f"\x1b[{codes}m{token}\x1b[0m"


def _tiles(tokens: list[str], *, color: bool) -> str:
    return " ".join(_paint(token, color=color) for token in tokens)


def _river(entries: list[tuple[str, bool, bool]], *, color: bool) -> str:
    parts = []
    for token, riichi, tsumogiri in entries:
        extra = _INVERT if riichi else (_DIM if tsumogiri else "")
        text = _paint(token, color=color, extra=extra)
        parts.append(f"{text}!" if riichi and not color else text)
    return " ".join(parts)


def render(
    view: TableView, *, viewpoint: int | None = None, color: bool = False
) -> str:
    """The whole table as text, one block per seat.

    Args:
        view: The table to draw.
        viewpoint: The seat marked as ``you``, or ``None`` for a spectator.
        color: Whether to use ANSI colors (riichi discards inverted,
            tsumogiri dimmed, suits tinted).
    """
    round_name = _ROUND_NAMES.get(view.round_wind, view.round_wind)
    header = (
        f"{round_name} {view.round_number} · honba {view.honba} · sticks {view.sticks}"
        f" · dora {_tiles(view.dora, color=color)} · wall {view.wall}"
    )
    lines = [header, ""]
    for index, seat in enumerate(view.seats):
        marker = "▶" if view.turn == index else " "
        name = view.names[index] if index < len(view.names) else f"seat {index}"
        you = " (you)" if viewpoint == index else ""
        flags = " riichi" if seat.riichi else ""
        lines.append(
            f"{marker}{view.seat_wind(index)}  {name:<16} {seat.score:>7}{flags}{you}"
        )
        if seat.hand is None:
            hand = f"[{seat.hidden_count} hidden]"
        else:
            hand = _tiles(sorted(seat.hand, key=_sort_key), color=color)
            if seat.drawn is not None:
                hand += f"  [{_paint(seat.drawn, color=color)}]"
        lines.append(f"     hand:  {hand}")
        extras = [
            f"{kind} {_tiles(tokens, color=color)}" for kind, tokens in seat.melds
        ]
        if seat.nuki:
            extras.append(f"nuki ×{seat.nuki}")
        if extras:
            lines.append(f"     melds: {' · '.join(extras)}")
        lines.append(f"     river: {_river(seat.river, color=color)}")
        lines.append("")
    for outcome in view.outcomes:
        lines.append(describe_outcome(outcome, view.names, color=color))
        lines.append("")
    return "\n".join(lines)


def describe_outcome(data: dict, names: list[str], *, color: bool = False) -> str:
    """A win or ryuukyoku as a short human-readable block."""
    if data["type"] == "ryuukyoku":
        kind = data["kind"].replace("_", " ")
        ready = ", ".join(names[seat] for seat in data["counted_ready"]) or "nobody"
        return f"— draw ({kind}); tenpai: {ready}"
    winner = names[data["seat"]]
    source = (
        "tsumo" if data["from_seat"] is None else f"ron off {names[data['from_seat']]}"
    )
    yaku = ", ".join(f"{value['name']} {value['value']}" for value in data["yaku"])
    dora = data["dora"]
    bonuses = [f"{label} {count}" for label, count in dora.items() if count]
    value = "yakuman" if data["is_yakuman"] else f"{data['han']} han {data['fu']} fu"
    if data["limit"] != "none":
        value += f" ({data['limit'].replace('_', ' ')})"
    hand = data["hand"]
    tiles = _tiles(sorted(hand["concealed"], key=_sort_key), color=color)
    melds = "".join(
        f"  +{_tiles(meld['tiles'], color=color)}" for meld in hand["melds"]
    )
    lines = [
        f"— {winner} wins {data['points']} by {source} on {_paint(data['winning_tile'], color=color)}",
        f"  {tiles}{melds}",
        f"  {value}: {yaku}" + (f" · {', '.join(bonuses)}" if bonuses else ""),
    ]
    if data["ura_indicators"]:
        lines.append(f"  ura: {_tiles(data['ura_indicators'], color=color)}")
    return "\n".join(lines)


def menu_order(actions: list[dict]) -> list[int]:
    """Display order for an action menu, as indices into ``actions``.

    Discard and riichi entries keep their group's positions in the menu but
    are sorted by tile within them, with the tsumogiri candidate last (its
    customary place); everything else stays where the server put it.
    """
    order = list(range(len(actions)))
    for tag in ("riichi", "discard"):
        positions = [
            index for index, action in enumerate(actions) if action["type"] == tag
        ]
        by_tile = sorted(
            positions,
            key=lambda index: (
                actions[index]["tsumogiri"],
                _sort_key(actions[index]["tile"]),
            ),
        )
        for position, index in zip(positions, by_tile):
            order[position] = index
    return order


def describe_action(data: dict, *, color: bool = False) -> str:
    """One offered action as a short human-readable label."""
    tag = data["type"]
    if tag == "discard":
        drawn = " (tsumogiri)" if data["tsumogiri"] else ""
        return f"discard {_paint(data['tile'], color=color)}{drawn}"
    if tag == "riichi":
        drawn = " (tsumogiri)" if data["tsumogiri"] else ""
        return f"riichi, discarding {_paint(data['tile'], color=color)}{drawn}"
    if tag == "tsumo":
        return "tsumo (win)"
    if tag == "ron":
        return "ron (win)"
    if tag in ("chii", "pon"):
        return f"{tag} with {_tiles(data['tiles'], color=color)}"
    if tag == "open_kan":
        return "open kan"
    if tag == "closed_kan":
        return f"closed kan of {_paint(data['kind'], color=color)}"
    if tag == "added_kan":
        return f"added kan {_paint(data['tile'], color=color)}"
    if tag == "nuki":
        return "extract north"
    if tag == "nine_terminals":
        return "abort: nine terminals"
    if tag == "pass":
        return "pass"
    if tag == "declare_tenpai":
        return "declare tenpai" if data["declare"] else "declare noten"
    return tag
