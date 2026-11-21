"""Sample customers and orders so you can try everything before you have real data. Only used with --demo."""
from __future__ import annotations

from datetime import datetime, timedelta

from . import db, settings_store

NAMES = ["أحمد سامي", "منة الله خالد", "يوسف عادل", "سارة محمود", "كريم نبيل", "هبة فتحي", "عمر حسن",
         "ندى إبراهيم", "مصطفى رضا", "ياسمين طارق", "محمود جمال", "رنا أشرف", "علي شريف", "دينا مجدي"]
PRODUCTS = [("جاكيت شتوي", 1040), ("2 هودي", 980), ("بنطلون كارجو", 620), ("طقم شتوي", 1350), ("3 تيشيرت", 750),
            ("جاكيت جينز", 890), ("هودي", 520), ("طقم شتوي", 1350), ("بنطلون كارجو", 620), ("هودي", 520),
            ("2 تيشيرت", 500), ("جاكيت", 990), ("هودي", 520), ("سويتشيرت", 640)]


def seed() -> bool:
    if db.one("SELECT id FROM customers LIMIT 1"):
        return False
    now = datetime.now().astimezone()
    plan = [  # (status, days since order, delivered days ago)
        ("new", 1, None), ("new", 1, None), ("new", 1, None), ("new", 1, None), ("new", 1, None), ("new", 1, None),
        ("delivered", 6, 3), ("delivered", 7, 4), ("delivered", 8, 4),
        ("delivered", 32, 29), ("delivered", 35, 31), ("delivered", 31, 28),
        ("delivered", 74, 70), ("delivered", 88, 84),
    ]
    with db.connect() as c:
        for i, (name, (product, amount), (status, ago, dago)) in enumerate(zip(NAMES, PRODUCTS, plan)):
            phone = f"20100555{i:04d}"
            cid = c.execute("INSERT INTO customers(name, phone, created_at) VALUES (?,?,?)",
                            (name, phone, (now - timedelta(days=ago + 30)).isoformat())).lastrowid
            created = (now - timedelta(days=ago)).isoformat(timespec="seconds")
            delivered = (now - timedelta(days=dago)).isoformat(timespec="seconds") if dago is not None else None
            source = "اهتمامات موضة · 25-34" if i % 3 else "زوار آخر 30 يوم"
            c.execute("INSERT INTO orders(customer_id, product, amount, payment, status, source, created_at, delivered_at) VALUES (?,?,?,?,?,?,?,?)",
                      (cid, product, amount, "cod" if i % 4 else "online", status, source, created, delivered))
    # the demo account also runs a messages campaign and a video campaign: give them room and their own targets
    settings_store.update({"monthly_cap": 60000, "daily_cap": 2000, "targets": {"messages": 15, "engagement": 0.05}})
    return True
