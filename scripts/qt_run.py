"""Produce quality-tracking runs: a manifest plus one gzipped report log per shard.

    python scripts/qt_run.py manifest --run-id ID --kind main --runs 1 --shards 2 --out DIR
    python scripts/qt_run.py shard --manifest DIR/ID/manifest.json --index 1
    python scripts/qt_run.py local --kind main --runs 1 --shards 2 --out qt-runs

CI calls `manifest` once and `shard` once per runner; `local` does both on one
machine. Component versions come from `--state` (CI) or from the local
checkouts: qt-graph itself, and every other component in components.toml from
QT_COMPONENT_<NAME>_PATH or a sibling directory named after its repo.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = 1
QUALITY_TESTS = "tests/quality"


def state_key(shas: dict[str, str]) -> str:
    """Must match the analyzer's definition: sorted `name=sha` lines, SHA-256."""
    text = "\n".join(f"{name}={sha}" for name, sha in sorted(shas.items()))
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def load_components(root: Path = ROOT) -> dict[str, dict[str, str]]:
    with (root / "components.toml").open("rb") as f:
        return tomllib.load(f)["components"]


def env_name(component: str) -> str:
    return f"QT_COMPONENT_{component.upper()}_PATH"


def component_paths(components: dict[str, dict[str, str]], root: Path = ROOT) -> dict[str, Path]:
    paths = {}
    for name, spec in components.items():
        if name == "graph":
            paths[name] = root
        elif configured := os.environ.get(env_name(name)):
            paths[name] = Path(configured)
        else:
            paths[name] = root.parent / spec["repo"].rsplit("/", 1)[-1]
    return paths


def _git(path: Path, *args: str) -> str | None:
    result = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def local_version(path: Path) -> tuple[str, str]:
    """(sha, committed_at) of a checkout; uncommitted changes get a content hash."""
    head = _git(path, "rev-parse", "HEAD")
    dirty = _git(path, "status", "--porcelain")
    if head and not dirty:
        return head, _git(path, "show", "-s", "--format=%cI", "HEAD") or _now()
    files = (_git(path, "ls-files", "--cached", "--others", "--exclude-standard") or "").split()
    digest = hashlib.sha256()
    for name in sorted(files):
        file = path / name
        if file.is_file():
            digest.update(name.encode() + b"\0" + file.read_bytes() + b"\0")
    return f"{(head or 'none')[:12]}+dirty.{digest.hexdigest()[:12]}", _now()


def make_manifest(
    *,
    run_id: str,
    kind: str,
    versions: dict[str, dict[str, str]],
    created_by: str | None,
    runs: int,
    shards: int,
    seed: int,
    pr: dict[str, Any] | None = None,
    producer: dict[str, str] | None = None,
) -> dict[str, Any]:
    shas = {name: v["sha"] for name, v in versions.items()}
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "kind": kind,
        "created_at": _now(),
        "state": {"key": state_key(shas), "created_by": created_by, "components": versions},
        "pr": pr,
        "runs_per_case": runs,
        "shards": shards,
        "seed": seed,
        "producer": producer or _producer(),
        "tools": {"python": sys.version.split()[0]},
    }


def _producer() -> dict[str, str]:
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return {"repo": "local", "run_url": ""}
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repo = os.environ["GITHUB_REPOSITORY"]
    run = os.environ["GITHUB_RUN_ID"]
    return {"repo": repo, "run_url": f"{server}/{repo}/actions/runs/{run}"}


def local_versions(components: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    versions = {}
    for name, path in component_paths(components).items():
        sha, committed_at = local_version(path)
        versions[name] = {
            "repo": components[name]["repo"],
            "sha": sha,
            "committed_at": committed_at,
        }
    return versions


def write_manifest(out: Path, manifest: dict[str, Any]) -> Path:
    run_dir = out / manifest["run_id"]
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    return path


def run_shard(manifest_path: Path, index: int, root: Path = ROOT) -> Path:
    """Run one shard of the quality tests and store its gzipped report log next to the manifest."""
    manifest = json.loads(manifest_path.read_text())
    shards = manifest["shards"]
    env = os.environ.copy()
    for name, path in component_paths(load_components(root), root).items():
        if name != "graph":
            env.setdefault(env_name(name), str(path))
    with tempfile.TemporaryDirectory() as tmp:
        log = Path(tmp) / "report.jsonl"
        command = [
            sys.executable, "-m", "pytest", QUALITY_TESTS, "-q", "-p", "no:cacheprovider",
            f"--qt-runs={manifest['runs_per_case']}", f"--qt-shard={index}/{shards}",
            f"--qt-seed={manifest['seed']}", f"--report-log={log}",
        ]  # fmt: skip
        result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True)
        # 0 = all passed, 1 = some failed, 5 = empty shard: all are results.
        if result.returncode not in (0, 1, 5):
            sys.stderr.write(result.stdout + result.stderr)
            raise SystemExit(f"pytest crashed in shard {index}/{shards} ({result.returncode})")
        out = manifest_path.parent / f"shard-{index}-of-{shards}.jsonl.gz"
        with log.open("rb") as src, gzip.open(out, "wb") as dst:
            shutil.copyfileobj(src, dst)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="qt_run")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("manifest", "local"):
        p = sub.add_parser(name)
        p.add_argument("--run-id")
        p.add_argument("--kind", choices=["pr", "main", "nightly"], default="main")
        p.add_argument("--runs", type=int, default=1)
        p.add_argument("--shards", type=int, default=2)
        p.add_argument("--out", type=Path, default=ROOT / "qt-runs")
        p.add_argument("--created-by", default="graph")
        p.add_argument("--seed", type=int, help="default: random")
        p.add_argument("--state", help='JSON {name: {"repo", "sha", "committed_at"}}')
        p.add_argument("--pr", help="JSON PR info for kind=pr")
    p_shard = sub.add_parser("shard")
    p_shard.add_argument("--manifest", type=Path, required=True)
    p_shard.add_argument("--index", type=int, required=True)

    args = parser.parse_args(argv)
    if args.command == "shard":
        print(run_shard(args.manifest, args.index))
        return 0

    seed = args.seed if args.seed is not None else random.SystemRandom().randrange(2**31)
    versions = json.loads(args.state) if args.state else local_versions(load_components())
    pr = json.loads(args.pr) if args.pr else None
    run_id = args.run_id or f"local-{datetime.now(UTC):%Y%m%dT%H%M%S}-{seed % 10000:04d}"
    manifest = make_manifest(
        run_id=run_id,
        kind=args.kind,
        versions=versions,
        created_by=None if args.kind == "pr" else args.created_by,
        runs=args.runs,
        shards=args.shards,
        seed=seed,
        pr=pr,
    )
    path = write_manifest(args.out, manifest)
    print(path)
    if args.command == "local":
        for index in range(1, args.shards + 1):
            print(run_shard(path, index))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
