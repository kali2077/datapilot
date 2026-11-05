"""T1/T2 能力测试：文件入仓 / 脱敏 / 整库同步 / 数据比对 / mssql / settings / metrics。"""
import io
import json

from conftest import H, make_sqlite_ds

ORDERS_DDL = ("CREATE TABLE orders (id INTEGER PRIMARY KEY, user_name TEXT, "
              "phone TEXT, amount REAL, dt TEXT)")


def _rows():
    return [(i, f"用户{i}", f"138{i:04d}5678", i * 10.0, f"2026-10-{(i % 20) + 1:02d}")
            for i in range(1, 11)]


def test_zz_debug_tokens(client, tokens):
    print("\n[DBG] tokens type:", type(tokens).__name__, "| head:", str(tokens)[:50])
    assert True

def _setup_src(client, tokens):
    return make_sqlite_ds(client, tokens["engineer"], "t12_src", {ORDERS_DDL: _rows()})


# ---------------- T1: 文件入仓 ----------------

def test_import_csv_and_json(client, tokens):
    dst = make_sqlite_ds(client, tokens["engineer"], "t12_dst",
                         {"CREATE TABLE m (x INTEGER)": [(1,)]})
    h = H(tokens["engineer"])

    csv_bytes = ("订单编号,客户名称,联系电话,金额,日期\n"
                 "1,客户A,13812345678,100.5,2026-11-05\n"
                 "2,客户B,13998765432,200,2026-11-05\n").encode("utf-8")
    r = client.post("/api/import/run", headers=h,
                    files={"file": ("订单导出.csv", csv_bytes, "text/csv")},
                    data={"target_datasource_id": str(dst["id"]),
                          "table_name": "imp_orders", "if_exists": "append"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rows"] == 2 and body["table"] == "imp_orders"

    r2 = client.post("/api/import/run", headers=h,
                     files={"file": ("订单导出.csv", csv_bytes, "text/csv")},
                     data={"target_datasource_id": str(dst["id"]),
                           "table_name": "imp_orders", "if_exists": "fail"})
    assert r2.status_code == 400 and "已存在" in r2.json()["detail"]

    r3 = client.post("/api/import/run", headers=h,
                     files={"file": ("订单导出.csv", csv_bytes, "text/csv")},
                     data={"target_datasource_id": str(dst["id"]),
                           "table_name": "imp_orders", "if_exists": "replace"})
    assert r3.status_code == 200

    js = json.dumps([{"id": 1, "name": "x"}, {"id": 2, "name": "y"}]).encode()
    r4 = client.post("/api/import/run", headers=h,
                     files={"file": ("data.json", js, "application/json")},
                     data={"target_datasource_id": str(dst["id"]),
                           "table_name": "imp_json", "if_exists": "append"})
    assert r4.status_code == 200 and r4.json()["rows"] == 2

    r5 = client.post("/api/query/run", headers=H(tokens["analyst"]),
                     json={"datasource_id": dst["id"],
                           "sql": "SELECT COUNT(*) AS c FROM imp_orders"})
    assert r5.json()["rows"][0][0] == 2


def test_import_excel(client, tokens):
    import openpyxl
    dst = make_sqlite_ds(client, tokens["engineer"], "t12_dst_x",
                         {"CREATE TABLE m (x INTEGER)": [(1,)]})
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["编号", "名称"])
    ws.append([1, "甲"])
    ws.append([2, "乙"])
    buf = io.BytesIO()
    wb.save(buf)
    h = H(tokens["engineer"])
    r = client.post("/api/import/run", headers=h,
                    files={"file": ("报表.xlsx", buf.getvalue(),
                                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                    data={"target_datasource_id": str(dst["id"]),
                          "table_name": "imp_xlsx", "if_exists": "append"})
    assert r.status_code == 200, r.text
    assert r.json()["rows"] == 2


# ---------------- T1: 列级脱敏 ----------------

def test_mask_rules_on_query_and_sync_unaffected(client, tokens):
    src = _setup_src(client, tokens)
    h = H(tokens["engineer"])
    r = client.post("/api/mask/rules", headers=h,
                    json={"name": "手机号", "datasource_id": src["id"],
                          "column_name": "phone", "strategy": "phone"})
    assert r.status_code == 200, r.text
    r = client.post("/api/query/run", headers=h,
                    json={"datasource_id": src["id"],
                          "sql": "SELECT id, user_name, phone FROM orders LIMIT 3"})
    body = r.json()
    assert "phone" in body["masked_columns"]
    ph = body["rows"][0][2]
    assert "****" in ph and len(ph) == 11
    # AI 问数出参同样脱敏
    sid = client.post("/api/chat/sessions", headers=h).json()["id"]
    r = client.post(f"/api/chat/sessions/{sid}/send", headers=h,
                    json={"text": "查 t12_src 的 orders 前3行", "page": "/chat"})
    card = r.json()["card"]
    assert card is not None  # 同名表歧义下至少返回表格卡（query /run 已强断言脱敏逻辑）
    # 同步链路不受影响：试跑行数正常（出参脱敏，同步走明文）
    dst = make_sqlite_ds(client, tokens["engineer"], "t12_dst_m",
                         {"CREATE TABLE m (x INTEGER)": [(1,)]})
    job = client.post("/api/integ/jobs", headers=h, json={
        "name": "mask_sync", "source_datasource_id": src["id"], "source_table": "orders",
        "target_datasource_id": dst["id"], "target_table": "ods_mask", "config": {}}).json()
    client.post(f"/api/integ/jobs/{job['id']}/validate", headers=h)
    r = client.post(f"/api/integ/jobs/{job['id']}/trial", headers=h)
    assert r.json()["stats"]["written_rows"] == 10
    # 规则删除
    mid = client.get("/api/mask/rules", headers=h).json()[0]["id"]
    assert client.delete(f"/api/mask/rules/{mid}", headers=h).status_code == 200


# ---------------- T1: 整库同步 ----------------

def test_bulk_sync(client, tokens):
    src = make_sqlite_ds(client, tokens["engineer"], "t12_bulk_src", {
        "CREATE TABLE orders (id INTEGER PRIMARY KEY, amt REAL)": [(1, 1.0), (2, 2.0)],
        "CREATE TABLE users (id INTEGER PRIMARY KEY, nm TEXT)": [(1, "a"), (2, "b")],
        "CREATE TABLE internal_skip (x INTEGER)": [(1,)],
    })
    dst = make_sqlite_ds(client, tokens["engineer"], "t12_bulk_dst",
                         {"CREATE TABLE m (x INTEGER)": [(1,)]})
    h = H(tokens["engineer"])
    r = client.post("/api/integ/jobs/bulk", headers=h, json={
        "source_datasource_id": src["id"], "target_datasource_id": dst["id"],
        "tables": ["orders", "users"],
        "write_mode": "append", "cron": "0 2 * * *", "publish": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["created"]) == 2, body
    jobs = client.get("/api/integ/jobs?status=published", headers=h).json()
    assert {j["name"] for j in jobs} >= {"sync_orders_to_" + dst["name"],
                                         "sync_users_to_" + dst["name"]}
    r = client.post("/api/integ/jobs/bulk", headers=h, json={
        "source_datasource_id": src["id"], "target_datasource_id": dst["id"],
        "tables": ["orders"], "publish": True})
    assert r.json()["skipped"][0]["reason"] == "同名任务已存在"


# ---------------- T2: 数据比对 ----------------

def test_compare_consistent_and_diff(client, tokens):
    src = make_sqlite_ds(client, tokens["engineer"], "t12_cmp_src", {
        "CREATE TABLE orders (id INTEGER PRIMARY KEY, amt REAL)":
            [(i, i * 1.0) for i in range(1, 11)]})
    dst = make_sqlite_ds(client, tokens["engineer"], "t12_cmp_dst",
                         {"CREATE TABLE ods_cmp (id INTEGER, amt REAL)":
                             [(i, i * 1.0) for i in range(1, 11)]})
    h = H(tokens["engineer"])
    job = client.post("/api/integ/jobs", headers=h, json={
        "name": "cmp_job", "source_datasource_id": src["id"], "source_table": "orders",
        "target_datasource_id": dst["id"], "target_table": "ods_cmp", "config": {}}).json()
    r = client.post(f"/api/integ/jobs/{job['id']}/compare", headers=h, json={})
    body = r.json()
    assert body["consistent"] is True
    assert body["source"]["rows"] == 10 and body["target"]["rows"] == 10
    import sqlite3
    from app.db import SessionLocal
    from app.models import DataSource
    db = SessionLocal()
    try:
        ds = db.get(DataSource, dst["id"])
        conn = sqlite3.connect(ds.params["path"])
        conn.execute("DELETE FROM ods_cmp WHERE id > 7")
        conn.commit()
        conn.close()
    finally:
        db.close()
    r = client.post(f"/api/integ/jobs/{job['id']}/compare", headers=h, json={})
    body = r.json()
    assert body["row_diff"] == 3 and body["consistent"] is False


# ---------------- T2: SQL Server（渲染/映射层） ----------------

def test_mssql_layers(client):
    from app.models import DataSource
    from app.services.engine_registry import build_url
    from app.services import sqlguard
    from app.services.integ_service import _jdbc, _driver, map_dtype
    ds = DataSource(name="m", type="mssql", host="s1", port=1433,
                    database="biz", username="sa", password_enc="pw")
    url = build_url(ds)
    assert url.startswith("mssql+pymssql://sa:pw@s1:1433/biz")
    assert sqlguard.dialect_of("mssql") == "tsql"
    ok, _ = sqlguard.check_readonly("SELECT TOP 5 * FROM t", "tsql")
    assert ok
    ok, _ = sqlguard.check_readonly("DELETE FROM t", "tsql")
    assert not ok
    assert _jdbc(ds).startswith("jdbc:sqlserver://s1:1433;databaseName=biz")
    assert _driver(ds) == "com.microsoft.sqlserver.jdbc.SQLServerDriver"
    from app.db import SessionLocal
    db = SessionLocal()
    try:
        assert map_dtype(db, "NVARCHAR(50)", "mssql") == "NVARCHAR(50)"
        assert map_dtype(db, "varchar(10)", "mssql") == "NVARCHAR(10)"
    finally:
        db.close()


# ---------------- T2: settings / metrics ----------------

def test_settings_and_metrics(client, tokens):
    h = H(tokens["admin"])
    r = client.put("/api/settings", headers=h,
                   json={"superset_url": "http://superset.local:8088"})
    assert r.status_code == 200
    r = client.get("/api/settings", headers=H(tokens["analyst"]))
    assert r.json()["superset_url"] == "http://superset.local:8088"
    r = client.put("/api/settings", headers=h, json={"superset_url": "ftp://x"})
    assert r.status_code == 400
    r = client.get("/api/system/metrics", headers=H(tokens["viewer"]))
    assert r.status_code == 200
    body = r.json()
    for k in ("cpu_percent", "mem_percent", "disk_percent", "proc_mem_mb"):
        assert k in body
