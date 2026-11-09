import json

import pytest

SUITE = """
import pytest

DEFINITION = {"a": 1, "b": 2}

def qt_definition(x):
    return DEFINITION[x]

def qt_family(x):
    return "fam-" + x

@pytest.mark.parametrize("x", ["a", "b"])
def test_param(x, qt_rng):
    assert qt_rng.random() < 2

def test_plain():
    pass
"""


def run(pytester: pytest.Pytester, *args: str) -> list[dict]:
    log = pytester.path / "log.jsonl"
    result = pytester.runpytest("-p", "qt_plugin", f"--report-log={log}", *args)
    # 5 = no tests collected: a shard of a tiny suite can be empty
    assert result.ret in (0, 1, 5), result.stdout.str()
    if not log.exists():
        return []
    calls = []
    for line in log.read_text().splitlines():
        entry = json.loads(line)
        if entry.get("$report_type") == "TestReport" and entry["when"] == "call":
            calls.append(dict(entry["user_properties"]) | {"nodeid": entry["nodeid"]})
    return calls


def keys(calls):
    return {c["qt_case_key"] for c in calls}


def test_every_item_gets_identity(pytester):
    pytester.makepyfile(test_suite=SUITE)
    calls = run(pytester)
    assert len(calls) == 3
    assert len(keys(calls)) == 3
    by_hrid = {c["qt_hrid"]: c for c in calls}
    assert set(by_hrid) == {"test_param[a]", "test_param[b]", "test_plain"}
    assert by_hrid["test_param[a]"]["qt_family"] == "fam-a"
    assert by_hrid["test_plain"]["qt_family"] == "test_suite.py::test_plain"


def test_repeats_share_the_case_key(pytester):
    pytester.makepyfile(test_suite=SUITE)
    calls = run(pytester, "--qt-runs=4")
    assert len(calls) == 12
    assert len(keys(calls)) == 3
    assert sorted({c["qt_repeat"] for c in calls}) == [0, 1, 2, 3]


def test_shards_are_disjoint_and_complete(pytester):
    pytester.makepyfile(test_suite=SUITE)
    everything = keys(run(pytester))
    shards = [keys(run(pytester, f"--qt-shard={i}/3", "--qt-runs=2")) for i in (1, 2, 3)]
    assert set().union(*shards) == everything
    assert sum(len(s) for s in shards) == len(everything)


def test_identity_is_stable_and_follows_edits(pytester):
    pytester.makepyfile(test_suite=SUITE)
    first = {c["qt_hrid"]: c for c in run(pytester)}
    pytester.makepyfile(test_suite=SUITE.replace('{"a": 1, "b": 2}', '{"a": 1, "b": 3}'))
    second = {c["qt_hrid"]: c for c in run(pytester)}
    for hrid in first:
        assert first[hrid]["qt_case_key"] == second[hrid]["qt_case_key"]
    assert (
        first["test_param[a]"]["qt_definition_hash"]
        == second["test_param[a]"]["qt_definition_hash"]
    )
    assert (
        first["test_param[b]"]["qt_definition_hash"]
        != second["test_param[b]"]["qt_definition_hash"]
    )


def test_unstable_parameter_is_a_usage_error(pytester):
    pytester.makepyfile(
        test_suite="""
import pytest

@pytest.mark.parametrize("x", [object()])
def test_obj(x):
    pass
"""
    )
    result = pytester.runpytest("-p", "qt_plugin")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*no stable JSON form*"])


def test_seed_changes_outcomes_between_runs(pytester):
    pytester.makepyfile(
        test_suite="""
def test_draw(qt_rng):
    print("DRAW", qt_rng.random())
"""
    )
    draws = []
    for seed in (1, 1, 2):
        result = pytester.runpytest("-p", "qt_plugin", f"--qt-seed={seed}", "-s")
        draws.append([line[line.index("DRAW") :] for line in result.stdout.lines if "DRAW" in line])
    assert draws[0]
    assert draws[0] == draws[1]
    assert draws[0] != draws[2]


MARKED = """
import pytest

@pytest.mark.parametrize("x", [
    pytest.param("a", marks=pytest.mark.core),
    pytest.param("b", marks=pytest.mark.extended),
    {extra}
])
def test_marked(x):
    pass
"""


@pytest.mark.parametrize(
    "extra, problem",
    [
        ("pytest.param('c')", "none"),
        ("pytest.param('d', marks=[pytest.mark.core, pytest.mark.extended])", "core, extended"),
    ],
)
def test_every_test_needs_exactly_one_shard_marker(pytester, extra, problem):
    pytester.makepyfile(test_suite=MARKED.format(extra=extra))
    result = pytester.runpytest("-p", "qt_plugin", "--qt-markers=core,extended", "-m", "core")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines([f"*exactly one of*({problem})*"])


def test_one_instance_per_marker_covers_every_test_once(pytester):
    pytester.makepyfile(test_suite=MARKED.format(extra=""))
    seen = []
    for marker in ("core", "extended"):
        calls = run(pytester, "--qt-markers=core,extended", "-m", marker)
        seen += [c["qt_hrid"] for c in calls]
    assert sorted(seen) == ["test_marked[a]", "test_marked[b]"]


def test_definition_ignores_decorators_comments_and_formatting(pytester):
    pytester.makepyfile(test_suite=SUITE)
    first = {c["qt_hrid"]: c["qt_definition_hash"] for c in run(pytester)}
    changed = SUITE.replace(
        '@pytest.mark.parametrize("x", ["a", "b"])',
        '@pytest.mark.core\n@pytest.mark.parametrize(  # comment\n    "x", ["a", "b"]\n)',
    ).replace(
        "    assert qt_rng.random() < 2", "    # same check\n    assert qt_rng.random()  <  2"
    )
    pytester.makepyfile(test_suite=changed)
    pytester.makeini("[pytest]\nmarkers =\n    core: c\n")
    assert {c["qt_hrid"]: c["qt_definition_hash"] for c in run(pytester)} == first

    pytester.makepyfile(test_suite=SUITE.replace("< 2", "< 3"))
    second = {c["qt_hrid"]: c["qt_definition_hash"] for c in run(pytester)}
    assert second["test_param[a]"] != first["test_param[a]"]


KEYED = """
import pytest

def qt_case_key(x):
    return {"a": "trace-a", "b": "trace-b", "c": "trace-a"}[x]

@pytest.mark.parametrize("x", {xs})
def test_param(x):
    pass
"""


def test_a_module_can_name_the_case_key(pytester):
    pytester.makepyfile(test_suite=KEYED.replace("{xs}", '["a", "b"]'))
    calls = run(pytester, "--qt-runs=2")
    assert keys(calls) == {"trace-a", "trace-b"}


def test_two_cases_with_one_key_stop_the_run(pytester):
    pytester.makepyfile(test_suite=KEYED.replace("{xs}", '["a", "c"]'))
    result = pytester.runpytest("-p", "qt_plugin")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*case key trace-a is used by*"])
