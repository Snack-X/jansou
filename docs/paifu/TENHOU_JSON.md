# Tenhou JSON Paifu Format

A complete game record (paifu) is encoded as JSON embedded in a Tenhou viewer URL:

```
https://tenhou.net/6/#json=<JSON>&ts=0
```

The `<JSON>` portion is URL-encoded. Unescaped, it parses as a single object with the structure described below.

---

## Top-level

```ts
interface TenhouJSON {
  title: [string, string];        // venue / lobby strings
  name:  [string, string, ...];   // player names; length 3 (sanma) or 4 (yonma)

  rule: {
    disp?: string;                // human-readable rule name (preferred)
    name?: string;                // legacy rule name (fallback)
    aka?:   0 | 1 | 2 | 3 | 4;    // total number of red-five tiles in the wall
    aka51?: 0 | 1 | 2 | 3 | 4;    // red 5m count (overrides `aka`)
    aka52?: 0 | 1 | 2 | 3 | 4;    // red 5p count
    aka53?: 0 | 1 | 2 | 3 | 4;    // red 5s count
  };

  log: Round[];                   // one entry per round played
}
```

The number of players is fixed for the whole game and must be inferred from `name.length`. Every `Round` entry carries `1 + 3*num_players + 4` array slots (see below).

---

## Round layout

A round is a flat heterogeneous array. Index positions are fixed:

```
[
  RoundInfo,                  // 0
  Scores,                     // 1
  DoraIndicators,             // 2
  UraDoraIndicators,          // 3

  // Per player, in seat order (E, S, W, [N]):
  InitialHand,                // 4 + 3*p + 0
  DrawEvents,                 // 4 + 3*p + 1
  DiscardEvents,              // 4 + 3*p + 2

  Result,                     // last index
]
```

### `RoundInfo` — `[round_id, honba, riichi_sticks]`

- `round_id`: `0..3` = East 1-4, `4..7` = South 1-4, `8..11` = West 1-4.
  Round wind is `(round_id // 4) + 1` (1=E, 2=S, 3=W); dealer seat is `round_id % 4`.
- `honba`: number of honba sticks at round start.
- `riichi_sticks`: number of 1000-point sticks pooled on the table at round start
  (carried over from previous rounds; the spec calls this "Deposit").

### `Scores` — `[number, ...]`

Per-seat point totals at round start. Length matches `num_players`.

### `DoraIndicators`, `UraDoraIndicators` — `Tile[]`

Variable length. The first element is the initial dora indicator; subsequent
elements are kan-dora reveals in chronological order. Ura-dora is recorded for
every round (revealed only on a riichi win in actual play).

### `Result`

See the *Result* section below.

---

## Tile codes

Tiles are encoded as two-digit decimal integers. The first digit is the suit,
the second is the rank:

| Suit          | First digit | Range   | Meaning                          |
|---------------|-------------|---------|----------------------------------|
| Manzu (萬子)  | `1`         | `11-19` | 1m - 9m                          |
| Pinzu (筒子)  | `2`         | `21-29` | 1p - 9p                          |
| Souzu (索子)  | `3`         | `31-39` | 1s - 9s                          |
| Zihai (字牌)  | `4`         | `41-47` | E, S, W, N, 白 (haku), 發 (hatsu), 中 (chun) |

Red fives use dedicated codes:

| Code | Tile         |
|------|--------------|
| `51` | red 5m (0m)  |
| `52` | red 5p (0p)  |
| `53` | red 5s (0s)  |

A regular `15` and a red `51` may both appear in a hand simultaneously when
multiple copies of 5 exist.

### Tsumogiri sentinel

The integer `60` may appear **only inside discard arrays** and means "the tile
just drawn this turn" (tsumogiri). The actual tile id has to be recovered from
the matching draw event. `60` is never a valid tile code anywhere else.

---

## Per-player event arrays

For each player, three arrays appear consecutively: the initial hand, the
draws, and the discards.

### `InitialHand` — `Tile[]`

The 13 tiles dealt to the player at the start of the round. The dealer's
14th tile is the *first* entry of their `DrawEvents` array (not part of the
initial hand).

### `DrawEvents` — `(Tile | MeldString)[]`

One entry per turn the player took the lead. Either:

- **Integer**: a tile drawn from the wall or rinshan (the format does not
  distinguish; rinshan must be inferred from a preceding `m` / `a` / `k` call).
- **String**: a meld call this player made *to begin the turn*: chi (`c`),
  pon (`p`), or daiminkan (`m`). The call letter and tile ordering encode
  which player the call came from (see *Meld strings* below).

### `DiscardEvents` — `(Tile | 60 | 0 | MeldString)[]`

One entry per turn the player took. The entry types:

- **Integer tile** (`11`-`53`): a discard chosen from the closed hand.
- **`60`**: tsumogiri — discard of the just-drawn tile.
- **`0`**: placeholder meaning "this draw was called by another player, so no
  discard happened." The matching opponent's draw event holds the call.
- **String starting with `r`**: a riichi declaration. The remainder is the
  discard tile in normal form (e.g. `r34` = riichi discarding 4s,
  `r60` = riichi tsumogiri).
- **String starting with `a` or `k`**: ankan (`a`) or shouminkan / kakan (`k`)
  declared in place of a discard. The string encodes the four meld tiles
  (see *Meld strings*); after an ankan/kakan the player draws a rinshan tile
  next, which appears as the next entry in their `DrawEvents`.

The draw and discard arrays are read in lockstep: `DrawEvents[i]` and
`DiscardEvents[i]` together describe turn `i` for that player.

---

## Reconstructing turn order

Turn order is **not stored**. It must be rebuilt by walking seat to seat from
the dealer, and diverting out of the normal rotation whenever a discard is
claimed. A seat's `DrawEvents` index doubles as its turn counter: a seat that
is skipped (because someone else called) consumes no entry.

A call is matched to a discard by *both* the called tile and the source seat,
which the meld string's letter position encodes. Matching on the tile alone
confuses two seats discarding the same tile in turn.

Even so, the pairing is genuinely ambiguous in two ways, because nothing in
the format says *which* discard a pending call claimed.

### Two calls pending on one tile

A seat may discard the same tile twice, leaving a chi and a pon both waiting on
it — one for each copy. **The pon is the earlier claim.** Had the chi come
first, the ponning seat would have drawn in between, so its pending entry would
be that draw rather than a call string. This also matches the priority a live
table gives a pon over a chi.

### A call pending across two identical discards

A player may *decline* a call and take the same call on a later, identical
discard from the same seat. If intervening calls skip that player's turn, its
call string is still the pending entry at both discards, and the earlier one
must be passed over. Hand contents cannot decide this — the declining player
may well have held the tiles all along.

The disambiguating invariant is:

> A seat holding a pending call has not acted since. Reaching such a seat by
> the **normal rotation** is therefore impossible.

So a walk that consumes a draw-slot call string without having claimed it has
taken a wrong branch, and must back up and pass on the earlier claim instead. A
round whose calls no turn order explains is malformed.

---

## Meld strings

A meld is a string consisting of two-digit tile codes interleaved with a single
lowercase letter that identifies the call type:

| Letter | Call                       | Tile count | Source                        |
|--------|----------------------------|------------|-------------------------------|
| `c`    | chi (sequence)             | 3          | left player's discard         |
| `p`    | pon (triplet)              | 3          | any opponent's discard        |
| `m`    | daiminkan (open quad)      | 4          | any opponent's discard        |
| `a`    | ankan (closed quad)        | 4          | self                          |
| `k`    | shouminkan / kakan         | 4          | self (upgrade of prior pon)   |

The **position of the call letter** within the string identifies which
opponent the call came from, and the tile immediately adjacent to the letter
is the called tile. Specifically:

- `c<aa><bb><cc>`: chi from the **kamicha** (left). `aa` is the called tile;
  `bb` and `cc` are taken from the caller's hand. Chi can only come from the
  left, so the letter is always at position 0.
- `<aa>p<bb><cc>`, `p<aa><bb><cc>`, `<aa><bb>p<cc>`: pon. The letter's
  position relative to the start identifies the source seat
  (kamicha / toimen / shimocha). The adjacent tile is the called tile;
  the others are from the caller's hand.
- `<aa>m<bb><cc><dd>` (and rotations): daiminkan. Same positional convention
  as pon; three tiles come from the caller's hand.
- `<aa><bb><cc><dd>a`: ankan. All four tiles share one tile id and all come
  from the caller's hand. The letter's position is conventionally trailing;
  parsers should not rely on it for source identification (there is none).
- `<aa><bb>k<cc><dd>`: shouminkan. Three of the tiles match an existing pon
  the caller previously made; the fourth is the newly added tile. The letter
  position matches the pon's original source seat.

Parsers that only need to reconstruct the player's hand can ignore the letter
position and use the call type plus the tile codes:

| Call          | Tiles from hand | Tiles from elsewhere |
|---------------|-----------------|----------------------|
| chi           | 2               | 1 (called)           |
| pon           | 2               | 1 (called)           |
| daiminkan     | 3               | 1 (called)           |
| ankan         | 4               | 0                    |
| shouminkan    | 1 (added)       | 3 (existing pon)     |

---

## Result

The final entry in a round's array is itself an array describing the outcome.

### Agari (win)

```
["和了", deltas, agari, deltas, agari, ...]
```

The leading `"和了"` tag appears **once**, followed by alternating `deltas` and
`agari` pairs. A single pair encodes a normal win; two or three pairs encode
double or triple ron (in which case the resulting state has multiple winners
sharing the same dealing-in player).

#### `deltas` — `[number, ...]`

Per-seat score delta. Length matches `num_players`. Includes honba bonus and
riichi-stick payouts.

Every point a payer loses the winner gains, so **the deltas sum to exactly the
deposits the winner sweeps off the table** — never to zero on a win. Those
deposits are not only `RoundInfo.riichi_sticks`: a riichi declared *during* the
round is collected too, and the round-start count alone understates them.

The win value (before honba and deposits) is therefore:

```
value = deltas[winner] - sum(deltas) - honba_total
honba_total = (num_players - 1) * 100 * honba      # 300 in yonma, 200 in sanma
```

Reading the winner's own delta this way holds for a tsumo and a ron alike, and
— unlike inspecting a single payer — survives pao (see below). In a multi-ron,
honba and deposits go to the first `(deltas, agari)` pair only; use `honba = 0`
for the rest.

#### `agari` — `[winner, ron, pao, score_string, ...yaku_strings]`

- `winner`: 0-indexed seat of the winner.
- `ron`: 0-indexed seat of the player whose discard fed the win. **Equal to
  `winner` for tsumo.**
- `pao`: 0-indexed seat of the pao-responsibility player (sekinin barai).
  **Equal to `winner` when no pao applies.** Under pao the ron payment is split
  between the dealing-in seat and the liable seat, so **no single payer's delta
  equals the win value** — recover it from the winner's delta as shown above.
- `score_string`: see below. May be an empty string in third-party dumps that
  cannot rebuild it; the deltas remain authoritative.
- `yaku_strings`: zero or more strings, each describing one yaku and its han
  contribution. See below.

### Ryuukyoku (no win)

```
[kind]               // when no payments occurred (e.g. abortive draw)
[kind, deltas]       // when payments occurred (tenpai/noten, nagashi mangan)
```

The `kind` tag is one of:

| Tag         | Meaning                                                    |
|-------------|------------------------------------------------------------|
| `流局`      | exhaustive draw (regular round end with no win)            |
| `全員聴牌`  | all-tenpai exhaustive draw (no transfers)                  |
| `全員不聴`  | all-noten exhaustive draw                                  |
| `流し満貫`  | nagashi mangan                                             |
| `九種九牌`  | nine terminals/honors abortive draw                        |
| `三家和`    | triple ron abort (ruleset-dependent)                       |
| `三家和了`  | alternate notation for triple ron abort                    |
| `四風連打`  | four winds discarded on first uchi (abortive)              |
| `四開槓`    | four kans by different players (abortive)                  |
| `四家立直`  | four players in riichi (abortive)                          |

---

## Score strings

The fourth element of `agari` is a free-form string like
`"30符4飜8000点"` or `"役満 32000点"`. Parsers should pull out three things:

1. The points text — substring matching `/\d+点(?:∀)?/` or `/\d+-\d+点(?:∀)?/`.
   The `∀` suffix marks dealer tsumo (each non-dealer pays the same amount).
2. Han and fu, using the patterns:

   | Pattern                           | han | fu |
   |-----------------------------------|-----|----|
   | `<fu>符<han>飜`                    | as parsed | as parsed |
   | `満貫`                             | 5   | 0  |
   | `跳満`                             | 6   | 0  |
   | `倍満`                             | 8   | 0  |
   | `三倍満`                           | 11  | 0  |
   | `役満`                             | 13  | 0  |
   | `数え役満`                         | 13  | 0  |

   When the tier name is present, fu is not displayed (use 0 or treat as
   unknown). For `役満` and `数え役満`, the score-string han is capped at 13;
   the *actual* han total may be higher and should be recovered by summing
   the per-yaku han values from `yaku_strings`.

---

## Yaku strings

Each yaku string takes one of two forms:

```
<jp_name>(<n>飜)
<jp_name>(役満)        // also (数え役満) for counted yakuman
```

The `<jp_name>` may be a fixed canonical string (e.g. `立直`, `平和`,
`断幺九`) or one of several yakuhai variants:

| String form              | Maps to                       |
|--------------------------|-------------------------------|
| `場風 <wind>`            | round-wind yakuhai            |
| `自風 <wind>`            | seat-wind yakuhai             |
| `役牌 <tile>`            | dragon yakuhai                |
| `中` / `白` / `發`       | bare-dragon yakuhai shorthand |

Per-instance yaku — `dora`, `aka_dora`, `ura_dora`, `nuki_dora`, and
`yakuhai` — record their *total* han in a single string (e.g. `ドラ(2飜)`)
even though the underlying hand has multiple instances. Validators that
compare against per-instance yaku results need to expand each such string
into `n` copies.

---

## Edge cases & quirks

- The dora-indicator array starts with **one** indicator; additional
  indicators appear as kans are called during the round. Ura-dora is recorded
  for every round, even when no riichi-win occurred.
- The dealer's first draw is the first integer of their `DrawEvents`, not the
  14th tile of `InitialHand`.
- A `0` in a discard array is **never** a tile; it is a placeholder for a
  turn that ended in another player's call.
- Riichi-prefixed discards (`r…`) may carry `60` (tsumogiri) as the inner
  payload: `r60` = "declare riichi, tsumogiri".
- After an ankan or kakan, the next entry in the same player's `DrawEvents`
  is the rinshan tile drawn. There is no explicit marker that distinguishes
  it from a wall draw.
- Triple ron (`和了` with three `(deltas, agari)` pairs) is the win form;
  triple-ron-aborts use the `三家和` / `三家和了` ryuukyoku tag instead.
- The dora array records *what* was revealed but not *when*. A round's kan-dora
  reveals cannot be placed back among the turns; a reader that rebuilds an event
  stream can only append them.
- The format has no place for the sanma North bonus, so it records
  four-player games only.

---

## References

- Tenhou viewer entry point: `https://tenhou.net/6/`
- This format is consumed by
  [`jansou.io.tenhou_json`](../../src/jansou/io/tenhou_json.py), which provides
  `parse_tenhou_json` (returning a format-neutral `Paifu`), `dump_tenhou_json`,
  and `dump_tenhou_json_url` (the viewer URL above).
  The chronological replay that turns a `RoundLog` into `AgariRecord`s
  (`replay_round`) lives in [`jansou.io.paifu`](../../src/jansou/io/paifu.py)
  and consumes the IR produced by every parser.
