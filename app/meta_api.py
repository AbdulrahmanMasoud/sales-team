"""Meta Marketing API client (free) + a fake account for demo mode.

Only a handful of calls are used: read insights, list ad sets / campaigns, pause, change daily budget.
The access token never leaves this module: not in logs, not in prompts, not in errors.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import random
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import httpx

from . import config

PERIODS = ("today", "yesterday", "last_3d", "last_7d", "last_14d", "last_30d", "this_month")
LEVELS = ("campaign", "adset", "ad")
# Currencies whose minimum unit is the main unit (no x100). Everything else uses x100.
_NO_OFFSET = {"JPY", "KRW", "CLP", "COP", "CRC", "HUF", "ISK", "IDR", "PYG", "TWD", "VND", "UGX"}
_PURCHASE_TYPES = ("omni_purchase", "purchase", "offsite_conversion.fb_pixel_purchase", "onsite_web_purchase")
_MESSAGE_TYPES = ("onsite_conversion.messaging_conversation_started_7d",)

# What counts as one "result" for each kind of ad. Not every ad sells: a clinic pays for messages or calls,
# a new page pays for reach or likes. Each campaign is judged by what it was built to get.
#   actions: Meta action types. sum=False → take the first one present (they overlap, e.g. "lead" already
#            includes on-Facebook and website leads). sum=True → they are different events, add them.
#   offsite: measured on a website (Pixel). Meta drops these from region and hour breakdowns.
#   funnel:  sale | lead (a real person reached out) | traffic | awareness (never judged by sales)
#   per:     cost is shown per this many results (reach is priced per 1000 people)
RESULT_TYPES: dict[str, dict[str, Any]] = {
    "purchase": {"label": "بيعة", "actions": _PURCHASE_TYPES, "sum": False, "offsite": True, "funnel": "sale", "per": 1},
    "messages": {"label": "محادثة", "actions": _MESSAGE_TYPES, "sum": False, "offsite": False, "funnel": "lead", "per": 1},
    "leads": {"label": "Lead (فورم)", "actions": ("lead", "onsite_conversion.lead_grouped", "offsite_conversion.fb_pixel_lead"),
              "sum": False, "offsite": True, "funnel": "lead", "per": 1},
    "calls": {"label": "مكالمة", "actions": ("click_to_call_call_confirm", "click_to_call_native_call_placed"),
              "sum": True, "offsite": False, "funnel": "lead", "per": 1},
    "registrations": {"label": "تسجيل", "actions": ("omni_complete_registration", "complete_registration",
                                                     "offsite_conversion.fb_pixel_complete_registration"),
                      "sum": False, "offsite": True, "funnel": "lead", "per": 1},
    "landing_page_views": {"label": "زيارة صفحة", "actions": ("landing_page_view",), "sum": False, "offsite": True, "funnel": "traffic", "per": 1},
    "link_clicks": {"label": "ضغطة على الرابط", "actions": ("link_click",), "sum": False, "offsite": False, "funnel": "traffic", "per": 1},
    "engagement": {"label": "تفاعل", "actions": ("post_engagement",), "sum": False, "offsite": False, "funnel": "awareness", "per": 1},
    "video_views": {"label": "مشاهدة فيديو (3 ثواني)", "actions": ("video_view",), "sum": False, "offsite": False, "funnel": "awareness", "per": 1},
    # ThruPlay = watched to the end or at least 15 seconds. Meta reports it in its own field, not in "actions".
    "thruplays": {"label": "ThruPlay", "actions": ("video_view",), "list_field": "video_thruplay_watched_actions",
                  "sum": False, "offsite": False, "funnel": "awareness", "per": 1},
    "page_likes": {"label": "لايك للصفحة", "actions": ("like",), "sum": False, "offsite": False, "funnel": "awareness", "per": 1},
    "reach": {"label": "1000 وصول", "actions": (), "field": "reach", "sum": False, "offsite": False, "funnel": "awareness", "per": 1000},
}
# Shown next to each row so the agents see everything an ad brought, not only its main result.
_SIDE_RESULTS = ("purchase", "messages", "leads", "calls", "registrations", "landing_page_views", "engagement", "video_views", "thruplays", "page_likes")

# Ad set optimization goal → result type. Website conversions depend on the Pixel event it optimizes for.
GOAL_TYPES = {
    "VALUE": "purchase", "LEAD_GENERATION": "leads", "QUALITY_LEAD": "leads",
    "CONVERSATIONS": "messages", "MESSAGING_PURCHASE_CONVERSION": "messages", "MESSAGING_APPOINTMENT_CONVERSION": "messages",
    "QUALITY_CALL": "calls", "LANDING_PAGE_VIEWS": "landing_page_views", "LINK_CLICKS": "link_clicks",
    "POST_ENGAGEMENT": "engagement", "ENGAGED_USERS": "engagement", "THRUPLAY": "thruplays",
    "TWO_SECOND_CONTINUOUS_VIDEO_VIEWS": "video_views", "REACH": "reach", "IMPRESSIONS": "reach",
    "AD_RECALL_LIFT": "reach", "PAGE_LIKES": "page_likes",
}
PIXEL_EVENT_TYPES = {"PURCHASE": "purchase", "LEAD": "leads", "COMPLETE_REGISTRATION": "registrations"}
_ID_RE = re.compile(r"^\d{5,25}$")
# effective_status values that cannot spend. Everything else (ACTIVE, IN_PROCESS, WITH_ISSUES,
# PENDING_REVIEW, PREAPPROVED, ...) counts toward the daily total, because it may start spending.
NOT_SPENDING = {"PAUSED", "CAMPAIGN_PAUSED", "ADSET_PAUSED", "DELETED", "ARCHIVED", "DISAPPROVED"}
# Rate-limit error codes: retrying makes it worse, so stop and tell the owner.
_RATE_LIMIT_CODES = {4, 17, 32, 613} | set(range(80000, 80015))


# dimension -> (Meta breakdowns param, fields that name the segment, results available?)
BREAKDOWNS = {
    "age": ("age", ("age",), True),
    "gender": ("gender", ("gender",), True),
    "age_gender": ("age,gender", ("age", "gender"), True),
    "placement": ("publisher_platform,platform_position", ("publisher_platform", "platform_position"), True),
    # Meta does not return off-Meta actions (website purchases) for these two ("Type 1" breakdowns)
    "region": ("region", ("region",), False),
    "hour": ("hourly_stats_aggregated_by_advertiser_time_zone", ("hourly_stats_aggregated_by_advertiser_time_zone",), False),
}


class MetaError(Exception):
    pass


def _redact(text: str) -> str:
    return re.sub(r"(access_token=|appsecret_proof=)[^&\s\"']+", r"\1***", text)


def valid_object_id(object_id: str) -> bool:
    return bool(_ID_RE.match(str(object_id)))


def _pick_action(actions: list[dict] | None, types: tuple[str, ...], add: bool = False) -> float:
    if not actions:
        return 0.0
    by_type = {a.get("action_type"): a.get("value") for a in actions}
    total = 0.0
    for t in types:  # first match only (unless add), so the same purchase is never counted twice
        if t in by_type:
            try:
                v = float(by_type[t])
            except (TypeError, ValueError):
                v = 0.0
            if not add:
                return v
            total += v
    return total


def count_results(row: dict, rtype: str) -> float:
    spec = RESULT_TYPES[rtype]
    if spec.get("list_field"):
        return _pick_action(row.get(spec["list_field"]), spec["actions"], spec["sum"])
    if spec.get("field"):
        try:
            return float(row.get(spec["field"]) or 0)
        except (TypeError, ValueError):
            return 0.0
    return _pick_action(row.get("actions"), spec["actions"], spec["sum"])


def goal_type(optimization_goal: str | None, promoted_object: dict | None = None) -> str | None:
    """Which result an ad set is built to get, from its optimization goal. None when we can't tell."""
    goal = (optimization_goal or "").upper()
    if goal == "OFFSITE_CONVERSIONS":
        event = ((promoted_object or {}).get("custom_event_type") or "").upper()
        return PIXEL_EVENT_TYPES.get(event)
    return GOAL_TYPES.get(goal)


def label(rtype: str | None) -> str:
    if rtype in RESULT_TYPES:
        return RESULT_TYPES[rtype]["label"]
    return "أهداف مختلفة" if rtype == "mixed" else "مش معروف"


def attach_goal_types(objects: list[dict]) -> list[dict]:
    """Give each campaign the result type of its ad sets ("mixed" when they differ)."""
    by_campaign: dict[str, set] = {}
    for o in objects:
        if o["level"] == "adset" and o.get("result_type") and o.get("campaign_id"):
            by_campaign.setdefault(o["campaign_id"], set()).add(o["result_type"])
    for o in objects:
        if o["level"] == "campaign":
            types = by_campaign.get(o["id"], set())
            o["result_type"] = next(iter(types)) if len(types) == 1 else ("mixed" if types else None)
        o["result_label"] = label(o.get("result_type"))
    return objects


def main_type_of(objects: list[dict]) -> str:
    """The result most of today's budget is paying for. Used for account-wide numbers."""
    weight: dict[str, float] = {}
    for o in objects:
        t = o.get("result_type")
        if t in RESULT_TYPES and o.get("status") not in NOT_SPENDING:
            weight[t] = weight.get(t, 0.0) + (o.get("daily_budget") or o.get("lifetime_daily") or 0.0) + 0.001
    return max(weight, key=weight.get) if weight else "purchase"


def summarize_row(row: dict, level: str, result_type: str | None) -> dict[str, Any]:
    spend = float(row.get("spend") or 0)
    rtype = result_type if result_type in RESULT_TYPES else "mixed"
    revenue = _pick_action(row.get("action_values"), _PURCHASE_TYPES)
    results = count_results(row, rtype) if rtype != "mixed" else None
    per = RESULT_TYPES[rtype]["per"] if rtype != "mixed" else 1
    out = {
        "id": row.get(f"{level}_id") or row.get("id"),
        "name": row.get(f"{level}_name") or row.get("name") or "",
        "result_type": rtype,
        "result_label": label(rtype),
        "spend": round(spend, 2),
        "results": round(results, 2) if results is not None else None,
        "cost_per_result": round(spend / results * per, 2) if results else None,
        "revenue": round(revenue, 2),
        "roas": round(revenue / spend, 2) if spend and revenue else None,
        "impressions": int(float(row.get("impressions") or 0)),
        "ctr_pct": round(float(row.get("ctr") or 0), 2),
        "frequency": round(float(row.get("frequency") or 0), 2),
    }
    side = {t: round(n, 2) for t in _SIDE_RESULTS if t != rtype and (n := count_results(row, t))}
    if side:
        out["other_results"] = side
    if level != "campaign" and row.get("campaign_name"):
        out["campaign_name"] = row["campaign_name"]
    if row.get("date_start"):
        out["date"] = row["date_start"]
    return out


class MetaClient:
    def __init__(self, token: str, account_id: str, version: str, app_secret: str = ""):
        if not token or not account_id:
            raise MetaError("Meta مش متوصّل")
        self._token = token
        self.account_id = account_id
        self._base = f"https://graph.facebook.com/{version}"
        self._proof = hmac.new(app_secret.encode(), token.encode(), hashlib.sha256).hexdigest() if app_secret else ""
        self._currency: str | None = None
        self._cache: dict[str, tuple[float, Any]] = {}

    def _cached(self, key: str, ttl: float, fn):
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        val = fn()
        self._cache[key] = (time.time(), val)
        return val

    # -- transport
    def _auth(self) -> dict[str, str]:
        p = {"access_token": self._token}
        if self._proof:
            p["appsecret_proof"] = self._proof
        return p

    def _request(self, method: str, url: str, params: dict | None = None, data: dict | None = None) -> dict:
        last = ""
        for attempt in range(3):
            try:
                with httpx.Client(timeout=30) as http:
                    if method == "GET":
                        r = http.get(url, params={**(params or {}), **self._auth()} if params is not None else None)
                    else:
                        r = http.post(url, data={**(data or {}), **self._auth()})
            except httpx.HTTPError as e:
                last = type(e).__name__
                time.sleep(2 * (attempt + 1))
                continue
            if r.status_code in (429, 500, 502, 503, 504):
                last = f"HTTP {r.status_code}"
                time.sleep(3 * (attempt + 1))
                continue
            try:
                body = r.json()
            except ValueError:
                raise MetaError(f"رد مش مفهوم من Meta (HTTP {r.status_code})") from None
            if r.status_code >= 400 or "error" in body:
                err = body.get("error") or {}
                if err.get("code") in _RATE_LIMIT_CODES:
                    raise MetaError("Meta طالبة نستنى شوية (وصلنا لحد الطلبات). هيرجع يشتغل لوحده بعد دقايق.")
                raise MetaError(_redact(f"Meta رفضت الطلب: {err.get('message', f'HTTP {r.status_code}')}"))
            return body
        raise MetaError(f"مش قادر أوصل لـMeta ({last})")

    def _get_all(self, path: str, params: dict) -> list[dict]:
        out: list[dict] = []
        body = self._request("GET", f"{self._base}/{path}", params=params)
        out.extend(body.get("data", []))
        pages = 0
        while body.get("paging", {}).get("next") and pages < 20:
            parts = urlsplit(body["paging"]["next"])
            if parts.scheme != "https" or parts.netloc != "graph.facebook.com":
                break
            # rebuild the query ourselves so our own token and appsecret_proof are always the ones sent
            query = {k: v for k, v in parse_qsl(parts.query) if k not in ("access_token", "appsecret_proof")}
            body = self._request("GET", f"https://graph.facebook.com{parts.path}", params=query)
            out.extend(body.get("data", []))
            pages += 1
        return out

    # -- reads
    def currency(self) -> str:
        if self._currency is None:
            info = self._request("GET", f"{self._base}/{self.account_id}", params={"fields": "currency"})
            self._currency = info.get("currency", "USD")
        return self._currency

    def offset(self) -> int:
        return 1 if self.currency() in _NO_OFFSET else 100

    def account_info(self) -> dict:
        return self._cached("account", 600, lambda: self._request(
            "GET", f"{self._base}/{self.account_id}", params={"fields": "name,currency,timezone_name,account_status"}))

    def _type_for_rows(self, level: str, result_type: str):
        """result_type "auto" → each row is judged by what its own ad set / campaign is built to get."""
        if result_type != "auto":
            return lambda r: result_type if result_type in RESULT_TYPES else "purchase"
        try:
            objs = self.budget_objects()
        except MetaError:
            objs = []
        main = main_type_of(objs)
        if level == "account":
            return lambda r: main
        by_id = {o["id"]: o.get("result_type") for o in objs}
        key = "campaign_id" if level == "campaign" else "adset_id"
        return lambda r: by_id.get(str(r.get(key) or "")) or main

    def main_type(self, result_type: str = "auto") -> str:
        if result_type in RESULT_TYPES:
            return result_type
        try:
            return main_type_of(self.budget_objects())
        except MetaError:
            return "purchase"

    def insights(self, level: str, period: str, result_type: str, daily: bool = False) -> list[dict]:
        if level not in LEVELS and level != "account":
            raise MetaError("level مش صالح")
        if period not in PERIODS:
            raise MetaError("period مش صالح")
        fields = ["spend", "impressions", "reach", "ctr", "frequency", "actions", "action_values", "video_thruplay_watched_actions"]
        if level in LEVELS:
            fields += [f"{level}_id", f"{level}_name"]
            if level != "campaign":
                fields.append("campaign_name")
            if level == "ad":
                fields.append("adset_id")
        params = {"level": level, "date_preset": period, "fields": ",".join(fields), "limit": 500}
        if daily:
            params["time_increment"] = 1

        def fetch():
            raw = self._get_all(f"{self.account_id}/insights", params)
            type_of = self._type_for_rows(level, result_type)
            return [summarize_row(r, level if level in LEVELS else "account", type_of(r)) for r in raw]
        return self._cached(f"insights:{level}:{period}:{result_type}:{daily}", 120, fetch)

    def breakdown(self, dimension: str, period: str, result_type: str) -> list[dict]:
        if dimension not in BREAKDOWNS or period not in PERIODS:
            raise MetaError("dimension أو period مش صالح")
        param, keys, _ = BREAKDOWNS[dimension]
        # reach can't be split by hour
        fields = "spend,impressions,ctr,frequency,actions,action_values,video_thruplay_watched_actions" + ("" if dimension == "hour" else ",reach")

        def fetch():
            rtype = self.main_type(result_type)
            params = {"level": "account", "date_preset": period, "breakdowns": param, "limit": 500, "fields": fields}
            if result_type == "auto":
                # only the ad sets built for the main result, so a video campaign's spend doesn't inflate cost per sale
                try:
                    ids = [o["id"] for o in self.budget_objects() if o["level"] == "adset" and o.get("result_type") == rtype]
                except MetaError:
                    ids = []
                if 0 < len(ids) <= 200:
                    params["filtering"] = '[{"field":"adset.id","operator":"IN","value":%s}]' % json.dumps(ids)
            raw = self._get_all(f"{self.account_id}/insights", params)
            out = []
            for r in raw:
                row = summarize_row(r, "account", rtype)
                row.pop("id", None); row.pop("name", None)
                row["segment"] = " · ".join(str(r.get(k, "")) for k in keys)
                out.append(row)
            return out
        return self._cached(f"bd:{dimension}:{period}:{result_type}", 600, fetch)

    def budget_objects(self) -> list[dict]:
        """Campaigns and ad sets with their budgets, in main currency units. Cached briefly; any write clears it."""
        return self._cached("budgets", 60, self._budget_objects)

    def _budget_objects(self) -> list[dict]:
        off = self.offset()
        out = []
        for kind, path in (("campaign", "campaigns"), ("adset", "adsets")):
            items = self._get_all(f"{self.account_id}/{path}", {
                "fields": "id,name,effective_status,daily_budget,lifetime_budget,budget_remaining,end_time"
                          + (",campaign_id,optimization_goal,promoted_object" if kind == "adset" else ""),
                "limit": 500,
                "filtering": '[{"field":"effective_status","operator":"NOT_IN","value":["DELETED","ARCHIVED"]}]',
            })
            for it in items:
                lifetime_daily = 0.0
                if it.get("lifetime_budget") and int(it["lifetime_budget"]) > 0:
                    remaining = int(it.get("budget_remaining") or it["lifetime_budget"]) / off
                    days = 1
                    if it.get("end_time"):
                        try:
                            end = datetime.fromisoformat(it["end_time"].replace("+0000", "+00:00"))
                            days = max(1, (end - datetime.now(timezone.utc)).days + 1)
                        except ValueError:
                            days = 1
                    lifetime_daily = round(remaining / days, 2)
                out.append({
                    "id": it["id"], "level": kind, "name": it.get("name", ""),
                    "status": it.get("effective_status", ""),
                    "daily_budget": (int(it["daily_budget"]) / off) if it.get("daily_budget") else 0.0,
                    "lifetime_daily": lifetime_daily,
                    "campaign_id": it.get("campaign_id"),
                    "result_type": goal_type(it.get("optimization_goal"), it.get("promoted_object")) if kind == "adset" else None,
                })
        return attach_goal_types(out)

    def month_spend(self) -> float:
        def fetch():
            rows = self.insights("account", "this_month", "purchase")
            return float(rows[0]["spend"]) if rows else 0.0
        return self._cached("month_spend", 300, fetch)

    # -- writes (only called from executor.py after every guard passed)
    def set_status(self, object_id: str, status: str) -> None:
        if status not in ("PAUSED", "ACTIVE") or not valid_object_id(object_id):
            raise MetaError("طلب مش صالح")
        self._request("POST", f"{self._base}/{object_id}", data={"status": status})
        self._cache.pop("budgets", None)

    def set_daily_budget(self, object_id: str, amount_main_unit: float) -> None:
        if not valid_object_id(object_id) or amount_main_unit <= 0:
            raise MetaError("طلب مش صالح")
        minor = int(round(amount_main_unit * self.offset()))
        self._request("POST", f"{self._base}/{object_id}", data={"daily_budget": str(minor)})
        self._cache.pop("budgets", None)


class FakeMeta:
    """Believable sample account so the whole app can be tried before connecting Meta.

    With extras=True (the demo) it also has a WhatsApp-messages campaign and a video-engagement campaign,
    to show that every campaign is judged by its own goal. Tests use the base account (sales only).
    """

    account_id = "act_demo"

    def __init__(self, seed: int = 7, extras: bool = False):
        rnd = random.Random(seed)
        self._objects = {
            "120200000000001": {"level": "campaign", "name": "مبيعات · كوليكشن الخريف", "status": "ACTIVE", "daily_budget": 0.0},
            "120200000000002": {"level": "campaign", "name": "Retargeting · زوار الموقع", "status": "ACTIVE", "daily_budget": 0.0},
            "120210000000011": {"level": "adset", "name": "Lookalike 3% · مصر", "status": "ACTIVE", "daily_budget": 300.0, "campaign_id": "120200000000001", "goal": "purchase"},
            "120210000000012": {"level": "adset", "name": "اهتمامات موضة · 25-34 · القاهرة والجيزة", "status": "ACTIVE", "daily_budget": 300.0, "campaign_id": "120200000000001", "goal": "purchase"},
            "120210000000013": {"level": "adset", "name": "Broad · 18-44 · مصر", "status": "ACTIVE", "daily_budget": 280.0, "campaign_id": "120200000000001", "goal": "purchase"},
            "120210000000021": {"level": "adset", "name": "زوار آخر 30 يوم", "status": "ACTIVE", "daily_budget": 400.0, "campaign_id": "120200000000002", "goal": "purchase"},
        }
        # yesterday & 7-day profiles per ad set: spend, results, revenue, ctr, freq
        self._perf = {
            "120210000000011": (300, 0, 0, 0.9, 2.1, 1900, 6, 3100, 1.1, 2.6),
            "120210000000012": (390, 5, 2600, 1.9, 1.7, 2650, 26, 13500, 2.0, 2.2),
            "120210000000013": (370, 3, 1500, 1.2, 1.5, 2500, 18, 9300, 1.4, 1.9),
            "120210000000021": (395, 7, 3640, 1.4, 3.8, 2700, 41, 21300, 2.1, 3.9),
        }
        if extras:
            self._objects.update({
                "120200000000003": {"level": "campaign", "name": "استفسارات · رسايل واتساب", "status": "ACTIVE", "daily_budget": 0.0},
                "120200000000004": {"level": "campaign", "name": "تفاعل · فيديو الكوليكشن", "status": "ACTIVE", "daily_budget": 0.0},
                "120210000000031": {"level": "adset", "name": "رسايل · 22-40 · القاهرة والجيزة", "status": "ACTIVE", "daily_budget": 200.0, "campaign_id": "120200000000003", "goal": "messages"},
                "120210000000041": {"level": "adset", "name": "فيديو · Broad · مصر", "status": "ACTIVE", "daily_budget": 100.0, "campaign_id": "120200000000004", "goal": "engagement"},
            })
            self._perf.update({
                "120210000000031": (196, 17, 0, 1.6, 1.8, 1380, 104, 0, 1.7, 2.3),
                "120210000000041": (99, 2400, 0, 0.8, 1.4, 700, 15600, 0, 0.9, 2.0),
            })
        self._daily = [round(rnd.uniform(1300, 1500), 2) for _ in range(14)]
        self._cpr = [142, 135, 128, 150, 121, 118, 124, 109, 112, 104, 115, 101, 97, 99]

    def currency(self) -> str:
        return "EGP"

    def offset(self) -> int:
        return 100

    def account_info(self) -> dict:
        return {"name": "حساب تجريبي", "currency": "EGP", "timezone_name": "Africa/Cairo", "account_status": 1}

    def _raw(self, oid: str, period: str) -> dict:
        """One ad set's numbers in the same shape Meta returns."""
        y = period in ("today", "yesterday", "last_3d")
        p = self._perf[oid]
        spend, res, rev, ctr, freq = (p[0], p[1], p[2], p[3], p[4]) if y else (p[5], p[6], p[7], p[8], p[9])
        if self._objects[oid]["status"] != "ACTIVE" and period == "today":
            spend, res, rev = 0, 0, 0
        action = RESULT_TYPES[self._objects[oid]["goal"]]["actions"][0]
        return {"spend": spend, "impressions": int(spend * 90), "reach": int(spend * 45), "ctr": ctr, "frequency": freq,
                "actions": [{"action_type": action, "value": res}] if res else [],
                "action_values": [{"action_type": "purchase", "value": rev}] if rev else []}

    @staticmethod
    def _merge(raws: list[dict]) -> dict:
        acts: dict[str, float] = {}
        vals: dict[str, float] = {}
        for r in raws:
            for a in r["actions"]:
                acts[a["action_type"]] = acts.get(a["action_type"], 0) + a["value"]
            for a in r["action_values"]:
                vals[a["action_type"]] = vals.get(a["action_type"], 0) + a["value"]
        spend = sum(r["spend"] for r in raws) or 0
        return {"spend": spend, "impressions": sum(r["impressions"] for r in raws), "reach": sum(r["reach"] for r in raws),
                "ctr": round(sum(r["ctr"] * r["spend"] for r in raws) / spend, 2) if spend else 0,
                "frequency": round(sum(r["frequency"] * r["spend"] for r in raws) / spend, 2) if spend else 0,
                "actions": [{"action_type": k, "value": v} for k, v in acts.items()],
                "action_values": [{"action_type": k, "value": v} for k, v in vals.items()]}

    def _type(self, result_type: str, goal: str | None) -> str:
        if result_type in RESULT_TYPES:
            return result_type
        return goal or self.main_type()

    def main_type(self, result_type: str = "auto") -> str:
        return result_type if result_type in RESULT_TYPES else main_type_of(self.budget_objects())

    def insights(self, level: str, period: str, result_type: str, daily: bool = False) -> list[dict]:
        if period not in PERIODS:
            raise MetaError("period مش صالح")
        if level == "account":
            rtype = self._type(result_type, None)
            if daily:
                start = date.today() - timedelta(days=14)
                out = []
                for i, (sp, cpr) in enumerate(zip(self._daily, self._cpr)):
                    res = round(sp / cpr)
                    out.append(summarize_row({
                        "spend": sp, "date_start": (start + timedelta(days=i)).isoformat(),
                        "actions": [{"action_type": "purchase", "value": res}],
                        "action_values": [{"action_type": "purchase", "value": res * 520}],
                        "impressions": 40000, "reach": 18000, "ctr": 1.8, "frequency": 2.1}, "account", rtype))
                return out
            row = self._merge([self._raw(o, period) for o in self._perf])
            if period == "this_month":
                row["spend"] *= 3
                for a in row["actions"] + row["action_values"]:
                    a["value"] *= 3
            return [summarize_row(row, "account", rtype)]
        if level not in LEVELS:
            raise MetaError("level مش صالح")
        groups: dict[str, list[tuple[str, dict]]] = {}
        for oid in self._perf:
            obj = self._objects[oid]
            key = obj["campaign_id"] if level == "campaign" else oid
            groups.setdefault(key, []).append((oid, self._raw(oid, period)))
        out = []
        for key, items in groups.items():
            oid = items[0][0]
            obj = self._objects[oid]
            camp = self._objects[obj["campaign_id"]]
            base = self._merge([r for _, r in items])
            if level == "adset":
                base.update({"adset_id": oid, "adset_name": obj["name"], "campaign_name": camp["name"]})
            elif level == "campaign":
                base.update({"campaign_id": key, "campaign_name": camp["name"]})
            else:
                base.update({"ad_id": oid + "9", "ad_name": "إعلان · " + obj["name"], "campaign_name": camp["name"], "adset_id": oid})
            out.append(summarize_row(base, level, self._type(result_type, obj["goal"])))
        return out

    def breakdown(self, dimension: str, period: str, result_type: str) -> list[dict]:
        if dimension not in BREAKDOWNS or period not in PERIODS:
            raise MetaError("dimension أو period مش صالح")
        segs = {
            "age": [("18-24", 1500, 9, 1.4), ("25-34", 4200, 52, 2.2), ("35-44", 2600, 21, 1.8), ("45-54", 900, 4, 1.1), ("55-64", 300, 0, 0.7)],
            "gender": [("female", 5600, 61, 2.1), ("male", 3700, 25, 1.5), ("unknown", 200, 0, 0.9)],
            "age_gender": [("25-34 · female", 2900, 41, 2.4), ("25-34 · male", 1300, 11, 1.7), ("35-44 · female", 1700, 15, 1.9), ("18-24 · female", 900, 6, 1.5)],
            "placement": [("instagram · reels", 3100, 38, 2.6), ("facebook · feed", 2800, 22, 1.6), ("instagram · feed", 1900, 17, 1.9),
                          ("facebook · reels", 900, 6, 1.4), ("audience_network · classic", 800, 1, 0.6)],
            "region": [("Cairo", 4100, 0, 2.0), ("Giza", 2400, 0, 1.9), ("Alexandria", 1600, 0, 1.6), ("Dakahlia", 700, 0, 1.2)],
            "hour": [(f"{h:02d}:00:00 - {h:02d}:59:59", sp, 0, ctr) for h, sp, ctr in
                     [(9, 300, 1.2), (13, 520, 1.6), (17, 610, 1.8), (20, 980, 2.4), (22, 1100, 2.6), (1, 420, 1.3)]],
        }[dimension]
        rtype = self.main_type(result_type)
        out = []
        for seg, sp, res, ctr in segs:
            res = res if BREAKDOWNS[dimension][2] else 0
            row = summarize_row({"spend": sp, "impressions": sp * 80, "reach": sp * 40, "ctr": ctr, "frequency": 2.0,
                                 "actions": [{"action_type": "purchase", "value": res}] if res else [],
                                 "action_values": [{"action_type": "purchase", "value": res * 520}] if res else []}, "account", rtype)
            row.pop("id", None); row.pop("name", None)
            row["segment"] = seg
            out.append(row)
        return out

    def budget_objects(self) -> list[dict]:
        return attach_goal_types([
            {"id": k, "level": v["level"], "name": v["name"], "status": v["status"], "daily_budget": v["daily_budget"],
             "lifetime_daily": 0.0, "campaign_id": v.get("campaign_id"), "result_type": v.get("goal")}
            for k, v in self._objects.items()])

    def month_spend(self) -> float:
        return 21_000.0

    def set_status(self, object_id: str, status: str) -> None:
        if object_id not in self._objects or status not in ("PAUSED", "ACTIVE"):
            raise MetaError("طلب مش صالح")
        self._objects[object_id]["status"] = status

    def set_daily_budget(self, object_id: str, amount_main_unit: float) -> None:
        if object_id not in self._objects or amount_main_unit <= 0:
            raise MetaError("طلب مش صالح")
        self._objects[object_id]["daily_budget"] = float(amount_main_unit)


_fake_singleton: FakeMeta | None = None
_real_singleton: MetaClient | None = None
_lock = threading.Lock()


def get_client():
    """One client per process, so currency and account info are fetched once (Meta rate limits are tight)."""
    global _fake_singleton, _real_singleton
    with _lock:
        if config.demo_mode():
            if _fake_singleton is None:
                # the full sample (messages + video campaigns) only in --demo; unconfigured installs see the basic one
                _fake_singleton = FakeMeta(extras=os.environ.get("SALES_TEAM_DEMO") == "1")
            return _fake_singleton
        if _real_singleton is None:
            _real_singleton = MetaClient(config.meta_token(), config.meta_account_id(), config.graph_version(), config.meta_app_secret())
        return _real_singleton
