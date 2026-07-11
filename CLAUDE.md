# CLAUDE.md

## Tooling

- Python 3.10+ (defined in `pyproject.toml`)
- uv (environments, packages)

## Commands

- Format: `uv run ruff format`
- Lint: `uv run ruff check`
- Test: `uv run pytest tests/`
- Coverage: `uv run pytest tests/ --cov`
- Validate: `uv run jansou-validate [-j N] <globs>`

## Code Conventions

- Always run formatter and linter after changes
- Unless requested, never change linter configuration
- When adding comments to the code, always keep the comments short, and never add unnecessary comment
- Use Google-style docstring

## Test and Coverage

- Always write tests, and run test after changes
- Always maintain 100% coverage

## Validation with Dataset

- Always validate against dataset
    - Dataset is considered as truth
    - Dataset is gitignored and not included in the repository

## Architecture

- `core/` tiles, notation, rules, hands — pure representation (frozen dataclasses).
- `analysis/` shanten, waits, efficiency — pure, stateless.
- `scoring/` fu, yaku, score — pure; `score(hand, tile, WinContext)` is the entry point.
- `game/` the engine; `state.py` is the only mutable layer, and `Environment`   holds the single seeded RNG (agents seed themselves).
- `io/` `paifu.py` defines the format-neutral `Paifu` IR + `replay_round`; every   reader/writer (mjlog, tenhou_json, mjai) and `from_game` route through it.   `replay.py` re-runs a `Paifu` through the engine as the live decision stream.
- `validation/` scores parsed logs against their recorded values.

Respect the layer boundaries: parsing enforces only surface grammar, hand validity lives in `hand.py`, positional legality lives only in the environment, and analysis/scoring are pure and stateless.
