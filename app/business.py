"""Orders, customers, follow-up candidates and sales numbers, straight from SQLite."""
from __future__ import annotations

import csv
import io
from datetime import date, datetime, timedelta
from typing import Any

from . import db, guards

SEGMENT_AR = {"confirm": "تأكيد طلب", "delivered": "بعد الاستلام", "repeat": "يشتري تاني", "lapsed": "بقاله فترة"}
STATUS_AR = {"new": "جديد", "confirmed": "متأكد", "shipped": "اتشحن", "delivered": "اتسلّم", "returned": "مرتجع", "cancelled": "اتلغى"}


class InputError(ValueError):
    pass


def _parse_day(value: str | None) -> str:
    if not value:
        return db.now()
    try:
        return datetime.fromisoformat(value.strip()).astimezone().isoformat(timespec="seconds")
    except ValueError:
        raise InputError(f"تاريخ مش مفهوم: {value}") from None


def upsert_customer(name: str, phone: str, country_code: str) -> int:
    name = guards.sanitize(name, 80)
    norm = guards.normalize_phone(phone, country_code)
    if not name:
        raise InputError("اسم العميل مطلوب")
    if not guards.valid_phone(norm):
        raise InputError("رقم التليفون مش صحيح")
    existing = db.one("SELECT id FROM customers WHERE phone = ?", (norm,))
    if existing:
        return existing["id"]
    return db.execute("INSERT INTO customers(name, phone, created_at) VALUES (?,?,?)", (name, norm, db.now()))


def add_order(data: dict[str, Any], country_code: str, imported: bool = False) -> int | None:
    """Returns the new order id, or None when an imported row already exists (re-importing is safe)."""
    try:
        amount = float(data.get("amount", ""))
    except (TypeError, ValueError):
        raise InputError("المبلغ لازم يكون رقم") from None
    if not 0 <= amount <= 10_000_000:
        raise InputError("المبلغ مش منطقي")
    payment = data.get("payment", "cod")
    status = data.get("status", "new")
    if payment not in ("cod", "online"):
        raise InputError("طريقة الدفع لازم تكون cod أو online")
    if status not in guards_status():
        raise InputError("حالة الطلب مش معروفة")
    product = guards.sanitize(data.get("product", ""), 120)
    if not product:
        raise InputError("اسم المنتج مطلوب")
    cid = upsert_customer(data.get("name", ""), data.get("phone", ""), country_code)
    created = _parse_day(data.get("created_at"))
    # An imported delivered order was delivered around its order date, not today: otherwise every old
    # order would get a "how did it fit?" message next week.
    delivered = (created if imported else db.now()) if status == "delivered" else None
    if imported and db.one("SELECT id FROM orders WHERE customer_id=? AND product=? AND amount=? AND substr(created_at,1,10)=?",
                           (cid, product, amount, created[:10])):
        return None
    return db.execute(
        "INSERT INTO orders(customer_id, product, amount, payment, status, source, created_at, delivered_at) VALUES (?,?,?,?,?,?,?,?)",
        (cid, product, amount, payment, status, guards.sanitize(data.get("source", ""), 80), created, delivered),
    )


def guards_status() -> tuple[str, ...]:
    return tuple(STATUS_AR)


def set_order_status(order_id: int, status: str) -> None:
    if status not in STATUS_AR:
        raise InputError("حالة الطلب مش معروفة")
    if not db.one("SELECT id FROM orders WHERE id = ?", (order_id,)):
        raise InputError("الطلب مش موجود")
    delivered = db.now() if status == "delivered" else None
    db.execute("UPDATE orders SET status = ?, delivered_at = COALESCE(?, delivered_at) WHERE id = ?", (status, delivered, order_id))


CSV_COLUMNS = ("name", "phone", "product", "amount", "payment", "status", "source", "created_at")


def import_csv(text: str, country_code: str) -> dict[str, Any]:
    if len(text) > 5_000_000:
        raise InputError("الملف كبير أوي (أقصى حاجة 5 ميجا)")
    reader = csv.DictReader(io.StringIO(text))
    missing = [c for c in ("name", "phone", "product", "amount") if c not in (reader.fieldnames or [])]
    if missing:
        raise InputError("أعمدة ناقصة في الملف: " + ", ".join(missing))
    ok, skipped, errors = 0, 0, []
    for i, row in enumerate(reader, start=2):
        if i > 20002:
            errors.append("وقفت عند 20,000 صف")
            break
        try:
            data = {k: (row.get(k) or "").strip() for k in CSV_COLUMNS if row.get(k) is not None}
            data["payment"] = data.get("payment") or "cod"
            if not data.get("status"):
                # No status column: an order older than 3 days is history, not something to confirm now.
                age = _days_ago(_parse_day(data.get("created_at"))) or 0
                data["status"] = "delivered" if age > 3 else "new"
            if add_order(data, country_code, imported=True) is None:
                skipped += 1
            else:
                ok += 1
        except InputError as e:
            errors.append(f"صف {i}: {e}")
    return {"imported": ok, "skipped_duplicates": skipped, "errors": errors[:50]}


def list_orders(limit: int = 200) -> list[dict]:
    return db.rows(
        """SELECT o.*, c.name AS customer_name, c.phone FROM orders o JOIN customers c ON c.id = o.customer_id
           ORDER BY o.created_at DESC LIMIT ?""", (limit,))


def _days_ago(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(iso).date()
    except ValueError:
        return None
    return (date.today() - d).days


def followup_candidates(s: dict[str, Any], limit: int = 40) -> list[dict[str, Any]]:
    """Who to talk to today, and why. Keys are stable so the same person is never messaged twice for one reason."""
    done_keys = {r["cand_key"] for r in db.rows("SELECT cand_key FROM followups")}
    recent_customer = {r["customer_id"] for r in db.rows(
        "SELECT customer_id FROM followups WHERE created_at >= ?", ((datetime.now() - timedelta(days=14)).isoformat(),))}
    out: list[dict[str, Any]] = []

    cod_matters = s["payment_methods"] in ("cod", "both")
    if cod_matters:
        for o in db.rows("""SELECT o.id, o.product, o.amount, o.created_at, o.source, c.id AS cid, c.name
                            FROM orders o JOIN customers c ON c.id = o.customer_id
                            WHERE o.status = 'new' AND o.payment = 'cod' ORDER BY o.created_at DESC"""):
            key = f"confirm:order:{o['id']}"
            age = _days_ago(o["created_at"])
            if key not in done_keys and age is not None and age <= 3:  # confirmation only makes sense for fresh orders
                out.append({"key": key, "segment": "confirm", "customer_id": o["cid"], "order_id": o["id"],
                            "first_name": o["name"].split(" ")[0], "product": o["product"], "amount": o["amount"],
                            "days_since_order": _days_ago(o["created_at"]), "ad_source": o["source"]})

    for o in db.rows("""SELECT o.id, o.product, o.delivered_at, c.id AS cid, c.name FROM orders o
                        JOIN customers c ON c.id = o.customer_id WHERE o.status = 'delivered'"""):
        d = _days_ago(o["delivered_at"])
        key = f"delivered:order:{o['id']}"
        if d is not None and 3 <= d <= 7 and key not in done_keys:
            out.append({"key": key, "segment": "delivered", "customer_id": o["cid"], "order_id": o["id"],
                        "first_name": o["name"].split(" ")[0], "product": o["product"], "days_since_delivery": d})

    for c in db.rows("""SELECT c.id, c.name, MAX(o.created_at) AS last_at, COUNT(o.id) AS n,
                               (SELECT product FROM orders WHERE customer_id = c.id ORDER BY created_at DESC LIMIT 1) AS last_product
                        FROM customers c JOIN orders o ON o.customer_id = c.id
                        WHERE o.status NOT IN ('cancelled','returned') GROUP BY c.id"""):
        d = _days_ago(c["last_at"])
        if d is None or c["id"] in recent_customer:
            continue
        seg = None
        if s["repeat_days"] <= d < s["lapsed_days"]:
            seg = "repeat"
        elif s["lapsed_days"] <= d <= s["lapsed_days"] + 120:
            seg = "lapsed"
        if seg:
            key = f"{seg}:customer:{c['id']}:{c['last_at'][:10]}"
            if key not in done_keys:
                out.append({"key": key, "segment": seg, "customer_id": c["id"], "order_id": None,
                            "first_name": c["name"].split(" ")[0], "last_product": c["last_product"],
                            "days_since_last_order": d, "orders_count": c["n"]})
    return out[:limit]


def sales_summary(days: int) -> dict[str, Any]:
    days = max(1, min(int(days), 90))
    since = (datetime.now() - timedelta(days=days)).isoformat()
    tot = db.one("""SELECT COUNT(*) AS orders, COALESCE(SUM(amount),0) AS revenue,
                    SUM(payment='cod') AS cod, SUM(status='returned') AS returned, SUM(status='cancelled') AS cancelled
                    FROM orders WHERE created_at >= ?""", (since,)) or {}
    by_source = db.rows("""SELECT COALESCE(NULLIF(source,''),'غير معروف') AS source, COUNT(*) AS orders,
                           ROUND(SUM(amount),2) AS revenue, SUM(status='returned') AS returned
                           FROM orders WHERE created_at >= ? GROUP BY 1 ORDER BY revenue DESC LIMIT 12""", (since,))
    repeat = db.one("""SELECT COUNT(*) AS n FROM (SELECT customer_id FROM orders WHERE created_at >= ?
                       AND customer_id IN (SELECT customer_id FROM orders WHERE created_at < ?) GROUP BY customer_id)""",
                    (since, since)) or {"n": 0}
    fu = db.one("""SELECT SUM(status='sent') AS sent FROM followups WHERE created_at >= ?""", (since,)) or {}
    n = tot.get("orders") or 0
    return {
        "days": days, "orders": n, "revenue": round(tot.get("revenue") or 0, 2),
        "avg_order": round((tot.get("revenue") or 0) / n, 2) if n else None,
        "cod_share_pct": round((tot.get("cod") or 0) / n * 100, 1) if n else None,
        "returned_pct": round((tot.get("returned") or 0) / n * 100, 1) if n else None,
        "cancelled_pct": round((tot.get("cancelled") or 0) / n * 100, 1) if n else None,
        "returning_customers": repeat["n"], "followups_sent": fu.get("sent") or 0,
        "by_source": by_source,
    }
