"""First-run setup: secrets are tested before saving, saved owner-only, and never returned."""
import json
import os
import stat

import httpx
import pytest
from fastapi.testclient import TestClient

from app import config, db, meta_api, settings_store, setup, telegram
from app.server import create_app

GOOD_TOKEN = "EAAB" + "x" * 120
BASE = f"http://127.0.0.1:{config.port()}"


@pytest.fixture(autouse=True)
def env_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ENV_PATH", tmp_path / ".env")
    for k in setup.FORMATS:
        monkeypatch.delenv(k, raising=False)
    yield tmp_path / ".env"
    for k in setup.FORMATS:
        os.environ.pop(k, None)


def test_bad_formats_rejected_and_nothing_saved(fresh, env_file):
    for token, acct in (("short", "123456"), (GOOD_TOKEN, "abc"), (GOOD_TOKEN + " ; rm -rf", "123456")):
        with pytest.raises(setup.SetupError):
            setup.connect_meta(token, acct)
    assert not env_file.exists()


def test_meta_saved_only_after_successful_test(fresh, env_file, monkeypatch):
    def fail(self):
        raise meta_api.MetaError("Invalid OAuth access token")
    monkeypatch.setattr(meta_api.MetaClient, "account_info", fail)
    with pytest.raises(setup.SetupError):
        setup.connect_meta(GOOD_TOKEN, "1234567")
    assert not env_file.exists()
    monkeypatch.setattr(meta_api.MetaClient, "account_info", lambda self: {"name": "حسابي", "currency": "EGP"})
    monkeypatch.setattr(meta_api.MetaClient, "budget_objects", lambda self: [])
    out = setup.connect_meta(GOOD_TOKEN, "1234567")
    assert out["account"] == "act_1234567"
    text = env_file.read_text()
    assert f"META_ACCESS_TOKEN={GOOD_TOKEN}" in text and "META_AD_ACCOUNT_ID=act_1234567" in text
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    st = setup.status()
    assert st["meta"]["connected"] and GOOD_TOKEN not in json.dumps(st, ensure_ascii=False)


def test_write_env_keeps_other_lines_and_removes(fresh, env_file):
    env_file.write_text("# comment\nPORT=9000\nMETA_APP_SECRET=old\n")
    setup.write_env({"META_APP_SECRET": None, "TELEGRAM_CHAT_ID": "42"})
    text = env_file.read_text()
    assert "# comment" in text and "PORT=9000" in text and "META_APP_SECRET" not in text and "TELEGRAM_CHAT_ID=42" in text


def test_telegram_setup_flow(fresh, env_file, monkeypatch):
    calls = []

    def handler(req):
        method = req.url.path.rsplit("/", 1)[-1]
        calls.append(method)
        if method == "getMe":
            return httpx.Response(200, json={"ok": True, "result": {"username": "my_brand_bot", "first_name": "Brand"}})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
    orig = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: orig(transport=httpx.MockTransport(handler), **kw))
    out = setup.connect_telegram("123456789:" + "A" * 35)
    assert out["username"] == "my_brand_bot" and settings_store.get_all()["telegram_enabled"]
    with pytest.raises(setup.SetupError):
        setup.confirm_telegram_chat()  # nobody wrote /start yet
    telegram.handle_update({"update_id": 1, "message": {"chat": {"id": 777, "type": "private"}, "from": {"id": 777, "first_name": "أحمد"}, "text": "/start"}})
    assert setup.confirm_telegram_chat()["name"] == "أحمد"
    assert "TELEGRAM_CHAT_ID=777" in env_file.read_text() and telegram.owner_id() == 777
    # from now on strangers can't claim the bot
    telegram.handle_update({"update_id": 2, "message": {"chat": {"id": 888, "type": "private"}, "from": {"id": 888}, "text": "/start"}})
    assert db.one("SELECT value FROM kv WHERE key='tg_candidate'") is None


def test_setup_endpoints_need_csrf_and_never_echo_secrets(fresh, env_file, monkeypatch):
    monkeypatch.setattr(meta_api.MetaClient, "account_info", lambda self: {"name": "حسابي", "currency": "EGP"})
    monkeypatch.setattr(meta_api.MetaClient, "budget_objects", lambda self: [])
    with TestClient(create_app(no_scheduler=True), base_url=BASE) as c:
        body = {"token": GOOD_TOKEN, "account_id": "1234567"}
        assert c.post("/api/setup/meta", json=body).status_code == 403
        r = c.post("/api/setup/meta", json=body, headers={"X-CSRF-Token": config.csrf_secret()})
        assert r.status_code == 200 and GOOD_TOKEN not in r.text
        assert GOOD_TOKEN not in c.get("/api/setup").text and GOOD_TOKEN not in c.get("/api/settings").text
        assert c.post("/api/setup/unknown", json={}, headers={"X-CSRF-Token": config.csrf_secret()}).status_code == 404


# ---------------------------------------------------------------- MCP token refused: say exactly why
def _mcp_refused(monkeypatch, me_status=200, me_body=None, perms_status=200, granted=()):
    def handler(req: httpx.Request):
        if req.url.host == "mcp.facebook.com":
            return httpx.Response(401, json={"error": "invalid_token"})
        if req.url.path.endswith("/me/permissions"):
            if perms_status != 200:
                return httpx.Response(perms_status, json={"error": {"code": 100, "message": "page token"}})
            return httpx.Response(200, json={"data": [{"permission": p, "status": "granted"} for p in granted]})
        if req.url.path.endswith("/me"):
            return httpx.Response(me_status, json=me_body or {"id": "1"})
        return httpx.Response(404)
    orig = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: orig(transport=httpx.MockTransport(handler), **kw))



def refused_reason(token=GOOD_TOKEN):
    with pytest.raises(setup.SetupError) as e:
        setup.connect_mcp(token)
    assert token not in str(e.value)
    return str(e.value)


def test_mcp_refused_missing_use_case(fresh, env_file, monkeypatch):
    _mcp_refused(monkeypatch, granted=("ads_read", "ads_management"))
    assert "Create & manage ads with ads MCP server" in refused_reason()
    assert not env_file.exists()  # nothing saved


def test_mcp_refused_expired_token(fresh, env_file, monkeypatch):
    _mcp_refused(monkeypatch, me_status=400, me_body={"error": {"code": 190, "message": "expired"}})
    msg = refused_reason()
    assert "ساعة بس" in msg and "Extend Access Token" in msg


def test_mcp_refused_page_token(fresh, env_file, monkeypatch):
    _mcp_refused(monkeypatch, perms_status=400)
    assert "User Token" in refused_reason()


def test_mcp_refused_other_missing_permissions(fresh, env_file, monkeypatch):
    _mcp_refused(monkeypatch, granted=("ads_mcp_management", "ads_read", "ads_management", "business_management"))
    msg = refused_reason()
    assert "pages_show_list" in msg and "ads_mcp_management" not in msg


# ---------------------------------------------------------------- update notice
def test_update_check_compares_versions_and_never_blocks(monkeypatch):
    from app import updates
    monkeypatch.setattr(updates, "local_version", lambda: "2026.11.21.1")
    updates._state.update({"remote": "2026.11.30.1", "checked": __import__("time").time(), "running": False})
    assert updates.status()["update_available"]
    updates._state["remote"] = "2026.11.21.1"
    assert not updates.status()["update_available"]
    updates._state["remote"] = "2026.11.9.5"  # compared as numbers, not text
    assert not updates.status()["update_available"]
    updates._state.update({"remote": None, "checked": 0.0})
