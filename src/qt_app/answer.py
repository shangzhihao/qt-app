"""A toy "answerer" whose correctness is random, like an LLM's.

The chance of a correct answer is `base_p x graph factor x knowledge quality`:
the graph factor comes from this repo (quality.toml), the knowledge quality
from the data component (kb.json), so either repo can cause a regression. The
questions themselves are Langfuse traces in the data component (traces.jsonl).
The answer text itself does not matter; only whether it matches `correct`.
"""

from __future__ import annotations

import json
import os
import random
import tomllib
from dataclasses import dataclass
from functools import cache
from pathlib import Path

DATA_PATH_ENV = "QT_COMPONENT_DATA_PATH"


@dataclass(frozen=True)
class Question:
    topic: str
    text: str


@cache
def graph_factors() -> dict[str, float]:
    with (Path(__file__).parent / "quality.toml").open("rb") as f:
        return tomllib.load(f)["factors"]


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
    p = base_p * graph_factors().get(question.topic, 1.0) * kb.get(question.topic, 0.0)
    return correct if rng.random() < p else f"wrong answer to {question.text}"
