"""Reading Tenhou's XML paifu (mjlog) into the neutral `Paifu`.

The document is a flat, order-significant list of leaf elements. Game mode is
read from `<GO>`, each round opens with `<INIT>`, draws and discards are
single-letter-prefixed tags, calls pack their meld into the `<N>` `m` integer,
and each round closes with `<AGARI>` or `<RYUUKYOKU>`. A win carries the
winner's hand outright, so it is scored from the log rather than the replay.
"""

from __future__ import annotations

import gzip
from dataclasses import replace
from pathlib import Path
from xml.etree import ElementTree as ET

from jansou.core.hand import CallSource, Hand, Meld, MeldType
from jansou.core.rules import preset
from jansou.core.tiles import TileKind, Wind
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
    replay_round,
)
from jansou.io.tiles import tile_from_136, tile_to_136

_DRAW_PREFIXES = "TUVW"
_DISCARD_PREFIXES = "DEFG"
_SANMA_BIT = 0x10
_NO_AKA_BIT = 0x02
_HUNDREDS = 100
_MIN_TAG_LEN = 2
# The <N> `m` offset counts seats downstream from the caller to the tile's
# source, so offset 1 is the shimocha (next to play) and offset 3 the kamicha.
_SOURCE_OF_OFFSET = {1: CallSource.SHIMOCHA, 2: CallSource.TOIMEN, 3: CallSource.KAMICHA}

#: A draw abort type carries no scorable win and no tenpai payments to model.
_ABORT_TYPES = frozenset({"yao9", "reach4", "ron3", "kaze4", "kan4"})


class MjlogError(ValueError):
    """A malformed or unsupported mjlog document. Subclasses ``ValueError``."""


def parse_mjlog(source: str | Path | bytes) -> Paifu:
    """Parse an mjlog document into a neutral game record.

    Args:
        source: The mjlog XML. A ``str`` or ``Path`` is read as a file path
            (gzipped or plain); ``bytes`` are the document itself, decompressed
            when gzip-framed.

    Returns:
        The parsed game.

    Raises:
        MjlogError: If the document has no ``<GO>`` game-mode element.
    """
    root = ET.fromstring(_read(source))  # noqa: S314 - local trusted paifu files, not network input
    go = root.find("GO")
    if go is None:
        raise MjlogError("mjlog has no <GO> game-mode element")
    game_type = int(go.get("type", "0"))
    sanma = bool(game_type & _SANMA_BIT)
    player_count = 3 if sanma else 4
    rules = preset("tenhou-3p") if sanma else preset("tenhou")
    if game_type & _NO_AKA_BIT:
        rules = replace(rules, aka_dora=False)
    rounds = _parse_rounds(root, player_count)
    final_scores, final_points = _parse_standings(root)
    return Paifu(
        rules=rules,
        player_count=player_count,
        rounds=tuple(rounds),
        final_scores=final_scores,
        final_points=final_points,
    )


def _parse_standings(root: ET.Element) -> tuple[tuple[int, ...] | None, tuple[float, ...] | None]:
    """The final standing from the closing element's ``owari`` attribute.

    The attribute alternates each seat's score in hundreds with the
    platform's adjusted result; a truncated log carries none.
    """
    owari = next((element.get("owari") for element in reversed(list(root)) if element.get("owari")), None)
    if owari is None:
        return None, None
    values = owari.split(",")
    return (
        tuple(int(value) * _HUNDREDS for value in values[0::2]),
        tuple(float(value) for value in values[1::2]),
    )


def _read(source: str | Path | bytes) -> bytes:
    """The document bytes, decompressing when the source is gzip."""
    data = source if isinstance(source, bytes) else Path(source).read_bytes()
    if data[:2] == b"\x1f\x8b":
        return gzip.decompress(data)
    return data


def _parse_rounds(root: ET.Element, player_count: int) -> list[RoundLog]:
    """Every round in the document, split on the opening <INIT> elements."""
    rounds: list[RoundLog] = []
    pending: list[ET.Element] = []
    for element in root:
        if element.tag == "INIT":
            pending = [element]
        elif pending:
            pending.append(element)
            if element.tag in ("AGARI", "RYUUKYOKU") and not _is_multi_ron_continuation(root, element):
                rounds.append(_build_round(pending, player_count))
                pending = []
    return rounds


def _is_multi_ron_continuation(root: ET.Element, element: ET.Element) -> bool:
    """Whether a following <AGARI> shares this round (a multiple ron)."""
    children = list(root)
    index = children.index(element)
    return element.tag == "AGARI" and index + 1 < len(children) and children[index + 1].tag == "AGARI"


def _build_round(elements: list[ET.Element], player_count: int) -> RoundLog:
    """Assemble one round from its <INIT> and the events up to its outcome."""
    init = elements[0]
    seed = [int(value) for value in init.get("seed", "").split(",")]
    round_id, honba, riichi_sticks = seed[0], seed[1], seed[2]
    scores = tuple(int(value) * _HUNDREDS for value in init.get("ten", "").split(","))
    hands = tuple(
        tuple(tile_from_136(int(index)) for index in (init.get(f"hai{seat}") or "").split(",") if index)
        for seat in range(player_count)
    )
    events, agari, ryuukyoku = _parse_body(elements[1:], player_count)
    outcome = tuple(agari) if agari else (ryuukyoku or Ryuukyoku())
    return RoundLog(
        round_wind=Wind(round_id // 4),
        dealer=round_id % 4,
        honba=honba,
        riichi_sticks=riichi_sticks,
        initial_dora=tile_from_136(seed[5]),
        scores=scores,
        hands=hands,
        events=tuple(events),
        outcome=outcome,
    )


def _parse_body(elements: list[ET.Element], player_count: int) -> tuple[list[Event], list[Agari], Ryuukyoku | None]:
    """The events and outcome of one round's body (everything after <INIT>)."""
    events: list[Event] = []
    agari: list[Agari] = []
    ryuukyoku: Ryuukyoku | None = None
    riichi_seat: int | None = None
    last_draw: dict[int, int] = {}  # each seat's pending draw index; tsumogiri is index identity
    for element in elements:
        tag = element.tag
        seat = _seat_of_tile_tag(tag)
        if seat is not None and tag[0] in _DRAW_PREFIXES:
            index = int(tag[1:])
            last_draw[seat] = index
            events.append(Draw(seat, tile_from_136(index)))
        elif seat is not None:
            index = int(tag[1:])
            declare = riichi_seat == seat
            riichi_seat = None
            tsumogiri = last_draw.pop(seat, None) == index
            events.append(Discard(seat, tile_from_136(index), riichi=declare, tsumogiri=tsumogiri))
        elif tag == "N":
            events.append(_call_event(element))
        elif tag == "REACH" and element.get("step") == "1":
            riichi_seat = int(element.get("who", "0"))
        elif tag == "DORA":
            events.append(DoraReveal(tile_from_136(int(element.get("hai", "0")))))
        elif tag == "AGARI":
            agari.append(_agari(element, player_count))
        elif tag == "RYUUKYOKU":
            ryuukyoku = _ryuukyoku(element, player_count)
    return events, agari, ryuukyoku


def _seat_of_tile_tag(tag: str) -> int | None:
    """The seat a draw or discard tag names, or None if it is not one."""
    if len(tag) >= _MIN_TAG_LEN and tag[0] in _DRAW_PREFIXES + _DISCARD_PREFIXES and tag[1:].isdigit():
        prefixes = _DRAW_PREFIXES if tag[0] in _DRAW_PREFIXES else _DISCARD_PREFIXES
        return prefixes.index(tag[0])
    return None


def _call_event(element: ET.Element) -> Event:
    """The meld or kita a <N> element encodes."""
    who = int(element.get("who", "0"))
    meld = _decode_meld(int(element.get("m", "0")))
    if meld is None:
        return Kita(who)
    return Call(who, meld)


def _deltas(element: ET.Element, player_count: int) -> tuple[int, ...]:
    """Per-seat score changes from an `sc` attribute (score, delta pairs)."""
    values = [int(value) for value in element.get("sc", "").split(",") if value != ""]
    return tuple(values[seat * 2 + 1] * _HUNDREDS for seat in range(player_count))


def _agari(element: ET.Element, player_count: int) -> Agari:
    """One <AGARI> win, carrying its explicit hand and expected score."""
    who = int(element.get("who", "0"))
    ten = [int(value) for value in element.get("ten", "").split(",")]
    ba = [int(value) for value in element.get("ba", "0,0").split(",")]
    concealed = tuple(tile_from_136(int(index)) for index in element.get("hai", "").split(",") if index)
    melds = tuple(
        meld for value in element.get("m", "").split(",") if value and (meld := _decode_meld(int(value))) is not None
    )
    ura = element.get("doraHaiUra")
    pao = element.get("paoWho")
    return Agari(
        winner=who,
        from_seat=int(element.get("fromWho", str(who))),
        winning_tile=tile_from_136(int(element.get("machi", "0"))),
        ura_indicators=tuple(tile_from_136(int(i)) for i in ura.split(",")) if ura else (),
        honba=ba[0],
        riichi_sticks=ba[1],
        deltas=_deltas(element, player_count),
        fu=ten[0],
        value=ten[1],
        hand=Hand(concealed, melds),
        liable_seat=int(pao) if pao is not None else None,
    )


def _ryuukyoku(element: ET.Element, player_count: int) -> Ryuukyoku:
    """One <RYUUKYOKU> draw, exhaustive or abortive."""
    kind = element.get("type", "exhaustive")
    tenpai = tuple(element.get(f"hai{seat}") is not None for seat in range(player_count))
    return Ryuukyoku(
        kind="exhaustive" if kind not in _ABORT_TYPES and kind != "nm" else kind,
        deltas=_deltas(element, player_count),
        tenpai=tenpai,
    )


# --- Meld integer decoding --------------------------------------------------


def _decode_meld(code: int) -> Meld | None:
    """The meld a Tenhou `m` integer encodes, or None for a kita (North set aside)."""
    offset = code & 0x3
    if code & 0x4:
        return _decode_chi(code)
    if code & 0x8:
        return _decode_pon(code, offset)
    if code & 0x10:
        return _decode_shouminkan(code, offset)
    if code & 0x20:
        return None
    return _decode_kan(code, offset)


def _decode_chi(code: int) -> Meld:
    """A sequence claimed from the left player."""
    copies = [(code >> 3) & 0x3, (code >> 5) & 0x3, (code >> 7) & 0x3]
    base = (code >> 10) & 0x3F
    called_position = base % 3
    base //= 3
    suit, rank = divmod(base, 7)
    low = suit * 9 + rank
    ids = [(low + offset) * 4 + copies[offset] for offset in range(3)]
    tiles = tuple(tile_from_136(index) for index in ids)
    return Meld(MeldType.CHII, tiles, called=tiles[called_position], source=CallSource.KAMICHA)


def _decode_pon(code: int, offset: int) -> Meld:
    """A triplet claimed from an opponent."""
    unused = (code >> 5) & 0x3
    base = (code >> 9) & 0x7F
    called_position = base % 3
    kind = base // 3
    present = [copy for copy in range(4) if copy != unused]
    tiles = tuple(tile_from_136(kind * 4 + copy) for copy in present)
    return Meld(MeldType.PON, tiles, called=tiles[called_position], source=_SOURCE_OF_OFFSET[offset])


def _decode_shouminkan(code: int, offset: int) -> Meld:
    """A pon promoted to a quad by its fourth copy."""
    added_copy = (code >> 5) & 0x3
    base = (code >> 9) & 0x7F
    kind = base // 3
    tiles = tuple(tile_from_136(kind * 4 + copy) for copy in range(4))
    added = tile_from_136(kind * 4 + added_copy)
    return Meld(MeldType.SHOUMINKAN, tiles, called=added, source=_SOURCE_OF_OFFSET[offset], added=added)


def _decode_kan(code: int, offset: int) -> Meld:
    """A closed kan (from self) or an open kan (claimed from an opponent)."""
    any_copy = (code >> 8) & 0xFF
    kind = any_copy // 4
    tiles = tuple(tile_from_136(kind * 4 + copy) for copy in range(4))
    if offset == 0:
        return Meld(MeldType.ANKAN, tiles)
    called = tile_from_136(any_copy)
    return Meld(MeldType.DAIMINKAN, tiles, called=called, source=_SOURCE_OF_OFFSET[offset])


# --- Writing ----------------------------------------------------------------

_DRAW_TAGS = "TUVW"
_DISCARD_TAGS = "DEFG"
_KITA_CODE = 0x20
_FIVE_KINDS = frozenset({TileKind.M5, TileKind.P5, TileKind.S5})
_OFFSET_OF_SOURCE = {source: offset for offset, source in _SOURCE_OF_OFFSET.items()}


def dump_mjlog(paifu: Paifu) -> str:
    """Serialize a game to an mjlog XML document that ``parse_mjlog`` reads back.

    Args:
        paifu: The game to serialize.

    Returns:
        An mjlog XML document as a string.
    """
    go_type = 0x1
    if paifu.player_count == 3:
        go_type |= _SANMA_BIT
    if not paifu.rules.aka_dora:
        go_type |= _NO_AKA_BIT
    owari = _standings_attribute(paifu)
    final = len(paifu.rounds) - 1
    rounds = "".join(
        _dump_round(round_log, paifu.rules, paifu.player_count, owari if index == final else None)
        for index, round_log in enumerate(paifu.rounds)
    )
    return f'<mjloggm ver="2.3"><GO type="{go_type}"/>{rounds}</mjloggm>'


def _standings_attribute(paifu: Paifu) -> str | None:
    """The ``owari`` value for the game's standing, or None when it has none."""
    if paifu.final_scores is None or paifu.final_points is None:
        return None
    pairs = zip(paifu.final_scores, paifu.final_points, strict=True)
    return ",".join(f"{score // _HUNDREDS},{points:.1f}" for score, points in pairs)


def _dump_round(round_log: RoundLog, rules: object, player_count: int, owari: str | None) -> str:
    """One round's <INIT>, event tags, and closing <AGARI>/<RYUUKYOKU>."""
    round_id = round_log.round_wind * 4 + round_log.dealer
    seed = f"{round_id},{round_log.honba},{round_log.riichi_sticks},0,0,{tile_to_136(round_log.initial_dora)}"
    ten = ",".join(str(score // _HUNDREDS) for score in round_log.scores)
    hands = "".join(
        f' hai{seat}="{",".join(str(tile_to_136(tile)) for tile in round_log.hands[seat])}"'
        for seat in range(player_count)
    )
    init = f'<INIT seed="{seed}" ten="{ten}" oya="{round_log.dealer}"{hands}/>'
    last_draw: dict[int, int] = {}
    body = "".join(_dump_event(event, last_draw) for event in round_log.events)
    return f"{init}{body}{_dump_outcome(round_log, rules, player_count, owari)}"


def _dump_event(event: Event, last_draw: dict[int, int]) -> str:
    """The mjlog tag(s) for one normalized event, threading each seat's last draw index."""
    if isinstance(event, Draw):
        index = tile_to_136(event.tile)
        last_draw[event.seat] = index
        return f"<{_DRAW_TAGS[event.seat]}{index}/>"
    if isinstance(event, Discard):
        tag = f"<{_DISCARD_TAGS[event.seat]}{_discard_136(event, last_draw.pop(event.seat, None))}/>"
        if event.riichi:
            return f'<REACH who="{event.seat}" step="1"/>{tag}'
        return tag
    if isinstance(event, Call):
        return f'<N who="{event.seat}" m="{_encode_meld(event.meld)}"/>'
    if isinstance(event, Kita):
        return f'<N who="{event.seat}" m="{_KITA_CODE}"/>'
    return f'<DORA hai="{tile_to_136(event.indicator)}"/>'


def _discard_136(event: Discard, drawn_index: int | None) -> int:
    """The 136-index carrying the tsumogiri mark, which mjlog conveys physically.

    A tsumogiri reuses the draw's own index; a tedashi of a tile identical to
    the draw takes the next copy, so a reader comparing indices reads it back
    as from the hand.
    """
    if event.tsumogiri and drawn_index is not None:
        return drawn_index
    index = tile_to_136(event.tile)
    if index == drawn_index:
        return index + 1
    return index


def _dump_outcome(round_log: RoundLog, rules: object, player_count: int, owari: str | None) -> str:
    """A round's closing element: one <AGARI> per win, or a <RYUUKYOKU>."""
    if isinstance(round_log.outcome, Ryuukyoku):
        return _dump_ryuukyoku(round_log.outcome, player_count, owari)
    records = replay_round(round_log, rules, player_count)  # type: ignore[arg-type]
    last = len(round_log.outcome) - 1
    return "".join(
        _dump_agari(agari, record, player_count, owari if index == last else None)
        for index, (agari, record) in enumerate(zip(round_log.outcome, records, strict=True))
    )


def _dump_agari(agari: Agari, record: object, player_count: int, owari: str | None) -> str:
    """One <AGARI>, carrying the winning hand, wait, expected value, and deltas."""
    hand: Hand = record.hand  # type: ignore[attr-defined]
    hai = ",".join(str(tile_to_136(tile)) for tile in hand.concealed)
    melds = ",".join(str(_encode_meld(meld)) for meld in hand.melds)
    ten = f"{agari.fu or 0},{agari.value or 0},0"
    sc = ",".join(f"0,{agari.deltas[seat] // _HUNDREDS}" for seat in range(player_count))
    attrs = (
        f'who="{agari.winner}" fromWho="{agari.from_seat}" machi="{tile_to_136(agari.winning_tile)}"'
        f' ten="{ten}" hai="{hai}" ba="{agari.honba},{agari.riichi_sticks}" sc="{sc}"'
    )
    if agari.liable_seat is not None:
        attrs += f' paoWho="{agari.liable_seat}"'
    if melds:
        attrs += f' m="{melds}"'
    if agari.ura_indicators:
        attrs += f' doraHaiUra="{",".join(str(tile_to_136(tile)) for tile in agari.ura_indicators)}"'
    if owari is not None:
        attrs += f' owari="{owari}"'
    return f"<AGARI {attrs}/>"


def _dump_ryuukyoku(ryuukyoku: Ryuukyoku, player_count: int, owari: str | None) -> str:
    """One <RYUUKYOKU>, marking any counted-tenpai hands and the payments."""
    tenpai = "".join(
        f' hai{seat}=""' for seat in range(player_count) if seat < len(ryuukyoku.tenpai) and ryuukyoku.tenpai[seat]
    )
    deltas = ryuukyoku.deltas or (0,) * player_count
    sc = ",".join(f"0,{deltas[seat] // _HUNDREDS}" for seat in range(player_count))
    kind = "" if ryuukyoku.kind == "exhaustive" else f' type="{ryuukyoku.kind}"'
    ending = "" if owari is None else f' owari="{owari}"'
    return f'<RYUUKYOKU{kind} ba="0,0" sc="{sc}"{tenpai}{ending}/>'


def _encode_meld(meld: Meld) -> int:
    """The Tenhou `m` integer that `_decode_meld` reads back to this meld."""
    if meld.type is MeldType.CHII:
        return _encode_chii(meld)
    if meld.type is MeldType.PON:
        return _encode_pon(meld)
    if meld.type is MeldType.SHOUMINKAN:
        return _encode_shouminkan(meld)
    return _encode_kan(meld)


def _copy_index(tile: object, *, red_slot: bool) -> int:
    """A 136-copy index for a tile: the red slot 0, or a plain slot for a five."""
    if red_slot:
        return 0
    return 1 if tile.kind in _FIVE_KINDS else 0  # type: ignore[attr-defined]


def _encode_chii(meld: Meld) -> int:
    """Pack a sequence into its `m` integer (offset bits unused: always kamicha)."""
    tiles = sorted(meld.tiles, key=lambda tile: tile.kind)
    low = tiles[0].kind
    suit, rank = divmod(low, 9)
    called_position = tiles.index(meld.called)  # type: ignore[arg-type]
    base = (suit * 7 + rank) * 3 + called_position
    code = 0x4 | (base << 10)
    for offset, tile in enumerate(tiles):
        code |= _copy_index(tile, red_slot=tile.red) << (3 + 2 * offset)
    return code


def _encode_pon(meld: Meld) -> int:
    """Pack a triplet into its `m` integer, keeping any red five in the set."""
    kind = meld.tiles[0].kind
    has_red = any(tile.red for tile in meld.tiles)
    unused = 0 if kind in _FIVE_KINDS and not has_red else 3
    present = [copy for copy in range(4) if copy != unused]
    base = kind * 3 + _called_position(present, kind, want_red=meld.called.red)  # type: ignore[union-attr]
    return 0x8 | _OFFSET_OF_SOURCE[meld.source] | (unused << 5) | (base << 9)


def _called_position(present: list[int], kind: object, *, want_red: bool) -> int:
    """The index among the present copies whose red-ness matches the claimed tile.

    Copy 0 of a five is the red slot; picking the position whose red-ness matches
    keeps the right tiles in hand when the meld's claimed copy is removed. A
    triplet always has a copy of each red-ness a claimed tile can carry.
    """
    return next(position for position, copy in enumerate(present) if (kind in _FIVE_KINDS and copy == 0) == want_red)


def _encode_shouminkan(meld: Meld) -> int:
    """Pack an added kan into its `m` integer, marking the added copy."""
    kind = meld.tiles[0].kind
    added_copy = _copy_index(meld.added, red_slot=meld.added.red)  # type: ignore[union-attr]
    base = kind * 3
    return 0x10 | _OFFSET_OF_SOURCE[meld.source] | (added_copy << 5) | (base << 9)


def _encode_kan(meld: Meld) -> int:
    """Pack a closed or open quad into its `m` integer over all four copies."""
    kind = meld.tiles[0].kind
    if meld.type is MeldType.ANKAN:
        return ((kind * 4) << 8) | 0
    # An open kan names the claimed copy, so its red-ness leaves the right hand.
    any_copy = kind * 4 + _copy_index(meld.called, red_slot=meld.called.red)  # type: ignore[union-attr]
    return (any_copy << 8) | _OFFSET_OF_SOURCE[meld.source]
