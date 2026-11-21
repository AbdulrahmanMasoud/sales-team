import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["SALES_TEAM_DEMO"] = "1"

from app import config, db, meta_api, meta_mcp, updates  # noqa: E402
import time  # noqa: E402


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    fake = meta_api.FakeMeta()
    monkeypatch.setattr(meta_api, "get_client", lambda: fake)
    meta_mcp.reset_for_tests()
    updates._state.update({"checked": time.time(), "running": False})  # no network from tests
    db.init()
    yield fake
