"""pytest plugin for quality tracking: case identity, repeats, sharding, seeds.

Tests stay plain pytest. For every test item the plugin records, as report
user_properties (exported by `--report-log`):

- qt_case_key: hash of the test's path, qualified name and parameter values
- qt_definition_hash: hash of the test's body (without decorators, comments or
  formatting) plus its module's optional `qt_definition(**params)` (data that
  defines what a correct result is)
- qt_family: the module's optional `qt_family(**params)`, else the test name
- qt_repeat, qt_hrid

Options: --qt-runs N, --qt-shard i/n, --qt-seed S, and --qt-markers a,b to
require that every test carries exactly one of these markers (so selecting
one marker per pytest instance runs every test exactly once).
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import inspect
import json
import random
import re
import textwrap
from pathlib import Path
from typing import Any

import pytest

REPEAT = "qt_repeat"
_REPEAT_ID = re.compile(r"(?<=\[)qt\d+-|-qt\d+(?=\]$)|(?<=\[)qt\d+(?=\]$)")


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("qt", "quality tracking")
    group.addoption("--qt-runs", type=int, default=1, help="runs per test case")
    group.addoption("--qt-shard", default="1/1", help="run shard i of n, e.g. 2/4")
    group.addoption("--qt-seed", type=int, default=0, help="seed of this run")
    group.addoption(
        "--qt-markers",
        default="",
        help="comma-separated markers; every test must carry exactly one of them",
    )


def _shard(config: pytest.Config) -> tuple[int, int]:
    raw = config.getoption("qt_shard")
    try:
        index, total = (int(x) for x in raw.split("/"))
    except ValueError:
        raise pytest.UsageError(f"--qt-shard must look like 2/4, got {raw!r}") from None
    if not 1 <= index <= total:
        raise pytest.UsageError(f"--qt-shard {raw}: need 1 <= i <= n")
    return index, total


@pytest.fixture
def qt_repeat(request: pytest.FixtureRequest) -> int:
    return request.param


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    runs = metafunc.config.getoption("qt_runs")
    if runs < 1:
        raise pytest.UsageError("--qt-runs must be at least 1")
    if REPEAT not in metafunc.fixturenames:
        metafunc.fixturenames.append(REPEAT)
    metafunc.parametrize(REPEAT, range(runs), indirect=True, ids=[f"qt{i}" for i in range(runs)])


def _encode(value: Any) -> Any:
    if isinstance(value, Path):
        return {"file_sha256": hashlib.sha256(value.read_bytes()).hexdigest()}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    raise TypeError(f"{type(value).__name__} has no stable JSON form")


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=_encode, ensure_ascii=False)


def function_source(function: Any) -> str:
    """The function without decorators, normalised: markers, parametrization,
    comments and formatting do not change what a test checks."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    node = tree.body[0]
    assert isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    node.decorator_list = []
    return ast.unparse(node)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _module_hook(item: pytest.Function, name: str, params: dict[str, Any]) -> Any:
    """Call the module's optional `name(**params)` if it accepts this test's parameters."""
    hook = getattr(item.module, name, None)
    if hook is None:
        return None
    try:
        inspect.signature(hook).bind(**params)
    except TypeError:
        return None
    return hook(**params)


def identity(item: pytest.Function) -> dict[str, Any]:
    callspec = getattr(item, "callspec", None)
    all_params = callspec.params if callspec else {}
    params = {k: v for k, v in all_params.items() if k != REPEAT}
    module = item.path.relative_to(item.config.rootpath).as_posix()
    name = f"{module}::{item.function.__qualname__}"
    definition = _module_hook(item, "qt_definition", params)
    try:
        params_json = canonical(params)
        definition_json = canonical(definition) if definition is not None else ""
    except TypeError as error:
        raise pytest.UsageError(f"{item.nodeid}: parameter {error}") from None
    return {
        "qt_case_key": _sha(f"{name}\n{params_json}")[:16],
        "qt_definition_hash": _sha(function_source(item.function) + "\n" + definition_json)[:16],
        "qt_family": _module_hook(item, "qt_family", params) or name,
        "qt_repeat": all_params.get(REPEAT, 0),
        "qt_hrid": _REPEAT_ID.sub("", item.name).removesuffix("[]"),
    }


def _check_markers(config: pytest.Config, items: list[pytest.Item]) -> None:
    required = {m for m in config.getoption("qt_markers").split(",") if m}
    if not required:
        return
    wrong = []
    for item in items:
        found = {m.name for m in item.iter_markers()} & required
        if len(found) != 1:
            wrong.append(f"{item.nodeid} ({', '.join(sorted(found)) or 'none'})")
    if wrong:
        shown = "; ".join(wrong[:5]) + (f" and {len(wrong) - 5} more" if len(wrong) > 5 else "")
        raise pytest.UsageError(f"every test needs exactly one of {sorted(required)}: {shown}")


# tryfirst: see every item before `-m` deselects the other markers.
@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    _check_markers(config, items)
    index, total = _shard(config)
    keep, drop = [], []
    for item in items:
        if not isinstance(item, pytest.Function):
            keep.append(item)
            continue
        props = identity(item)
        item.user_properties.extend(props.items())
        (keep if int(props["qt_case_key"], 16) % total == index - 1 else drop).append(item)
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = keep


@pytest.fixture
def qt_seed(request: pytest.FixtureRequest) -> int:
    return request.config.getoption("qt_seed")


@pytest.fixture
def qt_rng(request: pytest.FixtureRequest, qt_seed: int) -> random.Random:
    """Random stream for this run, case and repeat; independent across runs."""
    props = dict(request.node.user_properties)
    digest = hashlib.sha256(f"{qt_seed}:{props['qt_case_key']}:{props['qt_repeat']}".encode())
    return random.Random(int.from_bytes(digest.digest()[:8]))
