import pytest

from app.config import load_settings
from app.db import connect, init_db

NOW = 1_800_000_000


@pytest.fixture
def now():
    return NOW


@pytest.fixture
def settings(tmp_path):
    s = load_settings({"DATA_DIR": str(tmp_path)})
    for d in (s.videos_dir, s.thumbs_dir):
        d.mkdir(parents=True, exist_ok=True)
    return s


@pytest.fixture
def conn():
    c = connect(":memory:")
    init_db(c)
    yield c
    c.close()
