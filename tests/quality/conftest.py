import pytest

from qt_app.answer import knowledge


@pytest.fixture(scope="session")
def kb() -> dict[str, float]:
    """Knowledge from the data component (QT_COMPONENT_DATA_PATH or a sibling qt-dep)."""
    return knowledge()
