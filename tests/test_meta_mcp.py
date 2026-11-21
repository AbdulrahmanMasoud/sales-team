"""Meta's official MCP, proxied: per-agent modes, per-tool classes, approvals, dry run, and the raw client."""
import asyncio
import json

import httpx
import pytest

from app import db, executor, meta_mcp, settings_store, tools
from tests.test_tools import DEC, tools_for  # noqa: F401

WHY = {"decision_reason": "الـpixel مش بيبعت Purchase", "decision_hypothesis": "التحويلات ترجع تظهر",
       "decision_metric": "عدد الـPurchase events", "decision_check_in_days": 2}


@pytest.fixture
def calls(monkeypatch):
    seen = []
    fake = meta_mcp.FakeMCP()
    orig = fake.call_tool

    def spy(name, args):
        seen.append((name, args))
        return orig(name, args)
    fake.call_tool = spy
    monkeypatch.setattr(meta_mcp, "_client", fake)
    return seen


def names(agent, fresh, **kw):
    s = {"mcp_enabled": True, **kw}
    settings_store.update(s)
    ctx, t = tools_for(agent, fresh)
    ctx.mcp_tools = meta_mcp.tools()
    # rebuild with the MCP list in place
    captured = {}
    orig = tools.create_sdk_mcp_server

    def spy(name, version, tools):  # noqa: A002
        for x in tools:
            captured[x.name] = x.handler
        return orig(name=name, version=version, tools=tools)
    tools.create_sdk_mcp_server = spy
    try:
        tools.build(ctx)
    finally:
        tools.create_sdk_mcp_server = orig
    return ctx, captured


def run(h, args):
    return asyncio.run(h(args))


def test_classification():
    assert meta_mcp.classify({"name": "list_campaigns"}) == "read"
    assert meta_mcp.classify({"name": "get_ad_account_insights"}) == "read"
    assert meta_mcp.classify({"name": "update_ad_set"}) == "write"
    assert meta_mcp.classify({"name": "get_and_update_budget"}) == "write"
    assert meta_mcp.classify({"name": "mystery_tool"}) == "write"  # unclear = write
    assert meta_mcp.classify({"name": "delete_ad", "annotations": {"readOnlyHint": True, "destructiveHint": True}}) == "write"
    assert meta_mcp.classify({"name": "x", "annotations": {"readOnlyHint": True}}) == "read"
    for sneaky in ("list-and-delete-ads", "get_and_submit_ad", "check_and_toggle_campaign", "list_ads_then_boost"):
        assert meta_mcp.classify({"name": sneaky}) == "write", sneaky
    assert meta_mcp.classify({"name": "x", "annotations": ["bad"]}) == "write"
    assert len("mcp__team__" + meta_mcp.safe_name("a" * 90)) <= 64


def test_modes_control_which_tools_exist(fresh, calls):
    _, h = names("media_buyer", fresh, mcp_mb="off")
    assert not any(n.startswith("meta_") for n in h)
    _, h = names("media_buyer", fresh, mcp_mb="read")
    assert "meta_diagnose_pixel" in h and "meta_update_ad_set" not in h and "meta_create_campaign" not in h
    _, h = names("media_buyer", fresh, mcp_mb="write_ask")
    assert "meta_update_ad_set" in h and "meta_create_campaign" in h
    _, h = names("media_buyer", fresh, mcp_mb="write_ask", mcp_overrides={"create_campaign": "blocked", "diagnose_pixel": "blocked"})
    assert "meta_create_campaign" not in h and "meta_diagnose_pixel" not in h
    _, h = names("followup", fresh)  # follow-up is off by default
    assert not any(n.startswith("meta_") for n in h)
    _, h = names("media_buyer", fresh, mcp_enabled=False)
    assert not any(n.startswith("meta_") for n in h)


def test_read_results_are_wrapped(fresh, calls):
    _, h = names("media_buyer", fresh, mcp_mb="read")
    out = run(h["meta_diagnose_pixel"], {})
    assert "untrusted_data" in out["content"][0]["text"] and calls == [("diagnose_pixel", {})]


def test_write_ask_goes_to_owner_then_runs_exact_args(fresh, calls):
    _, h = names("media_buyer", fresh, mcp_mb="write_ask", dry_run=False)
    out = run(h["meta_create_campaign"], {"name": "كوليكشن الشتا", "objective": "SALES", **WHY})
    assert "يوافق" in out["content"][0]["text"] and calls == []
    d = db.one("SELECT * FROM decisions")
    assert d["status"] == "pending" and d["kind"] == "meta_mcp"
    res = executor.approve(d["id"])
    assert res["status"] == "executed"
    assert calls == [("create_campaign", {"name": "كوليكشن الشتا", "objective": "SALES"})]  # decision_* fields stripped


def test_write_needs_reasons(fresh, calls):
    _, h = names("media_buyer", fresh, mcp_mb="write_ask")
    assert run(h["meta_create_campaign"], {"name": "x"}).get("is_error")


AUTO = {"mcp_overrides": {"update_ad_set": "write_auto", "create_campaign": "write_auto"}}


def test_dry_run_never_calls_meta(fresh, calls):
    _, h = names("media_buyer", fresh, mcp_mb="write_ask", dry_run=True, **AUTO)
    out = run(h["meta_update_ad_set"], {"ad_set_id": "123", "status": "PAUSED", **WHY})
    assert "التجربة" in out["content"][0]["text"] and calls == []


def test_auto_only_for_owner_listed_tools_and_safe_changes(fresh, calls):
    _, h = names("media_buyer", fresh, mcp_mb="write_ask", dry_run=False, **AUTO)
    run(h["meta_update_ad_set"], {"ad_set_id": "123", "daily_budget": 99999, **WHY})       # money
    run(h["meta_update_ad_set"], {"ad_set_id": "123", "status": "ACTIVE", **WHY})          # starts spending
    run(h["meta_create_campaign"], {"name": "x", **WHY})                                    # creating is never automatic
    assert calls == [] and db.one("SELECT COUNT(*) AS n FROM decisions WHERE status='pending'")["n"] == 3
    run(h["meta_update_ad_set"], {"ad_set_id": "123", "status": "PAUSED", **WHY})          # safe: pausing
    assert calls == [("update_ad_set", {"ad_set_id": "123", "status": "PAUSED"})]


def test_tools_not_listed_by_owner_always_ask(fresh, calls):
    _, h = names("media_buyer", fresh, mcp_mb="write_auto", dry_run=False)  # agent "auto" alone is not enough
    run(h["meta_update_ad_set"], {"ad_set_id": "123", "status": "PAUSED", **WHY})
    assert calls == [] and db.one("SELECT status FROM decisions")["status"] == "pending"


def test_unknown_long_or_hidden_args_refused(fresh, calls):
    _, h = names("media_buyer", fresh, mcp_mb="write_ask", **AUTO)
    assert run(h["meta_create_campaign"], {"name": "x", "spec": '{"daily_budget": 1}', **WHY}).get("is_error")
    assert run(h["meta_create_campaign"], {"name": "x" * 2000, **WHY}).get("is_error")
    assert run(h["meta_create_campaign"], {"name": "x\u202e", **WHY}).get("is_error")
    assert db.one("SELECT COUNT(*) AS n FROM decisions")["n"] == 0


def test_owner_sees_exactly_what_will_be_sent(fresh, calls):
    _, h = names("media_buyer", fresh, mcp_mb="write_ask", dry_run=False)
    run(h["meta_update_ad_set"], {"ad_set_id": "123", "status": "PAUSED", "daily_budget": 999999, **WHY})
    d = db.one("SELECT * FROM decisions")
    action = json.loads(d["action"])
    assert json.loads(action["details"]) == action["args"] == {"ad_set_id": "123", "status": "PAUSED", "daily_budget": 999999}
    executor.approve(d["id"])
    assert calls == [("update_ad_set", action["args"])]


def test_auto_writes_capped_per_run(fresh, calls):
    _, h = names("media_buyer", fresh, mcp_mb="write_ask", dry_run=False, **AUTO)
    for i in range(5):
        run(h["meta_update_ad_set"], {"ad_set_id": str(100 + i), "status": "PAUSED", **WHY})
    assert len(calls) == tools.MAX_AUTO_MCP_WRITES_PER_RUN
    assert db.one("SELECT COUNT(*) AS n FROM decisions WHERE status='pending'")["n"] == 2


def test_mode_or_class_changed_mid_run(fresh, calls):
    _, h = names("media_buyer", fresh, mcp_mb="write_ask", dry_run=False)
    settings_store.update({"mcp_mb": "read"})
    assert run(h["meta_create_campaign"], {"name": "x", **WHY}).get("is_error") and calls == []
    settings_store.update({"mcp_mb": "write_ask"})
    run(h["meta_create_campaign"], {"name": "x", **WHY})
    did = db.one("SELECT id FROM decisions WHERE status='pending'")["id"]
    settings_store.update({"mcp_overrides": {"create_campaign": "blocked"}})
    assert executor.approve(did)["status"] == "failed" and calls == []


def test_kill_switch_blocks_mcp_tools(fresh, calls):
    ctx, h = names("media_buyer", fresh, mcp_mb="write_auto", dry_run=False)
    settings_store.update({"agents_enabled": False})
    # the logging wrapper sits in front of every tool, MCP ones included
    server_tools = []
    orig = tools.create_sdk_mcp_server
    tools.create_sdk_mcp_server = lambda name, version, tools: server_tools.extend(tools) or orig(name=name, version=version, tools=tools)
    try:
        tools.build(ctx)
    finally:
        tools.create_sdk_mcp_server = orig
    t = next(x for x in server_tools if x.name == "meta_create_campaign")
    assert asyncio.run(t.handler({"name": "x", **WHY})).get("is_error") and calls == []


def test_settings_overrides_validated():
    with pytest.raises(settings_store.SettingsError):
        settings_store.update({"mcp_overrides": {"x": "sudo"}})
    with pytest.raises(settings_store.SettingsError):
        settings_store.update({"mcp_mb": "god_mode"})
    assert settings_store.update({"mcp_overrides": json.dumps({"x": "blocked"})})["mcp_overrides"] == {"x": "blocked"}


def test_real_client_protocol(monkeypatch):
    seen = []

    def handler(req: httpx.Request):
        body = json.loads(req.content)
        seen.append((body.get("method"), req.headers.get("authorization"), req.headers.get("mcp-session-id")))
        if body.get("method") == "initialize":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {}}, headers={"mcp-session-id": "S1"})
        if "id" not in body:
            return httpx.Response(202)
        if body["method"] == "tools/list":
            sse = "event: message\ndata: " + json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": {"tools": [{"name": "list_campaigns"}]}}) + "\n\n"
            return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})
        if body["method"] == "tools/call":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "error": {"message": "nope"}})
        return httpx.Response(400)
    orig = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: orig(transport=httpx.MockTransport(handler), **kw))
    c = meta_mcp.MCPClient("TKN")
    assert [t["name"] for t in c.list_tools()] == ["list_campaigns"]
    assert seen[0] == ("initialize", "Bearer TKN", None) and seen[2] == ("tools/list", "Bearer TKN", "S1")
    with pytest.raises(meta_mcp.MetaMCPError):
        c.call_tool("list_campaigns", {})

    def deny(req):
        return httpx.Response(401)
    monkeypatch.setattr(httpx, "Client", lambda **kw: orig(transport=httpx.MockTransport(deny), **kw))
    with pytest.raises(meta_mcp.MetaMCPError, match="token"):
        meta_mcp.MCPClient("BAD").list_tools()
