# qt-app

Toy product for the quality-tracking prototype (plays the role of vws-graph).
Its answers are correct with a probability, like an LLM's:
`base_p × graph factor × knowledge quality`. The graph factors live here
(`src/qt_graph/quality.toml`), the knowledge quality in `qt-dep` (`kb.json`),
so either repository can cause a regression.

## Layout

- `tests/quality/`: the answer-quality cases (200, in 10 topics). These are the
  only tests whose logs are analysed. Each case carries exactly one marker,
  `core` (5 topics) or `extended` (5 topics).
- `[tool.qt.run]` in `pyproject.toml`: splits a run into one pytest instance per marker (`-m core`,
  `-m extended`), each on its own CI runner with its own report log. The
  plugin stops the run if a case has no marker or more than one.
- `tests/qt_plugin.py`: pytest plugin that gives every case an automatic
  identity (no IDs written by hand), repeats (`--qt-runs`), sharding
  (`--qt-shard i/n`) and a per-run seed (`--qt-seed`).
- `tests/infra/`: tests of the plugin and the run script.
- `scripts/qt_run.py`: produces a run (manifest + one gzipped pytest report log
  per shard) for qt-det.
- `[tool.qt.components]` in `pyproject.toml`: components whose content affects quality.

## Usage

```sh
uv sync
uv run pytest tests/infra                      # infrastructure tests
uv run python scripts/qt_run.py local --runs 10 --out qt-runs
uv run --project ../qt-det qt-det ingest --logs qt-runs --results ../qt-results
```

Other components are found through `QT_COMPONENT_<NAME>_PATH`, or as sibling
checkouts (`../qt-dep`).
