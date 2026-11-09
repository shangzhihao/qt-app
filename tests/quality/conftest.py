import os
from pathlib import Path

import pytest

from qt_graph.answer import DATA_PATH_ENV, knowledge


@pytest.fixture(scope="session")
def kb(pytestconfig: pytest.Config) -> dict[str, float]:
    """Knowledge from the data component; defaults to a sibling qt-dep checkout."""
    configured = os.environ.get(DATA_PATH_ENV)
    path = Path(configured) if configured else pytestconfig.rootpath.parent / "qt-dep"
    return knowledge(path)
