"""Answer-quality cases: each passes with a probability, like an LLM evaluation."""

import tomllib
from pathlib import Path

import pytest

from qt_graph.answer import Question, answer

with (Path(__file__).parent / "cases.toml").open("rb") as f:
    CASES = {c["id"]: c for c in tomllib.load(f)["case"]}

QUESTIONS = [{"id": c["id"], "topic": c["topic"], "text": c["question"]} for c in CASES.values()]


def qt_definition(question):
    """What counts as correct for this question; editing it changes the definition hash."""
    case = CASES[question["id"]]
    return {"expected": case["expected"], "base_p": case["base_p"]}


def qt_family(question):
    return question["topic"]


@pytest.mark.parametrize("question", QUESTIONS, ids=lambda q: q["id"])
def test_answer(question, qt_rng, kb):
    case = CASES[question["id"]]
    got = answer(
        Question(question["topic"], question["text"]), case["expected"], case["base_p"], qt_rng, kb
    )
    assert got == case["expected"]
