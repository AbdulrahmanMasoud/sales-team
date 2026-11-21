"""Business settings edited from the dashboard. Every value is validated here, never trusted as-is.

Agents can READ a safe subset of these, but there is no tool that lets an agent WRITE them.
"""
from __future__ import annotations

import json
import re
from typing import Any

from . import db, meta_api

PERM_CHOICES = ("auto", "ask", "no")
MODEL_CHOICES = ("sonnet", "opus", "haiku")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

# key: (kind, default, extra)
SPEC: dict[str, tuple[str, Any, dict]] = {
    "brand_name": ("str", "براندك", {"max": 80}),
    "payment_methods": ("choice", "both", {"choices": ("cod", "online", "both")}),
    "country_code": ("digits", "20", {"max": 4}),
    "repeat_days": ("int", 30, {"min": 7, "max": 365}),
    "lapsed_days": ("int", 60, {"min": 14, "max": 730}),
    # "auto": every campaign is judged by what it is built to get (sales, messages, leads, calls, reach...)
    "result_type": ("choice", "auto", {"choices": ("auto", *meta_api.RESULT_TYPES)}),
    "target_cpr": ("float", 110.0, {"min": 1, "max": 1_000_000}),  # target cost per SALE
    # target cost per result for every other result type, e.g. {"messages": 15, "leads": 40}. Missing = no target.
    "targets": ("targets", {}, {}),
    "daily_cap": ("float", 1500.0, {"min": 1, "max": 100_000_000}),
    "monthly_cap": ("float", 40000.0, {"min": 1, "max": 3_000_000_000}),
    "max_increase_pct": ("int", 20, {"min": 1, "max": 100}),
    "max_decrease_pct": ("int", 50, {"min": 5, "max": 90}),
    "budget_cooldown_hours": ("int", 72, {"min": 0, "max": 336}),
    "rule_pause_multiple": ("float", 2.0, {"min": 1, "max": 10}),
    "rule_scale_roas": ("float", 4.0, {"min": 0.5, "max": 50}),
    "rule_scale_days": ("int", 3, {"min": 1, "max": 14}),
    "rule_fatigue_freq": ("float", 3.0, {"min": 1, "max": 20}),
    "rule_fatigue_ctr_drop": ("int", 25, {"min": 5, "max": 90}),
    # Safe start: everything needs your approval. Loosen it once you trust the decisions.
    "perm_pause": ("choice", "ask", {"choices": PERM_CHOICES}),
    "perm_budget_up": ("choice", "ask", {"choices": PERM_CHOICES}),
    "perm_budget_down": ("choice", "ask", {"choices": PERM_CHOICES}),
    "perm_new_campaign": ("choice", "ask", {"choices": ("ask", "no")}),
    "perm_new_creative": ("choice", "ask", {"choices": ("ask", "no")}),
    "dry_run": ("bool", True, {}),
    "agents_enabled": ("bool", True, {}),
    "enabled_mb": ("bool", True, {}),
    "enabled_fu": ("bool", True, {}),
    "enabled_wr": ("bool", True, {}),
    "time_mb": ("time", "09:00", {}),
    "time_fu": ("time", "10:00", {}),
    "time_wr": ("time", "20:00", {}),
    "day_wr": ("int", 6, {"min": 0, "max": 6}),  # Python weekday: Monday=0 ... Sunday=6
    "model_mb": ("choice", "sonnet", {"choices": MODEL_CHOICES}),
    "model_fu": ("choice", "sonnet", {"choices": MODEL_CHOICES}),
    "model_wr": ("choice", "opus", {"choices": MODEL_CHOICES}),
    "max_usd_per_run": ("float", 2.0, {"min": 0.05, "max": 20}),
    # Profit: what one order really costs you (used to compute break-even cost per sale)
    "cogs_pct": ("int", 45, {"min": 0, "max": 95}),
    "shipping_cost": ("float", 60.0, {"min": 0, "max": 100_000}),
    "return_cost": ("float", 80.0, {"min": 0, "max": 100_000}),
    "target_profit_share": ("int", 30, {"min": 0, "max": 90}),
    # Telegram (bot token and your chat id live in .env)
    "telegram_enabled": ("bool", False, {}),
    "telegram_brief": ("bool", True, {}),
    "telegram_approvals": ("bool", True, {}),
    # Meta's official ads MCP: off by default; per agent: off | read | write_ask | write_auto
    "mcp_enabled": ("bool", False, {}),
    "mcp_mb": ("choice", "read", {"choices": ("off", "read", "write_ask", "write_auto")}),
    "mcp_fu": ("choice", "off", {"choices": ("off", "read", "write_ask", "write_auto")}),
    "mcp_wr": ("choice", "read", {"choices": ("off", "read", "write_ask", "write_auto")}),
    "mcp_overrides": ("json", {}, {}),
    "brand_voice": ("text", "مصري ودود ومختصر. بنقول «أهلاً» مش «عزيزي العميل». من غير ضغط في البيع. اسم المنتج والسعر دايماً في الرسالة.", {"max": 1500}),
    "offers": ("text", "", {"max": 1000}),
}


class SettingsError(ValueError):
    pass


def _coerce(key: str, value: Any) -> Any:
    kind, _default, extra = SPEC[key]
    try:
        if kind == "bool":
            if isinstance(value, bool):
                return value
            if str(value).lower() in ("1", "true", "yes", "on"):
                return True
            if str(value).lower() in ("0", "false", "no", "off"):
                return False
            raise ValueError
        if kind == "int":
            v = int(float(value))
            if not extra["min"] <= v <= extra["max"]:
                raise ValueError
            return v
        if kind == "float":
            v = float(value)
            if v != v or not extra["min"] <= v <= extra["max"]:  # NaN check
                raise ValueError
            return round(v, 2)
        if kind == "choice":
            if value not in extra["choices"]:
                raise ValueError
            return value
        if kind == "time":
            if not isinstance(value, str) or not TIME_RE.match(value):
                raise ValueError
            return value
        if kind == "digits":
            v = str(value).strip().lstrip("+")
            if not v.isdigit() or len(v) > extra["max"]:
                raise ValueError
            return v
        if kind == "json":
            v = json.loads(value) if isinstance(value, str) else value
            if not isinstance(v, dict) or len(v) > 300:
                raise ValueError
            clean = {}
            for k2, v2 in v.items():
                if not isinstance(k2, str) or not 0 < len(k2) <= 120 or v2 not in ("read", "write", "write_auto", "blocked"):
                    raise ValueError
                clean[k2] = v2
            return clean
        if kind == "targets":
            v = json.loads(value) if isinstance(value, str) else value
            if not isinstance(v, dict):
                raise ValueError
            clean = {}
            for k2, v2 in v.items():
                if k2 not in meta_api.RESULT_TYPES or k2 == "purchase":  # the sale target is target_cpr
                    raise ValueError
                if v2 in (None, "", 0, "0"):
                    continue  # empty box = no target for this type
                f = float(v2)
                if f != f or not 0.01 <= f <= 1_000_000:
                    raise ValueError
                clean[k2] = round(f, 2)
            return clean
        if kind in ("str", "text"):
            v = str(value).replace("\x00", "").strip()
            if len(v) > extra["max"]:
                raise ValueError
            return v
    except (TypeError, ValueError):
        raise SettingsError(f"قيمة مش صالحة للإعداد {key}") from None
    raise SettingsError(f"نوع إعداد مش معروف {key}")


def get_all() -> dict[str, Any]:
    stored = {r["key"]: json.loads(r["value"]) for r in db.rows("SELECT key, value FROM settings")}
    out = {}
    for key, (_kind, default, _extra) in SPEC.items():
        out[key] = stored.get(key, default)
    return out


def update(changes: dict[str, Any]) -> dict[str, Any]:
    unknown = [k for k in changes if k not in SPEC]
    if unknown:
        raise SettingsError(f"إعدادات مش معروفة: {', '.join(unknown)}")
    clean = {k: _coerce(k, v) for k, v in changes.items()}
    merged = {**get_all(), **clean}
    if merged["daily_cap"] > merged["monthly_cap"]:
        raise SettingsError("السقف اليومي لازم يكون أقل من السقف الشهري")
    if merged["lapsed_days"] <= merged["repeat_days"]:
        raise SettingsError("مدة «بقاله فترة» لازم تكون أطول من مدة «يشتري تاني»")
    with db.connect() as c:
        for k, v in clean.items():
            c.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (k, json.dumps(v, ensure_ascii=False)),
            )
    return get_all()


def for_agents(s: dict[str, Any]) -> dict[str, Any]:
    """The subset agents may see. No secrets live in settings, but keep it focused anyway."""
    keys = [
        "brand_name", "payment_methods", "repeat_days", "lapsed_days", "result_type", "target_cpr", "targets",
        "daily_cap", "monthly_cap", "max_increase_pct", "max_decrease_pct", "budget_cooldown_hours",
        "rule_pause_multiple", "rule_scale_roas", "rule_scale_days", "rule_fatigue_freq",
        "rule_fatigue_ctr_drop", "perm_pause", "perm_budget_up", "perm_budget_down",
        "perm_new_campaign", "perm_new_creative", "dry_run", "brand_voice", "offers",
        "cogs_pct", "shipping_cost", "return_cost", "target_profit_share",
    ]
    return {k: s[k] for k in keys}


def target_for(s: dict[str, Any], rtype: str | None) -> float | None:
    """The owner's target cost per result for this kind of result, or None when they set none."""
    if rtype == "purchase":
        return s["target_cpr"]
    return (s.get("targets") or {}).get(rtype) if rtype else None


def migrate() -> None:
    """Upgrade settings saved by older versions. Runs at every start; each step runs once.

    Old versions had one result type for the whole account ("purchase" or "messages") and target_cpr was the
    target for THAT result. Now every campaign is judged by its own goal ("auto") and target_cpr is per sale,
    so an old "messages" target becomes the messages target. Otherwise a messages or video campaign would be
    judged by sales after the update.
    """
    if db.one("SELECT value FROM kv WHERE key = 'migrated_result_types'"):
        return
    stored = {r["key"]: json.loads(r["value"]) for r in db.rows("SELECT key, value FROM settings")}
    changes: dict[str, Any] = {}
    if stored.get("result_type") in ("purchase", "messages"):
        changes["result_type"] = "auto"
        if stored["result_type"] == "messages" and "target_cpr" in stored:
            changes["targets"] = {**(stored.get("targets") or {}), "messages": stored["target_cpr"]}
            changes["target_cpr"] = SPEC["target_cpr"][1]  # their old number was per conversation, not per sale
    with db.connect() as c:
        for k, v in changes.items():
            c.execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                      (k, json.dumps(_coerce(k, v), ensure_ascii=False)))
        c.execute("INSERT OR REPLACE INTO kv(key, value) VALUES ('migrated_result_types', ?)", (db.now(),))
