"""The ONLY place that changes anything in your ad account.

Agents never call Meta directly: they create a decision, and this module re-checks every limit at the
moment of execution (settings may have changed, budgets may have moved) before touching Meta.
In dry-run mode nothing is sent to Meta; the decision is marked as a test run.
"""
from __future__ import annotations

import json
import threading
from datetime import date, datetime, timedelta
from typing import Any

from . import db, guards, meta_api, meta_mcp, settings_store

# One change at a time across the whole app: two decisions validated against the same "before"
# numbers could otherwise both pass the daily cap.
_EXEC_LOCK = threading.Lock()


def hours_since_last_change(object_id: str, dry_run: bool | None = None) -> float | None:
    """Test-mode changes only count toward the cooldown while test mode is on."""
    if dry_run is None:
        dry_run = bool(settings_store.get_all()["dry_run"])
    r = db.one("SELECT created_at FROM budget_changes WHERE object_id = ? AND dry_run = ? ORDER BY id DESC LIMIT 1",
               (object_id, int(dry_run)))
    if not r:
        return None
    return (datetime.now().astimezone() - datetime.fromisoformat(r["created_at"])).total_seconds() / 3600


def budget_context(meta) -> dict[str, Any]:
    objs = meta.budget_objects()
    # Count everything that can spend, not only ACTIVE (in review / in process objects start spending later),
    # and lifetime budgets spread over their remaining days.
    active = [o for o in objs if o["status"] not in meta_api.NOT_SPENDING and o["daily_budget"] > 0]
    today = date.today()
    next_month = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
    return {
        "objects": objs,
        "active_total_daily": round(sum(o["daily_budget"] for o in active)
                                    + sum(o.get("lifetime_daily", 0) for o in objs if o["status"] not in meta_api.NOT_SPENDING), 2),
        "month_spend": round(meta.month_spend(), 2),
        "days_left_incl_today": (next_month - today).days,
    }


def validate_action(action: dict[str, Any], s: dict[str, Any], meta) -> tuple[bool, str, dict[str, Any]]:
    """Returns (ok, reason, enriched_action). Used when an agent proposes AND right before executing."""
    kind = action.get("type")
    oid = str(action.get("object_id", ""))
    if kind not in ("pause", "budget") or not meta_api.valid_object_id(oid):
        return False, "أكشن مش معروف أو رقم مش صالح", action
    ctx = budget_context(meta)
    obj = next((o for o in ctx["objects"] if o["id"] == oid), None)
    if obj is None:
        return False, "الحاجة دي مش موجودة في حساب الإعلانات بتاعك", action
    enriched = {**action, "object_id": oid, "object_name": obj["name"], "level": obj["level"],
                "result_type": obj.get("result_type"), "result_label": obj.get("result_label") or meta_api.label(obj.get("result_type"))}
    if kind == "pause":
        if obj["status"] in meta_api.NOT_SPENDING:
            return False, "هي أصلاً مش شغالة", enriched
        return True, "", {**enriched, "perm_key": "perm_pause"}
    try:
        new_budget = round(float(action.get("new_daily_budget")), 2)
    except (TypeError, ValueError):
        return False, "الـbudget الجديد لازم يكون رقم", enriched
    old = obj["daily_budget"]
    spending = obj["status"] not in meta_api.NOT_SPENDING
    check = guards.check_budget_change(
        s, old_budget=old, new_budget=new_budget,
        active_total_daily=ctx["active_total_daily"] if spending else ctx["active_total_daily"] + old,
        month_spend=ctx["month_spend"], days_left_incl_today=ctx["days_left_incl_today"],
        hours_since_last_change=hours_since_last_change(oid, bool(s["dry_run"])),
    )
    if not check.ok:
        return False, check.reason, enriched
    perm_key = "perm_budget_up" if new_budget > old else "perm_budget_down"
    return True, "", {**enriched, "old_daily_budget": old, "new_daily_budget": new_budget, "perm_key": perm_key}


def execute(decision_id: int, by_agent: bool = False) -> dict[str, Any]:
    with _EXEC_LOCK:
        return _execute(decision_id, by_agent)


def _execute(decision_id: int, by_agent: bool) -> dict[str, Any]:
    # Claim the decision atomically so it can never run twice (double click, agent + owner at once).
    if db.update("UPDATE decisions SET status='executing' WHERE id = ? AND status IN ('pending','approved')", (decision_id,)) != 1:
        raise ValueError("القرار ده اتنفذ أو اتقفل قبل كده")
    d = db.one("SELECT * FROM decisions WHERE id = ?", (decision_id,))
    s = settings_store.get_all()
    if by_agent and not s["agents_enabled"]:
        db.execute("UPDATE decisions SET status='failed', exec_note=? WHERE id=?", ("الفريق متوقف من الإعدادات", decision_id))
        return {"status": "failed", "reason": "الفريق متوقف"}
    action = json.loads(d["action"] or "{}")
    if action.get("type") == "mcp_call":
        return _execute_mcp(decision_id, action, s, by_agent)
    if action.get("type") not in ("pause", "budget"):
        db.execute("UPDATE decisions SET status='manual', executed_at=?, exec_note=? WHERE id=?",
                   (db.now(), "اقتراح بيتنفذ بإيدك في Ads Manager", decision_id))
        return {"status": "manual"}
    dry = bool(s["dry_run"])
    try:
        meta = meta_api.get_client()
        ok, reason, act = validate_action(action, s, meta)
        if not ok:
            db.execute("UPDATE decisions SET status='failed', exec_note=? WHERE id=?", (f"اتمنع وقت التنفيذ: {reason}", decision_id))
            return {"status": "failed", "reason": reason}
        if by_agent and s[act["perm_key"]] != "auto":  # the owner took the permission away meanwhile
            db.execute("UPDATE decisions SET status='pending', mode='ask', exec_note=? WHERE id=?",
                       ("الصلاحية اتغيرت، فالقرار مستني موافقتك", decision_id))
            return {"status": "pending", "reason": "permission changed"}
        if act["type"] == "pause":
            if not dry:
                meta.set_status(act["object_id"], "PAUSED")
        else:
            if not dry:
                meta.set_daily_budget(act["object_id"], act["new_daily_budget"])
            db.execute("INSERT INTO budget_changes(object_id, old_budget, new_budget, created_at, decision_id, dry_run) VALUES (?,?,?,?,?,?)",
                       (act["object_id"], act["old_daily_budget"], act["new_daily_budget"], db.now(), decision_id, int(dry)))
    except meta_api.MetaError as e:
        db.execute("UPDATE decisions SET status='failed', exec_note=? WHERE id=?", (str(e)[:300], decision_id))
        return {"status": "failed", "reason": str(e)}
    except Exception as e:  # never leave a decision stuck in 'executing'
        db.execute("UPDATE decisions SET status='failed', exec_note=? WHERE id=?", (f"خطأ غير متوقع: {type(e).__name__}", decision_id))
        return {"status": "failed", "reason": type(e).__name__}
    note = "وضع التجربة: متبعتش حاجة لـMeta" if dry else "اتنفذ في Meta"
    db.execute("UPDATE decisions SET status='executed', executed_at=?, exec_note=? WHERE id=?", (db.now(), note, decision_id))
    return {"status": "executed", "dry_run": dry}


def _execute_mcp(decision_id: int, action: dict, s: dict, by_agent: bool) -> dict[str, Any]:
    def fail(why: str) -> dict:
        db.execute("UPDATE decisions SET status='failed', exec_note=? WHERE id=?", (why[:300], decision_id))
        return {"status": "failed", "reason": why}
    try:
        if not s["mcp_enabled"]:
            return fail("الـMCP بتاع Meta مقفول من الإعدادات")
        name = str(action.get("tool", ""))
        args = action.get("args") or {}
        tool_def = next((t for t in meta_mcp.tools() if t["name"] == name), None)
        if tool_def is None:
            return fail("الأداة دي مش موجودة في الـMCP بتاع Meta")
        cls = meta_mcp.effective_class(tool_def, s["mcp_overrides"])
        if cls not in ("write", "write_auto"):
            return fail("الأداة دي مش مسموح تكتب بيها (اتغيرت من الإعدادات)")
        problem = meta_mcp.check_args(tool_def, args)
        if problem:
            return fail(problem)
        mode = s.get(f"mcp_{action.get('agent_short', '')}", "off")
        if by_agent and not (mode in ("write_ask", "write_auto") and cls == "write_auto" and meta_mcp.auto_allowed(name, args)):
            db.execute("UPDATE decisions SET status='pending', mode='ask', exec_note=? WHERE id=?",
                       ("محتاج موافقتك", decision_id))
            return {"status": "pending", "reason": "needs owner"}
        if s["dry_run"]:
            db.execute("UPDATE decisions SET status='executed', executed_at=?, exec_note=? WHERE id=?",
                       (db.now(), "وضع التجربة: متبعتش حاجة لـMeta", decision_id))
            return {"status": "executed", "dry_run": True}
        res = meta_mcp.client().call_tool(name, args)
        text = meta_mcp.result_text(res)
        if res.get("isError"):
            return fail("Meta رفضت: " + guards.sanitize(text, 250))
        db.execute("UPDATE decisions SET status='executed', executed_at=?, exec_note=? WHERE id=?",
                   (db.now(), "اتنفذ في Meta: " + guards.sanitize(text, 250), decision_id))
        return {"status": "executed", "dry_run": False, "result": text[:4000]}
    except meta_mcp.MetaMCPError as e:
        return fail(str(e))
    except Exception as e:
        return fail(f"خطأ غير متوقع: {type(e).__name__}")


def recover_interrupted() -> int:
    """At startup nothing can be executing; a leftover means the app stopped mid-call."""
    return db.update("UPDATE decisions SET status='failed', exec_note=? WHERE status='executing'",
                     ("التنفيذ اتقطع (البرنامج اتقفل). راجع Ads Manager قبل ما تعيده",))


def approve(decision_id: int) -> dict[str, Any]:
    d = db.one("SELECT * FROM decisions WHERE id = ?", (decision_id,))
    if not d or db.update("UPDATE decisions SET status='approved', resolved_at=? WHERE id=? AND status='pending'",
                          (db.now(), decision_id)) != 1:
        raise ValueError("القرار ده مش مستني موافقة")
    result = execute(decision_id)
    _tell_agent(d, "وافق على" if result["status"] in ("executed", "manual") else "وافق بس التنفيذ اتمنع في", result.get("reason", ""))
    return result


def reject(decision_id: int, note: str = "") -> None:
    d = db.one("SELECT * FROM decisions WHERE id = ?", (decision_id,))
    note = guards.sanitize(note, 300)
    if not d or db.update("UPDATE decisions SET status='rejected', resolved_at=?, exec_note=? WHERE id=? AND status='pending'",
                          (db.now(), note, decision_id)) != 1:
        raise ValueError("القرار ده مش مستني موافقة")
    _tell_agent(d, "رفض", note)


def _tell_agent(d: dict, verb: str, note: str) -> None:
    # Only the id and the owner's own words: the title may contain an ad name, i.e. outside text.
    body = f"صاحب البيزنس {verb} القرار #{d['id']}" + (f" · ملاحظته: {note}" if note else "")
    db.execute("INSERT INTO messages(from_agent, to_agent, body, created_at) VALUES ('owner', ?, ?, ?)",
               (d["agent"], body, db.now()))
