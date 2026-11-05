"""资产搜索、标签、审计日志测试。"""
from conftest import H, make_sqlite_ds

ORDERS_DDL = "CREATE TABLE sales_华北 (id INTEGER PRIMARY KEY, region TEXT, revenue REAL)"


def test_asset_search_and_tags(client, tokens):
    ds = make_sqlite_ds(client, tokens["engineer"], "as_src",
                        {ORDERS_DDL: [(1, "华北", 100.0)]})
    h = H(tokens["engineer"])
    # 搜索（表名）
    r = client.get("/api/asset/search?keyword=sales_华北", headers=h)
    assert any(t["name"] == "sales_华北" for t in r.json()["tables"])
    # 标签
    r = client.post("/api/asset/tags", headers=h, json={"name": "核心表", "color": "#F56C6C"})
    tag_id = r.json()["id"]
    r = client.get("/api/asset/search?keyword=sales_华北", headers=h)
    tid = r.json()["tables"][0]["id"]
    r = client.post(f"/api/asset/tables/{tid}/tags", headers=h,
                    json={"tag_id": tag_id, "on": True})
    assert r.status_code == 200
    r = client.get("/api/asset/search?keyword=核心表", headers=h)
    assert any(t["id"] == tid for t in r.json()["tables"])
    # 取消标签
    client.post(f"/api/asset/tables/{tid}/tags", headers=h,
                json={"tag_id": tag_id, "on": False})
    r = client.get("/api/asset/search?keyword=核心表", headers=h)
    assert not any(t["id"] == tid for t in r.json()["tables"])


def test_audit_logs_admin_only(client, tokens):
    # 触发一次可审计动作
    client.post("/api/asset/tags", headers=H(tokens["engineer"]),
                json={"name": f"审计标签{int(time.time())}"})
    r = client.get("/api/audit/logs", headers=H(tokens["viewer"]))
    assert r.status_code == 403
    r = client.get("/api/audit/logs?source=ai", headers=H(tokens["admin"]))
    assert r.status_code == 200


import time  # noqa: E402
