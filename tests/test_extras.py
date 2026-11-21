"""Profit math, deeper analysis and the Telegram bot."""
import json

import httpx
import pytest

from app import analysis, business, db, executor, meta_api, settings_store, telegram
from tests.test_tools import DEC, LOOKALIKE, tools_for

S = lambda **kw: {**settings_store.get_all(), **kw}  # noqa: E731


def add(name, phone, product, amount, status="delivered", source="إعلان أ"):
    from datetime import date, timedelta
    business.add_order({"name": name, "phone": phone, "product": product, "amount": amount, "payment": "cod",
                        "status": status, "source": source, "created_at": (date.today() - timedelta(days=1)).isoformat()}, "20")


# ---------------------------------------------------------------- profit
def test_profit_math_by_hand(fresh):
    settings_store.update({"cogs_pct": 40, "shipping_cost": 50, "return_cost": 80, "target_profit_share": 30, "target_cpr": 110})
    analysis.set_product("هودي", 200)
    add("أ", "01000000001", "هودي", 500)                       # known cost: 500-200-50 = 250
    add("ب", "01000000002", "جاكيت", 1000)                     # 40%: 1000-400-50 = 550
    add("ج", "01000000003", "هودي", 500, status="returned")     # -80
    add("د", "01000000004", "هودي", 500, status="cancelled")    # ignored
    p = analysis.profit_summary(settings_store.get_all(), 7, None)
    assert p["orders"] == 3 and p["returned"] == 1
    assert p["gross_before_ads"] == 250 + 550 - 80
    assert p["breakeven_cost_per_order"] == 240.0
    assert p["suggested_target_cpr"] == 168.0
    assert not p["target_above_breakeven"]
    assert p["orders_with_estimated_cost"] == 1


def test_profit_flags_target_above_breakeven(fresh):
    settings_store.update({"cogs_pct": 80, "shipping_cost": 60, "target_cpr": 110})
    add("أ", "01000000001", "تيشيرت", 250)  # 250-200-60 = -10 per order
    p = analysis.profit_summary(settings_store.get_all(), 7, fresh)
    assert p["target_above_breakeven"] and p["net_profit"] < 0 and p["ad_spend"] > 0


def test_profit_by_source_counts_returns(fresh):
    settings_store.update({"cogs_pct": 40, "shipping_cost": 50, "return_cost": 80})
    for i in range(4):
        add(f"x{i}", f"0100000001{i}", "هودي", 500, status="returned" if i < 2 else "delivered", source="رخيص بس بيرجع")
    add("y", "01000000020", "هودي", 500, source="غالي بس بيكمل")
    srcs = {r["source"]: r for r in analysis.profit_summary(settings_store.get_all(), 7, None)["by_source"]}
    assert srcs["رخيص بس بيرجع"]["return_pct"] == 50.0
    assert srcs["غالي بس بيكمل"]["margin_per_order"] > srcs["رخيص بس بيرجع"]["margin_per_order"]


def test_undelivered_orders_expect_usual_returns_and_messages_mode(fresh):
    settings_store.update({"cogs_pct": 40, "shipping_cost": 50, "return_cost": 80})
    for i in range(10):  # history: 2 of 10 came back
        add(f"h{i}", f"0101000000{i}", "هودي", 500, status="returned" if i < 2 else "delivered")
    add("new", "01020000000", "هودي", 500, status="shipped")
    p = analysis.profit_summary(settings_store.get_all(), 7, None)
    assert p["expected_return_rate_used"] == 20.0
    settings_store.update({"result_type": "messages", "target_cpr": 9999})
    p = analysis.profit_summary(settings_store.get_all(), 7, None)
    assert p["suggested_target_cpr"] is None and not p["target_above_breakeven"]


def test_products_validation(fresh):
    with pytest.raises(ValueError):
        analysis.set_product("", 10)
    with pytest.raises(ValueError):
        analysis.set_product("x", "abc")
    pid = analysis.set_product("هودي", 200)
    assert analysis.set_product("  هودي ", 210) == pid  # same product, updated
    assert analysis.list_products()[0]["unit_cost"] == 210


# ---------------------------------------------------------------- analysis
def test_breakdowns_and_fatigue(fresh):
    s = settings_store.get_all()
    bd = analysis.breakdown(s, fresh, "placement", "last_14d")
    assert bd["results_available"] and abs(sum(r["spend_share_pct"] for r in bd["rows"]) - 100) < 1
    assert not analysis.breakdown(s, fresh, "region", "last_7d")["results_available"]
    with pytest.raises(meta_api.MetaError):
        fresh.breakdown("religion", "last_7d", "purchase")
    fat = analysis.fatigue_report(s, fresh)
    assert fat[0]["tired"] and fat[0]["ctr_drop_pct"] > 25  # retargeting ad: CTR 2.1 -> 1.4, frequency 3.9


def test_agent_tools_for_analysis(fresh):
    ctx, t = tools_for("media_buyer", fresh)
    for name, args in (("get_profit_summary", {"days": 7}), ("get_breakdown", {"dimension": "age_gender", "period": "last_14d"}),
                       ("get_creative_fatigue", {})):
        out = t(name, args)
        assert not out.get("is_error") and "untrusted_data" in out["content"][0]["text"], name
    assert t("get_breakdown", {"dimension": "income", "period": "last_7d"}).get("is_error")


# ---------------------------------------------------------------- telegram
OWNER = 555000111


@pytest.fixture
def tg(monkeypatch):
    sent = []

    def handler(req: httpx.Request):
        method = req.url.path.rsplit("/", 1)[-1]
        body = json.loads(req.content or b"{}")
        sent.append((method, body))
        if method == "sendMessage":
            return httpx.Response(200, json={"ok": True, "result": {"message_id": len(sent)}})
        return httpx.Response(200, json={"ok": True, "result": True})
    orig = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: orig(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:SECRET")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", str(OWNER))
    settings_store.update({"telegram_enabled": True})
    return sent


def pending_decision(fresh):
    ctx, t = tools_for("media_buyer", fresh)
    t("pause_object", {"object_id": LOOKALIKE, **DEC})
    return db.one("SELECT id FROM decisions WHERE status='pending'")["id"]


def press(did, action="a", who=OWNER, sig=None, dry=1, ts=None):
    import time as _t
    ts = ts or int(_t.time())
    return {"update_id": 1, "callback_query": {"id": "q1", "from": {"id": who},
            "data": f"{action}:{did}:{dry}:{ts}:{sig or telegram.sign(action, did, dry, ts)}",
            "message": {"message_id": 9, "chat": {"id": who}, "text": "قرار"}}}


def test_decision_card_has_signed_buttons(fresh, tg):
    did = pending_decision(fresh)
    telegram.notify_decision(did)
    method, body = next(x for x in tg if x[0] == "sendMessage")
    assert body["chat_id"] == OWNER and "Lookalike" in body["text"]
    data = [b["callback_data"] for b in body["reply_markup"]["inline_keyboard"][0]]
    assert data[0].startswith(f"a:{did}:1:") and data[1].startswith(f"r:{did}:1:")
    assert all(len(d.encode()) <= 64 for d in data)
    assert "اللي هيتنفذ" in body["text"] and "120210000000011" in body["text"] and "وضع التجربة" in body["text"]


def test_owner_can_approve_from_phone(fresh, tg):
    did = pending_decision(fresh)
    telegram.handle_update(press(did))
    assert db.one("SELECT status FROM decisions WHERE id=?", (did,))["status"] == "executed"
    assert any(m == "editMessageText" for m, _ in tg)


def test_strangers_and_forged_buttons_do_nothing(fresh, tg):
    did = pending_decision(fresh)
    telegram.handle_update(press(did, who=999))                      # someone else
    telegram.handle_update(press(did, sig="0" * 16))                 # forged signature
    telegram.handle_update(press(did, action="a", sig=telegram.sign("r", did, 1, 1700000000), ts=1700000000))  # other button's signature
    assert db.one("SELECT status FROM decisions WHERE id=?", (did,))["status"] == "pending"
    telegram.handle_update({"update_id": 2, "message": {"chat": {"id": 999}, "from": {"id": 999}, "text": "/stop"}})
    assert settings_store.get_all()["agents_enabled"]


def test_reject_and_stop_commands(fresh, tg):
    did = pending_decision(fresh)
    telegram.handle_update(press(did, action="r"))
    assert db.one("SELECT status FROM decisions WHERE id=?", (did,))["status"] == "rejected"
    telegram.handle_update({"update_id": 3, "message": {"chat": {"id": OWNER}, "from": {"id": OWNER}, "text": "/stop"}})
    assert not settings_store.get_all()["agents_enabled"]


def test_token_never_leaks_in_errors(fresh, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:SECRET")
    orig = httpx.Client

    def boom(req):
        raise httpx.ConnectError(f"cannot reach {req.url}")
    monkeypatch.setattr(httpx, "Client", lambda **kw: orig(transport=httpx.MockTransport(boom), **kw))
    with pytest.raises(telegram.TelegramError) as e:
        telegram.call("getMe")
    assert "SECRET" not in str(e.value)


def test_setup_remembers_who_wrote_start(fresh, monkeypatch, tg):
    monkeypatch.delenv("TELEGRAM_CHAT_ID")
    telegram.handle_update({"update_id": 4, "message": {"chat": {"id": 42, "type": "private"}, "from": {"id": 42, "first_name": "منى"}, "text": "/start"}})
    assert tg[-1][1]["chat_id"] == 42
    assert json.loads(db.one("SELECT value FROM kv WHERE key='tg_candidate'")["value"]) == {"chat_id": 42, "name": "منى"}
    telegram.handle_update({"update_id": 5, "message": {"chat": {"id": -100, "type": "group"}, "from": {"id": 7}, "text": "/start"}})
    assert json.loads(db.one("SELECT value FROM kv WHERE key='tg_candidate'")["value"])["chat_id"] == 42  # groups ignored


def test_stale_or_changed_buttons_are_refused(fresh, tg):
    did = pending_decision(fresh)
    telegram.handle_update(press(did, ts=1700000000))           # days old
    settings_store.update({"dry_run": False})
    telegram.handle_update(press(did, dry=1))                     # shown as test mode, now it would be real
    assert db.one("SELECT status FROM decisions WHERE id=?", (did,))["status"] == "pending"
    assert sum(1 for m, b in tg if m == "sendMessage" and "قرار #" in b.get("text", "")) >= 2  # fresh cards re-sent (plus the original)


def test_buttons_ignored_when_telegram_approvals_off(fresh, tg):
    did = pending_decision(fresh)
    settings_store.update({"telegram_approvals": False})
    telegram.handle_update(press(did, action="r"))
    assert db.one("SELECT status FROM decisions WHERE id=?", (did,))["status"] == "pending"


def test_poll_once_runs_without_crashing(fresh, tg, monkeypatch):
    import asyncio
    did = pending_decision(fresh)
    upd = press(did)
    upd["update_id"] = 77
    real_call = telegram.call

    def fake_call(method, _http_timeout=30, **params):
        if method == "getUpdates":
            assert params["timeout"] == 25 and _http_timeout > 25
            return [upd]
        return real_call(method, _http_timeout, **params)
    monkeypatch.setattr(telegram, "call", fake_call)
    assert asyncio.run(telegram.poll_once()) == 1
    assert telegram._get_offset() == 78
    assert db.one("SELECT status FROM decisions WHERE id=?", (did,))["status"] == "executed"


def test_message_fits_telegram_limit():
    long = "😀" * 3000
    assert len(telegram.fit(long).encode("utf-16-le")) // 2 <= 4000
