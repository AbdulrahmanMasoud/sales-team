import pytest
from fastapi.testclient import TestClient

from app import config, db, demo
from app.server import create_app

BASE = f"http://127.0.0.1:{config.port()}"


@pytest.fixture
def client(fresh):
    with TestClient(create_app(no_scheduler=True), base_url=BASE) as c:
        yield c


def H():
    return {"X-CSRF-Token": config.csrf_secret()}


def test_page_has_token_and_security_headers(client):
    r = client.get("/")
    assert r.status_code == 200 and config.csrf_secret() in r.text
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"


def test_foreign_host_blocked(fresh):
    with TestClient(create_app(no_scheduler=True), base_url="http://evil.example.com") as c:
        assert c.get("/api/status").status_code == 403


def test_changes_need_csrf_and_same_origin(client):
    assert client.put("/api/settings", json={"target_cpr": 90}).status_code == 403
    assert client.put("/api/settings", json={"target_cpr": 90}, headers={**H(), "Origin": "https://evil.example.com"}).status_code == 403
    r = client.put("/api/settings", json={"target_cpr": 90}, headers={**H(), "Origin": BASE})
    assert r.status_code == 200 and r.json()["values"]["target_cpr"] == 90


def test_settings_validation_errors(client):
    assert client.put("/api/settings", json={"daily_cap": -5}, headers=H()).status_code == 400
    assert client.put("/api/settings", json=["x"], headers=H()).status_code == 400
    assert client.put("/api/settings", content="not json", headers={**H(), "Content-Type": "application/json"}).status_code == 400


def test_overview_status_and_views(client):
    demo.seed()
    for path in ("/api/status", "/api/overview", "/api/events", "/api/agents/media_buyer", "/api/decisions?status=all",
                 "/api/followups", "/api/orders", "/api/settings"):
        r = client.get(path)
        assert r.status_code == 200, path
    assert client.get("/api/agents/nope").status_code == 404
    assert client.get("/api/events?day=nope").status_code == 400
    assert client.get("/api/decisions?status=';drop").status_code == 400


def test_orders_and_import(client):
    r = client.post("/api/orders", json={"name": "منى علي", "phone": "01001112233", "product": "هودي", "amount": 520,
                                         "payment": "cod", "status": "new", "source": "إعلان الهودي"}, headers=H())
    assert r.status_code == 200
    assert client.post("/api/orders", json={"name": "x", "phone": "12", "product": "p", "amount": 1}, headers=H()).status_code == 400
    assert client.post("/api/orders", json={"name": "x", "phone": "01001112233", "product": "p", "amount": "abc"}, headers=H()).status_code == 400
    csv = "name,phone,product,amount,payment\nسعاد,01011112222,جاكيت,990,online\nبايظ,1,x,1,cod\n"
    out = client.post("/api/orders/import", json={"csv": csv}, headers=H()).json()
    assert out["imported"] == 1 and len(out["errors"]) == 1
    oid = r.json()["id"]
    assert client.post(f"/api/orders/{oid}/status", json={"status": "delivered"}, headers=H()).status_code == 200
    assert client.post(f"/api/orders/{oid}/status", json={"status": "stolen"}, headers=H()).status_code == 400


def test_decision_flow_over_http(client, fresh):
    did = db.execute("""INSERT INTO decisions(agent, kind, title, action, mode, status, created_at)
                        VALUES ('media_buyer','pause','قفل','{"type":"pause","object_id":"120210000000011"}','ask','pending',?)""", (db.now(),))
    assert client.post(f"/api/decisions/{did}/approve").status_code == 403  # no csrf
    r = client.post(f"/api/decisions/{did}/approve", headers=H())
    assert r.status_code == 200 and r.json()["status"] == "executed"
    assert client.post(f"/api/decisions/{did}/approve", headers=H()).status_code == 409  # not twice


def test_followup_update(client):
    demo.seed()
    db.execute("INSERT INTO followups(customer_id, order_id, segment, cand_key, message, status, created_at) VALUES (1,1,'confirm','confirm:order:1','أهلاً','draft',?)", (db.now(),))
    fid = db.one("SELECT id FROM followups")["id"]
    assert client.post(f"/api/followups/{fid}", json={"status": "sent"}, headers=H()).status_code == 200
    assert client.post(f"/api/followups/{fid}", json={"status": "hacked"}, headers=H()).status_code == 400
    items = client.get("/api/followups").json()["items"]
    assert items[0]["status"] == "sent" and items[0]["phone"].startswith("20")
