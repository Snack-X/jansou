# MJAI JSONL Paifu Format

The MJAI protocol is a JSON-based event stream for Riichi Mahjong agents and
replays, originally introduced by [gimite/mjai](https://gimite.net/pukiwiki/index.php?MJAI%20%CB%E3%BF%FD%20AI%20%C2%D0%C0%EF%A5%B5%A1%BC%A5%D0) and adopted by tooling
including `mjai-reviewer`, `mortal`, and `akochan`. A complete game record is
stored as a [JSONL](https://jsonlines.org/) file: one JSON object per line,
no enclosing array, replayed top-to-bottom. Files typically have a `.json` or
`.jsonl` extension.

This spec covers both the rich variant produced by `mjai-reviewer` / `mortal`,
which carries score-detail fields (yaku list, fu, winning tile), and the lean
variant that carries only `deltas` on a win. Both are parseable: everything the
rich variant states outright can be recovered by replaying the stream, so score
validation works either way, and only the yaku/fu cross-checks are skipped.

Three- and four-player games are both covered; sanma adds the `kita` event.

---

## File structure

```
{"type":"start_game","names":[...],...}
{"type":"start_kyoku","bakaze":"E","dora_marker":"5p","kyoku":1,...}
{"type":"tsumo","actor":0,"pai":"5m"}
{"type":"dahai","actor":0,"pai":"9p","tsumogiri":false}
…
{"type":"hora","actor":2,"target":0,...}
{"type":"end_kyoku"}
{"type":"start_kyoku",...}
…
{"type":"end_game"}
```

- One JSON object per line. Lines are not indented; `\n` is the only whitespace.
- Lines are *order-significant*: events are replayed in file order.
- Empty lines are ignored.
- The number of players (3 or 4) is inferred from the length of the
  `names` / `tehais` arrays in `start_game` / `start_kyoku` and is fixed for
  the whole game. `start_game` may lack `names`, so `start_kyoku.tehais` is the
  reliable source.
- Files may be gzip-compressed (`.jsonl.gz`); readers should sniff the
  `1f 8b` magic rather than trust the extension.

---

## Tile notation

Every tile is a short string. There is no integer encoding.

| Group           | Tiles                                                     |
|-----------------|-----------------------------------------------------------|
| Manzu (man)     | `1m`, `2m`, `3m`, `4m`, `5m`, `6m`, `7m`, `8m`, `9m`      |
| Pinzu (pin)     | `1p`, `2p`, `3p`, `4p`, `5p`, `6p`, `7p`, `8p`, `9p`      |
| Souzu (sou)     | `1s`, `2s`, `3s`, `4s`, `5s`, `6s`, `7s`, `8s`, `9s`      |
| Red 5s          | `5mr`, `5pr`, `5sr`                                       |
| Honors          | `E`, `S`, `W`, `N`, `P`, `F`, `C`                         |
| Unknown/masked  | `?`                                                       |

- Honors are uppercase: East / South / West / North / Haku (白, P) / Hatsu
  (發, F) / Chun (中, C). Some MJAI variants use `1z`-`7z` instead;
  parsers should accept either. This spec emits the uppercase form.
- `?` appears in event streams that mask other players' hands (e.g. the
  perspective of one agent). For E2E validation we expect *full-information*
  logs where `?` does not occur.

---

## Game-info events

These appear once at the start and once at the end. The fields named
*optional* below are honored when present but not required.

### `start_game`

```json
{
  "type": "start_game",
  "names": ["Alice", "Bob", "Carol", "Dave"],
  "kyoku_first": 0,                  // optional: 0=tonpuu start (E1), 4=south-only (S1)
  "aka_flag": true,                  // optional: red 5s enabled
  "rule": {...}                      // optional: ruleset metadata
}
```

`names.length` determines the player count (3 or 4).

### `end_game`

```json
{ "type": "end_game" }
```

May carry final scores in some variants — not required.

---

## Round-info events

### `start_kyoku`

```json
{
  "type": "start_kyoku",
  "bakaze": "E",                     // round wind: "E" | "S" | "W" | "N"
  "dora_marker": "5p",               // initial dora indicator
  "kyoku": 1,                        // 1-indexed within the wind (E1 = 1, E2 = 2, …)
  "honba": 0,
  "kyotaku": 0,                      // riichi sticks on table at round start
  "oya": 0,                          // dealer seat (0..3)
  "scores": [25000, 25000, 25000, 25000],
  "tehais": [
    ["1m","2m","3m","4m","5m","6m","7m","8m","9m","E","E","E","C"],
    [...],
    [...],
    [...]
  ]
}
```

Each `tehais[i]` lists exactly 13 tiles for non-dealer seats and 13 tiles
for the dealer too — the dealer's first draw appears as a separate `tsumo`
event immediately after `start_kyoku`.

### `end_kyoku`

```json
{ "type": "end_kyoku" }
```

Closes the round. The preceding event is always a `hora` or `ryukyoku`.

---

## Per-player events

Every action event carries an `actor` field (0..3). For events triggered by
another player's discard (`chi`, `pon`, `daiminkan`, ron-`hora`), `target` is
the source seat.

### `tsumo`

```json
{ "type": "tsumo", "actor": 0, "pai": "5m" }
```

The drawn tile is added to the actor's hand. After a kan, the rinshan draw
also uses `tsumo`.

### `dahai`

```json
{ "type": "dahai", "actor": 0, "pai": "9p", "tsumogiri": false }
```

Discard. `tsumogiri` is `true` if the discarded tile is the just-drawn one.

### `chi`

```json
{
  "type": "chi",
  "actor": 0,
  "target": 3,
  "pai": "5p",                       // called tile (from target's discard)
  "consumed": ["6p","7p"]            // tiles taken from actor's hand
}
```

Chi only fires from the left-of-discarder. After the call, the actor must
emit a `dahai`.

### `pon`

```json
{
  "type": "pon",
  "actor": 1,
  "target": 0,
  "pai": "C",
  "consumed": ["C","C"]
}
```

Two tiles from hand + one called tile.

### `daiminkan`

```json
{
  "type": "daiminkan",
  "actor": 2,
  "target": 0,
  "pai": "5s",
  "consumed": ["5s","5s","5sr"]      // includes red 5s when applicable
}
```

Three tiles from hand + one called tile. Triggers a rinshan `tsumo`.

### `ankan`

```json
{
  "type": "ankan",
  "actor": 1,
  "consumed": ["1z","1z","1z","1z"]
}
```

No `pai` / `target`. Concealed kan; preserves menzen status. Triggers a
rinshan `tsumo`.

### `kakan`

```json
{
  "type": "kakan",
  "actor": 0,
  "pai": "C",                        // the added tile (chankan target)
  "consumed": ["C","C","C"]          // the original pon's three tiles
}
```

Added kan — the actor upgrades a prior `pon` of the same tile. Triggers a
rinshan `tsumo`. An opponent may chankan-ron on `pai`; the next event in
that case is `hora` with `actor` = ron-er and `target` = kakan declarer.

### `kita` (3-player only)

```json
{ "type": "kita", "actor": 0, "pai": "N" }
```

The sanma North bonus (nuki dora): the actor sets a North aside from hand as a
bonus tile. `pai` is always `N`. Like `kakan`, a kita **is robbable** — an
opponent may chankan-ron the extracted North, in which case the next event is
`hora` with `target` = the kita declarer and the winning tile is that North,
*not* the last discard. Otherwise the actor draws a replacement, which appears
as the next `tsumo`, and the turn continues.

Note the event is named `kita`, not `nukidora`.

### `reach`

```json
{ "type": "reach", "actor": 0 }
```

Riichi declaration. The next event is the riichi-discard (`dahai`), then
`reach_accepted` once the deposit is committed.

### `reach_accepted`

```json
{
  "type": "reach_accepted",
  "actor": 0,
  "deltas": [-1000,0,0,0],           // optional
  "scores": [24000,25000,25000,25000] // optional
}
```

Confirms the 1000-point deposit. Sources that omit `deltas`/`scores` are
still valid — the deduction is implied.

### `dora`

```json
{ "type": "dora", "dora_marker": "5s" }
```

Kan dora reveal. Both ankan and minkan reveals appear under this single
event type.

---

## Round-end events

### `hora`

```json
{
  "type": "hora",
  "actor": 0,                        // winner
  "target": 2,                       // dealt-in player; equals actor for tsumo
  "pai": "5m",                       // winning tile
  "uradora_markers": ["5p"],         // optional, riichi-only; absent if no riichi
                                     // (mortal / akochan use `ura_markers`; both accepted)
  "hora_tehais": ["1m","2m","3m",...,"5m"],  // optional; full 14-tile winning shape
  "yakus": [                         // optional; rich-variant only
    {"name": "menzentsumo", "han": 1},
    {"name": "riichi",      "han": 1},
    {"name": "pinfu",       "han": 1},
    {"name": "akadora",     "han": 1}
  ],
  "fu": 30,                          // optional; rich-variant only
  "fan": 4,                          // optional; total han
  "hora_points": 8000,               // optional; total winner gain (pre-honba)
  "deltas": [8000,-2000,-2000,-4000],
  "scores": [33000,23000,23000,21000]
}
```

Every field above except `type`, `actor`, `target`, and `deltas` is optional,
and **lean producers omit all of them** — including `pai`. Such a stream carries
neither the winning hand nor the score breakdown, so both must be recovered:
the hand by replaying the winner's draws, discards, and calls from
`start_kyoku.tehais`, the winning tile from the last `tsumo` (self-draw) or the
robbed / last-discarded tile (ron), and the value from `deltas`.

Some producers write `ura_markers` rather than `uradora_markers`, and a boolean
`tsumo` flag; `actor == target` already says the same thing.

In multi-ron rounds, multiple consecutive `hora` events appear with the same
`target`; each carries the per-winner deltas. Honba and riichi sticks on the
table are conventionally awarded only to the first `hora` (head bump).

#### Recovering the value from `deltas`

Every point a payer loses the winner gains, so **the deltas sum to exactly the
deposits the winner sweeps**, never to zero. Those deposits exceed
`start_kyoku.kyotaku` whenever a riichi was declared during the round.

```
value = deltas[winner] - sum(deltas) - honba_total
honba_total = (num_players - 1) * 100 * honba      # 300 in yonma, 200 in sanma
```

This holds for a tsumo and a ron alike. Reading the payer's side instead
(`-deltas[target]`) breaks under **pao** (sekinin barai), where the dealing-in
seat and the liable seat split the payment and neither delta equals the value.
For the second and later `hora` of a multi-ron, use `honba = 0`.

#### Yaku names

The library compares against canonical English names (`riichi`, `pinfu`,
`tanyao`, `menzen_tsumo`, …). MJAI yaku names are commonly written without
the underscore (`menzentsumo`, `chitoitsu`, `junchantaiyao`, …). The
parser maps the common spellings to canonical names; unrecognized names are
left untouched and will surface as a yaku-set mismatch in the validator.

Frequently encountered MJAI → canonical mappings (non-exhaustive):

| MJAI name              | Canonical                |
|------------------------|--------------------------|
| `menzentsumo`          | `menzen_tsumo`           |
| `riichi`               | `riichi`                 |
| `ippatsu`              | `ippatsu`                |
| `pinfu`                | `pinfu`                  |
| `tanyao`               | `tanyao`                 |
| `iipeikou`             | `ippeiko`                |
| `yakuhai` / `haku` / `hatsu` / `chun` / `bakaze N` / `jikaze N` | `yakuhai` |
| `chanta`               | `chanta`                 |
| `rinshan` / `rinshan_kaihou` | `rinshan`          |
| `chankan`              | `chankan`                |
| `haitei`               | `haitei`                 |
| `houtei`               | `houtei`                 |
| `sanshoku` / `sanshoku_doujun` | `sanshoku_doujun` |
| `sanshokudoukou`       | `sanshoku_doukou`        |
| `ittsuu` / `ittsu`     | `ittsuu`                 |
| `toitoi`               | `toitoi`                 |
| `sanankou`             | `sanankou`               |
| `sankantsu`            | `sankantsu`              |
| `honroutou`            | `honroutou`              |
| `shousangen`           | `shousangen`             |
| `honitsu`              | `honitsu`                |
| `junchan` / `junchantaiyao` | `junchan`           |
| `ryanpeikou`           | `ryanpeikou`             |
| `chinitsu`             | `chinitsu`               |
| `dora`                 | `dora`                   |
| `akadora` / `aka`      | `aka_dora`               |
| `uradora`              | `ura_dora`               |
| `kokushimusou` / `kokushi` | `kokushi`            |
| `suuankou`             | `suuankou`               |
| `daisangen`            | `daisangen`              |
| `shousuushi` / `shousuushii` | `shousuushi`       |
| `daisuushi` / `daisuushii`   | `daisuushi`        |
| `tsuuiisou`            | `tsuuiisou`              |
| `chinroutou`           | `chinroutou`             |
| `ryuuiisou`            | `ryuuiisou`              |
| `chuurenpoutou` / `chuuren` | `chuuren`           |
| `daburu_riichi` / `dabururiichi` | `double_riichi`|

Yakuman variants (`suuankou_tanki`, `kokushi_juusan`, `chuuren_junsei`,
double-yakuman `daisuushi_dora`, etc.) collapse onto their base yaku — the
library doesn't model them as distinct yaku and applies the double-yakuman
multiplier through the ruleset.

### `ryukyoku`

```json
{
  "type": "ryukyoku",
  "reason": "exhaustive_draw",       // see below
  "can_act": [true, false, true, true],  // tenpai flags (exhaustive only)
  "deltas": [1500, -1500, 1500, -1500],
  "scores": [...],
  "tehais": [["1m",…], null, ["1p",…], null]    // optional; revealed hands per player
}
```

`reason` is **not standardized across producers**. Alongside the MJAI-canonical
`fanpai` (exhaustive), `kyuushuukyuuhai`, `suufon_renda`, `suukan_sanra`,
`suuchariichi`, `sanchaho`, and `nagashi_mangan`, real logs carry spellings
such as `exhaustive_draw` and `kyushu_kyuhai`, and even free-form abort text
like `"Error: Illegal Action by Player 3"`. Treat `reason` as an opaque label,
and fall back to "exhaustive" when it is absent.

In `tehais`, a non-null entry marks a tenpai seat and `null` a noten one.

---

## What the validator extracts

For E2E score validation, only the following needs to land in an
`AgariRecord`:

- **Closed tiles** — reconstructed by replaying `tsumo` / `dahai` / `chi` /
  `pon` / `*kan` / `reach` from `start_kyoku.tehais[winner]` until the
  matching `hora`.
- **Open melds** — accumulated chi/pon/daiminkan/ankan/kakan events for the
  winner.
- **Winning tile** — `hora.pai` when present; otherwise the last `tsumo` for a
  self-draw, or the robbed `kakan` / `kita` tile, or the last `dahai`, for a
  ron. Red status is carried by the suffix (`5mr` etc.).
- **Win context** — built from observed events: riichi (`reach` before `hora`
  for that player), double riichi (riichi declared in the first uncalled
  round), ippatsu (riichi → no calls / kans → immediate hora), haitei / houtei
  (last live-wall draw / last discard), rinshan (hora on a rinshan `tsumo`),
  chankan (hora on another player's `kakan` or `kita` tile), dora (from
  `start_kyoku.dora_marker` + every `dora`), ura dora (`hora.uradora_markers`
  or `ura_markers`, riichi only), the sanma nuki dora count (`kita` events),
  `honba`, and the deposits at round start (`start_kyoku.kyotaku`).
- **Expected value** — recovered from `hora.deltas` as shown above. `hora.fu`,
  `hora.fan`, and `hora.yakus` are cross-checked when the producer supplies
  them.

For multi-ron rounds, only the first `hora` carries the honba bonus and the
deposits (matching the mjlog / Tenhou-JSON convention); subsequent winners drop
their `honba` and deposits to 0.

---

## Out of scope

- MJAI variants without `yakus` / `fu` in `hora` — supported, but the yaku/fu
  assertions are skipped.
- `start_game.rule` — read but otherwise unused; the ruleset is pinned to a
  preset chosen by player count.

---

## References

- MJAI protocol: [gimite/mjai](https://gimite.net/pukiwiki/index.php?MJAI%20%CB%E3%BF%FD%20AI%20%C2%D0%C0%EF%A5%B5%A1%BC%A5%D0)
- This format is consumed by [`jansou.io.mjai`](../../src/jansou/io/mjai.py),
  which provides `parse_mjai` (returning a format-neutral `Paifu`) and
  `dump_mjai`, for three- and four-player games alike. The chronological replay
  that turns a `RoundLog` into `AgariRecord`s (`replay_round`) lives in
  [`jansou.io.paifu`](../../src/jansou/io/paifu.py) and consumes the IR
  produced by every parser.
