"""state_at, PR states and nightly listing against local git repositories."""

import json
import os
import subprocess
from datetime import UTC, datetime

import pytest
import qt_ci

COMPONENTS = {
    "graph": {"repo": "me/qt-graph", "branch": "main"},
    "data": {"repo": "me/qt-data", "branch": "main"},
}


def _git(path, *args, date=None):
    env = os.environ | {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}  # fmt: skip
    if date:
        env |= {"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True,
                          text=True, env=env).stdout.strip()  # fmt: skip


@pytest.fixture
def repos(tmp_path):
    """data d0 (01:00), graph g1 (02:00), data d1 (03:00), graph g2 (04:00); then clones."""
    origins, shas = {}, {}
    for name in COMPONENTS:
        origin = tmp_path / "origin" / name
        origin.mkdir(parents=True)
        _git(origin, "init", "-q", "-b", "main")
        origins[name] = origin
    for label, name, hour in [("d0", "data", 1), ("g1", "graph", 2), ("d1", "data", 3),
                              ("g2", "graph", 4)]:  # fmt: skip
        (origins[name] / "file").write_text(label)
        _git(origins[name], "add", ".")
        _git(origins[name], "commit", "-q", "-m", label, date=f"2026-11-01T0{hour}:00:00+00:00")
        shas[label] = _git(origins[name], "rev-parse", "HEAD")
    clones = {}
    for name, origin in origins.items():
        clones[name] = tmp_path / "clones" / name
        subprocess.run(["git", "clone", "-q", str(origin), str(clones[name])], check=True)
    return clones, shas


def _shas(state):
    return {name: v["sha"] for name, v in state.items()}


def test_state_at_takes_other_components_at_commit_time(repos):
    clones, sha = repos
    assert _shas(qt_ci.state_at("graph", sha["g2"], clones, COMPONENTS)) == {
        "graph": sha["g2"], "data": sha["d1"]}  # fmt: skip
    assert _shas(qt_ci.state_at("data", sha["d1"], clones, COMPONENTS)) == {
        "graph": sha["g1"], "data": sha["d1"]}  # fmt: skip
    state = qt_ci.state_at("graph", sha["g1"], clones, COMPONENTS)
    assert state["graph"]["committed_at"].startswith("2026-11-01T02:00:00")


def test_state_at_without_an_earlier_commit_fails(repos):
    clones, sha = repos
    with pytest.raises(qt_ci.NoState, match="graph has no commit"):
        qt_ci.state_at("data", sha["d0"], clones, COMPONENTS)


def test_pr_state_uses_main_heads_of_other_components(repos):
    clones, sha = repos
    assert _shas(qt_ci.pr_state("data", sha["d0"], clones, COMPONENTS)) == {
        "graph": sha["g2"], "data": sha["d0"]}  # fmt: skip


def test_nightly_lists_unevaluated_states_oldest_first(repos):
    clones, sha = repos
    now = datetime(2026, 11, 2, tzinfo=UTC)
    index = [_index_line("complete", "nightly", {"graph": sha["g1"], "data": sha["d0"]})]

    # d0 has no graph commit before it, so the window starts after it
    states, backlog = qt_ci.nightly_states(
        clones, COMPONENTS, index, lookback_days=1, max_states=1, run_prefix="graph-9", now=now
    )

    assert backlog == 1
    assert [s["created_by"] for s in states] == ["data"]
    assert states[0]["run_id"] == "graph-9-n1"
    assert _shas(json.loads(states[0]["state"])) == {"graph": sha["g1"], "data": sha["d1"]}


def _index_line(status, kind, shas):
    components = {
        name: {"repo": "x", "sha": sha, "committed_at": "t"} for name, sha in shas.items()
    }
    return json.dumps(
        {"status": status, "manifest": {"kind": kind, "state": {"components": components}}}
    )


def test_nightly_evaluated_ignores_incomplete_and_non_nightly_runs():
    lines = [
        _index_line("incomplete", "nightly", {"graph": "a"}),
        _index_line("complete", "main", {"graph": "b"}),
        _index_line("complete", "nightly", {"graph": "c"}),
        "",
    ]
    assert qt_ci.nightly_evaluated(lines) == {qt_ci.state_id({"graph": "c"})}


def test_pr_info_names_the_merge_parent_as_baseline(repos):
    clones, sha = repos
    # a merge commit on data whose first parent is d1
    data = clones["data"]
    _git(data, "checkout", "-q", "-b", "feature", sha["d0"])
    (data / "other").write_text("x")
    _git(data, "add", ".")
    _git(data, "commit", "-q", "-m", "feature", date="2026-11-01T05:00:00+00:00")
    _git(data, "checkout", "-q", "--detach", sha["d1"])
    _git(data, "merge", "-q", "--no-ff", "-m", "merge", "feature", date="2026-11-01T06:00:00+00:00")
    merge = _git(data, "rev-parse", "HEAD")

    state = qt_ci.pr_state("data", merge, clones, COMPONENTS)
    pr = qt_ci.pr_info("data", merge, state, clones, repo="me/qt-data", number=4, head_sha="h")

    assert pr["merge_sha"] == merge and pr["number"] == 4
    assert pr["baseline"] == {"graph": sha["g2"], "data": sha["d1"]}
