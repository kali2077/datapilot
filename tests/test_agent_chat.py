"""AI 对话 e2e 测试（M1/M2 核心链路）。"""
from conftest import H, make_sqlite_ds, wait_instance

ORDERS_DDL = "CREATE TABLE orders (id INTEGER PRIMARY KEY, user_name TEXT, amount REAL, dt TEXT, is_test INTEGER)"


def _rows():
    return [(i, f"用户{i % 6}", i * 10.0, f"2026-10-{(i % 20) + 1:02d}", 1 if i % 9 == 0 else 0)
            for i in range(1, 41)]


def test_chat_query_data(client, tokens):
    make_sqlite_ds(client, tokens["engineer"], "c_src", {ORDERS_DDL: _rows()})
    h = H(tokens["analyst"])
    r = client.post("/api/chat/sessions", headers=h)
    sid = r.json()["id"]
    r = client.post(f"/api/chat/sessions/{sid}/send", headers=h,
                    json={"text": "查 c_src 的 orders 前5行"})
    assert r.status_code == 200, r.text
    body = r.json()
    card = body["card"]
    assert card["type"] == "table"
    assert len(card["rows"]) == 5
    assert card["sql"].upper().startswith("SELECT")


def test_chat_create_sync_and_confirm(client, tokens):
    src = make_sqlite_ds(client, tokens["engineer"], "c_src2", {ORDERS_DDL: _rows()})
    dst = make_sqlite_ds(client, tokens["engineer"], "c_dst2",
                         {"CREATE TABLE marker (x INTEGER)": [(1,)]})
    h = H(tokens["engineer"])
    r = client.post("/api/chat/sessions", headers=h)
    sid = r.json()["id"]
    r = client.post(f"/api/chat/sessions/{sid}/send", headers=h,
                    json={"text": "把 c_src2 的 orders 表每天凌晨2点同步到 c_dst2 的 ods_orders"})
    body = r.json()
    assert body["card"]["type"] == "confirm", body["content"]
    assert body["card"]["trial"]["stats"]["written_rows"] == 40
    assert body["confirm_token"]    # 确认上线
    r = client.post(f"/api/chat/sessions/{sid}/confirm", headers=h,
                    json={"message_id": body["message_id"], "token": body["confirm_token"]})
    assert r.status_code == 200, r.text
    assert "已上线" in r.json()["summary"]
    # 工作流存在且启用
    r = client.get("/api/sched/workflows", headers=h)
    assert any(w["name"].endswith("ods_orders") and w["enabled"] for w in r.json())


def test_chat_create_etl_and_run(client, tokens):
    src = make_sqlite_ds(client, tokens["engineer"], "c_src3", {ORDERS_DDL: _rows()})
    dst = make_sqlite_ds(client, tokens["engineer"], "c_dst3",
                         {"CREATE TABLE ods_base (id INTEGER)": [(1,)]})
    h = H(tokens["engineer"])
    r = client.post("/api/chat/sessions", headers=h)
    sid = r.json()["id"]
    r = client.post(f"/api/chat/sessions/{sid}/send", headers=h,
                    json={"text": "基于 ods_base 做 dwd 明细，剔除测试账号，每天凌晨3点跑"})
    body = r.json()
    assert body["card"]["type"] == "confirm", body["content"]
    assert "EXPLAIN" in body["content"] or "校验通过" in body["content"]
    r = client.post(f"/api/chat/sessions/{sid}/confirm", headers=h,
                    json={"message_id": body["message_id"], "token": body["confirm_token"]})
    assert "已上线" in r.json()["summary"]
    # 触发该 ETL
    r = client.get("/api/sched/workflows", headers=h)
    wf = next(w for w in r.json() if w["name"].startswith("etl_"))
    r = client.post(f"/api/sched/workflows/{wf['id']}/trigger", headers=h)
    inst = wait_instance(client, tokens["engineer"], r.json()["instance_id"])
    assert inst["status"] == "success", inst.get("nodes")


def test_chat_permission_denied_for_analyst(client, tokens):
    make_sqlite_ds(client, tokens["engineer"], "c_src4", {ORDERS_DDL: _rows()})
    make_sqlite_ds(client, tokens["engineer"], "c_dst4",
                   {"CREATE TABLE m (x INTEGER)": [(1,)]})
    h = H(tokens["analyst"])
    r = client.post("/api/chat/sessions", headers=h)
    sid = r.json()["id"]
    r = client.post(f"/api/chat/sessions/{sid}/send", headers=h,
                    json={"text": "把 c_src4 的 orders 表同步到 c_dst4 的 ods_orders"})
    body = r.json()
    # 工具应被拒绝（错误信息而不是上线）
    assert body.get("error") or "权限不足" in body["content"]


def test_chat_search_metadata(client, tokens):
    h = H(tokens["viewer"])
    r = client.post("/api/chat/sessions", headers=h)
    sid = r.json()["id"]
    r = client.post(f"/api/chat/sessions/{sid}/send", headers=h,
                    json={"text": "找订单相关的表"})
    body = r.json()
    assert body["card"]["type"] == "tables"
    assert len(body["card"]["tables"]) >= 1
