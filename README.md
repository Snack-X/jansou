# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/Snack-X/jansou/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                  |    Stmts |     Miss |   Branch |   BrPart |    Cover |   Missing |
|-------------------------------------- | -------: | -------: | -------: | -------: | -------: | --------: |
| src/jansou/\_\_init\_\_.py            |        2 |        0 |        0 |        0 |     100% |           |
| src/jansou/analysis/\_\_init\_\_.py   |        0 |        0 |        0 |        0 |     100% |           |
| src/jansou/analysis/decompose.py      |      120 |        0 |       30 |        0 |     100% |           |
| src/jansou/analysis/efficiency.py     |       76 |        0 |       28 |        0 |     100% |           |
| src/jansou/analysis/shanten.py        |       53 |        0 |        8 |        0 |     100% |           |
| src/jansou/analysis/waits.py          |        6 |        0 |        0 |        0 |     100% |           |
| src/jansou/core/\_\_init\_\_.py       |        0 |        0 |        0 |        0 |     100% |           |
| src/jansou/core/hand.py               |      103 |        0 |       38 |        0 |     100% |           |
| src/jansou/core/notation.py           |       95 |        0 |       46 |        0 |     100% |           |
| src/jansou/core/rules.py              |       80 |        0 |       10 |        0 |     100% |           |
| src/jansou/core/tiles.py              |      171 |        0 |       24 |        0 |     100% |           |
| src/jansou/game/\_\_init\_\_.py       |        0 |        0 |        0 |        0 |     100% |           |
| src/jansou/game/actions.py            |      280 |        0 |       86 |        0 |     100% |           |
| src/jansou/game/agents.py             |      204 |        0 |       64 |        0 |     100% |           |
| src/jansou/game/environment.py        |       58 |        0 |       10 |        0 |     100% |           |
| src/jansou/game/events.py             |       76 |        0 |        2 |        0 |     100% |           |
| src/jansou/game/flow.py               |      515 |        0 |      168 |        0 |     100% |           |
| src/jansou/game/progression.py        |       63 |        0 |       26 |        0 |     100% |           |
| src/jansou/game/state.py              |       75 |        0 |        6 |        0 |     100% |           |
| src/jansou/game/wall.py               |       40 |        0 |        8 |        0 |     100% |           |
| src/jansou/io/\_\_init\_\_.py         |        0 |        0 |        0 |        0 |     100% |           |
| src/jansou/io/from\_game.py           |       64 |        0 |       22 |        0 |     100% |           |
| src/jansou/io/mjai.py                 |      111 |        0 |       36 |        0 |     100% |           |
| src/jansou/io/mjlog.py                |      280 |        0 |       84 |        0 |     100% |           |
| src/jansou/io/paifu.py                |      183 |        0 |       38 |        0 |     100% |           |
| src/jansou/io/replay.py               |      222 |        0 |       78 |        0 |     100% |           |
| src/jansou/io/tenhou\_json.py         |      209 |        0 |       70 |        0 |     100% |           |
| src/jansou/io/tiles.py                |       34 |        0 |       12 |        0 |     100% |           |
| src/jansou/scoring/\_\_init\_\_.py    |        0 |        0 |        0 |        0 |     100% |           |
| src/jansou/scoring/context.py         |       28 |        0 |        0 |        0 |     100% |           |
| src/jansou/scoring/fu.py              |       85 |        0 |       32 |        0 |     100% |           |
| src/jansou/scoring/score.py           |      129 |        0 |       26 |        0 |     100% |           |
| src/jansou/scoring/yaku.py            |      268 |        0 |      106 |        0 |     100% |           |
| src/jansou/validation/\_\_init\_\_.py |        0 |        0 |        0 |        0 |     100% |           |
| src/jansou/validation/check.py        |       27 |        0 |        4 |        0 |     100% |           |
| src/jansou/validation/cli.py          |      129 |        0 |       44 |        0 |     100% |           |
| **TOTAL**                             | **3786** |    **0** | **1106** |    **0** | **100%** |           |


## Setup coverage badge

Below are examples of the badges you can use in your main branch `README` file.

### Direct image

[![Coverage badge](https://raw.githubusercontent.com/Snack-X/jansou/python-coverage-comment-action-data/badge.svg)](https://htmlpreview.github.io/?https://github.com/Snack-X/jansou/blob/python-coverage-comment-action-data/htmlcov/index.html)

This is the one to use if your repository is private or if you don't want to customize anything.

### [Shields.io](https://shields.io) Json Endpoint

[![Coverage badge](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/Snack-X/jansou/python-coverage-comment-action-data/endpoint.json)](https://htmlpreview.github.io/?https://github.com/Snack-X/jansou/blob/python-coverage-comment-action-data/htmlcov/index.html)

Using this one will allow you to [customize](https://shields.io/endpoint) the look of your badge.
It won't work with private repositories. It won't be refreshed more than once per five minutes.

### [Shields.io](https://shields.io) Dynamic Badge

[![Coverage badge](https://img.shields.io/badge/dynamic/json?color=brightgreen&label=coverage&query=%24.message&url=https%3A%2F%2Fraw.githubusercontent.com%2FSnack-X%2Fjansou%2Fpython-coverage-comment-action-data%2Fendpoint.json)](https://htmlpreview.github.io/?https://github.com/Snack-X/jansou/blob/python-coverage-comment-action-data/htmlcov/index.html)

This one will always be the same color. It won't work for private repos. I'm not even sure why we included it.

## What is that?

This branch is part of the
[python-coverage-comment-action](https://github.com/marketplace/actions/python-coverage-comment)
GitHub Action. All the files in this branch are automatically generated and may be
overwritten at any moment.