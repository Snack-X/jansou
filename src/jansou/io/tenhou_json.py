"""Reading Tenhou's JSON paifu into the neutral `Paifu`.

Each round is a flat array: opening info, indicators, then per player an
initial hand, a draw list, and a discard list read in lockstep, and finally a
result. Turn order is not stored -- it is rebuilt by walking seat to seat,
following a call (which appears as a meld string in the caller's draw list) out
of the normal rotation. A win carries a Japanese score string, from which the
value and fu are read.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from pathlib import Path

from jansou.core.hand import CallSource, Meld, MeldType
from jansou.core.rules import Rules, preset
from jansou.core.tiles import Tile, Wind
from jansou.io.paifu import (
    Agari,
    Call,
    Discard,
    DoraReveal,
    Draw,
    Event,
    Paifu,
    RoundLog,
    Ryuukyoku,
)
from jansou.io.tiles import tile_from_tenhou, tile_to_tenhou

_TSUMOGIRI = 60
_CALLED_AWAY = 0
#: Honba a single non-winner pays: two shares in sanma, three in yonma.
_HONBA_SHARE = 100
_VIEWER_PREFIX = "https://tenhou.net/6/#json="
_VIEWER_SUFFIX = "&ts=0"
_INFO, _SCORES, _DORA, _URA = 0, 1, 2, 3
_PER_PLAYER = 3
_AGARI_TAG = "和了"
_CALL_LETTERS = "cpm"
_CHII_LETTER = "c"
_KAN_LETTERS = "ak"
_SOURCE_BY_INDEX = {0: CallSource.KAMICHA, 1: CallSource.TOIMEN, 2: CallSource.SHIMOCHA}
_MELD_TYPE = {
    "c": MeldType.CHII,
    "p": MeldType.PON,
    "m": MeldType.DAIMINKAN,
    "a": MeldType.ANKAN,
    "k": MeldType.SHOUMINKAN,
}

#: Ryuukyoku result tags that carry no scorable win.
_DRAW_TAGS = frozenset(
    {"流局", "全員聴牌", "全員不聴", "流し満貫", "九種九牌", "三家和", "三家和了", "四風連打", "四開槓", "四家立直"}
)


class TenhouJsonError(ValueError):
    """A malformed Tenhou JSON document. Subclasses ``ValueError``."""


def parse_tenhou_json(source: str | Path | bytes | dict) -> Paifu:
    """Parse a Tenhou JSON game into a neutral game record.

    Args:
        source: The Tenhou JSON. A ``str`` may be a viewer URL (the object is
            read from its ``#json=`` fragment), JSON text, or a file path;
            ``bytes`` are decoded as JSON text; a ``dict`` is the parsed object.

    Returns:
        The parsed game.

    Raises:
        TenhouJsonError: If a round names a win with no preceding draw or discard.
    """
    data = _load(source)
    player_count = len(data["name"])
    rules = preset("tenhou-3p") if player_count == 3 else preset("tenhou")
    rounds = [_build_round(entry, rules, player_count) for entry in data["log"]]
    return Paifu(rules=rules, player_count=player_count, rounds=tuple(rounds))


def _load(source: str | Path | bytes | dict) -> dict:
    """The top-level object, unwrapping a viewer URL when given one."""
    if isinstance(source, dict):
        return source
    text = source.decode() if isinstance(source, bytes) else str(source)
    if "#json=" in text:
        text = urllib.parse.unquote(text.split("#json=", 1)[1].split("&", 1)[0])
    elif not text.lstrip().startswith("{"):
        text = Path(source).read_text()  # type: ignore[arg-type]
    return json.loads(text)


def _build_round(entry: list, rules: Rules, player_count: int) -> RoundLog:
    """One round from its heterogeneous array."""
    round_id, honba, riichi_sticks = entry[_INFO]
    dora = [tile_from_tenhou(code) for code in entry[_DORA]]
    ura = [tile_from_tenhou(code) for code in entry[_URA]]
    hands = tuple(
        tuple(tile_from_tenhou(code) for code in entry[4 + _PER_PLAYER * seat]) for seat in range(player_count)
    )
    draws = [entry[4 + _PER_PLAYER * seat + 1] for seat in range(player_count)]
    discards = [entry[4 + _PER_PLAYER * seat + 2] for seat in range(player_count)]
    events, last_discard, last_drawn, robbed = _replay_turns(draws, discards, round_id % 4, player_count)
    won_on = robbed if robbed is not None else last_discard
    outcome = _outcome(entry[-1], ura, honba, riichi_sticks, rules, player_count, won_on, last_drawn)
    return RoundLog(
        round_wind=Wind(round_id // 4),
        dealer=round_id % 4,
        honba=honba,
        riichi_sticks=riichi_sticks,
        initial_dora=dora[0],
        scores=tuple(entry[_SCORES]),
        hands=hands,
        events=tuple(events) + tuple(DoraReveal(indicator) for indicator in dora[1:]),
        outcome=outcome,
    )


_Turns = tuple[list[Event], Tile | None, list[Tile | None], Tile | None]


def _replay_turns(draws: list[list], discards: list[list], dealer: int, player_count: int) -> _Turns:
    """Interleave the per-seat lockstep lists into one ordered event stream.

    Raises:
        TenhouJsonError: If no turn order accounts for every call the round records.
    """
    turns = _walk(draws, discards, [0] * player_count, dealer, [], None, [None] * player_count, claimed=False)
    if turns is None:
        raise TenhouJsonError("no turn order reproduces the round's calls")
    return turns


def _walk(
    draws: list[list],
    discards: list[list],
    pointer: list[int],
    current: int,
    events: list[Event],
    last_discard: Tile | None,
    last_drawn: list[Tile | None],
    *,
    claimed: bool,
) -> _Turns | None:
    """Play the round out from `current`, or None if this turn order contradicts the log.

    A seat holding a pending call has not acted since, so reaching one by the
    normal rotation means an earlier discard was claimed that should have been
    passed over -- the walk says so by failing, and the caller tries the pass.
    """
    robbed: Tile | None = None  # a kakan tile robbed by a chankan win, if the round ends on one
    while pointer[current] < len(draws[current]):
        drawn = draws[current][pointer[current]]
        robbed = None
        if isinstance(drawn, str):
            if not claimed:
                return None
            events.append(Call(current, _meld_from_string(drawn, last_discard)))
        else:
            last_drawn[current] = tile_from_tenhou(drawn)
            events.append(Draw(current, last_drawn[current]))
        claimed = False
        if pointer[current] >= len(discards[current]):
            break  # a winning self-draw: the last draw has no following discard
        discarded = discards[current][pointer[current]]
        if isinstance(discarded, str) and _meld_letter(discarded) in _KAN_LETTERS:
            meld = _meld_from_string(discarded, last_discard)
            events.append(Call(current, meld))
            robbed = meld.added
            pointer[current] += 1
            continue
        if discarded == _CALLED_AWAY:
            pointer[current] += 1
            continue
        tile, riichi, tsumogiri = _discard_tile(discarded, last_drawn[current])
        events.append(Discard(current, tile, riichi=riichi, tsumogiri=tsumogiri))
        last_discard = tile
        pointer[current] += 1
        caller = _next_caller(draws, pointer, current, tile, player_count=len(draws))
        if caller is not None:
            claim = _walk(draws, discards, list(pointer), caller, list(events), tile, list(last_drawn), claimed=True)
            if claim is not None:
                return claim
            # The claim belongs to a later, identical discard by this same seat.
        current = (current + 1) % len(draws)
    return events, last_discard, last_drawn, robbed


def _discard_tile(entry: object, drawn: Tile | None) -> tuple[Tile, bool, bool]:
    """The tile a discard entry names, whether it declared riichi, and its tsumogiri mark."""
    riichi = isinstance(entry, str) and entry[0] == "r"
    code = int(entry[1:]) if riichi else entry
    if code == _TSUMOGIRI:
        if drawn is None:
            raise TenhouJsonError("tsumogiri with no preceding draw")
        return drawn, riichi, True
    return tile_from_tenhou(code), riichi, False  # type: ignore[arg-type]


def _next_caller(draws: list[list], pointer: list[int], discarder: int, tile: Tile, player_count: int) -> int | None:
    """The seat, if any, whose next action claims this discard.

    A call names both the tile and the seat it came from -- a chii from the left
    neighbour, a pon or open kan from the position its letter encodes -- so both
    must agree, or two seats discarding the same tile in turn would be confused.

    A seat discarding the same tile twice can leave a chii and a pon both waiting
    on it, one for each copy. The pon is the earlier claim: had the chii come
    first, the ponning seat would have drawn in between and its pending entry
    would be that draw, not the call. Taking the pon also follows the priority a
    live table gives it.
    """
    chii: int | None = None
    for seat in range(player_count):
        if seat == discarder or pointer[seat] >= len(draws[seat]):
            continue
        entry = draws[seat][pointer[seat]]
        if not (
            isinstance(entry, str)
            and _called_tile(entry) == tile
            and _call_source(entry, seat, player_count) == discarder
        ):
            continue
        if _meld_letter(entry) != _CHII_LETTER:
            return seat
        chii = seat
    return chii


def _call_source(text: str, caller: int, player_count: int) -> int:
    """The seat a draw-slot call claims from, decoded from its letter position."""
    letter, _, letter_index = _meld_string_parts(text)
    steps = 1 if letter == "c" else _INDEX_OF_SOURCE_STEPS.get(letter_index, _SHIMOCHA_STEPS)
    return (caller - steps) % player_count


_SHIMOCHA_STEPS = 3
_INDEX_OF_SOURCE_STEPS = {0: 1, 1: 2, 2: 3}


def _meld_letter(text: str) -> str:
    """The single call letter of a meld string, wherever it sits."""
    return next(character for character in text if character.isalpha())


def _meld_string_parts(text: str) -> tuple[str, list[int], int]:
    """The call letter, the tile codes, and the letter's index among them."""
    codes: list[int] = []
    letter = ""
    letter_index = 0
    position = 0
    while position < len(text):
        if text[position].isalpha():
            letter = text[position]
            letter_index = len(codes)
            position += 1
        else:
            codes.append(int(text[position : position + 2]))
            position += 2
    return letter, codes, letter_index


def _called_tile(text: str) -> Tile:
    """The claimed tile of a call meld string."""
    _, codes, letter_index = _meld_string_parts(text)
    return tile_from_tenhou(codes[letter_index])


def _meld_from_string(text: str, last_discard: Tile | None) -> Meld:
    """The meld a Tenhou meld string encodes."""
    letter, codes, letter_index = _meld_string_parts(text)
    tiles = tuple(tile_from_tenhou(code) for code in codes)
    meld_type = _MELD_TYPE[letter]
    if meld_type is MeldType.ANKAN:
        return Meld(MeldType.ANKAN, tiles)
    if meld_type is MeldType.CHII:
        called = last_discard if last_discard in tiles else tiles[letter_index]
        return Meld(MeldType.CHII, tiles, called=called, source=CallSource.KAMICHA)
    called = codes_tile = tile_from_tenhou(codes[letter_index])
    source = _SOURCE_BY_INDEX.get(letter_index, CallSource.SHIMOCHA)
    if meld_type is MeldType.SHOUMINKAN:
        return Meld(MeldType.SHOUMINKAN, tiles, called=codes_tile, source=source, added=codes_tile)
    return Meld(meld_type, tiles, called=called, source=source)


def _outcome(
    result: list,
    ura: list[Tile],
    honba: int,
    riichi_sticks: int,
    rules: Rules,
    player_count: int,
    last_discard: Tile | None,
    last_drawn: list[Tile | None],
) -> tuple[Agari, ...] | Ryuukyoku:
    """The wins or draw a round's result array describes."""
    _ = player_count
    tag = result[0]
    if tag != _AGARI_TAG:
        deltas = tuple(result[1]) if len(result) > 1 else ()
        return Ryuukyoku(kind=tag if tag in _DRAW_TAGS else "exhaustive", deltas=deltas)
    agari: list[Agari] = []
    for index in range(1, len(result), 2):
        deltas = tuple(result[index])
        agari.append(
            _agari(
                result[index + 1], deltas, ura, honba, riichi_sticks, rules, last_discard, last_drawn, first=not agari
            )
        )
    return tuple(agari)


def _agari(
    detail: list,
    deltas: tuple[int, ...],
    ura: list[Tile],
    honba: int,
    riichi_sticks: int,
    rules: Rules,
    last_discard: Tile | None,
    last_drawn: list[Tile | None],
    *,
    first: bool,
) -> Agari:
    """One win from an `agari` detail array."""
    winner, from_seat = detail[0], detail[1]
    is_tsumo = winner == from_seat
    winning_tile = last_drawn[winner] if is_tsumo else last_discard
    if winning_tile is None:
        raise TenhouJsonError("win with no preceding draw or discard")
    honba = honba if first else 0
    return Agari(
        winner=winner,
        from_seat=from_seat,
        winning_tile=winning_tile,
        ura_indicators=tuple(ura),
        honba=honba,
        riichi_sticks=riichi_sticks if first else 0,
        deltas=deltas,
        fu=_parse_score_fu(detail[3]),
        value=_value_from_deltas(deltas, winner, honba, rules),
    )


_SCORE_FU_HAN = re.compile(r"(\d+)符(\d+)飜")


def _parse_score_fu(text: str) -> int | None:
    """The fu a Japanese score string reports, or None for a limit hand."""
    fu_han = _SCORE_FU_HAN.search(text)
    return int(fu_han.group(1)) if fu_han else None


def _value_from_deltas(deltas: tuple[int, ...], winner: int, honba: int, rules: Rules) -> int | None:
    """The win value the deltas imply, read from the winner's own gain.

    Every point a payer loses the winner gains, so the deltas sum to just the
    deposits swept off the table. Subtracting that sum and the honba from the
    winner's gain leaves the value, for a tsumo and a ron alike -- and, unlike
    reading a single payer, it still holds when a pao liability splits the
    payment between the discarder and the responsible player.
    """
    if not deltas:
        return None
    honba_total = (rules.player_count - 1) * _HONBA_SHARE * honba
    return deltas[winner] - sum(deltas) - honba_total


# --- Writing ----------------------------------------------------------------

_SANMA = 3
_EXHAUSTIVE_TAG = "流局"
_LETTER_OF_TYPE = {kind: letter for letter, kind in _MELD_TYPE.items()}
_INDEX_OF_SOURCE = {source: index for index, source in _SOURCE_BY_INDEX.items()}


def dump_tenhou_json(paifu: Paifu) -> dict:
    """Serialize a game to a Tenhou JSON object (four-player games only).

    The three-player North bonus has no place in this reader's turn model, so a
    sanma game is rejected rather than written without its nuki dora.

    Args:
        paifu: The game to serialize.

    Returns:
        A Tenhou JSON object that ``parse_tenhou_json`` reads back.

    Raises:
        TenhouJsonError: If the game is a three-player game.
    """
    if paifu.player_count == _SANMA:
        raise TenhouJsonError("Tenhou JSON export supports four-player games only")
    return {
        "name": [f"P{seat}" for seat in range(paifu.player_count)],
        "rule": {"aka": 1},
        "log": [_dump_round(round_log, paifu.player_count) for round_log in paifu.rounds],
    }


def dump_tenhou_json_url(paifu: Paifu) -> str:
    """Serialize a game to a Tenhou viewer URL (four-player games only).

    The game is written as a Tenhou JSON object, encoded compactly into the
    ``#json=`` fragment of the viewer URL that ``parse_tenhou_json`` reads back.

    Args:
        paifu: The game to serialize.

    Returns:
        A ``https://tenhou.net/6/#json=...`` URL that opens the game in the viewer.

    Raises:
        TenhouJsonError: If the game is a three-player game.
    """
    text = json.dumps(dump_tenhou_json(paifu), ensure_ascii=False, separators=(",", ":"))
    # Tenhou leaves commas and parentheses unescaped in the fragment.
    return f"{_VIEWER_PREFIX}{urllib.parse.quote(text, safe=',()')}{_VIEWER_SUFFIX}"


def _dump_round(round_log: RoundLog, player_count: int) -> list:
    """One round's heterogeneous array, de-interleaving the event stream."""
    draws: list[list] = [[] for _ in range(player_count)]
    discards: list[list] = [[] for _ in range(player_count)]
    dora = [tile_to_tenhou(round_log.initial_dora)]
    for event in round_log.events:
        if isinstance(event, Draw):
            draws[event.seat].append(tile_to_tenhou(event.tile))
        elif isinstance(event, Discard):
            code = _TSUMOGIRI if event.tsumogiri else tile_to_tenhou(event.tile)
            discards[event.seat].append(f"r{code}" if event.riichi else code)
        elif isinstance(event, Call):
            _dump_call_into(event, draws, discards)
        else:  # DoraReveal (a four-player game has no Kita)
            dora.append(tile_to_tenhou(event.indicator))
    info = [round_log.round_wind * 4 + round_log.dealer, round_log.honba, round_log.riichi_sticks]
    array: list = [info, list(round_log.scores), dora, _dump_ura(round_log.outcome)]
    for seat in range(player_count):
        array += [[tile_to_tenhou(tile) for tile in round_log.hands[seat]], draws[seat], discards[seat]]
    array.append(_dump_result(round_log.outcome))
    return array


def _dump_call_into(event: Call, draws: list[list], discards: list[list]) -> None:
    """Place a call into the draw or discard slot the reader expects."""
    meld = event.meld
    string = _meld_to_string(meld)
    if meld.type in (MeldType.CHII, MeldType.PON, MeldType.DAIMINKAN):
        draws[event.seat].append(string)
        if meld.type is MeldType.DAIMINKAN:
            discards[event.seat].append(_CALLED_AWAY)  # the claim slot carries no discard
    else:  # a self-kan declared during the seat's own turn
        discards[event.seat].append(string)


def _meld_to_string(meld: Meld) -> str:
    """Encode a meld as a Tenhou meld string the reader decodes back."""
    letter = _LETTER_OF_TYPE[meld.type]
    if meld.type is MeldType.ANKAN:
        codes = [tile_to_tenhou(tile) for tile in meld.tiles]
        return _assemble(codes, letter, len(codes) - 1)
    if meld.type is MeldType.SHOUMINKAN:
        index = _INDEX_OF_SOURCE.get(meld.source, 0)
        return _assemble(_place(meld.tiles, meld.added, index), letter, index)  # type: ignore[arg-type]
    index = 0 if meld.type is MeldType.CHII else _INDEX_OF_SOURCE[meld.source]
    return _assemble(_place(meld.tiles, meld.called, index), letter, index)  # type: ignore[arg-type]


def _place(tiles: tuple[Tile, ...], focus: Tile, index: int) -> list[int]:
    """The tile codes with `focus` at `index` and the rest in order around it."""
    rest = list(tiles)
    rest.remove(focus)
    return [tile_to_tenhou(focus if position == index else rest.pop(0)) for position in range(len(tiles))]


def _assemble(codes: list[int], letter: str, index: int) -> str:
    """Join two-digit codes with the call letter placed before position `index`."""
    parts: list[str] = []
    for position, code in enumerate(codes):
        if position == index:
            parts.append(letter)
        parts.append(f"{code:02d}")
    return "".join(parts)


def _dump_ura(outcome: tuple[Agari, ...] | Ryuukyoku) -> list[int]:
    """The one ura array a round records, from whichever win reveals it.

    On a multiple ron only the riichi winners carry ura, so the first non-empty
    set is the round's shared reveal; a non-riichi winner scores no ura from it.
    """
    if isinstance(outcome, Ryuukyoku):
        return []
    indicators = next((agari.ura_indicators for agari in outcome if agari.ura_indicators), ())
    return [tile_to_tenhou(tile) for tile in indicators]


def _dump_result(outcome: tuple[Agari, ...] | Ryuukyoku) -> list:
    """A round's result array: a draw tag, or the win tag with each win detail."""
    if isinstance(outcome, Ryuukyoku):
        tag = outcome.kind if outcome.kind in _DRAW_TAGS else _EXHAUSTIVE_TAG
        return [tag, list(outcome.deltas)]
    result: list = [_AGARI_TAG]
    for agari in outcome:
        result.append(list(agari.deltas))
        # The Japanese score string carries fu; without the han total it cannot be
        # rebuilt, so it is left empty and the value is recovered from the deltas.
        result.append([agari.winner, agari.from_seat, agari.winner, ""])
    return result
