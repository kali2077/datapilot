"""数据查询工作台 e2e 测试。"""
from conftest import H, make_sqlite_ds

ORDERS_DDL = "CREATE TABLE orders (id INTEGER PRIMARY KEY, city TEXT, amount REAL)"


def _setup(client, tokens):
    return make_sqlite_ds(
        client, tokens["engineer"], "q_src",
        {ORDERS_DDL: [(i, ["北京", "上海"][i % 2], i * 1.0) for i in range(1, 31)]})


def test_run_readonly_query(client, tokens):
    ds = _setup(client, tokens)
    h = H(tokens["analyst"])  # 分析师可自助取数
    r = client.post("/api/query/run", headers=h,
                    json={"datasource_id": ds["id"],
                          "sql": "SELECT city, COUNT(*) AS cnt FROM orders GROUP BY city"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["row_count"] == 2
    assert body["columns"] == ["city", "cnt"]
    assert body["cost_ms"] >= 0
    # 自动包裹 LIMIT
    assert "LIMIT" in body["sql"].upper()


def test_run_blocks_dml(client, tokens):
    ds = _setup(client, tokens)
    r = client.post("/api/query/run", headers=H(tokens["analyst"]),
                    json={"datasource_id": ds["id"], "sql": "DELETE FROM orders"})
    assert r.status_code == 400
    r = client.post("/api/query/run", headers=H(tokens["analyst"]),
                    json={"datasource_id": ds["id"], "sql": "ATTACH DATABASE 'x' AS y"})
    assert r.status_code == 400


def test_history_recorded_and_capped(client, tokens):
    ds = _setup(client, tokens)
    h = H(tokens["analyst"])
    client.post("/api/query/run", headers=h,
                json={"datasource_id": ds["id"], "sql": "SELECT COUNT(*) FROM orders"})
    r = client.get("/api/query/history", headers=h)
    items = r.json()
    assert len(items) >= 1
    assert items[0]["status"] == "ok"
    assert items[0]["datasource"] == "q_src" or items[0]["datasource"].startswith("q_src")
    # 清空自己的历史
    r = client.delete("/api/query/history", headers=h)
    assert r.status_code == 200
    assert client.get("/api/query/history", headers=h).json() == []


def test_saved_queries(client, tokens):
    ds = _setup(client, tokens)
    h = H(tokens["analyst"])
    r = client.post("/api/query/saved", headers=h,
                    json={"name": "每日订单量", "datasource_id": ds["id"],
                          "sql": "SELECT dt2, COUNT(*) FROM orders GROUP BY dt2"})
    assert r.status_code == 200
    sid = r.json()["id"]
    r = client.get("/api/query/saved", headers=h)
    assert any(s["id"] == sid and s["name"] == "每日订单量" for s in r.json())
    # 别人看不到
    r = client.get("/api/query/saved", headers=H(tokens["engineer"]))
    assert not any(s["id"] == sid for s in r.json())
    r = client.delete(f"/api/query/saved/{sid}", headers=h)
    assert r.status_code == 200
    assert client.get("/api/query/saved", headers=h).json() == []


def test_query_audited(client, tokens):
    ds = _setup(client, tokens)
    client.post("/api/query/run", headers=H(tokens["viewer"]),
                json={"datasource_id": ds["id"], "sql": "SELECT 1"})
    r = client.get("/api/audit/logs?action=query.run", headers=H(tokens["admin"]))
    assert r.json()["total"] >= 1
