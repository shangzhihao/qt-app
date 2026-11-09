"""CI helpers: which state to test, and checking components out at that state.

    python scripts/qt_ci.py clone --dest .qt-clones
    python scripts/qt_ci.py state-at --clones .qt-clones --component data --sha SHA
    python scripts/qt_ci.py pr-state --clones .qt-clones --component data --sha MERGE_SHA
    python scripts/qt_ci.py nightly-states --clones .qt-clones --run-prefix graph-123
    python scripts/qt_ci.py checkout --state JSON --dest components

Every trigger computes its state with `state_at`: the triggering version plus,
for every other component, the version that was on its main branch at the
triggering version's commit time. So a merge always maps to the same state,
whenever its job happens to run, and the nightly lists the same states.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from qt_run import env_name, load_components, state_id

INDEX_URL = "https://raw.githubusercontent.com/shangzhihao/qt-det/qt-results/index/runs.jsonl"

State = dict[str, dict[str, str]]  # name -> {repo, sha, committed_at}


class NoState(Exception):
    """Another component had no commit on its main branch yet at that time."""


def git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def clone_all(dest: Path, components: dict[str, dict[str, str]]) -> dict[str, Path]:
    clones = {}
    for name, spec in components.items():
        path = dest / name
        if path.exists():
            git(path, "fetch", "-q", "origin")
        else:
            url = f"https://github.com/{spec['repo']}.git"
            subprocess.run(
                ["git", "clone", "-q", "--filter=blob:none", "--no-checkout", url, str(path)],
                check=True,
            )
        clones[name] = path
    return clones


def _main_ref(spec: dict[str, str]) -> str:
    return f"refs/remotes/origin/{spec['branch']}"


def committed_at(clone: Path, sha: str) -> str:
    return git(clone, "show", "-s", "--format=%cI", sha)


def state_at(
    component: str, sha: str, clones: dict[str, Path], components: dict[str, dict[str, str]]
) -> State:
    """The main state created by `sha` of `component`."""
    at = committed_at(clones[component], sha)
    state = {}
    for name, spec in components.items():
        if name == component:
            version = sha
        else:
            version = git(clones[name], "rev-list", "-1", "--first-parent", f"--before={at}",
                          _main_ref(spec))  # fmt: skip
            if not version:
                raise NoState(f"{name} has no commit on {spec['branch']} before {at}")
        state[name] = {
            "repo": spec["repo"],
            "sha": version,
            "committed_at": committed_at(clones[name], version),
        }
    return state


def pr_state(
    component: str, merge_sha: str, clones: dict[str, Path], components: dict[str, dict[str, str]]
) -> State:
    """A PR state: the PR's merge commit plus every other component at its main head."""
    state = {}
    for name, spec in components.items():
        if name == component:
            git(clones[name], "fetch", "-q", "origin", merge_sha)
            version = merge_sha
        else:
            version = git(clones[name], "rev-parse", _main_ref(spec))
        state[name] = {
            "repo": spec["repo"],
            "sha": version,
            "committed_at": committed_at(clones[name], version),
        }
    return state


def pr_info(
    component: str,
    merge_sha: str,
    state: State,
    clones: dict[str, Path],
    *,
    repo: str,
    number: int,
    head_sha: str,
) -> dict[str, Any]:
    """PR details for the manifest, including the main state to compare with:
    the PR state with the PR's component at the merge commit's first parent."""
    base = git(clones[component], "rev-parse", f"{merge_sha}^1")
    return {
        "component": component,
        "repo": repo,
        "number": number,
        "head_sha": head_sha,
        "merge_sha": merge_sha,
        "baseline": {name: v["sha"] for name, v in state.items()} | {component: base},
    }


def main_states_since(
    since: datetime, clones: dict[str, Path], components: dict[str, dict[str, str]]
) -> list[tuple[str, State]]:
    """(created_by, state) for every main commit of every component since `since`, oldest first."""
    events = []
    for name, spec in components.items():
        out = git(clones[name], "log", "--first-parent", "--format=%H %cI",
                  f"--since={since.isoformat()}", _main_ref(spec))  # fmt: skip
        for line in out.splitlines():
            sha, at = line.split()
            events.append((datetime.fromisoformat(at), name, sha))
    events.sort()
    states = []
    for _, name, sha in events:
        try:
            states.append((name, state_at(name, sha, clones, components)))
        except NoState:
            continue  # before every component existed: no state to test
    return states


def nightly_evaluated(index_lines: list[str]) -> set[str]:
    """Ids (`state_id`) of states that already have a complete nightly run."""
    keys = set()
    for line in index_lines:
        if not line.strip():
            continue
        entry = json.loads(line)
        manifest = entry.get("manifest") or {}
        if entry.get("status") == "complete" and manifest.get("kind") == "nightly":
            components = manifest["state"]["components"]
            keys.add(state_id({name: c["sha"] for name, c in components.items()}))
    return keys


def fetch_index(url: str = INDEX_URL) -> list[str]:
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            return response.read().decode().splitlines()
    except urllib.error.HTTPError as error:
        if error.code == 404:  # no results branch yet
            return []
        raise


def nightly_states(
    clones: dict[str, Path],
    components: dict[str, dict[str, str]],
    index_lines: list[str],
    *,
    lookback_days: int,
    max_states: int,
    run_prefix: str,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """States in the lookback window without a nightly run, oldest first; and the backlog."""
    since = (now or datetime.now(UTC)) - timedelta(days=lookback_days)
    done = nightly_evaluated(index_lines)
    todo = []
    for created_by, state in main_states_since(since, clones, components):
        key = state_id({n: v["sha"] for n, v in state.items()})
        if key not in done and all(t["key"] != key for t in todo):
            todo.append({"key": key, "created_by": created_by, "state": state})
    selected = todo[:max_states]
    for i, item in enumerate(selected, start=1):
        item["run_id"] = f"{run_prefix}-n{i}"
        item["state"] = json.dumps(item["state"])
    return selected, len(todo) - len(selected)


def checkout(state: State, dest: Path, components: dict[str, dict[str, str]]) -> dict[str, Path]:
    """Check out every component except graph at its version; return their paths."""
    paths = {}
    for name, version in state.items():
        if name == "graph":
            continue
        path = dest / name
        if not path.exists():
            path.mkdir(parents=True)
            git(path, "init", "-q")
            git(
                path,
                "remote",
                "add",
                "origin",
                f"https://github.com/{components[name]['repo']}.git",
            )
        git(path, "fetch", "-q", "--depth=1", "origin", version["sha"])
        git(path, "checkout", "-q", "--detach", version["sha"])
        paths[name] = path.resolve()
    return paths


def _github_output(**values: str) -> None:
    out = os.environ.get("GITHUB_OUTPUT")
    lines = "".join(f"{k}={v}\n" for k, v in values.items())
    if out:
        with open(out, "a") as f:
            f.write(lines)
    print(lines, end="")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="qt_ci")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("clone")
    p.add_argument("--dest", type=Path, default=Path(".qt-clones"))
    for name in ("state-at", "pr-state"):
        p = sub.add_parser(name)
        p.add_argument("--clones", type=Path, default=Path(".qt-clones"))
        p.add_argument("--component", required=True)
        p.add_argument("--sha", required=True, help="pushed commit, or a PR's merge commit")
        if name == "pr-state":
            p.add_argument("--repo", required=True, help="repository of the PR")
            p.add_argument("--number", type=int, required=True)
            p.add_argument("--head", required=True, help="PR head commit")
    p = sub.add_parser("nightly-states")
    p.add_argument("--clones", type=Path, default=Path(".qt-clones"))
    p.add_argument("--run-prefix", required=True)
    p.add_argument("--lookback-days", type=int, default=7)
    p.add_argument("--max-states", type=int, default=20)
    p.add_argument("--index-url", default=INDEX_URL)
    p = sub.add_parser("checkout")
    p.add_argument("--state", required=True)
    p.add_argument("--dest", type=Path, default=Path("components"))

    args = parser.parse_args(argv)
    components = load_components()
    if args.command == "clone":
        clone_all(args.dest, components)
        return 0
    if args.command == "checkout":
        paths = checkout(json.loads(args.state), args.dest, components)
        env_file = os.environ.get("GITHUB_ENV")
        for name, path in paths.items():
            line = f"{env_name(name)}={path}\n"
            if env_file:
                with open(env_file, "a") as f:
                    f.write(line)
            print(line, end="")
        return 0
    clones = {name: args.clones / name for name in components}
    if args.command == "nightly-states":
        states, backlog = nightly_states(
            clones, components, fetch_index(args.index_url),
            lookback_days=args.lookback_days, max_states=args.max_states,
            run_prefix=args.run_prefix,
        )  # fmt: skip
        _github_output(states=json.dumps(states), count=str(len(states)), backlog=str(backlog))
        return 0
    if args.command == "pr-state":
        state = pr_state(args.component, args.sha, clones, components)
        pr = pr_info(args.component, args.sha, state, clones, repo=args.repo,
                     number=args.number, head_sha=args.head)  # fmt: skip
        _github_output(state=json.dumps(state), pr=json.dumps(pr))
        return 0
    try:
        state = state_at(args.component, args.sha, clones, components)
    except NoState as error:
        raise SystemExit(str(error)) from None
    _github_output(state=json.dumps(state))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
