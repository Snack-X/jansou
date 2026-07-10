# Jansou

![License](https://img.shields.io/github/license/Snack-X/jansou)
[![PyPI](https://img.shields.io/pypi/v/jansou)](https://pypi.org/project/jansou/)
[![Python](https://img.shields.io/pypi/pyversions/jansou)](https://pypi.org/project/jansou/)
[![Coverage](https://raw.githubusercontent.com/Snack-X/jansou/python-coverage-comment-action-data/badge.svg)](https://github.com/Snack-X/jansou/tree/python-coverage-comment-action-data)

Python library for Riichi Mahjong environments and ugittilities.

- Conversion between multiple tile notations (MPSZ, MJAI, 136)
- Hand analysis (shanten, wait, efficiency, yaku, fu/han, score)
- Game environment for 3/4 player game with configurable rules
- Game replay log in multiple formats (mjlog XML, mjai JSONL, Tenhou JSON)

## Examples

### Notation

```python
from jansou.core.notation import parse_mpsz, dump_mpsz, parse_mjai, dump_mjai, parse_136, dump_136

tiles = parse_mpsz("667s 34668m 2357p 2z")

dump_mpsz(tiles)  # 34668m2357p667s2z
dump_mjai(tiles)  # 6s 6s 7s 3m 4m 6m 6m 8m 2p 3p 5p 7p S
dump_136(tiles)   # [92, 93, 96, 8, 12, 20, 21, 28, 40, 44, 53, 60, 112]
```

### Hand

```python
from jansou.core.tiles import Tile, TileKind
from jansou.core.notation import parse_mpsz
from jansou.core.hand import Hand
from jansou.analysis.efficiency import discard_evaluation
from jansou.analysis.shanten import shanten, is_tenpai
from jansou.analysis.waits import waits

# (1) shanten advancement

hand = Hand(parse_mpsz("279m 1569p 1168s 35z 2m"))

shanten(hand)    # 3
is_tenpai(hand)  # False

options = discard_evaluation(hand)  # sorted by shanten advancement and wider acceptance
                                    # in this case, 1p 9p 3z 5z are the best, with the same number of acceptance

best = options[0]
best.discard           # 1p
best.shanten           # 3
best.total_acceptance  # 20

# (2) acceptance

hand = Hand(parse_mpsz("567m 34567p 23489s 2z"))

options = discard_evaluation(hand)  # there are multiple options for 1 shanten

best = options[0]      # but the best option is 2z with the widest acceptance
best.discard           # 2z
best.shanten           # 1
best.total_acceptance  # 33

# (3) waits

hand = Hand(parse_mpsz("1112345678999m"))

waits(hand)  # 1m 2m 3m 4m 5m 6m 7m 8m 9m
```

### Score

```python
from jansou.core.tiles import Tile, TileKind, Wind
from jansou.core.notation import parse_mpsz
from jansou.core.rules import preset
from jansou.core.hand import Hand, Meld, MeldType, CallSource
from jansou.scoring.context import WinContext
from jansou.scoring.score import score

hand = Hand(
    parse_mpsz("77m 34p 055s 5p"),
    [
        Meld(MeldType.PON, parse_mpsz("111m"), Tile(TileKind.M1), CallSource.TOIMEN),
        Meld(MeldType.PON, parse_mpsz("777z"), Tile(TileKind.CHUN), CallSource.KAMICHA),
    ]
)

context = WinContext(
    rules=preset("m-league"),
    seat_wind=Wind.WEST,
    is_tsumo=False,
    dora_indicators=[Tile(TileKind.CHUN)],
)

result = score(hand, Tile(TileKind.P5), context)

result.yaku      # YAKUHAI_CHUN
result.han       # 2
result.fu.total  # 40 (20 + 4 + 4 + 4 = 32)

result.payment.total  # 2600

shanten(hand)    # 0
is_tenpai(hand)  # True
waits(hand)      # 2p, 5p
```

### Game environment, replay log

```python
from jansou.core.rules import preset
from jansou.game.environment import Environment
from jansou.game.agents import SmartEfficiencyAgent
from jansou.io.from_game import paifu_from_game
from jansou.io.tenhou_json import dump_tenhou_json_url

env = Environment(preset("tenhou"))
agents = [SmartEfficiencyAgent() for i in range(4)]

result = env.run(agents)

result.scores
result.ranking

paifu = paifu_from_game(env)
dump_tenhou_json_url(paifu)   # https://tenhou.net/6/#json=...
                              # can be viewed at https://mjv.snack.studio
```

## Development

```sh
uv sync

uv run pytest
uv run pytest --cov

uv run ruff format
uv run ruff check
```
