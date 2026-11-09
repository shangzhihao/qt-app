"""Answer-quality cases replayed from Langfuse traces: each passes with a probability,
like an LLM evaluation. The trace id is the case's identity."""

import pytest

from qt_app.answer import Question, answer, traces

TRACES = {t["id"]: t for t in traces()}

# Every case carries exactly one of the markers in [tool.qt.run]; CI runs one
# pytest instance per marker.
CORE_TOPICS = {"benefits", "deadlines", "eligibility", "forms", "income"}

QUESTIONS = [
    pytest.param(
        trace_id,
        id=t["metadata"]["hrid"],
        marks=pytest.mark.core if t["metadata"]["topic"] in CORE_TOPICS else pytest.mark.extended,
    )
    for trace_id, t in TRACES.items()
]


def qt_case_key(trace_id):
    return trace_id


def qt_definition(trace_id):
    """What counts as correct for this trace; editing it changes the definition hash."""
    t = TRACES[trace_id]
    return {"input": t["input"], "expected": t["output"], "base_p": t["metadata"]["base_p"]}


def qt_family(trace_id):
    return TRACES[trace_id]["metadata"]["topic"]


@pytest.mark.parametrize("trace_id", QUESTIONS)
def test_answer(trace_id, qt_rng, kb):
    t = TRACES[trace_id]
    topic, expected = t["metadata"]["topic"], t["output"]["answer"]
    got = answer(Question(topic, t["input"]["question"]), expected, t["metadata"]["base_p"],
                 qt_rng, kb)  # fmt: skip
    assert got == expected
