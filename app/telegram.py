"""Telegram bot (free): morning brief, approvals with two buttons, and /status /pending /stop from your phone.

Safety:
- The bot only listens to ONE chat: TELEGRAM_CHAT_ID from .env. Everyone else is ignored.
- Every button carries a signature made with this laptop's secret, so a forged button press does nothing.
- Messages are sent as plain text (no Markdown/HTML), so nothing in an ad name can change how they look.
- Long polling (getUpdates): no webhook, no open port, works behind any home router.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
from typing import Any

import httpx

from . import config, db, guards, settings_store

log = logging.getLogger("telegram")
API = "https://api.telegram.org/bot{token}/{method}"
BUTTON_MAX_AGE = 48 * 3600  # an approval button older than this asks you to look again


class TelegramError(Exception):
    pass


def token() -> str:
    return os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()


def owner_id() -> int | None:
    raw = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    return int(raw) if re.fullmatch(r"-?\d{1,20}", raw) else None


def ready() -> bool:
    s = settings_store.get_all()
    return bool(s["telegram_enabled"] and token() and owner_id() is not None)


def _redact(text: str) -> str:
    t = token()
    return text.replace(t, "***") if t else text


def call(method: str, _http_timeout: float = 30, **params) -> Any:
    """Bot API call. `_http_timeout` is ours; any `timeout` in params is Telegram's long-poll timeout."""
    if not token():
        raise TelegramError("مفيش TELEGRAM_BOT_TOKEN في ملف .env")
    try:
        with httpx.Client(timeout=_http_timeout) as http:
            r = http.post(API.format(token=token(), method=method), json=params)
        body = r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise TelegramError(_redact(f"مش قادر أوصل لـTelegram ({type(e).__name__})")) from None
    if not isinstance(body, dict):
        raise TelegramError("رد مش مفهوم من Telegram")
    if not body.get("ok"):
        raise TelegramError(_redact("Telegram: " + str(body.get("description", r.status_code))))
    return body.get("result")


def sign(action: str, did: int, dry: int = 0, ts: int = 0) -> str:
    """The button is bound to the decision, the dry-run state it was shown with, and when it was sent."""
    msg = f"{action}:{did}:{dry}:{ts}"
    return hmac.new(config.csrf_secret().encode(), msg.encode(), hashlib.sha256).hexdigest()[:16]


def fit(text: str, limit: int = 4000) -> str:
    """Telegram counts UTF-16 units (emoji count twice); cut safely under its 4096 limit."""
    if len(text.encode("utf-16-le")) // 2 <= limit:
        return text
    out, n = [], 0
    for ch in text:
        n += len(ch.encode("utf-16-le")) // 2
        if n > limit - 1:
            break
        out.append(ch)
    return "".join(out) + "…"


def send(text: str, buttons: list[list[dict]] | None = None) -> int | None:
    chat = owner_id()
    if chat is None:
        return None
    params: dict[str, Any] = {"chat_id": chat, "text": fit(text), "disable_web_page_preview": True}
    if buttons:
        params["reply_markup"] = {"inline_keyboard": buttons}
    res = call("sendMessage", **params)
    return (res or {}).get("message_id")


def _bg(fn, *args) -> None:
    """Fire and forget: a Telegram hiccup must never slow down or break an agent run."""
    def run():
        try:
            fn(*args)
        except Exception as e:  # noqa: BLE001
            log.warning("telegram: %s", _redact(str(e)))
    threading.Thread(target=run, daemon=True).start()


# ---------------------------------------------------------------- outgoing
AGENT_NAMES = {"media_buyer": "Media buyer", "followup": "متابعة العملاء", "weekly_review": "المراجعة الأسبوعية"}


def decision_text(d: dict) -> str:
    action = json.loads(d.get("action") or "{}")
    dry = settings_store.get_all()["dry_run"]
    head = [f"قرار #{d['id']} مستني موافقتك · {AGENT_NAMES.get(d['agent'], d['agent'])}"]
    # 1) what will actually run, built from the stored action (not from anything the agent wrote)
    run = ["— اللي هيتنفذ —"]
    t = action.get("type")
    if t == "pause":
        run.append(f"قفل {action.get('level', '')} رقم {action.get('object_id')} («{guards.sanitize(action.get('object_name', ''), 60)}»)")
    elif t == "budget":
        run.append(f"daily budget لـ{action.get('level', '')} رقم {action.get('object_id')} («{guards.sanitize(action.get('object_name', ''), 60)}»)")
        run.append(f"الجديد: {action.get('new_daily_budget')} في اليوم (كان {action.get('old_daily_budget')} وقت الاقتراح)")
        run.append("السقف والحدود بيتراجعوا تاني لحظة التنفيذ.")
    if t in ("pause", "budget") and action.get("result_label"):
        run.append(f"هدف الحملة دي: {guards.sanitize(action['result_label'], 40)}")
    if t == "mcp_call":
        run.append(f"أداة Meta: {action.get('tool')}")
        run.append(str(action.get("details", "")))
    elif t == "proposal":
        run.append("اقتراح: مفيش حاجة هتتغير لوحدها، انت اللي هتنفذه في Ads Manager.")
    run.append("(وضع التجربة شغال: مفيش حاجة هتتبعت لـMeta)" if dry else "(هيتنفذ في حسابك الحقيقي)")
    # 2) what the agent says, clearly labelled as its words
    said = ["— كلام الـagent —", "العنوان: " + guards.sanitize(d["title"], 200)]
    for label, key in (("ليه", "reason"), ("الفرضية", "hypothesis"), ("هيتقاس بـ", "metric")):
        if d.get(key):
            said.append(f"{label}: {guards.sanitize(d[key], 400)}")
    if t == "proposal" and action.get("details"):
        said.append("التفاصيل: " + guards.sanitize(action["details"], 1500, multiline=True))
    return "\n".join(head + [""] + run + [""] + said)


def notify_decision(did: int) -> None:
    s = settings_store.get_all()
    if not (ready() and s["telegram_approvals"]):
        return
    d = db.one("SELECT * FROM decisions WHERE id = ?", (did,))
    if not d or d["status"] != "pending":
        return
    dry, ts = int(bool(s["dry_run"])), int(time.time())
    send(decision_text(d), [[{"text": "وافق", "callback_data": f"a:{did}:{dry}:{ts}:{sign('a', did, dry, ts)}"},
                             {"text": "ارفض", "callback_data": f"r:{did}:{dry}:{ts}:{sign('r', did, dry, ts)}"}]])


def notify_run(run_id: int) -> None:
    s = settings_store.get_all()
    if not (ready() and s["telegram_brief"]):
        return
    r = db.one("SELECT * FROM runs WHERE id = ?", (run_id,))
    if not r or r["status"] == "skipped":
        return
    name = AGENT_NAMES.get(r["agent"], r["agent"])
    if r["status"] == "error":
        text = f"{name} وقف بخطأ: {r['error'] or ''}"
    else:
        pending = db.one("SELECT COUNT(*) AS n FROM decisions WHERE status='pending'")["n"]
        drafts = db.one("SELECT COUNT(*) AS n FROM followups WHERE status='draft'")["n"]
        text = f"{name} خلص.\n\n{(r['summary'] or '')[:2500]}\n\nمستني موافقتك: {pending} · رسايل جاهزة: {drafts}"
    send(guards.sanitize(text, 3500, multiline=True))


def notify_decision_bg(did: int) -> None:
    _bg(notify_decision, did)


def notify_run_bg(run_id: int) -> None:
    _bg(notify_run, run_id)


# ---------------------------------------------------------------- incoming
def _status_text() -> str:
    s = settings_store.get_all()
    pending = db.one("SELECT COUNT(*) AS n FROM decisions WHERE status='pending'")["n"]
    drafts = db.one("SELECT COUNT(*) AS n FROM followups WHERE status='draft'")["n"]
    last = db.rows("SELECT agent, status, started_at FROM runs ORDER BY id DESC LIMIT 3")
    lines = ["الفريق " + ("شغال" if s["agents_enabled"] else "متوقف"), "وضع التجربة " + ("شغال" if s["dry_run"] else "مقفول"),
             f"مستني موافقتك: {pending}", f"رسايل متابعة جاهزة: {drafts}"]
    lines += [f"{AGENT_NAMES.get(x['agent'], x['agent'])}: {x['status']} ({x['started_at'][5:16].replace('T', ' ')})" for x in last]
    return "\n".join(lines)


def handle_update(u: dict) -> None:
    """One update from getUpdates. Synchronous so it can be tested directly."""
    from . import agents, executor  # lazy: avoids an import cycle
    owner = owner_id()
    if "callback_query" in u:
        q = u["callback_query"]
        from_id = (q.get("from") or {}).get("id")
        chat_id = ((q.get("message") or {}).get("chat") or {}).get("id")
        if owner is None or from_id != owner or chat_id != owner:
            return  # not the owner: ignore silently
        m = re.fullmatch(r"([ar]):(\d{1,12}):([01]):(\d{9,11}):([0-9a-f]{16})", str(q.get("data", "")))
        if not m or not hmac.compare_digest(m.group(5), sign(m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4)))):
            call("answerCallbackQuery", callback_query_id=q["id"], text="زرار مش صالح")
            return
        action, did, dry, ts = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
        s = settings_store.get_all()
        if not s["telegram_approvals"]:
            call("answerCallbackQuery", callback_query_id=q["id"], text="الموافقات من Telegram مقفولة. استخدم الـdashboard.")
            return
        if dry != int(bool(s["dry_run"])) or time.time() - ts > BUTTON_MAX_AGE:
            call("answerCallbackQuery", callback_query_id=q["id"], text="الرسالة دي قديمة أو الإعدادات اتغيرت. بعتلك نسخة جديدة.")
            notify_decision(did)
            return
        # answer right away: Telegram drops answers that come too late, and Meta can be slow
        call("answerCallbackQuery", callback_query_id=q["id"], text="بنفّذ…" if action == "a" else "بنسجّل الرفض…")
        try:
            if action == "a":
                res = executor.approve(did)
                result = {"executed": "اتنفذ" + (" (وضع التجربة)" if res.get("dry_run") else ""),
                          "manual": "تمام، نفّذه في Ads Manager",
                          "pending": "محتاج مراجعة تانية من الـdashboard"}.get(res["status"], "اتمنع: " + str(res.get("reason", "")))
            else:
                executor.reject(did, "رفض من Telegram")
                result = "اترفض"
        except ValueError as e:
            result = str(e)
        msg = q.get("message") or {}
        try:
            call("editMessageText", chat_id=owner, message_id=msg["message_id"],
                 text=fit(str(msg.get("text", "")) + "\n\n← " + result))
        except (TelegramError, KeyError):
            send(f"قرار #{did}: {result}")
        return
    msg = u.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    text = str(msg.get("text", "")).strip()
    if owner is None:
        if text.startswith("/start") and chat_id is not None and (msg.get("chat") or {}).get("type", "private") == "private":
            # setup: remember who wrote /start; the owner confirms it in the dashboard (so nobody else can claim the bot)
            name = guards.sanitize((msg.get("from") or {}).get("first_name", ""), 40)
            db.execute("INSERT INTO kv(key, value) VALUES ('tg_candidate', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                       (json.dumps({"chat_id": chat_id, "name": name}, ensure_ascii=False),))
            call("sendMessage", chat_id=chat_id, text="تمام! ارجع لصفحة الإعداد في البرنامج ودوس «ده أنا» عشان نكمّل.")
        return
    if chat_id != owner or (msg.get("from") or {}).get("id") != owner:
        return
    if text.startswith("/stop"):
        settings_store.update({"agents_enabled": False})
        agents.cancel_all()
        send("وقفت الفريق كله. ترجّعه من الإعدادات في الـdashboard.")
    elif text.startswith("/pending"):
        rows = db.rows("SELECT id FROM decisions WHERE status='pending' ORDER BY id LIMIT 10")
        if not rows:
            send("مفيش حاجة مستنياك.")
        for r in rows:
            notify_decision(r["id"])
    else:  # /status, /start or anything else
        send(_status_text() + "\n\nالأوامر: /status /pending /stop")


def _get_offset() -> int:
    r = db.one("SELECT value FROM kv WHERE key = 'tg_offset'")
    return int(r["value"]) if r else 0


def _set_offset(v: int) -> None:
    db.execute("INSERT INTO kv(key, value) VALUES ('tg_offset', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (str(v),))


async def poll(stop: asyncio.Event) -> None:
    while not stop.is_set():
        if not (token() and settings_store.get_all()["telegram_enabled"]):
            try:
                await asyncio.wait_for(stop.wait(), timeout=30)
            except asyncio.TimeoutError:
                pass
            continue
        try:
            await poll_once()
        except Exception as e:  # noqa: BLE001 - the loop must never die
            log.warning("telegram: %s", _redact(str(e)))
            try:
                await asyncio.wait_for(stop.wait(), timeout=20)
            except asyncio.TimeoutError:
                pass


async def poll_once() -> int:
    updates = await asyncio.to_thread(call, "getUpdates", 40, offset=_get_offset(), timeout=25,
                                      allowed_updates=["message", "callback_query"])
    for u in updates or []:
        if not isinstance(u, dict) or "update_id" not in u:
            continue
        _set_offset(int(u["update_id"]) + 1)  # saved first: an update is handled at most once
        try:
            await asyncio.to_thread(handle_update, u)
        except Exception as e:  # noqa: BLE001
            log.warning("telegram update failed: %s", _redact(str(e)))
    return len(updates or [])
