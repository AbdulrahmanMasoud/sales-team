"""Ads that don't sell: messages, leads, calls, reach... Each campaign is judged by its own goal."""
import json

import httpx
import pytest

from app import analysis, business, db, meta_api, settings_store
from tests.test_tools import DEC, tools_for

MESSAGES_ADSET = "120210000000031"
VIDEO_ADSET = "120210000000041"


def row(actions, spend=100, **kw):
    return {"spend": spend, "impressions": 1000, "ctr": 1.0, "frequency": 1.5,
            "actions": [{"action_type": k, "value": v} for k, v in actions.items()], **kw}


# ---------------------------------------------------------------- counting results
def test_leads_count_once_even_when_meta_reports_overlapping_types():
    r = meta_api.summarize_row(row({"lead": 5, "onsite_conversion.lead_grouped": 3, "offsite_conversion.fb_pixel_lead": 2}), "adset", "leads")
    assert r["results"] == 5 and r["cost_per_result"] == 20 and r["result_label"] == "Lead (فورم)"


def test_calls_add_phone_and_messenger_calls():
    r = meta_api.summarize_row(row({"click_to_call_call_confirm": 2, "click_to_call_native_call_placed": 3}), "adset", "calls")
    assert r["results"] == 5 and r["cost_per_result"] == 20


def test_thruplays_come_from_their_own_field():
    r = meta_api.summarize_row(row({"video_view": 900}, video_thruplay_watched_actions=[{"action_type": "video_view", "value": "200"}]),
                               "adset", "thruplays")
    assert r["results"] == 200 and r["cost_per_result"] == 0.5 and r["other_results"]["video_views"] == 900


def test_reach_is_priced_per_1000_people():
    r = meta_api.summarize_row(row({}, spend=100, reach=20000), "campaign", "reach")
    assert r["results"] == 20000 and r["cost_per_result"] == 5.0


def test_other_results_are_shown_next_to_the_main_one():
    r = meta_api.summarize_row(row({"purchase": 2, "onsite_conversion.messaging_conversation_started_7d": 9}), "adset", "purchase")
    assert r["results"] == 2 and r["other_results"] == {"messages": 9}


def test_mixed_rows_have_no_cost_per_result():
    r = meta_api.summarize_row(row({"purchase": 2}), "campaign", "mixed")
    assert r["results"] is None and r["cost_per_result"] is None and r["result_type"] == "mixed"


# ---------------------------------------------------------------- knowing each campaign's goal
@pytest.mark.parametrize("goal,event,expected", [
    ("OFFSITE_CONVERSIONS", "PURCHASE", "purchase"), ("OFFSITE_CONVERSIONS", "LEAD", "leads"),
    ("OFFSITE_CONVERSIONS", "COMPLETE_REGISTRATION", "registrations"), ("OFFSITE_CONVERSIONS", "SOMETHING_ELSE", None),
    ("CONVERSATIONS", None, "messages"), ("LEAD_GENERATION", None, "leads"), ("QUALITY_CALL", None, "calls"),
    ("THRUPLAY", None, "thruplays"), ("TWO_SECOND_CONTINUOUS_VIDEO_VIEWS", None, "video_views"), ("REACH", None, "reach"), ("POST_ENGAGEMENT", None, "engagement"),
    ("PAGE_LIKES", None, "page_likes"), ("LANDING_PAGE_VIEWS", None, "landing_page_views"), ("APP_INSTALLS", None, None),
])
def test_goal_type(goal, event, expected):
    assert meta_api.goal_type(goal, {"custom_event_type": event} if event else None) == expected


def test_campaign_goal_and_main_goal():
    objs = meta_api.attach_goal_types([
        {"id": "1", "level": "campaign", "status": "ACTIVE", "daily_budget": 0},
        {"id": "11", "level": "adset", "campaign_id": "1", "status": "ACTIVE", "daily_budget": 100, "result_type": "messages"},
        {"id": "12", "level": "adset", "campaign_id": "1", "status": "ACTIVE", "daily_budget": 100, "result_type": "engagement"},
        {"id": "2", "level": "campaign", "status": "ACTIVE", "daily_budget": 500},
        {"id": "21", "level": "adset", "campaign_id": "2", "status": "ACTIVE", "daily_budget": 0, "result_type": "purchase"},
    ])
    assert next(o for o in objs if o["id"] == "1")["result_type"] == "mixed"
    assert next(o for o in objs if o["id"] == "2")["result_label"] == "بيعة"
    assert meta_api.main_type_of(objs) == "purchase"  # 500 a day on the sales campaign (CBO)


def test_real_client_reads_each_ad_set_goal(monkeypatch):
    seen = {}

    def handler(req: httpx.Request):
        path = req.url.path
        if path.endswith("/adsets"):
            return httpx.Response(200, json={"data": [
                {"id": "111", "name": "رسايل", "effective_status": "ACTIVE", "daily_budget": "20000", "campaign_id": "11",
                 "optimization_goal": "CONVERSATIONS"},
                {"id": "222", "name": "بيع", "effective_status": "ACTIVE", "daily_budget": "50000", "campaign_id": "22",
                 "optimization_goal": "OFFSITE_CONVERSIONS", "promoted_object": {"pixel_id": "9", "custom_event_type": "PURCHASE"}}]})
        if path.endswith("/campaigns"):
            return httpx.Response(200, json={"data": [{"id": "11", "name": "c1", "effective_status": "ACTIVE"},
                                                      {"id": "22", "name": "c2", "effective_status": "ACTIVE"}]})
        if path.endswith("/insights") and "breakdowns" in req.url.params:
            seen["filtering"] = req.url.params.get("filtering", "")
            return httpx.Response(200, json={"data": [{"age": "25-34", "spend": "100", "actions": [{"action_type": "purchase", "value": "2"}]}]})
        if path.endswith("/insights"):
            assert "adset_id" in req.url.params["fields"]
            return httpx.Response(200, json={"data": [
                {"adset_id": "111", "adset_name": "رسايل", "spend": "150", "actions": [
                    {"action_type": "onsite_conversion.messaging_conversation_started_7d", "value": "10"}]},
                {"adset_id": "222", "adset_name": "بيع", "spend": "300", "actions": [{"action_type": "purchase", "value": "3"}]}]})
        return httpx.Response(200, json={"currency": "EGP"})
    orig = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: orig(transport=httpx.MockTransport(handler), **kw))
    c = meta_api.MetaClient("TOKEN", "act_1", "v26.0")
    rows = {r["id"]: r for r in c.insights("adset", "yesterday", "auto")}
    assert rows["111"]["result_type"] == "messages" and rows["111"]["cost_per_result"] == 15
    assert rows["222"]["result_type"] == "purchase" and rows["222"]["cost_per_result"] == 100
    assert c.main_type() == "purchase"
    # breakdowns only count the sales ad sets, so the messages campaign's money doesn't inflate cost per sale
    bd = c.breakdown("age", "last_7d", "auto")
    assert '"222"' in seen["filtering"] and '"111"' not in seen["filtering"] and bd[0]["result_type"] == "purchase"
    # forcing one type still works
    assert {r["result_type"] for r in c.insights("adset", "yesterday", "messages")} == {"messages"}


# ---------------------------------------------------------------- settings
def test_targets_validation():
    s = settings_store.update({"targets": {"messages": "15", "leads": "", "reach": 20}})
    assert s["targets"] == {"messages": 15.0, "reach": 20.0}
    for bad in ({"purchase": 50}, {"nonsense": 1}, {"messages": -3}, {"messages": "nan"}, ["messages"]):
        with pytest.raises(settings_store.SettingsError):
            settings_store.update({"targets": bad})
    assert settings_store.target_for(s, "purchase") == s["target_cpr"]
    assert settings_store.target_for(s, "calls") is None


# ---------------------------------------------------------------- what the agents see and may do
@pytest.fixture
def full(monkeypatch):
    fake = meta_api.FakeMeta(extras=True)
    monkeypatch.setattr(meta_api, "get_client", lambda: fake)
    settings_store.update({"daily_cap": 5000, "monthly_cap": 200000})
    return fake


def test_each_row_carries_its_own_goal_and_target(full):
    settings_store.update({"targets": {"messages": 15}})
    _, t = tools_for("media_buyer", full)
    data = t("get_ad_performance", {"level": "adset", "period": "last_7d"})["content"][0]["text"]
    rows = {r["id"]: r for r in json.loads(data[data.index("{"):data.rindex("}") + 1])["rows"]}
    assert rows[MESSAGES_ADSET]["result_type"] == "messages" and rows[MESSAGES_ADSET]["target_cost_per_result"] == 15
    assert rows[VIDEO_ADSET]["funnel"] == "awareness" and rows[VIDEO_ADSET]["target_cost_per_result"] is None
    assert rows["120210000000011"]["target_cost_per_result"] == settings_store.get_all()["target_cpr"]


def test_awareness_campaign_is_never_changed_automatically(full):
    _, t = tools_for("media_buyer", full, perm_pause="auto", dry_run=False)
    t("pause_object", {"object_id": VIDEO_ADSET, **DEC})
    d = db.one("SELECT status, mode, action FROM decisions ORDER BY id DESC LIMIT 1")
    assert d["status"] == "pending" and d["mode"] == "ask"
    assert json.loads(d["action"])["result_label"] == "تفاعل"
    assert full._objects[VIDEO_ADSET]["status"] == "ACTIVE"
    # a messages campaign brings customers, so the owner's "auto" applies
    t("pause_object", {"object_id": MESSAGES_ADSET, **DEC})
    assert full._objects[MESSAGES_ADSET]["status"] == "PAUSED"


def test_profit_prices_one_message_from_recorded_orders(full):
    from datetime import date, timedelta
    settings_store.update({"result_type": "messages", "targets": {"messages": 500}, "cogs_pct": 40, "shipping_cost": 50})
    for i in range(8):
        business.add_order({"name": f"عميل {i}", "phone": f"0100000010{i}", "product": "كورس", "amount": 1000,
                            "payment": "online", "status": "delivered", "source": "رسايل",
                            "created_at": (date.today() - timedelta(days=2)).isoformat()}, "20")
    p = analysis.profit_summary(settings_store.get_all(), 7, full)
    pr = p["per_result"]
    assert p["ads_main_result"] == "messages" and pr["results_per_order"] == 13.0  # 104 conversations / 8 orders
    assert pr["breakeven_cost_per_result"] == round(550 / 13, 2)
    assert pr["target_above_breakeven"]  # 500 per conversation is far above what one is worth
    assert p["real_cost_per_order"] == round(p["ad_spend"] / 8, 2)
    assert p["suggested_target_cpr"] is None  # the per-sale target does not apply here


def test_breakdown_keeps_messages_for_region(full):
    settings_store.update({"result_type": "messages"})
    bd = analysis.breakdown(settings_store.get_all(), full, "region", "last_7d")
    assert bd["results_available"] and bd["result_type"] == "messages"
    settings_store.update({"result_type": "purchase"})
    assert not analysis.breakdown(settings_store.get_all(), full, "region", "last_7d")["results_available"]


# ---------------------------------------------------------------- upgrading from an older version
def _old_setting(k, v):
    db.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)", (k, json.dumps(v)))


def test_old_messages_setup_keeps_its_target_as_the_messages_target():
    _old_setting("result_type", "messages")
    _old_setting("target_cpr", 12)
    settings_store.migrate()
    s = settings_store.get_all()
    assert s["result_type"] == "auto" and s["targets"] == {"messages": 12.0} and s["target_cpr"] == 110.0
    settings_store.update({"result_type": "purchase"})  # a choice made after the upgrade is never undone
    settings_store.migrate()
    assert settings_store.get_all()["result_type"] == "purchase"


def test_old_purchase_setup_becomes_auto_and_keeps_its_sale_target():
    _old_setting("result_type", "purchase")
    _old_setting("target_cpr", 95)
    settings_store.migrate()
    s = settings_store.get_all()
    assert s["result_type"] == "auto" and s["target_cpr"] == 95.0 and s["targets"] == {}


def test_old_database_gets_new_columns_and_keeps_its_rows(tmp_path, monkeypatch):
    import sqlite3
    from app import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "old")
    (tmp_path / "old").mkdir()
    old = sqlite3.connect(config.db_path())
    old.execute("CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, unit_cost REAL NOT NULL)")
    old.execute("INSERT INTO products(name, unit_cost) VALUES ('هودي', 200)")
    old.commit(); old.close()
    db.init()
    row = db.one("SELECT * FROM products")
    assert row["name"] == "هودي" and row["unit_cost"] == 200 and "updated_at" in row
    db.init()  # running again changes nothing
