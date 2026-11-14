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


def test_meta_schema_for_completion(client, tokens):
    """SQL 编辑器补全数据：表 + 字段 + 类型。"""
    ds = _setup(client, tokens)
    r = client.get(f"/api/meta/schema/{ds['id']}", headers=H(tokens["analyst"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["datasource"] == ds["name"]
    tb = next(t for t in body["tables"] if t["name"] == "orders")
    col_names = {c["name"] for c in tb["columns"]}
    assert {"id", "city", "amount"} <= col_names
    assert any(c["type"] for c in tb["columns"])


def test_meta_schema_404(client, tokens):
    r = client.get("/api/meta/schema/99999", headers=H(tokens["analyst"]))
    assert r.status_code == 404


def test_assist_rule_mode_generates_sql(client, tokens):
    """AI 助写（规则引擎模式）：NL → SQL 只生成不执行，结果过只读校验。"""
    ds = _setup(client, tokens)
    r = client.post("/api/query/assist", headers=H(tokens["analyst"]),
                    json={"datasource_id": ds["id"], "text": "查 q_src 的 orders 前5行"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sql"].upper().startswith("SELECT")
    assert body["provider"] == "rule-engine"
    # 不落查询历史（只生成不执行）
    h = H(tokens["analyst"])
    hist = client.get("/api/query/history", headers=h).json()
    assert not any(item["sql"] == body["sql"] for item in hist)


def test_assist_validates_readonly(client, tokens):
    """助写生成结果强制过只读校验（纵深防御）。"""
    ds = _setup(client, tokens)
    from unittest.mock import patch
    from app.services import llm as llm_mod

    class _FakeLLM:
        def chat(self, messages, tools):
            from app.services.llm import ChatResult
            return ChatResult(content="DELETE FROM orders")

    with patch.object(llm_mod, "get_llm", lambda: _FakeLLM()):
        r = client.post("/api/query/assist", headers=H(tokens["analyst"]),
                        json={"datasource_id": ds["id"], "text": "删掉 orders 表数据"})
    assert r.status_code == 400
    assert "只读" in r.json()["detail"]


def test_assist_empty_text(client, tokens):
    ds = _setup(client, tokens)
    r = client.post("/api/query/assist", headers=H(tokens["analyst"]),
                    json={"datasource_id": ds["id"], "text": "  "})
    assert r.status_code == 400
