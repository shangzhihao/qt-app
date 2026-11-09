"""A toy "answerer" whose correctness is random, like an LLM's.

The chance of a correct answer is `base_p x graph factor x knowledge quality`:
the graph factor comes from this repo (quality.toml), the knowledge quality
from the data component (kb.json), so either repo can cause a regression.
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


def knowledge(data_path: Path | None = None) -> dict[str, float]:
    if data_path is None:
        configured = os.environ.get(DATA_PATH_ENV)
        if not configured:
            raise RuntimeError(f"{DATA_PATH_ENV} must point at a qt-dep checkout")
        data_path = Path(configured)
    return json.loads((data_path / "kb.json").read_text())["topics"]


def answer(
    question: Question,
    correct: str,
    base_p: float,
    rng: random.Random,
    kb: dict[str, float],
) -> str:
    p = base_p * graph_factors().get(question.topic, 1.0) * kb.get(question.topic, 0.0)
    return correct if rng.random() < p else f"wrong answer to {question.text}"
