"""A toy "answerer" whose correctness is random, like an LLM's.

The chance of a correct answer is
`base_p x graph factor x knowledge quality x daily factor`: the graph factor
comes from this repo (quality.toml), the knowledge quality from the data
component (kb.json), so either repo can cause a regression. The daily factor is
noise shared by every run on the same UTC day, like a model or infrastructure
that behaves a little differently each day. The
questions themselves are Langfuse traces in the data component (traces.jsonl).
The answer text itself does not matter; only whether it matches `correct`.
"""

from __future__ import annotations

import json
import os
import random
import tomllib
from dataclasses import dataclass
from datetime import UTC, date, datetime
from functools import cache
from pathlib import Path

DATA_PATH_ENV = "QT_COMPONENT_DATA_PATH"


@dataclass(frozen=True)
class Question:
    topic: str
    text: str


@cache
def _quality() -> dict:
    with (Path(__file__).parent / "quality.toml").open("rb") as f:
        return tomllib.load(f)


def graph_factors() -> dict[str, float]:
    return _quality()["factors"]


def daily_factor(topic: str, day: date | None = None) -> float:
    """1 + an overall and a per-topic shift, both drawn from the date (UTC, default today)."""
    noise = _quality().get("daily_noise", {})
    day = day or datetime.now(UTC).date()
    overall = random.Random(f"{day}").gauss(0.0, noise.get("overall", 0.0))
    per_topic = random.Random(f"{day}:{topic}").gauss(0.0, noise.get("topic", 0.0))
    return max(0.0, 1.0 + overall + per_topic)


def data_path() -> Path:
    """The data component: QT_COMPONENT_DATA_PATH, else a sibling qt-dep checkout."""
    configured = os.environ.get(DATA_PATH_ENV)
    return Path(configured) if configured else Path(__file__).resolve().parents[3] / "qt-dep"


def knowledge(path: Path | None = None) -> dict[str, float]:
    return json.loads(((path or data_path()) / "kb.json").read_text())["topics"]


def traces(path: Path | None = None) -> list[dict]:
    """The answer-quality cases: simulated Langfuse traces, one JSON object per line."""
    lines = ((path or data_path()) / "traces.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def answer(
    question: Question,
    correct: str,
    base_p: float,
    rng: random.Random,
    kb: dict[str, float],
) -> str:
    p = (base_p * graph_factors().get(question.topic, 1.0) * kb.get(question.topic, 0.0)
         * daily_factor(question.topic))  # fmt: skip
    return correct if rng.random() < p else f"wrong answer to {question.text}"
