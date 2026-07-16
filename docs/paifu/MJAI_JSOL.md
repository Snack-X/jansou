# MJAI JSONL — jansou's dialect

This document specifies exactly what `jansou.io.mjai` reads and writes: the
base MJAI log vocabulary, the tolerances the reader extends to other dialects,
and the extensions the writer adds so that a game — rules, identity, and
standing included — re-parses whole. It is kept in sync with the
implementation; when the reader or writer changes, this document changes in
the same commit.

The base protocol is the community MJAI convention (one JSON object per line,
in play order). Where dialects disagree, the reference points are the
[Cryolite schemas](https://github.com/Cryolite/mjai),
[RiichiEnv](https://github.com/smly/RiichiEnv), and Tenhou-converted logs.
Every jansou extension is an *optional additional key* on a standard object:
a jansou-written log is a valid MJAI stream for consumers that ignore unknown
keys, and streams without the extensions parse exactly as before.

## Stream shape

```
{"type":"start_game", ...}          once, first (reader: optional)
{"type":"start_kyoku", ...}         one block per round
  ... play events ...
{"type":"hora", ...} (1..n)  |  {"type":"ryukyoku", ...}
{"type":"end_kyoku"}
{"type":"end_game", ...}            once, last (reader: optional)
```

The reader requires at least one `start_kyoku`; a round without its
`end_kyoku` (a truncated stream) is dropped. Blank lines are ignored.
Gzip-compressed input is decompressed transparently.

## `start_game`

Written:

```json
{"type":"start_game","names":["a","b","c","d"],"preset":"tenhou","rules":{...}}
```

- `names` — the player names per seat (`Paifu.names`); omitted when unknown.
  Standard key (Cryolite).
- `preset` — the name of the rule preset the configuration equals
  (`Paifu.preset`); omitted when the configuration matches no preset.
  jansou extension.
- `rules` — the **full rules configuration**, always written: every field of
  `jansou.core.rules.Rules` as a flat object of snake_case keys, with
  `game_length` as a wind letter (`"E"`, `"S"`, `"W"`, `"N"`). jansou
  extension.

Read, in order of precedence:

1. `rules` present — the configuration is taken outright. Keys the library
   does not know are dropped (a log written by a newer jansou still parses);
   absent keys keep the `Rules` baseline defaults.
2. `preset` present (without `rules`) — the named preset is used.
3. Neither — the Tenhou preset matching the table size is inferred
   (`tenhou` / `tenhou-3p`), as for any foreign MJAI log.

The resolved rules must agree with the dealt hands: a `rules`/`preset` naming
three players over a four-hand `start_kyoku` is rejected (`MjaiError`).
`Paifu.preset` is filled from the `preset` key or, failing that, by matching
the resolved rules against the known presets.

## `start_kyoku`

Standard fields, all read and written: `bakaze`, `kyoku`, `honba`, `kyotaku`,
`oya`, `dora_marker`, `scores`, `tehais`. **`kyotaku` counts sticks** (one
riichi deposit = 1), never points; `scores` are points.

## Play events

Standard vocabulary, read and written: `tsumo`, `dahai` (with `tsumogiri`),
`chi`, `pon`, `daiminkan`, `kakan`, `ankan`, `reach`, `dora`, `kita` (3p).

- `reach` precedes the declaring `dahai`.
- `reach_accepted` (`{"type":"reach_accepted","actor":n}`) is **written**
  immediately after every riichi discard that banks its deposit — that is,
  every declaring discard except one the round ends on by ron (a winning ron,
  or the three rons of a triple-ron abort), which never pays. The reader
  ignores the event: acceptance is derivable, and the writer's placement
  reproduces it. Emitting it keeps external viewers' deposit accounting
  exact.

## `hora`

Written: `actor`, `target`, `deltas`, `uradora_markers` when ura were
revealed, and `pao` — the seat answering for the win under liability — when
one applied (jansou extension; no surveyed dialect has a field for it).
Read: the same, with `ura_markers` accepted as an alias (RiichiEnv). No
han/fu/yaku breakdown is carried — no surveyed dialect logs one — so the win
value is recovered from the winner's side of `deltas` (deltas sum to the
swept deposits; subtracting that sum and the honba from the winner's gain
leaves the value, pao splits included).

Delta semantics (shared with mjlog's `sc`): `deltas` carry the outcome's
transfers — payments, honba, and the deposits the win sweeps — but **not**
the riichi-declaration payments, which happen at acceptance. On a multiple
ron, each `hora` carries its own deltas; the pot and honba ride the first.

## `ryukyoku`

Written: `reason` (canonical, below), `deltas`, and `tehais` (revealed hands
as `[]`, concealed as `null`) when tenpai is known.

Read: `reason` is normalized to the canonical kinds through the alias table;
a reason-less draw (Tenhou-converted logs) is classified from the stream —
in place of a discard it is `yao9`; with the wall spent, `exhaustive`;
otherwise the abort its own events account for (`kan4`, `reach4`, `kaze4`),
with a triple ron (`ron3`) as the remainder no count explains.

Canonical kinds and accepted aliases:

| canonical | meaning | read aliases |
|---|---|---|
| `exhaustive` | wall exhausted | `exhaustive_draw`, `流局`, `全員聴牌`, `全員不聴` |
| `nm` | nagashi mangan | `nagashi`, `nagashimangan`, `nagashi_mangan`, `流し満貫` |
| `yao9` | nine terminals | `nine_terminals`, `kyushu_kyuhai`, `kyushukyuhai`, `九種九牌` |
| `reach4` | four riichi | `four_riichi`, `suucha_riichi`, `四家立直` |
| `ron3` | triple ron | `triple_ron`, `sanchaho`, `sanchahou`, `三家和`, `三家和了` |
| `kaze4` | four winds | `four_winds`, `sufuurenta`, `suufon_renda`, `四風連打` |
| `kan4` | four kans | `four_kans`, `suukansansen`, `suukaikan`, `四開槓` |

An unrecognized reason is kept verbatim. `ron3` matters for settlement: a
riichi discard the triple ron fell on never banks its deposit.

## `end_game`

Written with the final standing when the record carries one:

```json
{"type":"end_game","scores":[30000,20000,25000,25000]}
```

`scores` follows RiichiEnv's server protocol. On read, a stated `scores`
becomes `Paifu.final_scores` outright; without it, the standing is settled
from the rounds — the last round's settled scores plus the end-of-game
deposit settlement the rules prescribe (leftover deposits to first place,
where the rule set says so). For jansou-written logs the two are equal by
the score-chain invariant.

## What is deliberately not carried

- **Hidden tiles** — the unrevealed dead wall, undrawn live wall, and
  non-riichi ura never enter the stream. `start_game.seed` (Cryolite) is the
  reserved home for reproducibility metadata if it is ever wanted; jansou
  does not write it today.
- **Uma / placement bonuses** (`Paifu.final_points`) — unmodeled; `scores`
  are raw points.
