import gzip
import json
import subprocess

import pytest
import qt_run


def test_state_key_matches_the_analyzer_contract():
    # Same vector as the analyzer: sha256 of "data=d1\ngraph=g1".
    assert qt_run.state_key({"graph": "g1", "data": "d1"}) == (
        "sha256:" + __import__("hashlib").sha256(b"data=d1\ngraph=g1").hexdigest()
    )


def test_local_version_of_clean_and_dirty_checkouts(tmp_path):
    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    (tmp_path / "kb.json").write_text("{}")
    dirty_uncommitted, _ = qt_run.local_version(tmp_path)
    assert dirty_uncommitted.startswith("none+dirty.")

    git("add", ".")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x")
    head = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert qt_run.local_version(tmp_path)[0] == head

    (tmp_path / "kb.json").write_text('{"a": 1}')
    first, _ = qt_run.local_version(tmp_path)
    (tmp_path / "kb.json").write_text('{"a": 2}')
    second, _ = qt_run.local_version(tmp_path)
    assert first.startswith(head[:12] + "+dirty.") and first != second


def _calls(path):
    entries = [json.loads(line) for line in gzip.open(path, "rt").read().splitlines()]
    assert entries[-1]["$report_type"] == "SessionFinish"
    return [
        (dict(e["user_properties"]), e["keywords"])
        for e in entries
        if e.get("$report_type") == "TestReport" and e["when"] == "call"
    ]


def test_local_run_has_one_pytest_instance_per_marker(tmp_path):
    qt_run.main(["local", "--run-id", "r1", "--runs", "2", "--seed", "7", "--out", str(tmp_path)])
    run_dir = tmp_path / "r1"
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["state"]["key"] == qt_run.state_key(
        {n: v["sha"] for n, v in manifest["state"]["components"].items()}
    )
    assert set(manifest["state"]["components"]) == {"graph", "data"}
    assert manifest["shards"] == 2
    assert manifest["shard_markers"] == ["core", "extended"]

    seen: dict[str, int] = {}
    for index, marker in enumerate(manifest["shard_markers"], start=1):
        calls = _calls(run_dir / f"shard-{index}-of-2.jsonl.gz")
        assert len(calls) == 100 * 2  # 100 cases of this marker, 2 runs each
        assert all(marker in keywords for _, keywords in calls)
        for props, _ in calls:
            seen[props["qt_case_key"]] = seen.get(props["qt_case_key"], 0) + 1
    assert len(seen) == 200
    assert set(seen.values()) == {2}


def test_shards_must_match_the_markers(capsys):
    with pytest.raises(SystemExit):
        qt_run.main(["manifest", "--shards", "3", "--out", "unused"])
    assert "--shards must match" in capsys.readouterr().err


def test_without_markers_runs_split_by_hash(tmp_path):
    manifest = qt_run.make_manifest(
        run_id="r", kind="main", versions={"graph": {"repo": "x", "sha": "s", "committed_at": "t"}},
        created_by="graph", runs=1, shards=3, seed=1,
    )  # fmt: skip
    assert "shard_markers" not in manifest
    with pytest.raises(ValueError, match="3 shards for 2 markers"):
        qt_run.make_manifest(
            run_id="r", kind="main", versions={}, created_by="graph", runs=1, shards=3, seed=1,
            markers=["core", "extended"],
        )  # fmt: skip
