"""First-run setup from the dashboard: connect Claude, Meta and Telegram without touching any file.

Secrets typed in the setup page are tested first, then written to .env (readable by this user only).
They are never sent back to the page: the API only ever returns masked values.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from typing import Any

import httpx

from . import config, db, meta_api, meta_mcp, settings_store, telegram

FORMATS = {
    "META_ACCESS_TOKEN": re.compile(r"^[A-Za-z0-9_\-|.]{40,1000}$"),
    "META_AD_ACCOUNT_ID": re.compile(r"^(act_)?\d{5,25}$"),
    "META_APP_SECRET": re.compile(r"^[a-f0-9]{32}$"),
    "META_MCP_TOKEN": re.compile(r"^[A-Za-z0-9_\-|.]{40,1000}$"),
    "TELEGRAM_BOT_TOKEN": re.compile(r"^\d{5,15}:[A-Za-z0-9_\-]{30,60}$"),
    "TELEGRAM_CHAT_ID": re.compile(r"^-?\d{1,20}$"),
}
_lock = threading.Lock()


class SetupError(ValueError):
    pass


def mask(value: str) -> str:
    return (value[:4] + "••••" + value[-4:]) if len(value) > 10 else ("••••" if value else "")


def _clean(key: str, value: Any) -> str:
    v = str(value or "").strip().replace(" ", "")
    if not FORMATS[key].match(v):
        raise SetupError({
            "META_ACCESS_TOKEN": "الـtoken شكله مش مظبوط. انسخه كله من Meta من غير مسافات.",
            "META_AD_ACCOUNT_ID": "رقم حساب الإعلانات لازم يكون أرقام بس (ممكن يبدأ بـact_).",
            "META_APP_SECRET": "الـApp secret لازم يكون 32 حرف ورقم.",
            "META_MCP_TOKEN": "الـtoken شكله مش مظبوط. انسخه كله من غير مسافات.",
            "TELEGRAM_BOT_TOKEN": "الـtoken بتاع البوت شكله زي 123456789:ABC... انسخه كله من BotFather.",
            "TELEGRAM_CHAT_ID": "chat id مش صالح.",
        }[key])
    return v


def write_env(updates: dict[str, str | None]) -> None:
    """Rewrite .env with these keys changed (None removes a key). Atomic, owner-only permissions."""
    with _lock:
        path = config.ENV_PATH
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        kept, seen = [], set()
        for line in lines:
            key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
            if key in updates:
                seen.add(key)
                if updates[key] is not None:
                    kept.append(f"{key}={updates[key]}")
                continue
            kept.append(line)
        for key, val in updates.items():
            if key not in seen and val is not None:
                kept.append(f"{key}={val}")
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".env.")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(kept).strip() + "\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        for key, val in updates.items():
            config.ENV_FILE_KEYS.add(key)
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        _reset_clients()


def _reset_clients() -> None:
    meta_api._real_singleton = None
    meta_api._fake_singleton = None
    meta_mcp.reset_for_tests()


def status() -> dict[str, Any]:
    s = settings_store.get_all()
    env = os.environ
    cand = db.one("SELECT value FROM kv WHERE key = 'tg_candidate'")
    return {
        "demo_flag": os.environ.get("SALES_TEAM_DEMO") == "1",
        "business_done": s["brand_name"] not in ("", "براندك"),
        "meta": {"connected": bool(env.get("META_ACCESS_TOKEN") and env.get("META_AD_ACCOUNT_ID")),
                 "token": mask(env.get("META_ACCESS_TOKEN", "")), "account": config.meta_account_id(),
                 "app_secret": bool(env.get("META_APP_SECRET"))},
        "telegram": {"token": bool(telegram.token()), "chat": telegram.owner_id() is not None,
                     "bot": (db.one("SELECT value FROM kv WHERE key = 'tg_bot'") or {}).get("value", ""),
                     "candidate": json.loads(cand["value"]) if cand else None},
        "mcp": {"token": bool(env.get("META_MCP_TOKEN"))},
        "values": {k: s[k] for k in ("brand_name", "payment_methods", "shipping_cost", "target_cpr", "targets", "daily_cap", "monthly_cap")},
    }


# ---------------------------------------------------------------- testers (nothing is saved unless the test passes)
def connect_meta(token: str, account: str, app_secret: str = "") -> dict[str, Any]:
    token = _clean("META_ACCESS_TOKEN", token)
    account = _clean("META_AD_ACCOUNT_ID", account)
    secret = _clean("META_APP_SECRET", app_secret) if app_secret else ""
    acct = account if account.startswith("act_") else f"act_{account}"
    client = meta_api.MetaClient(token, acct, config.graph_version(), secret)
    try:
        info = client.account_info()
        client.budget_objects()  # proves we can read campaigns too, not only the account name
    except meta_api.MetaError as e:
        raise SetupError(f"Meta رفضت: {e}. اتأكد إن الـsystem user متضاف لحساب الإعلانات وإن الـtoken فيه ads_management.") from None
    write_env({"META_ACCESS_TOKEN": token, "META_AD_ACCOUNT_ID": acct, "META_APP_SECRET": secret or None})
    return {"name": info.get("name"), "currency": info.get("currency"), "account": acct}


def connect_telegram(bot_token: str) -> dict[str, Any]:
    bot_token = _clean("TELEGRAM_BOT_TOKEN", bot_token)
    old = os.environ.get("TELEGRAM_BOT_TOKEN")
    os.environ["TELEGRAM_BOT_TOKEN"] = bot_token
    try:
        me = telegram.call("getMe")
    except telegram.TelegramError as e:
        if old is None:
            os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        else:
            os.environ["TELEGRAM_BOT_TOKEN"] = old
        raise SetupError(f"Telegram رفض الـtoken: {e}") from None
    write_env({"TELEGRAM_BOT_TOKEN": bot_token})
    db.execute("DELETE FROM kv WHERE key = 'tg_candidate'")
    db.execute("INSERT INTO kv(key, value) VALUES ('tg_bot', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
               (str(me.get("username") or ""),))
    settings_store.update({"telegram_enabled": True})
    return {"username": me.get("username"), "name": me.get("first_name")}


def confirm_telegram_chat() -> dict[str, Any]:
    cand = db.one("SELECT value FROM kv WHERE key = 'tg_candidate'")
    if not cand:
        raise SetupError("لسه موصلناش /start منك. افتح البوت في Telegram وابعتله /start.")
    c = json.loads(cand["value"])
    chat = _clean("TELEGRAM_CHAT_ID", str(c["chat_id"]))
    write_env({"TELEGRAM_CHAT_ID": chat})
    db.execute("DELETE FROM kv WHERE key = 'tg_candidate'")
    try:
        telegram.send("تمام، اتوصلنا. من هنا هيوصلك ملخص الفريق والقرارات اللي محتاجة موافقتك.\nالأوامر: /status /pending /stop")
    except telegram.TelegramError:
        pass
    return {"name": c.get("name")}


MCP_PERMISSIONS = ("ads_mcp_management", "ads_read", "ads_management", "business_management",
                   "pages_show_list", "instagram_basic", "catalog_management")


def diagnose_mcp_token(token: str) -> str:
    """When the MCP refuses a token, ask Graph what the token really is, and say the fix in plain words."""
    base = f"https://graph.facebook.com/{config.graph_version()}"
    try:
        with httpx.Client(timeout=20) as http:
            me = http.get(f"{base}/me", params={"fields": "id", "access_token": token})
            perms = http.get(f"{base}/me/permissions", params={"access_token": token}) if me.status_code == 200 else None
    except httpx.HTTPError:
        return ""
    if me.status_code != 200:
        try:
            code = (me.json().get("error") or {}).get("code")
        except ValueError:
            code = None
        if code == 190:
            return ("الـtoken خلص أو مش صالح. الـtoken اللي بيطلع من Graph API Explorer بيعيش ساعة بس: "
                    "اعمل واحد جديد، وطوّله من Access Token Debugger (زرار Extend Access Token) قبل ما تلزقه هنا.")
        return ""
    if perms is None or perms.status_code != 200:
        return "ده مش User token. في Graph API Explorer، من «User or Page» اختار User Token، وبعدين Generate Access Token."
    granted = {p.get("permission") for p in perms.json().get("data", []) if p.get("status") == "granted"}
    missing = [p for p in MCP_PERMISSIONS if p not in granted]
    if "ads_mcp_management" in missing:
        return ("الـtoken ناقصه صلاحية ads_mcp_management. دي بتظهر بس لما تضيف للـApp الـuse case "
                "«Create & manage ads with ads MCP server» (من developers.facebook.com/apps ← الـApp ← Use cases ← Add). "
                "بعدها اعمل token جديد وعلّم عليها.")
    if missing:
        return "الـtoken ناقصه صلاحيات: " + "، ".join(missing) + ". اعمل token جديد وعلّم عليهم كلهم."
    return ("الـtoken صالح وفيه كل الصلاحيات، بس Meta لسه رافضة الـMCP. غالباً الـApp محتاج الـuse case "
            "«Create & manage ads with ads MCP server»، أو الصلاحية دي محتاجة Advanced Access من «Permissions and features» في الـApp.")


def connect_mcp(token: str) -> dict[str, Any]:
    token = _clean("META_MCP_TOKEN", token)
    try:
        tools = meta_mcp.MCPClient(token).list_tools()
    except meta_mcp.MetaMCPAuthError as e:
        raise SetupError(diagnose_mcp_token(token) or str(e)) from None
    except meta_mcp.MetaMCPError as e:
        raise SetupError(str(e)) from None
    write_env({"META_MCP_TOKEN": token})
    return {"tools": len(tools)}


def disconnect(service: str) -> None:
    keys = {"meta": ("META_ACCESS_TOKEN", "META_AD_ACCOUNT_ID", "META_APP_SECRET"),
            "telegram": ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"), "mcp": ("META_MCP_TOKEN",)}.get(service)
    if not keys:
        raise SetupError("خدمة مش معروفة")
    write_env({k: None for k in keys})
    if service == "telegram":
        settings_store.update({"telegram_enabled": False})


async def test_claude() -> dict[str, Any]:
    """Tiny real call through the same locked-down setup the agents use: proves login works."""
    from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, ResultMessage, TextBlock
    from . import agents
    opts = ClaudeAgentOptions(system_prompt="Reply with the single word: تمام", tools=[], allowed_tools=[],
                              setting_sources=[], strict_mcp_config=True, max_turns=1, model="haiku",
                              cwd=str(config.sandbox_dir()), env=config.agent_env(),
                              can_use_tool=agents._deny_everything_else)
    text, err = "", None
    try:
        async with ClaudeSDKClient(options=opts) as client:
            await client.query("قول تمام")
            async for m in client.receive_response():
                if isinstance(m, AssistantMessage):
                    err = m.error or err
                    text += "".join(b.text for b in m.content if isinstance(b, TextBlock))
                elif isinstance(m, ResultMessage) and m.is_error:
                    err = err or m.subtype
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": agents._friendly_error(e)}
    if err:
        return {"ok": False, "error": agents._api_error_text(err, text)}
    return {"ok": True, "reply": text.strip()[:50]}
