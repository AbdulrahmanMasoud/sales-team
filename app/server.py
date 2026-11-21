"""Dashboard API. Listens on 127.0.0.1 only, and every change needs the per-install CSRF token."""
from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import agents, analysis, business, config, db, executor, guards, meta_api, meta_mcp, scheduler, settings_store, setup, telegram, updates

STATIC = config.BASE_DIR / "static"
_background: set[asyncio.Task] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.load_env()
    db.init()
    settings_store.migrate()
    executor.recover_interrupted()
    db.execute("UPDATE runs SET status='error', error='البرنامج اتقفل أثناء التشغيل', finished_at=? WHERE status='running'", (db.now(),))
    stop = asyncio.Event()
    tasks = []
    if not app.state.no_scheduler:
        tasks.append(asyncio.create_task(scheduler.loop(stop)))
        tasks.append(asyncio.create_task(telegram.poll(stop)))
    yield
    stop.set()
    if tasks:
        await asyncio.wait(tasks, timeout=5)


def create_app(no_scheduler: bool = False) -> FastAPI:
    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.no_scheduler = no_scheduler

    @app.middleware("http")
    async def security(request: Request, call_next):
        port = config.port()
        allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if request.headers.get("host") not in allowed_hosts:  # blocks DNS-rebinding attacks
            return JSONResponse({"error": "host not allowed"}, status_code=403)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin and origin not in {f"http://{h}" for h in allowed_hosts}:
                return JSONResponse({"error": "origin not allowed"}, status_code=403)
            if request.headers.get("x-csrf-token") != config.csrf_secret():
                return JSONResponse({"error": "csrf"}, status_code=403)
        response: Response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; "
            "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cache-Control"] = "no-store"
        return response

    # ------------------------------------------------------------------ page
    @app.get("/", response_class=HTMLResponse)
    def index():
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        return html.replace("__CSRF__", config.csrf_secret())

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    async def body(request: Request) -> dict[str, Any]:
        try:
            data = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(400, "JSON مش صالح")
        if not isinstance(data, dict):
            raise HTTPException(400, "JSON مش صالح")
        return data

    # ------------------------------------------------------------------ status & overview
    @app.get("/api/status")
    def status():
        s = settings_store.get_all()
        meta_ok, meta_err, account = True, "", {}
        try:
            account = meta_api.get_client().account_info()
        except meta_api.MetaError as e:
            meta_ok, meta_err = False, str(e)
        return {"demo": config.demo_mode(), "demo_flag": os.environ.get("SALES_TEAM_DEMO") == "1", "update": updates.status(), "dry_run": s["dry_run"], "agents_enabled": s["agents_enabled"],
                "meta_ok": meta_ok, "meta_error": meta_err, "account": {k: account.get(k) for k in ("name", "currency")},
                "running": [a for a in agents.AGENTS if agents.is_running(a)],
                "last_run": db.one("SELECT finished_at FROM runs WHERE status='done' ORDER BY id DESC LIMIT 1"),
                "counts": _counts()}

    _cache: dict[str, tuple[float, Any]] = {}

    def cached(key: str, ttl: int, fn):
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        val = fn()
        _cache[key] = (time.time(), val)
        return val

    def _daily_series(meta, result_type: str, main: str | None) -> list[dict]:
        """Cost per main result per day. With mixed goals, only the campaigns built for that result count."""
        if result_type == "auto" and main:
            rows = meta.insights("campaign", "last_14d", "auto", daily=True)
            if rows and all(r.get("date") for r in rows) and len({r["result_type"] for r in rows}) > 1:
                per = meta_api.RESULT_TYPES[main]["per"]
                days: dict[str, list[float]] = {}
                for r in rows:
                    if r["result_type"] == main:
                        d = days.setdefault(r["date"], [0.0, 0.0])
                        d[0] += r["spend"]
                        d[1] += r["results"] or 0
                return [{"date": k, "spend": round(v[0], 2), "results": v[1],
                         "cost_per_result": round(v[0] / v[1] * per, 2) if v[1] else None} for k, v in sorted(days.items())]
        return meta.insights("account", "last_14d", result_type, daily=True)

    @app.get("/api/overview")
    def overview():
        s = settings_store.get_all()
        meta = meta_api.get_client()
        ads, series, meta_error, by_goal = None, [], "", []
        try:
            rows = cached("y_account", 300, lambda: meta.insights("account", "yesterday", s["result_type"]))
            ads = rows[0] if rows else {"spend": 0, "results": 0, "cost_per_result": None, "roas": None,
                                        "result_type": meta.main_type(s["result_type"])}
            ads["result_label"] = meta_api.label(ads.get("result_type"))
            ads["target"] = settings_store.target_for(s, ads.get("result_type"))
            series = cached("cpr14", 600, lambda: _daily_series(meta, s["result_type"], ads.get("result_type")))
            # yesterday, one line per goal: a video campaign and a sales campaign are never added together
            groups: dict[str, dict] = {}
            for r in cached("y_campaigns", 300, lambda: meta.insights("campaign", "yesterday", s["result_type"])):
                g = groups.setdefault(r["result_type"], {"result_type": r["result_type"], "result_label": r["result_label"],
                                                         "campaigns": 0, "spend": 0.0, "results": 0.0})
                g["campaigns"] += 1
                g["spend"] += r["spend"]
                g["results"] += r["results"] or 0
            for g in groups.values():
                per = meta_api.RESULT_TYPES.get(g["result_type"], {}).get("per", 1)
                g["spend"] = round(g["spend"], 2)
                g["cost_per_result"] = round(g["spend"] / g["results"] * per, 2) if g["results"] else None
                g["target"] = settings_store.target_for(s, g["result_type"])
                g["funnel"] = meta_api.RESULT_TYPES.get(g["result_type"], {}).get("funnel", "mixed")
            by_goal = sorted(groups.values(), key=lambda g: -g["spend"])
            # the headline cost counts only the campaigns built for that result, not the video campaign's money too
            main = groups.get(ads.get("result_type"))
            if s["result_type"] == "auto" and main and len(groups) > 1:
                ads["cost_per_result"], ads["results"] = main["cost_per_result"], main["results"]
                ads["roas"] = round(ads["revenue"] / main["spend"], 2) if main["spend"] and ads.get("revenue") else None
                ads["only_main_goal"] = True
        except meta_api.MetaError as e:
            meta_error = str(e)
        y = (datetime.now() - timedelta(days=1)).date().isoformat()
        sales_y = db.one("SELECT COUNT(*) AS n, COALESCE(SUM(amount),0) AS revenue FROM orders WHERE substr(created_at,1,10) = ?", (y,))
        week = business.sales_summary(7)
        team = []
        for key, meta_a in agents.AGENTS.items():
            last = db.one("SELECT * FROM runs WHERE agent = ? ORDER BY id DESC LIMIT 1", (key,))
            team.append({"key": key, **meta_a, "schedule": scheduler.next_run_label(key), "running": agents.is_running(key), "last": last})
        return {
            "target_cpr": s["target_cpr"], "daily_cap": s["daily_cap"], "result_type": s["result_type"],
            "ads": ads, "meta_error": meta_error, "series": series, "by_goal": by_goal,
            "sales_yesterday": sales_y, "week": week,
            "pending": _decisions("pending", 5), "recent": _decisions(None, 6), "team": team,
            "followups_today": db.one("SELECT COUNT(*) AS n FROM followups WHERE status='draft'")["n"],
            "accuracy": db.one("SELECT SUM(verdict='right') AS right, COUNT(*) AS total FROM decisions WHERE verdict IN ('right','wrong')"),
            "profit7": cached("profit7", 60, lambda: analysis.profit_summary(s, 7, meta)),
        }

    def _counts():
        return {"pending": db.one("SELECT COUNT(*) AS n FROM decisions WHERE status='pending'")["n"],
                "drafts": db.one("SELECT COUNT(*) AS n FROM followups WHERE status='draft'")["n"]}

    def _decisions(status: str | None, limit: int = 100):
        if status == "measure":
            q, p = "SELECT * FROM decisions WHERE status IN ('executed','manual') AND verdict IS NULL ORDER BY id DESC LIMIT ?", (limit,)
        elif status == "done":
            q, p = "SELECT * FROM decisions WHERE status IN ('measured','rejected','refused','failed','executing') ORDER BY id DESC LIMIT ?", (limit,)
        elif status:
            q, p = "SELECT * FROM decisions WHERE status = ? ORDER BY id DESC LIMIT ?", (status, limit)
        else:
            q, p = "SELECT * FROM decisions WHERE status != 'refused' ORDER BY id DESC LIMIT ?", (limit,)
        out = db.rows(q, p)
        for d in out:
            d["action"] = json.loads(d["action"] or "{}")
        return out

    # ------------------------------------------------------------------ team
    @app.get("/api/events")
    def events(day: str = "", agent: str = ""):
        day = day or db.today()
        try:
            datetime.fromisoformat(day)
        except ValueError:
            raise HTTPException(400, "تاريخ مش صالح")
        if agent and agent not in agents.AGENTS:
            raise HTTPException(400, "agent مش معروف")
        q = "SELECT * FROM events WHERE substr(created_at,1,10) = ?" + (" AND agent = ?" if agent else "") + " ORDER BY id LIMIT 2000"
        return db.rows(q, (day, agent) if agent else (day,))

    @app.get("/api/agents/{key}")
    def agent_detail(key: str):
        if key not in agents.AGENTS:
            raise HTTPException(404)
        s = settings_store.get_all()
        short = agents.AGENTS[key]["short"]
        return {"key": key, **agents.AGENTS[key], "model": s[f"model_{short}"], "schedule": scheduler.next_run_label(key),
                "running": agents.is_running(key),
                "memory": db.rows("SELECT id, fact, created_at FROM memory WHERE agent=? AND active=1 ORDER BY id DESC LIMIT 30", (key,)),
                "runs": db.rows("SELECT * FROM runs WHERE agent=? ORDER BY id DESC LIMIT 10", (key,)),
                "perms": _perms(key, s)}

    def _perms(key: str, s: dict) -> list[dict]:
        if key == "followup":
            fm = s["mcp_fu"] if s["mcp_enabled"] else "off"
            return [{"what": "يقرا الطلبات والعملاء", "mode": "allowed"}, {"what": "يكتب مسودات رسايل", "mode": "allowed"},
                    {"what": "يبعت رسالة لعميل", "mode": "owner"}, {"what": "يعدّل طلب أو سعر", "mode": "no"},
                    {"what": "الـMCP الرسمي بتاع Meta", "mode": {"off": "no", "read": "read", "write_ask": "ask", "write_auto": "auto"}[fm]}]
        mcp_mode = s[f"mcp_{agents.AGENTS[key]['short']}"] if s["mcp_enabled"] else "off"
        mcp_row = {"what": "الـMCP الرسمي بتاع Meta", "mode": {"off": "no", "read": "read", "write_ask": "ask", "write_auto": "auto"}[mcp_mode]}
        rows = [{"what": "يقرا أرقام الإعلانات", "mode": "allowed"},
                {"what": "يقفل ad set أو حملة", "mode": s["perm_pause"]},
                {"what": "يزوّد budget (لحد %d%%)" % s["max_increase_pct"], "mode": s["perm_budget_up"]},
                {"what": "يقلل budget", "mode": s["perm_budget_down"]},
                {"what": "يقترح حملة أو إعلان جديد", "mode": "ask" if "ask" in (s["perm_new_campaign"], s["perm_new_creative"]) else "no"},
                {"what": "يعدّي السقف اليومي أو الشهري", "mode": "no"}]
        if key == "weekly_review":
            rows.insert(1, {"what": "يقيّم القرارات القديمة", "mode": "allowed"})
        return rows + [mcp_row]

    @app.post("/api/agents/{key}/run")
    async def run_agent(key: str):
        if key not in agents.AGENTS:
            raise HTTPException(404)
        if agents.is_running(key):
            raise HTTPException(409, "الـagent ده شغال دلوقتي")
        t = asyncio.create_task(agents.run(key, trigger="manual"))
        _background.add(t)
        t.add_done_callback(_background.discard)
        return {"started": True}

    @app.post("/api/run/daily")
    async def run_daily():
        async def seq():
            await agents.run("media_buyer", trigger="manual")
            await agents.run("followup", trigger="manual")
        if agents.is_running("media_buyer") or agents.is_running("followup"):
            raise HTTPException(409, "التيم شغال دلوقتي")
        t = asyncio.create_task(seq())
        _background.add(t)
        t.add_done_callback(_background.discard)
        return {"started": True}

    @app.delete("/api/memory/{mid}")
    def forget(mid: int):
        db.execute("UPDATE memory SET active = 0 WHERE id = ?", (mid,))
        return {"ok": True}

    # ------------------------------------------------------------------ decisions
    @app.get("/api/decisions")
    def decisions(status: str = "pending"):
        if status not in ("pending", "measure", "executed", "done", "all"):
            raise HTTPException(400)
        return _decisions(None if status == "all" else status, 200)

    @app.post("/api/decisions/{did}/approve")
    def approve(did: int):
        try:
            return executor.approve(did)
        except ValueError as e:
            raise HTTPException(409, str(e))

    @app.post("/api/decisions/{did}/reject")
    async def reject(did: int, request: Request):
        data = await body(request)
        try:
            executor.reject(did, str(data.get("note", "")))
        except ValueError as e:
            raise HTTPException(409, str(e))
        return {"ok": True}

    # ------------------------------------------------------------------ customers & orders
    @app.get("/api/followups")
    def followups():
        s = settings_store.get_all()
        rows = db.rows("""SELECT f.*, c.name, c.phone FROM followups f JOIN customers c ON c.id = f.customer_id
                          WHERE f.status = 'draft' OR substr(f.sent_at,1,10) = ? ORDER BY f.status, f.id""", (db.today(),))
        for r in rows:
            o = db.one("SELECT product, amount, payment, created_at, source FROM orders WHERE id = ?", (r["order_id"],)) if r["order_id"] else None
            last = db.one("SELECT product, amount, created_at FROM orders WHERE customer_id = ? ORDER BY created_at DESC LIMIT 1", (r["customer_id"],))
            r["order"] = o or last
            r["segment_ar"] = business.SEGMENT_AR[r["segment"]]
        week = db.one("""SELECT SUM(status='sent') AS sent FROM followups WHERE created_at >= ?""",
                      ((datetime.now() - timedelta(days=7)).isoformat(),))
        return {"items": rows, "country_code": s["country_code"], "week_sent": week["sent"] or 0, "week": business.sales_summary(7)}

    @app.post("/api/followups/{fid}")
    async def update_followup(fid: int, request: Request):
        data = await body(request)
        f = db.one("SELECT * FROM followups WHERE id = ?", (fid,))
        if not f:
            raise HTTPException(404)
        if "message" in data:
            msg = str(data["message"]).strip()
            if not msg or len(msg) > 1000:
                raise HTTPException(400, "الرسالة لازم تكون بين 1 و1000 حرف")
            db.execute("UPDATE followups SET message = ? WHERE id = ?", (msg, fid))
        if "status" in data:
            st = data["status"]
            if st not in ("draft", "sent", "skipped"):
                raise HTTPException(400)
            db.execute("UPDATE followups SET status = ?, sent_at = ? WHERE id = ?", (st, db.now() if st == "sent" else None, fid))
        return {"ok": True}

    @app.get("/api/orders")
    def orders():
        return {"items": business.list_orders(300), "statuses": business.STATUS_AR}

    @app.post("/api/orders")
    async def add_order(request: Request):
        data = await body(request)
        try:
            oid = business.add_order(data, settings_store.get_all()["country_code"])
        except business.InputError as e:
            raise HTTPException(400, str(e))
        return {"id": oid}

    @app.post("/api/orders/{oid}/status")
    async def order_status(oid: int, request: Request):
        data = await body(request)
        try:
            business.set_order_status(oid, str(data.get("status", "")))
        except business.InputError as e:
            raise HTTPException(400, str(e))
        return {"ok": True}

    @app.post("/api/orders/import")
    async def import_orders(request: Request):
        data = await body(request)
        try:
            # off the event loop, so the dashboard and running agents never freeze during a big import
            return await asyncio.to_thread(business.import_csv, str(data.get("csv", "")), settings_store.get_all()["country_code"])
        except business.InputError as e:
            raise HTTPException(400, str(e))

    # ------------------------------------------------------------------ first-run setup
    @app.get("/api/setup")
    def setup_status():
        return setup.status()

    @app.post("/api/setup/claude-test")
    async def setup_claude():
        return await setup.test_claude()

    @app.post("/api/setup/{service}")
    async def setup_connect(service: str, request: Request):
        data = await body(request)
        try:
            if service == "meta":
                out = await asyncio.to_thread(setup.connect_meta, data.get("token", ""), data.get("account_id", ""), data.get("app_secret", ""))
            elif service == "telegram":
                out = await asyncio.to_thread(setup.connect_telegram, data.get("token", ""))
            elif service == "telegram-confirm":
                out = await asyncio.to_thread(setup.confirm_telegram_chat)
            elif service == "mcp":
                out = await asyncio.to_thread(setup.connect_mcp, data.get("token", ""))
            else:
                raise HTTPException(404)
        except setup.SetupError as e:
            raise HTTPException(400, str(e))
        _cache.clear()
        return out

    @app.delete("/api/setup/{service}")
    def setup_disconnect(service: str):
        try:
            setup.disconnect(service)
        except setup.SetupError as e:
            raise HTTPException(400, str(e))
        _cache.clear()
        return {"ok": True}

    # ------------------------------------------------------------------ profit & analysis
    @app.get("/api/profit")
    def profit(days: int = 7):
        if days not in (7, 14, 30):
            raise HTTPException(400)
        return cached(f"profit{days}", 60, lambda: analysis.profit_summary(settings_store.get_all(), days, meta_api.get_client()))

    @app.get("/api/breakdown")
    def get_breakdown(dimension: str = "age_gender", period: str = "last_14d"):
        if dimension not in meta_api.BREAKDOWNS or period not in meta_api.PERIODS:
            raise HTTPException(400)
        try:
            return analysis.breakdown(settings_store.get_all(), meta_api.get_client(), dimension, period)
        except meta_api.MetaError as e:
            raise HTTPException(502, str(e))

    @app.get("/api/fatigue")
    def fatigue():
        try:
            return analysis.fatigue_report(settings_store.get_all(), meta_api.get_client())
        except meta_api.MetaError as e:
            raise HTTPException(502, str(e))

    @app.get("/api/products")
    def products():
        return analysis.list_products()

    @app.post("/api/products")
    async def save_product(request: Request):
        data = await body(request)
        try:
            pid = analysis.set_product(str(data.get("name", "")), data.get("unit_cost"))
        except ValueError as e:
            raise HTTPException(400, str(e))
        _cache.pop("profit7", None); _cache.pop("profit14", None); _cache.pop("profit30", None)
        return {"id": pid}

    @app.delete("/api/products/{pid}")
    def remove_product(pid: int):
        analysis.delete_product(pid)
        for k in ("profit7", "profit14", "profit30"):
            _cache.pop(k, None)
        return {"ok": True}

    # ------------------------------------------------------------------ Meta official MCP
    @app.get("/api/meta-mcp")
    def meta_mcp_status(refresh: bool = False):
        s = settings_store.get_all()
        out = {"enabled": s["mcp_enabled"], "demo": config.demo_mode(), "has_token": bool(meta_mcp.token()) or config.demo_mode(),
               "connected": False, "error": "", "tools": []}
        if not out["has_token"]:
            out["error"] = "مفيش META_MCP_TOKEN في ملف .env"
            return out
        try:
            lst = meta_mcp.tools(force=refresh)
            out["connected"] = True
            out["tools"] = [{"name": t["name"], "description": guards.sanitize(t.get("description", ""), 240),
                             "auto": meta_mcp.classify(t), "class": meta_mcp.effective_class(t, s["mcp_overrides"])} for t in lst]
        except meta_mcp.MetaMCPError as e:
            out["error"] = str(e)
        return out

    # ------------------------------------------------------------------ settings
    @app.get("/api/settings")
    def get_settings():
        return {"values": settings_store.get_all(),
                "meta": {"connected": not config.demo_mode(), "account_id": _mask(config.meta_account_id()),
                         "graph_version": config.graph_version()},
                "telegram": {"has_token": bool(telegram.token()), "has_chat": telegram.owner_id() is not None}}

    @app.post("/api/telegram/test")
    def telegram_test():
        if not (telegram.token() and telegram.owner_id() is not None):
            raise HTTPException(400, "حط TELEGRAM_BOT_TOKEN و TELEGRAM_CHAT_ID في ملف .env الأول")
        try:
            telegram.send("رسالة تجربة من تيم المبيعات. لو وصلتك يبقى كله تمام.\n\nالأوامر: /status /pending /stop")
        except telegram.TelegramError as e:
            raise HTTPException(502, str(e))
        return {"ok": True}

    @app.put("/api/settings")
    async def put_settings(request: Request):
        data = await body(request)
        try:
            values = settings_store.update(data)
        except settings_store.SettingsError as e:
            raise HTTPException(400, str(e))
        if not values["agents_enabled"]:
            agents.cancel_all()  # the kill switch stops runs that are already going
        return {"values": values}

    return app


def _mask(account: str) -> str:
    return (account[:4] + "••••" + account[-4:]) if len(account) > 8 else account
