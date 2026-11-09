# qt-app

Toy product for the quality-tracking prototype (plays the role of vws-graph).
Its answers are correct with a probability, like an LLM's:
`base_p × graph factor × knowledge quality × daily factor`. The graph factors
live here (`src/qt_app/quality.toml`), the knowledge quality in `qt-dep`
(`kb.json`), so either repository can cause a regression. The daily factor is
noise with no code or data change: each UTC day draws one overall and one
per-topic shift (`[daily_noise]` in `quality.toml`, 0 turns it off), shared by
every run that day. Each pytest run prints that day's factors in its header.

## Layout

- `tests/quality/`: the answer-quality cases (200, in 10 topics), replayed from
  simulated Langfuse traces in `qt-dep` (`traces.jsonl`); the trace id is the
  case's identity (`qt_case_key`). These are the only tests whose logs are
  analysed. Each case carries exactly one marker,
  `core` (5 topics) or `extended` (5 topics).
- `[tool.qt.run]` in `pyproject.toml`: splits a run into one pytest instance per marker (`-m core`,
  `-m extended`), each on its own CI runner with its own report log. The
  plugin stops the run if a case has no marker or more than one.
- `tests/qt_plugin.py`: pytest plugin that gives every case an identity (a
  module's `qt_case_key`, such as a trace id, else automatic; duplicates stop
  the run), repeats (`--qt-runs`), sharding
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
