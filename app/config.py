"""Paths, environment and secrets. Secrets live in .env only, never in the DB or prompts."""
from __future__ import annotations

import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("SALES_TEAM_DATA", BASE_DIR / "data"))
ENV_PATH = BASE_DIR / ".env"

HOST = "127.0.0.1"  # never 0.0.0.0: the dashboard is for this laptop only


ENV_FILE_KEYS: set[str] = set()
SECRET_NAMES = {"META_ACCESS_TOKEN", "META_APP_SECRET", "META_AD_ACCOUNT_ID", "META_MCP_TOKEN",
                "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"}


def agent_env() -> dict[str, str]:
    """Blank every secret we know about (and everything from .env) in the agent process environment."""
    return {k: "" for k in SECRET_NAMES | ENV_FILE_KEYS}


def load_env(path: Path = ENV_PATH) -> None:
    """Minimal .env loader (KEY=VALUE lines). Real environment variables win."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key:
            ENV_FILE_KEYS.add(key)
        if key and key not in os.environ:
            os.environ[key] = value


def port() -> int:
    return int(os.environ.get("PORT", "8765"))


def data_dir() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR


def db_path() -> Path:
    return data_dir() / "app.db"


def sandbox_dir() -> Path:
    """Empty working directory for agents. They get no file tools anyway."""
    p = data_dir() / "agent-sandbox"
    p.mkdir(parents=True, exist_ok=True)
    return p


def csrf_secret() -> str:
    """Per-install secret the dashboard must send with every change."""
    f = data_dir() / ".csrf_secret"
    if not f.exists():
        f.write_text(secrets.token_urlsafe(32), encoding="utf-8")
        try:
            f.chmod(0o600)
        except OSError:
            pass
    return f.read_text(encoding="utf-8").strip()


def meta_token() -> str:
    return os.environ.get("META_ACCESS_TOKEN", "").strip()


def meta_app_secret() -> str:
    return os.environ.get("META_APP_SECRET", "").strip()


def meta_account_id() -> str:
    raw = os.environ.get("META_AD_ACCOUNT_ID", "").strip()
    if not raw:
        return ""
    return raw if raw.startswith("act_") else f"act_{raw}"


def graph_version() -> str:
    return os.environ.get("META_GRAPH_VERSION", "v26.0").strip() or "v26.0"


def demo_mode() -> bool:
    """No Meta credentials -> run against built-in sample ads so you can try everything."""
    return not (meta_token() and meta_account_id()) or os.environ.get("SALES_TEAM_DEMO") == "1"
