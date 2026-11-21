"""Profit (not ROAS), creative fatigue and audience breakdowns: the numbers behind better decisions.

ROAS counts revenue. Profit counts what is left after product cost, shipping, returns and ads.
A sale that comes back costs you shipping twice and earns nothing, so a "cheap" ad that brings
many returns can lose money while showing a great ROAS.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta
from typing import Any

from . import db, guards, meta_api, settings_store

PERIOD_FOR_DAYS = {7: "last_7d", 14: "last_14d", 30: "last_30d"}


def _norm(name: str) -> str:
    # same cleaning as when a product or an order is saved, so names always match
    return guards.sanitize(name or "", 200).casefold()


# ---------------------------------------------------------------- products
def list_products() -> list[dict]:
    return db.rows("SELECT id, name, unit_cost, updated_at FROM products ORDER BY name")


def set_product(name: str, unit_cost: Any) -> int:
    name = guards.sanitize(name, 120)
    try:
        cost = float(unit_cost)
    except (TypeError, ValueError):
        raise ValueError("التكلفة لازم تكون رقم") from None
    if not name or not 0 <= cost <= 10_000_000 or cost != cost:
        raise ValueError("اسم المنتج والتكلفة مطلوبين")
    existing = next((p for p in list_products() if _norm(p["name"]) == _norm(name)), None)
    if existing:
        db.execute("UPDATE products SET unit_cost = ?, updated_at = ? WHERE id = ?", (cost, db.now(), existing["id"]))
        return existing["id"]
    return db.execute("INSERT INTO products(name, unit_cost, updated_at) VALUES (?,?,?)", (name, cost, db.now()))


def delete_product(pid: int) -> None:
    db.execute("DELETE FROM products WHERE id = ?", (pid,))


# ---------------------------------------------------------------- profit
def _order_cost(order: dict, costs: dict[str, float], s: dict) -> tuple[float, bool]:
    """(product cost, known?) — known when the owner entered a cost for this product name."""
    c = costs.get(_norm(order["product"]))
    if c is not None:
        return c, True
    return order["amount"] * s["cogs_pct"] / 100, False


def historical_return_rate() -> float | None:
    """Share of finished orders (delivered or returned) that came back, last 90 days. None if too few to trust."""
    since = (datetime.now() - timedelta(days=90)).date().isoformat()
    r = db.one("""SELECT SUM(status='returned') AS ret, SUM(status IN ('returned','delivered')) AS done
                  FROM orders WHERE created_at >= ?""", (since,)) or {}
    done = r.get("done") or 0
    return (r.get("ret") or 0) / done if done >= 10 else None


def profit_summary(s: dict, days: int, meta=None) -> dict[str, Any]:
    days = days if days in PERIOD_FOR_DAYS else 7
    # same window as Meta's last_Nd presets: the N full days before today
    start = (datetime.now().date() - timedelta(days=days)).isoformat()
    end = datetime.now().date().isoformat()
    orders = db.rows("""SELECT id, product, amount, status, source FROM orders
                        WHERE substr(created_at,1,10) >= ? AND substr(created_at,1,10) < ? AND status != 'cancelled'""", (start, end))
    hist_rate = historical_return_rate()
    costs = {_norm(p["name"]): p["unit_cost"] for p in list_products()}
    by_source: dict[str, dict] = {}
    tot = {"orders": 0, "returned": 0, "revenue": 0.0, "product_cost": 0.0, "shipping": 0.0, "returns_cost": 0.0, "estimated_costs": 0}
    for o in orders:
        src = o["source"] or "غير معروف"
        b = by_source.setdefault(src, {"source": src, "orders": 0, "returned": 0, "revenue": 0.0, "margin": 0.0})
        tot["orders"] += 1
        b["orders"] += 1
        if o["status"] == "returned":
            tot["returned"] += 1
            b["returned"] += 1
            tot["returns_cost"] += s["return_cost"]
            b["margin"] -= s["return_cost"]
            continue
        cost, known = _order_cost(o, costs, s)
        tot["estimated_costs"] += 0 if known else 1
        # not delivered yet: some will come back (cash on delivery refusals), so expect your usual return rate
        keep = 1.0
        if o["status"] in ("new", "confirmed", "shipped") and hist_rate:
            keep = 1 - hist_rate
            tot["returns_cost"] += s["return_cost"] * hist_rate
            b["margin"] -= s["return_cost"] * hist_rate
        tot["revenue"] += o["amount"] * keep
        tot["product_cost"] += cost * keep
        tot["shipping"] += s["shipping_cost"] * keep
        b["revenue"] += o["amount"] * keep
        b["margin"] += (o["amount"] - cost - s["shipping_cost"]) * keep
    gross = tot["revenue"] - tot["product_cost"] - tot["shipping"] - tot["returns_cost"]
    n = tot["orders"]
    margin_per_order = gross / n if n else None
    main_type = s["result_type"] if s["result_type"] in meta_api.RESULT_TYPES else "purchase"
    ad_spend, ad_error, ad_results = None, "", None
    if meta is not None:
        try:
            main_type = meta.main_type(s["result_type"])
            rows = meta.insights("account", PERIOD_FOR_DAYS[days], main_type)
            ad_spend = round(float(rows[0]["spend"]), 2) if rows else 0.0
            ad_results = rows[0]["results"] if rows else 0
        except meta_api.MetaError as e:
            ad_error = str(e)
    net = gross - ad_spend if ad_spend is not None else None
    breakeven = margin_per_order
    spec = meta_api.RESULT_TYPES.get(main_type, {})
    per_order_target = main_type == "purchase"
    suggested = round(breakeven * (1 - s["target_profit_share"] / 100), 2) if per_order_target and breakeven and breakeven > 0 else None
    # Ads that bring messages, leads or calls: the sale happens later (on WhatsApp, on the phone). Orders you
    # record tell us how many of those it takes to make one order, so we can price one message in real money.
    per_result = None
    if spec.get("funnel") == "lead" and n and ad_results and breakeven and breakeven > 0:
        results_per_order = ad_results / n
        be_result = breakeven / results_per_order
        current = settings_store.target_for(s, main_type)
        per_result = {
            "result_type": main_type, "result_label": spec["label"],
            "results_per_order": round(results_per_order, 1),
            "breakeven_cost_per_result": round(be_result, 2),
            "suggested_target": round(be_result * (1 - s["target_profit_share"] / 100), 2),
            "current_target": current,
            "target_above_breakeven": bool(current and current > be_result),
        }
    sources = []
    for b in by_source.values():
        b["return_pct"] = round(b["returned"] / b["orders"] * 100, 1) if b["orders"] else None
        b["margin_per_order"] = round(b["margin"] / b["orders"], 2) if b["orders"] else None
        b["revenue"] = round(b["revenue"], 2)
        b["margin"] = round(b["margin"], 2)
        sources.append(b)
    sources.sort(key=lambda x: x["margin"], reverse=True)
    return {
        "days": days, "orders": n, "returned": tot["returned"],
        "return_pct": round(tot["returned"] / n * 100, 1) if n else None,
        "revenue": round(tot["revenue"], 2), "product_cost": round(tot["product_cost"], 2),
        "shipping": round(tot["shipping"], 2), "returns_cost": round(tot["returns_cost"], 2),
        "gross_before_ads": round(gross, 2), "ad_spend": ad_spend, "ad_error": ad_error,
        "net_profit": round(net, 2) if net is not None else None,
        "margin_per_order_before_ads": round(margin_per_order, 2) if margin_per_order is not None else None,
        "breakeven_cost_per_order": round(breakeven, 2) if breakeven is not None else None,
        "suggested_target_cpr": suggested, "current_target_cpr": s["target_cpr"],
        "target_above_breakeven": bool(per_order_target and breakeven is not None and s["target_cpr"] > breakeven),
        "expected_return_rate_used": round(hist_rate * 100, 1) if hist_rate else None,
        "orders_with_estimated_cost": tot["estimated_costs"],
        "ads_main_result": main_type, "ads_main_result_label": meta_api.label(main_type),
        "real_cost_per_order": round(ad_spend / n, 2) if ad_spend is not None and n else None,
        "per_result": per_result,
        "by_source": sources[:15],
        "note": "Break-even = average profit per order before ads (after product cost, shipping and returns). "
                "Paying more than this for one order loses money. Orders include non-ad orders. Orders not delivered yet "
                "are discounted by the usual return rate. Cancelled orders are excluded here but Meta may still count them. "
                + ("" if per_order_target else
                   f"Most of the budget buys {main_type}, not sales: cost per {main_type} is NOT comparable to profit per order. "
                   "Use per_result (results needed per recorded order → break-even per result) when it is present; "
                   "real_cost_per_order = ad spend / recorded orders. Awareness and engagement ads are not judged by orders."),
    }


# ---------------------------------------------------------------- creative fatigue
def fatigue_report(s: dict, meta) -> list[dict]:
    """Per ad: CTR last 3 days vs last 14 days, and frequency. Tired ads are the ones to refresh first."""
    recent = {r["id"]: r for r in meta.insights("ad", "last_3d", s["result_type"])}
    base = {r["id"]: r for r in meta.insights("ad", "last_14d", s["result_type"])}
    week = {r["id"]: r for r in meta.insights("ad", "last_7d", s["result_type"])}
    out = []
    for ad_id, b in base.items():
        r = recent.get(ad_id)
        if not r or not b.get("ctr_pct"):
            continue
        drop = round((b["ctr_pct"] - r["ctr_pct"]) / b["ctr_pct"] * 100, 1)
        freq = (week.get(ad_id) or b).get("frequency") or 0
        tired = freq >= s["rule_fatigue_freq"] and drop >= s["rule_fatigue_ctr_drop"]
        out.append({"ad_id": ad_id, "ad_name": b.get("name", ""), "campaign_name": b.get("campaign_name", ""),
                    "result_type": b.get("result_type"),
                    "ctr_last_3d": r["ctr_pct"], "ctr_last_14d": b["ctr_pct"], "ctr_drop_pct": drop,
                    "frequency_7d": freq, "spend_14d": b["spend"], "cost_per_result_3d": r.get("cost_per_result"),
                    "cost_per_result_14d": b.get("cost_per_result"), "tired": tired})
    out.sort(key=lambda x: (not x["tired"], -x["ctr_drop_pct"]))
    return out


# ---------------------------------------------------------------- breakdowns
def breakdown(s: dict, meta, dimension: str, period: str) -> dict:
    main_type = meta.main_type(s["result_type"])
    rows = meta.breakdown(dimension, period, main_type)
    total = sum(r["spend"] for r in rows) or 1
    for r in rows:
        r["spend_share_pct"] = round(r["spend"] / total * 100, 1)
    rows.sort(key=lambda r: r["spend"], reverse=True)
    # region/hour drop website (Pixel) results only; on-Facebook results like messages are still there
    has_results = meta_api.BREAKDOWNS[dimension][2] or not meta_api.RESULT_TYPES[main_type]["offsite"]
    return {"dimension": dimension, "period": period, "result_type": main_type, "result_label": meta_api.label(main_type),
            "target_cost_per_result": settings_store.target_for(s, main_type),
            "rows": rows[:40], "results_available": has_results,
            "note": "" if has_results else "Meta does not report website results for this breakdown; compare CTR and spend share only."}
