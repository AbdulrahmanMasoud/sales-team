"""Is a newer version on GitHub? Read-only check of one small text file, at most every 6 hours, in the background.

Nothing is downloaded or changed here: updating is always the owner's choice (double-click «تحديث.command»).
"""
from __future__ import annotations

import re
import threading
import time

import httpx

from . import config

REPO = "AbdulrahmanMasoud/sales-team"
URL = f"https://raw.githubusercontent.com/{REPO}/main/VERSION"
_VERSION_RE = re.compile(r"^\d[\d.]{0,30}$")
_state = {"remote": None, "checked": 0.0, "running": False}
_lock = threading.Lock()


def local_version() -> str:
    try:
        v = (config.BASE_DIR / "VERSION").read_text().strip()
        return v if _VERSION_RE.match(v) else "0"
    except OSError:
        return "0"


def _key(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split(".") if x.isdigit())


def _fetch() -> None:
    try:
        with httpx.Client(timeout=8) as http:
            r = http.get(URL)
        v = r.text.strip() if r.status_code == 200 else ""
        if _VERSION_RE.match(v):
            _state["remote"] = v
    except httpx.HTTPError:
        pass
    finally:
        _state["checked"] = time.time()
        _state["running"] = False


def status() -> dict:
    """Never waits on the network: returns what we know and refreshes in the background when stale."""
    with _lock:
        if not _state["running"] and time.time() - _state["checked"] > 6 * 3600:
            _state["running"] = True
            threading.Thread(target=_fetch, daemon=True).start()
    local, remote = local_version(), _state["remote"]
    return {"version": local, "latest": remote, "update_available": bool(remote and _key(remote) > _key(local))}
