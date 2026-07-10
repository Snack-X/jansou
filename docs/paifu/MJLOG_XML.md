# Tenhou XML (mjlog) Paifu Format

The classic Tenhou paifu format. A complete game record (paifu) is an XML
document containing one root `<mjloggm>` element with a flat sequence of
event children. Files typically have a `.mjlog` extension, are gzip-compressed
on disk, and can be downloaded from the Tenhou viewer URL:

```
https://tenhou.net/0/?log=<LOG_ID>
https://tenhou.net/5/mjlog2xml.cgi?<LOG_ID>          # canonical XML
```

A log id looks like `2019010100gm-0089-0000-1abcdef2`. The XML form is
self-contained — no external lookup is needed.

---

## Top-level

```xml
<mjloggm ver="2.3">
  <SHUFFLE  .../>
  <GO       .../>
  <UN       .../>
  <TAIKYOKU .../>

  <!-- repeated, one block per round: -->
  <INIT     .../>
  <!-- … draws / discards / calls / riichi / dora / bye … -->
  <AGARI    .../>     <!-- or <RYUUKYOKU .../> -->
</mjloggm>
```

Children are *order-significant*: events are replayed top-to-bottom. There is
no nesting beyond the root — every event is a self-closing leaf.

The number of players is fixed for the whole game and is determined from the
`type` bits of `<GO>` (see below). It does not change mid-game.

---

## Game-info elements

These appear once at the start.

### `<SHUFFLE seed="..." ref=""/>`

Random-seed metadata used by the Tenhou client to reproduce wall shuffles in
the viewer. Not needed for replay-based score validation.

### `<GO type="N" lobby="..."/>`

Game-mode encoding. `type` is a bitfield:

| Bit    | Meaning                                                |
|--------|--------------------------------------------------------|
| `0x01` | PVP (vs. humans)                                       |
| `0x02` | no aka (red 5s disabled)                               |
| `0x04` | no kuitan (open tanyao disabled)                       |
| `0x08` | hanchan (else tonpuu)                                  |
| `0x10` | 3-player (sanma) — when set, only 3 seats are in play  |
| `0x20` | tokujou (上級卓)                                       |
| `0x40` | soku (fast)                                            |
| `0x80` | houou (鳳凰卓) / phoenix table                         |

Only the lowest 8 bits encode mode. Higher bits are reserved.

### `<UN n0="..." n1="..." n2="..." n3="..." dan="..." rate="..." sx="..."/>`

Player metadata.

- `n0` … `n3` — URL-encoded player display names. In sanma the fourth seat is
  empty (`n3=""`).
- `dan` — comma-separated dan ranks per seat (integer 0-20).
- `rate` — comma-separated player ratings (float).
- `sx` — comma-separated `M` / `F` / `C` (male / female / unspecified).

Re-broadcasts of `<UN>` mid-game (without all attributes) signal a player
reconnect after `<BYE>` and only carry the `n*` attributes that changed; they
can be ignored for score-validation purposes.

### `<TAIKYOKU oya="P"/>`

Marks the start of the match. `oya` is the seat index (0-3) of the *initial*
dealer of the very first round. Subsequent rounds rotate the dealer
internally.

---

## Round-init element

### `<INIT seed="..." ten="..." oya="P" hai0="..." hai1="..." hai2="..." hai3="..."/>`

Begins a new round.

- `seed` — six comma-separated ints: `round_id, honba, riichi_sticks, die1, die2, dora_indicator`.
  - `round_id`: `0..3` = East 1-4, `4..7` = South 1-4, `8..11` = West 1-4.
    Round wind = `(round_id // 4) + 1`; dealer seat = `round_id % 4`.
  - `honba`, `riichi_sticks`: pool counts at round start.
  - `die1`, `die2`: 1-6 each (dice roll for break point); informational only.
  - `dora_indicator`: tile id (0-135) of the initial dora indicator. Kan-dora
    indicators come later via `<DORA>` events.
- `ten` — comma-separated per-seat scores at round start, in **units of 100
  points**. A seat showing `250` means 25 000 points.
- `oya` — dealer seat index for *this* round (redundant with `round_id % 4`
  but always present).
- `hai0` … `hai3` — comma-separated tile ids (0-135) for each player's
  initial 13-tile hand. In sanma, `hai3` is omitted.

The dealer's 14th tile is **not** in `hai*`; it appears as the round's first
draw event.

---

## Per-turn events

### Draws — `<TN/>`, `<UN/>`, `<VN/>`, `<WN/>`

A draw is a single tag whose first character identifies the seat and whose
remaining characters are the decimal tile id:

| Tag prefix | Seat |
|------------|------|
| `T`        | 0    |
| `U`        | 1    |
| `V`        | 2    |
| `W`        | 3    |

Examples: `<T123/>` = seat 0 draws tile 123; `<W17/>` = seat 3 draws tile 17.
The format does not distinguish wall draws from rinshan — a draw immediately
following the player's own kan call (`<N/>` with kan-flag set, or a `<DORA/>`
reveal) is a rinshan draw.

### Discards — `<DN/>`, `<EN/>`, `<FN/>`, `<GN/>`

Same convention as draws but with a different letter prefix:

| Tag prefix | Seat |
|------------|------|
| `D`        | 0    |
| `E`        | 1    |
| `F`        | 2    |
| `G`        | 3    |

Tsumogiri is **not** flagged in the tag itself — recover it by comparing the
discarded tile id to the player's most recent draw. (Some third-party encoders
use lowercase letters for tsumogiri, but Tenhou's canonical mjlog does not.)

---

## Call events

### `<N who="P" m="M"/>`

A meld call by seat `P`. `m` is a packed 16-bit integer encoding the call
type, source player, called tile, and the four (or three) tile ids involved.

The exact bit layout depends on the call type, identified by which low bit of
`m` is set:

| Bit    | Call type                |
|--------|--------------------------|
| `0x04` | chi (sequence)           |
| `0x08` | pon (triplet)            |
| `0x10` | shouminkan (kakan)       |
| `0x20` | nuki dora (sanma kita)   |
| none   | ankan (closed quad) **or** daiminkan (open quad) — distinguished by `from` source bits |

Common low-bit fields (bits 0-1):

- `m & 0x03` — `from` source: `0` = self (ankan, kakan, kita), `1` = kamicha,
  `2` = toimen, `3` = shimocha.

#### Chi (`m & 0x04`)

```
bits 0-1   from (always 3 = kamicha)
bits 3-4   tile-0 copy index (0-3)
bits 5-6   tile-1 copy index
bits 7-8   tile-2 copy index
bits 10-15 base = (suit*7 + lowest_rank)*3 + called_position
            where called_position ∈ {0,1,2} marks which tile in the
            sequence was the called one
```

Tile ids are reconstructed as `(base_rank + i) * 4 + copy_index` for each
position `i ∈ {0,1,2}`, with the called position's copy index being the
called tile.

#### Pon (`m & 0x08`)

```
bits 0-1   from (1/2/3 = kamicha/toimen/shimocha)
bits 5-6   "unused" copy index (the missing 4th tile)
bits 9-15  base = tile_kind * 3 + called_position (0..2)
```

The three tiles in the meld are the three copies of `tile_kind` whose
indices ≠ `unused`, with the called-position tile being the called one.

#### Shouminkan / kakan (`m & 0x10`)

```
bits 0-1   from (matches the original pon's source)
bits 5-6   added-tile copy index
bits 9-15  base = tile_kind * 3 + called_position  (matches the prior pon)
```

The added tile is the fourth copy of `tile_kind`. The pre-existing three are
the same as the prior pon.

#### Ankan (no chi/pon/kakan/kita bits; `from = 0`)

```
bits 8-15  hai0 = any tile id (0-135) of one of the four copies
```

The four tiles are all four copies of `hai0 // 4`.

#### Daiminkan (no chi/pon/kakan/kita bits; `from ≠ 0`)

```
bits 0-1   from (1/2/3)
bits 8-15  hai0 = any tile id of one of the four copies
```

The four tiles are all four copies of `hai0 // 4`. The called tile is
the one taken from the source player; the other three came from the
caller's hand.

#### Nuki dora / kita (`m & 0x20`, sanma only)

```
bits 0-1   from (always 0 = self)
bits 8-15  hai0 = tile id of the kita'd tile (always a 4z = north)
```

A kita does not interrupt the turn — the player draws a rinshan-style
replacement immediately and continues.

---

## Riichi

### `<REACH who="P" step="1"/>`

Step 1: the riichi declaration **before** the discard. The next tag is the
declaring player's discard (the riichi-declaring discard).

### `<REACH who="P" step="2" ten="..."/>`

Step 2: the deposit is confirmed (i.e. the discard was not ron'd). `ten` is
the comma-separated post-deposit scores in units of 100. If a player declares
riichi but the discard is immediately ron'd, **only step 1 appears** — no
deposit is taken.

---

## Dora reveal

### `<DORA hai="T"/>`

A new kan-dora indicator is revealed. `hai` is the tile id (0-135) of the new
indicator. Ura-dora indicators are not announced via `<DORA>` — they appear
only inside the matching `<AGARI>` element when a riichi win occurs.

---

## Disconnect / reconnect

### `<BYE who="P"/>`

Seat `P` disconnected. The replay continues; the disconnected seat plays
tsumogiri on every turn until reconnect.

### `<UN n0="..." .../>` (mid-game)

Reconnect: only the `n*` for the rejoining seat is present. No state change
beyond connectivity.

These can be ignored for hand-state and score reconstruction.

---

## Win — `<AGARI .../>`

Attributes (all comma-separated where applicable):

| Attribute     | Meaning                                                     |
|---------------|-------------------------------------------------------------|
| `who`         | winning seat                                                |
| `fromWho`     | dealing-in seat; equals `who` for tsumo (note the capital `W`) |
| `paoWho`      | pao (sekinin barai) responsibility seat; absent if no pao   |
| `hai`         | winner's closed hand tile ids at moment of win              |
| `m`           | winner's open melds, comma-separated `m` integers (same as `<N>`) |
| `machi`       | winning tile id                                             |
| `ten`         | three ints `fu, value, limit_kind` (see below)              |
| `yaku`        | comma-separated `(yaku_id, han)` pairs — non-yakuman yaku   |
| `yakuman`     | comma-separated yaku ids — yakuman (no han, each implies 13) |
| `doraHai`     | comma-separated dora indicator tile ids at the time of win  |
| `doraHaiUra`  | comma-separated ura-dora indicator tile ids (riichi only)   |
| `ba`          | two ints `honba, riichi_sticks` at the time of win          |
| `sc`          | per-seat `score, delta` pairs (in units of 100)             |
| `owari`       | end-of-game scores + final placements; present only on the last `<AGARI>` of the game |

### `ten`

Three integers: `[fu, value, limit_kind]`.

- `fu` — fu count. `0` for kazoe yakuman; `25` for chiitoitsu; otherwise the
  rounded-up sum. For a **true yakuman**, whose value does not depend on fu, the
  reported number is decorative: real logs carry `0`, `25`, `30`, `40`, `50`,
  `60`, and `80` on yakuman wins, and it need not match a recomputed fu (one
  chiihou records `20` where the hand's fu is `30`). Do not check fu on a
  yakuman.
- `value` — the winner's total gain **before** honba and deposits: the sum of
  every payer's payment. This is *not* the pre-multiplier base (a 40fu 2han
  non-dealer ron records `2600`, not the base `640`), and it is not the
  per-payer amount either. It equals
  `deltas[who] - sum(deltas) - honba_total`, and holds even under pao.
- `limit_kind` — tier marker:

  | Value | Meaning            |
  |-------|--------------------|
  | `0`   | none (under mangan)|
  | `1`   | mangan             |
  | `2`   | haneman            |
  | `3`   | baiman             |
  | `4`   | sanbaiman          |
  | `5`   | yakuman            |

### `sc`

Eight (or six in sanma) ints: `s0, d0, s1, d1, s2, d2, s3, d3` where `sN` is
seat `N`'s post-payment score and `dN` is the delta. All in units of 100.
Honba and riichi-stick payouts are folded into the deltas. The deltas sum to
the deposits the winner sweeps, not to zero.

The honba a winner collects is `100` per **non-winner**: `300` on a yonma ron,
`200` on a sanma ron. A tsumo collects the same `100` from each payer.

### `paoWho` (sekinin barai)

When present, the liable seat carries the payment:

- **Pao ron** — the dealing-in seat pays exactly half the value; `paoWho` pays
  the other half *plus the entire honba*. No other seat pays. So **no single
  payer's delta equals the value**, and reading `-deltas[fromWho]` halves it.
- **Pao tsumo** — `paoWho` pays the whole value plus the whole honba, alone;
  every other non-winner's delta is `0`.

`ten`'s `value` records the full amount either way, so mjlog needs no delta
arithmetic. A format that must infer the value from deltas has to read the
*winner's* side, since summing the payers is only correct for a tsumo.

### `yaku` and `yakuman`

Yaku id table (non-exhaustive — exact mapping per the Tenhou client):

| ID  | Yaku (JP / canonical)                | Type     |
|-----|--------------------------------------|----------|
| 0   | 門前清自摸和 (mentsumo)              | regular  |
| 1   | 立直 (riichi)                        | regular  |
| 2   | 一発 (ippatsu)                       | regular  |
| 3   | 槍槓 (chankan)                       | regular  |
| 4   | 嶺上開花 (rinshan)                   | regular  |
| 5   | 海底摸月 (haitei)                    | regular  |
| 6   | 河底撈魚 (houtei)                    | regular  |
| 7   | 平和 (pinfu)                         | regular  |
| 8   | 断幺九 (tanyao)                      | regular  |
| 9   | 一盃口 (iipeikou)                    | regular  |
| 10  | 自風 東                              | yakuhai  |
| 11  | 自風 南                              | yakuhai  |
| 12  | 自風 西                              | yakuhai  |
| 13  | 自風 北                              | yakuhai  |
| 14  | 場風 東                              | yakuhai  |
| 15  | 場風 南                              | yakuhai  |
| 16  | 場風 西                              | yakuhai  |
| 17  | 場風 北                              | yakuhai  |
| 18  | 役牌 白                              | yakuhai  |
| 19  | 役牌 發                              | yakuhai  |
| 20  | 役牌 中                              | yakuhai  |
| 21  | 両立直 (double riichi)               | regular  |
| 22  | 七対子 (chiitoitsu)                  | regular  |
| 23  | 混全帯幺九 (chanta)                  | regular  |
| 24  | 一気通貫 (ittsu)                     | regular  |
| 25  | 三色同順 (sanshoku doujun)           | regular  |
| 26  | 三色同刻 (sanshoku doukou)           | regular  |
| 27  | 三槓子 (sankantsu)                   | regular  |
| 28  | 対々和 (toitoi)                      | regular  |
| 29  | 三暗刻 (san ankou)                   | regular  |
| 30  | 小三元 (shousangen)                  | regular  |
| 31  | 混老頭 (honroutou)                   | regular  |
| 32  | 二盃口 (ryanpeikou)                  | regular  |
| 33  | 純全帯幺九 (junchan)                 | regular  |
| 34  | 混一色 (honitsu)                     | regular  |
| 35  | 清一色 (chinitsu)                    | regular  |
| 36  | 人和 (renhou) — rule-dependent       | regular  |
| 37  | 天和 (tenhou)                        | yakuman  |
| 38  | 地和 (chiihou)                       | yakuman  |
| 39  | 大三元 (daisangen)                   | yakuman  |
| 40  | 四暗刻 (suuankou)                    | yakuman  |
| 41  | 四暗刻単騎 (suuankou tanki)          | yakuman  |
| 42  | 字一色 (tsuuiisou)                   | yakuman  |
| 43  | 緑一色 (ryuuiisou)                   | yakuman  |
| 44  | 清老頭 (chinroutou)                  | yakuman  |
| 45  | 九蓮宝燈 (chuuren)                   | yakuman  |
| 46  | 純正九蓮宝燈 (junsei chuuren)        | yakuman  |
| 47  | 国士無双 (kokushi)                   | yakuman  |
| 48  | 国士無双１３面待ち (kokushi 13-way)  | yakuman  |
| 49  | 大四喜 (daisuushii)                  | yakuman  |
| 50  | 小四喜 (shousuushii)                 | yakuman  |
| 51  | 四槓子 (suukantsu)                   | yakuman  |
| 52  | ドラ (dora)                          | bonus    |
| 53  | 裏ドラ (ura dora)                    | bonus    |
| 54  | 赤ドラ (aka dora)                    | bonus    |

`yaku` pairs the id with explicit han (han > 0). `yakuman` lists ids only —
each implies one yakuman multiplier; multiple ids in the list (e.g. dai-suushii
+ tsuuiisou) compound to multi-yakuman per the ruleset.

### Multi-ron

Multiple `<AGARI>` elements may appear consecutively for the same dealing-in
discard. When that happens, every element except the last carries the same
`fromWho` and partial `sc` deltas. The **final** `<AGARI>` of a multi-ron
includes the consolidated `owari` (if it ends the game) and the post-payment
scores.

Honba and riichi sticks go to the head-bump winner — the closest
counterclockwise to `fromWho`, which is the **first** `<AGARI>` emitted. Every
later element's `sc` deltas reflect the win value alone, even though its `ba`
still reports the round's honba. Treat `honba` as `0` for all but the first.

---

## Draw — `<RYUUKYOKU .../>`

Attributes:

| Attribute              | Meaning                                                        |
|------------------------|----------------------------------------------------------------|
| `type`                 | reason; absent for an exhaustive draw                          |
| `ba`                   | `honba, riichi_sticks` at round end                            |
| `sc`                   | post-payment per-seat `score, delta` pairs (units of 100)      |
| `hai0`…`hai3`          | revealed tenpai hands (omitted seats are noten)                |
| `owari`                | end-of-game scores; only on the final round                    |

### `type` values

| Value     | Meaning                                              |
|-----------|------------------------------------------------------|
| (omitted) | exhaustive draw with notenpai/tenpai redistribution  |
| `yao9`    | nine terminals/honors abortive draw                  |
| `reach4`  | four-riichi abortive draw                            |
| `ron3`    | triple-ron abortive draw                             |
| `kaze4`   | four-winds abortive draw                             |
| `kan4`    | four-kans-different-players abortive draw            |
| `nm`      | nagashi mangan (tenpai/noten payments may be applied) |

For exhaustive draws, only seats with `haiN` were tenpai. The standard
3000-point pool is split per ruleset; the exact deltas appear in `sc`.

---

## Tile encoding (0-135)

Every tile in the wall is identified by an integer `0..135`. Mapping:

```
base = tile_id // 4   (0..33)
copy = tile_id % 4    (0..3)
```

`base` follows the standard ordering:

| Range  | Suit / honors      |
|--------|--------------------|
| 0-8    | 1m - 9m            |
| 9-17   | 1p - 9p            |
| 18-26  | 1s - 9s            |
| 27-30  | E, S, W, N         |
| 31-33  | 白 (haku), 發 (hatsu), 中 (chun) |

Red 5s replace one specific copy of each 5:

| Tile id | Tile          |
|---------|---------------|
| 16      | red 5m (`0m`) |
| 52      | red 5p (`0p`) |
| 88      | red 5s (`0s`) |

A red 5 carries the same `base` as the regular 5 — distinguish on the literal
tile id, not on `base`. Other copies of 5m/5p/5s remain `base*4 + {1,2,3}`.

---

## Edge cases & quirks

- The XML is *not* whitespace-significant, but Tenhou writes events on
  separate lines with no surrounding indentation.
- `<INIT>` always carries exactly one initial dora indicator inside its
  `seed`. Subsequent indicators come from `<DORA>` events.
- Ura-dora indicators are **only** included in `<AGARI>` (`doraHaiUra`) when
  the win was a riichi-win. There is no `<DORA>` event for ura.
- After a kan (any of the four call kinds that produce a kan), a `<DORA>`
  event may appear immediately or after the rinshan draw — the timing
  (before-vs-after kan) follows the ruleset.
- For sanma (`<GO type>` bit `0x10`), seat 3 (`hai3`, `n3`, `W*`/`G*`) is
  absent. Manzu 2-8 are removed from the wall. Kita declarations appear as
  `<N m="..."/>` with the kita bit set.
- The `ten` attribute's second integer is the winner's **total** gain before
  honba and deposits — not the pre-multiplier base, and not a per-payer amount.
- The `sc` delta for the dealing-in seat (ron) or for non-winners (tsumo)
  always includes the honba portion; the deltas are the authoritative
  end-of-round score change. Ura-dora and aka-dora flags fold into the
  reported `yaku` list, not into separate fields.
- A true yakuman's `ten` fu is decorative and need not match a recomputed fu;
  its value comes from the yakuman tier alone.
- A `<BYE>`d player's discards are still emitted as normal `<D>`/`<E>`/etc.
  events — the disconnect only affects timing, not log structure.

---

## References

- The Tenhou viewer (XML form): `https://tenhou.net/5/mjlog2xml.cgi?<LOG_ID>`
- This format is consumed by [`jansou.io.mjlog`](../../src/jansou/io/mjlog.py),
  which provides `parse_mjlog` (returning a format-neutral `Paifu`) and
  `dump_mjlog`, for three- and four-player games alike. The chronological
  replay that turns a `RoundLog` into `AgariRecord`s (`replay_round`) lives in
  [`jansou.io.paifu`](../../src/jansou/io/paifu.py) and consumes the IR
  produced by every parser.
