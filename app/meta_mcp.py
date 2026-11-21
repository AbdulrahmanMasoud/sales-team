"""Meta's official ads MCP server (https://mcp.facebook.com/ads), used through OUR process.

Agents never connect to Meta's MCP directly. We list its tools, decide per tool whether it only reads
or can change something, and expose each allowed tool to an agent as our own proxy tool. That way:
- the token stays in this Python process (never in the agent process),
- every call is logged, every result is wrapped as untrusted data,
- every write goes through the same approval / dry-run / kill-switch path as our own actions.

Small JSON-RPC client over httpx on purpose: no dependency on a fast-moving MCP client library.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from typing import Any

import httpx

from . import config

URL = "https://mcp.facebook.com/ads"
PROTOCOL = "2025-06-18"
MODES = ("off", "read", "write_ask", "write_auto")  # write_auto kept for old settings; it behaves like write_ask
# per tool: read | write (always asks) | write_auto (owner allow-listed; still asks for anything risky) | blocked
CLASSES = ("read", "write", "write_auto", "blocked")
MAX_ARGS_CHARS = 1500  # anything longer can't be reviewed on a phone, so it is refused

_READ_VERBS = {"get", "list", "search", "read", "describe", "fetch", "find", "lookup", "diagnose", "check", "estimate",
               "preview", "query", "insights", "insight", "report", "analyze", "analyse", "view", "show", "count"}
_RISKY_AUTO = re.compile(r"(create|duplicate|copy|clone|activate|resume|enable|publish|launch|start|boost|submit|upsert|import)", re.I)


def _tokens(name: str) -> list[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    return [t for t in re.split(r"[^A-Za-z0-9]+", spaced.lower()) if t]


class MetaMCPError(Exception):
    pass


class MetaMCPAuthError(MetaMCPError):
    """The token was refused (401/403). setup.py then asks Graph which permissions the token really has."""


def _why(r: httpx.Response) -> str:
    """Meta's own short reason, if it gave one. Never echoes anything that looks like a token."""
    reason = ""
    try:
        body = r.json()
        err = body.get("error") if isinstance(body, dict) else None
        reason = (err.get("message") if isinstance(err, dict) else err) or body.get("error_description") or ""
    except ValueError:
        reason = r.headers.get("www-authenticate", "")
    reason = re.sub(r"[A-Za-z0-9_\-]{40,}", "***", str(reason))[:200].strip()
    return f": {reason}" if reason else ""


class _SessionExpired(Exception):
    pass


def token() -> str:
    return (os.environ.get("META_MCP_TOKEN") or os.environ.get("META_ACCESS_TOKEN") or "").strip()


def classify(tool: dict[str, Any]) -> str:
    """'read' only when we are confident. Anything unclear is treated as a write (needs approval).
    Never returns write_auto: only the owner can allow a tool to write without asking."""
    ann = tool.get("annotations")
    ann = ann if isinstance(ann, dict) else {}
    if ann.get("destructiveHint") is True:
        return "write"
    if ann.get("readOnlyHint") is True:
        return "read"
    toks = _tokens(str(tool.get("name", "")))
    # every word must be harmless: a read verb first, no second verb ("list_and_delete"), only nouns after
    if toks and toks[0] in _READ_VERBS and "and" not in toks and not any(_looks_like_verb(t) for t in toks[1:]):
        return "read"
    return "write"


_NOUN_OK = re.compile(r"^(ad|ads|adset|adsets|set|sets|account|accounts|campaign|campaigns|creative|creatives|insights?|pixel|pixels|"
                      r"audience|audiences|catalog|catalogs|product|products|page|pages|business|businesses|by|for|of|the|a|id|ids|"
                      r"details?|status|health|performance|report|reports|stats|metrics|summary|events?|conversions?|capi|api|"
                      r"budget|budgets|spend|reach|estimate|delivery|targeting|options|info|history|breakdowns?|lead|leads|forms?|"
                      r"instagram|facebook|meta|users?|me|list|test|tests|study|studies|lift|ab|image|images|video|videos)$")


def _looks_like_verb(tok: str) -> bool:
    return not _NOUN_OK.match(tok)


def effective_class(tool: dict[str, Any], overrides: dict[str, str]) -> str:
    """The owner's choice in the dashboard wins; otherwise our conservative guess."""
    o = overrides.get(tool["name"])
    return o if o in CLASSES else classify(tool)


def safe_name(name: str) -> str:
    """Short, valid tool names (the full name gets an 'mcp__team__' prefix and must stay under 64 chars)."""
    clean = re.sub(r"[^A-Za-z0-9_]", "_", name)
    if len(clean) > 40:
        clean = clean[:32] + "_" + hashlib.sha1(name.encode()).hexdigest()[:7]
    return "meta_" + clean


def check_args(tool: dict[str, Any], args: dict[str, Any]) -> str | None:
    """Return a reason to refuse, or None. Keeps what the owner reviews identical to what is sent."""
    props = ((tool.get("inputSchema") or {}).get("properties") or {}) if isinstance(tool.get("inputSchema"), dict) else {}
    unknown = [k for k in args if props and k not in props]
    if unknown:
        return "فيه حقول مش موجودة في تعريف الأداة: " + ", ".join(unknown[:5])
    text = json.dumps(args, ensure_ascii=False)
    if len(text) > MAX_ARGS_CHARS:
        return "الطلب أطول من إنه يتراجع على الموبايل. قسّمه أو قلّله."
    if text != "".join(ch for ch in text if __import__("unicodedata").category(ch) not in ("Cf", "Co", "Cn")):
        return "فيه حروف مخفية في الطلب"
    return None


def auto_allowed(tool_name: str, args: dict[str, Any]) -> bool:
    """Even on an allow-listed tool, anything that can start spending waits for the owner."""
    if _RISKY_AUTO.search(tool_name):
        return False

    def walk(obj: Any) -> bool:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if re.search(r"budget|bid|spend|amount|cost|cap|price|value|field|target|optimi[sz]", str(k), re.I):
                    return False
                if str(k).lower() in ("status", "effective_status", "configured_status") and str(v).upper() != "PAUSED":
                    return False
                if not walk(v):
                    return False
            return True
        if isinstance(obj, list):
            return all(walk(v) for v in obj)
        if isinstance(obj, str):
            st = obj.strip()
            if st[:1] in "{[":  # JSON hidden inside a string
                return False
        return True
    return walk(args)


def pretty_args(args: dict[str, Any]) -> str:
    return json.dumps(args, ensure_ascii=False, indent=1, sort_keys=True)


class MCPClient:
    def __init__(self, bearer: str, url: str = URL):
        if not bearer:
            raise MetaMCPError("مفيش token للـMCP بتاع Meta في ملف .env")
        self._bearer = bearer
        self._url = url
        self._session: str | None = None
        self._id = 0
        self._lock = threading.Lock()

    def _headers(self) -> dict[str, str]:
        h = {"Authorization": f"Bearer {self._bearer}", "Content-Type": "application/json",
             "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": PROTOCOL}
        if self._session:
            h["Mcp-Session-Id"] = self._session
        return h

    def _post(self, payload: dict, expect_id: int | None) -> dict | None:
        try:
            with httpx.Client(timeout=60) as http:
                r = http.post(self._url, headers=self._headers(), content=json.dumps(payload))
        except httpx.HTTPError as e:
            raise MetaMCPError(f"مش قادر أوصل لـMCP بتاع Meta ({type(e).__name__})") from None
        if r.status_code == 404 and self._session:
            raise _SessionExpired()
        if r.status_code in (401, 403):
            raise MetaMCPAuthError(f"Meta رفضت الـtoken بتاع الـMCP (HTTP {r.status_code}){_why(r)}")
        if r.status_code >= 400:
            raise MetaMCPError(f"الـMCP بتاع Meta رد بخطأ HTTP {r.status_code}")
        if r.headers.get("mcp-session-id"):
            self._session = r.headers["mcp-session-id"]
        if expect_id is None:
            return None
        ctype = r.headers.get("content-type", "")
        messages: list[dict] = []
        if "text/event-stream" in ctype:
            for line in r.text.splitlines():
                if line.startswith("data:"):
                    try:
                        messages.append(json.loads(line[5:].strip()))
                    except ValueError:
                        continue
        else:
            try:
                body = r.json()
            except ValueError:
                raise MetaMCPError("رد مش مفهوم من الـMCP بتاع Meta") from None
            messages = body if isinstance(body, list) else [body]
        for m in messages:
            if isinstance(m, dict) and m.get("id") == expect_id:
                if "error" in m:
                    raise MetaMCPError("Meta MCP: " + str((m["error"] or {}).get("message", "خطأ"))[:300])
                return m.get("result") or {}
        raise MetaMCPError("الـMCP بتاع Meta مرجّعش رد")

    def _rpc(self, method: str, params: dict | None = None) -> dict:
        with self._lock:
            for attempt in range(2):
                if self._session is None and method != "initialize":
                    self._initialize()
                self._id += 1
                try:
                    return self._post({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}}, self._id) or {}
                except _SessionExpired:
                    self._session = None  # the server forgot us: start a new session once
            raise MetaMCPError("الـsession مع Meta MCP انتهت ومقدرتش أبدأ واحدة جديدة")

    def _initialize(self) -> None:
        self._id += 1
        self._post({"jsonrpc": "2.0", "id": self._id, "method": "initialize", "params": {
            "protocolVersion": PROTOCOL, "capabilities": {}, "clientInfo": {"name": "sales-team", "version": "1.0"}}}, self._id)
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, None)
        if self._session is None:
            self._session = ""  # stateless server: fine, just don't initialize again

    def list_tools(self) -> list[dict]:
        tools, cursor, pages = [], None, 0
        while pages < 20:
            res = self._rpc("tools/list", {"cursor": cursor} if cursor else {})
            tools.extend(res.get("tools", []))
            cursor = res.get("nextCursor")
            pages += 1
            if not cursor:
                break
        return tools

    def call_tool(self, name: str, arguments: dict) -> dict:
        return self._rpc("tools/call", {"name": name, "arguments": arguments})


class FakeMCP:
    """Demo mode: a few tools shaped like Meta's, so the settings page and approvals can be tried."""

    def list_tools(self) -> list[dict]:
        obj = {"type": "object", "properties": {"ad_account_id": {"type": "string"}}, "required": []}
        return [
            {"name": "get_ad_account_insights", "description": "Performance insights for an ad account.", "inputSchema": obj,
             "annotations": {"readOnlyHint": True}},
            {"name": "list_campaigns", "description": "List campaigns in an ad account.", "inputSchema": obj},
            {"name": "diagnose_pixel", "description": "Check pixel and Conversions API health.", "inputSchema": obj},
            {"name": "update_ad_set", "description": "Update an ad set (status, budget, schedule).",
             "inputSchema": {"type": "object", "properties": {"ad_set_id": {"type": "string"}, "status": {"type": "string"},
                                                              "daily_budget": {"type": "number"}}, "required": ["ad_set_id"]},
             "annotations": {"destructiveHint": False}},
            {"name": "create_campaign", "description": "Create a new campaign.",
             "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}, "objective": {"type": "string"}}, "required": ["name"]}},
        ]

    def call_tool(self, name: str, arguments: dict) -> dict:
        if name == "diagnose_pixel":
            return {"content": [{"type": "text", "text": "Pixel active · Purchase event firing · CAPI connected · match quality 7.1/10"}]}
        if name in ("get_ad_account_insights", "list_campaigns"):
            return {"content": [{"type": "text", "text": json.dumps({"demo": True, "tool": name, "rows": 4})}]}
        return {"content": [{"type": "text", "text": json.dumps({"demo": True, "applied": name, "args": arguments})}]}


_client = None
_tools_cache: tuple[float, list[dict]] | None = None
_lock = threading.Lock()


def client():
    global _client
    with _lock:
        if _client is None:
            _client = FakeMCP() if config.demo_mode() else MCPClient(token())
        return _client


def tools(force: bool = False) -> list[dict]:
    """Meta's tool list, cached 10 minutes."""
    global _tools_cache
    if not force and _tools_cache and time.time() - _tools_cache[0] < 600:
        return _tools_cache[1]
    lst = client().list_tools()
    clean = [t for t in lst if isinstance(t, dict) and isinstance(t.get("name"), str) and t["name"]][:300]
    _tools_cache = (time.time(), clean)
    return clean


def result_text(res: dict) -> str:
    parts = []
    for c in res.get("content") or []:
        if isinstance(c, dict) and c.get("type") == "text":
            parts.append(str(c.get("text", "")))
    if res.get("structuredContent") and not parts:
        parts.append(json.dumps(res["structuredContent"], ensure_ascii=False))
    text = "\n".join(parts) or "(فاضي)"
    return text[:20000]


def reset_for_tests() -> None:
    global _client, _tools_cache
    _client, _tools_cache = None, None
