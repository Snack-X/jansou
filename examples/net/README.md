# Example: multiplayer game over network

1. Run `server.py`
2. Run `client.py` to use existing agent, or `play.py` to play by yourself

- Each script has bunch of options, so check for the usages (`-h/--help`)

```sh
uv run server.py
```

### Saving paifu

Pass `--save DIR` to the server to write each game's record into `DIR`,
named `<session timestamp>-game-<n>`. `--save-format` picks the format:
`tenhou` (Tenhou JSON, default), `mjai` (MJAI JSONL), or `mjlog` (Tenhou XML).

```sh
uv run server.py --save logs/ --save-format mjai
```

## Protocol

Newline-delimited JSON are sent over TCP. Every message is an object with a `"type"`.
Tiles are MJAI tokens (`5m`, `5mr` for a red five, `E S W N / P F C` for honors). Seats are integers, `0`-based.

### Handshake

| direction | message |
|---|---|
| client → server | `{"type": "join", "name": "my-bot"}` |
| server → client | `{"type": "welcome", "seat": 0, "player_count": 4, "games": 100}` |

### Server → client, during play

- `{"type": "event", "data": {...}}`
    - one game event, masked for your seat (other players' draws have `"tile": null`, other hands in `deal_start` are `null`).
- `{"type": "decision", "kind": K, "actions": [...]}`
    - you must answer. `kind` is `self`, `discard_reaction`, `robbed_kan`, `north_reaction`, or `tenpai`.
- `{"type": "result", "game": 3, "games": 100, "scores": [...], "ranking": [...]}`
    - one game finished; `ranking` lists seats best-first.
- `{"type": "end", "games": 100, "summary": [{"seat", "name", "average_placement", "average_score", "first_places"}, ...]}`
    - session over, connection closes after this.

### Client → server, answering a decision

```json
{"type": "action", "index": 2}
```

`index` points into the `actions` array of the decision being answered.
An out-of-range index or malformed reply aborts the session.

### Actions

- `{"type": "discard", "tile": T, "tsumogiri": B}`
- `{"type": "riichi", "tile": T, "tsumogiri": B}`
    - `tsumogiri` marks the drawn-tile candidate, offered separately from an identical hand copy
- `{"type": "tsumo"}`
- `{"type": "ron"}`
- `{"type": "chii", "tiles": [T, T]}`
- `{"type": "pon", "tiles": [T, T]}`
- `{"type": "open_kan"}`
- `{"type": "closed_kan", "kind": T}`
- `{"type": "added_kan", "tile": T}`
- `{"type": "nuki"}`
- `{"type": "nine_terminals"}`
- `{"type": "pass"}`
- `{"type": "declare_tenpai", "declare": true}`

### Events

- `game_start` — `player_count`, `names`, `scores`
- `deal_start` — `dealer`, `round_wind`, `round_number`, `honba`, `deposits`, `scores`, `hands` (yours only; others `null`), `dora_indicator`
- `draw` — `seat`, `tile` (`null` unless yours), `replacement`
- `discard` — `seat`, `tile`, `tsumogiri`, `riichi`
- `call` — `meld_type` (`chii`/`pon`/`daiminkan`/`ankan`/`shouminkan`), `caller`, `source`, `tiles`
- `indicator_reveal` — `tile`
- `north_extraction` — `seat`, `tile` (three-player)
- `riichi_accepted` — `seat`
- `win` — `seat`, `from_seat` (`null` on tsumo), `winning_tile`, `hand` (`{"concealed": [...], "melds": [{"type", "tiles"}]}`), `yaku` (`[{"name", "value"}]`), `is_yakuman`, `han`, `fu`, `dora` (`{"dora", "ura", "aka", "nuki"}`), `limit`, `points`, `ura_indicators`
- `ryuukyoku` — `kind`, `revealed` (`[{"seat", "hand"}]`), `counted_ready`
- `score_change` — `deltas`, `scores`
- `game_end` — `final_scores`, `ranking`
