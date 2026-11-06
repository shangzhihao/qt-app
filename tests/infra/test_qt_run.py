import gzip
import json
import subprocess

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


def test_local_run_produces_a_complete_sharded_run(tmp_path):
    qt_run.main(
        [
            "local",
            "--run-id",
            "r1",
            "--runs",
            "2",
            "--shards",
            "3",
            "--seed",
            "7",
            "--out",
            str(tmp_path),
        ]
    )
    run_dir = tmp_path / "r1"
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["state"]["key"] == qt_run.state_key(
        {n: v["sha"] for n, v in manifest["state"]["components"].items()}
    )
    assert set(manifest["state"]["components"]) == {"graph", "data"}

    cases: dict[str, int] = {}
    for i in (1, 2, 3):
        lines = gzip.open(run_dir / f"shard-{i}-of-3.jsonl.gz", "rt").read().splitlines()
        entries = [json.loads(line) for line in lines]
        assert entries[-1]["$report_type"] == "SessionFinish"
        for e in entries:
            if e.get("$report_type") == "TestReport" and e["when"] == "call":
                key = dict(e["user_properties"])["qt_case_key"]
                cases[key] = cases.get(key, 0) + 1
    assert len(cases) == 200
    assert set(cases.values()) == {2}
