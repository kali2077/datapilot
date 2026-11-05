"""Bug 审查补充测试：版本回滚 / 任务删除 / 工作流删除 / pre_sql。"""
from conftest import H, make_sqlite_ds, wait_instance

ORDERS_DDL = "CREATE TABLE orders (id INTEGER PRIMARY KEY, amt REAL, dt TEXT, status TEXT)"
ROWS = [(i, i * 2.0, f"2026-10-{(i % 20) + 1:02d}", "paid") for i in range(1, 21)]


def _make_job(client, tok, name="rb_job"):
    src = make_sqlite_ds(client, tok, f"{name}_src", {ORDERS_DDL: ROWS})
    dst = make_sqlite_ds(client, tok, f"{name}_dst", {"CREATE TABLE m (x INTEGER)": [(1,)]})
    h = H(tok)
    r = client.post("/api/integ/jobs", headers=h, json={
        "name": name, "source_datasource_id": src["id"], "source_table": "orders",
        "target_datasource_id": dst["id"], "target_table": f"ods_{name}",
        "config": {"write_mode": "truncate_insert"}})
    return r.json(), src["id"], dst["id"]


def test_rollback_to_version(client, tokens):
    job, src_id, dst_id = _make_job(client, tokens["engineer"], "rb_v1")
    h = H(tokens["engineer"])
    # v1: truncate_insert
    client.post(f"/api/integ/jobs/{job['id']}/validate", headers=h)
    r = client.post(f"/api/integ/jobs/{job['id']}/publish", headers=h, json={"cron": ""})
    assert r.status_code == 200
    # 改配置产生 v2：append + 增量列
    client.post(f"/api/integ/jobs/{job['id']}/offline", headers=h)
    r = client.put(f"/api/integ/jobs/{job['id']}", headers=h, json={
        "name": "rb_v1", "source_datasource_id": src_id, "source_table": "orders",
        "source_query": "", "target_datasource_id": dst_id, "target_table": "ods_rb_v1",
        "config": {"write_mode": "append", "increment": {"enabled": True,
                                                         "column": "id", "mode": "max_value"}}})
    assert r.status_code == 200
    r = client.post(f"/api/integ/jobs/{job['id']}/publish", headers=h, json={"cron": ""})
    assert r.json()["version"] == 2
    # 回滚前需先下线
    client.post(f"/api/integ/jobs/{job['id']}/offline", headers=h)
    # 回滚到 v1
    versions = client.get(f"/api/integ/jobs/{job['id']}/versions", headers=h).json()
    v1 = next(v for v in versions if v["version"] == 1)
    r = client.post(f"/api/integ/jobs/{job['id']}/versions/{v1['id']}/rollback", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["config"]["write_mode"] == "truncate_insert"
    # published 状态回滚被拒
    client.post(f"/api/integ/jobs/{job['id']}/publish", headers=h, json={"cron": ""})
    r = client.post(f"/api/integ/jobs/{job['id']}/versions/{v1['id']}/rollback", headers=h)
    assert r.status_code == 400


def test_delete_job_and_cascade(client, tokens):
    job, src_id, dst_id = _make_job(client, tokens["engineer"], "del_job")
    h = H(tokens["engineer"])
    # published 不可删
    client.post(f"/api/integ/jobs/{job['id']}/validate", headers=h)
    client.post(f"/api/integ/jobs/{job['id']}/publish", headers=h, json={"cron": "0 5 * * *"})
    r = client.delete(f"/api/integ/jobs/{job['id']}", headers=h)
    assert r.status_code == 400
    # 下线后可删
    client.post(f"/api/integ/jobs/{job['id']}/offline", headers=h)
    r = client.delete(f"/api/integ/jobs/{job['id']}", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    r = client.get("/api/integ/jobs", headers=h)
    assert not any(j["id"] == job["id"] for j in r.json())
    # 版本已级联删除
    r = client.get(f"/api/integ/jobs/{job['id']}/versions", headers=h)
    assert r.status_code == 200 and r.json() == []


def test_delete_workflow_guard(client, tokens):
    h = H(tokens["engineer"])
    r = client.post("/api/sched/workflows", headers=h, json={
        "name": "wf_del_guard", "enabled": True,
        "nodes": [{"name": "py", "node_type": "python",
                   "config": {"script": "print(1)"}}]})
    wf_id = r.json()["id"]
    # 启用中不可直接删
    r = client.delete(f"/api/sched/workflows/{wf_id}", headers=h)
    assert r.status_code == 400
    # force 可删
    r = client.delete(f"/api/sched/workflows/{wf_id}", headers=h, params={"force": "true"})
    assert r.status_code == 200
    r = client.get("/api/sched/workflows", headers=h)
    assert not any(w["id"] == wf_id for w in r.json())


def test_pre_sql_executed(client, tokens):
    src = make_sqlite_ds(client, tokens["engineer"], "pre_src", {ORDERS_DDL: ROWS})
    dst = make_sqlite_ds(client, tokens["engineer"], "pre_dst",
                         {"CREATE TABLE ods_pre (id INTEGER, amt REAL, dt TEXT, status TEXT)": []})
    h = H(tokens["engineer"])
    r = client.post("/api/integ/jobs", headers=h, json={
        "name": "pre_sql_job", "source_datasource_id": src["id"], "source_table": "orders",
        "target_datasource_id": dst["id"], "target_table": "ods_pre",
        "config": {"write_mode": "append",
                   "pre_sql": "CREATE TABLE IF NOT EXISTS pre_marker (ts TEXT)",
                   "post_sql": "INSERT INTO pre_marker VALUES (datetime('now'))"}})
    job_id = r.json()["id"]
    client.post(f"/api/integ/jobs/{job_id}/validate", headers=h)
    r = client.post(f"/api/integ/jobs/{job_id}/trial", headers=h)
    assert r.status_code == 200, r.text
    # post_sql 生效：marker 表有记录
    from app.db import SessionLocal
    from app.models import DataSource
    from app.services.engine_registry import run_query
    db = SessionLocal()
    try:
        ds = db.get(DataSource, dst["id"])
        cols, rows, _ = run_query(ds, "SELECT COUNT(*) FROM pre_marker")
        assert rows[0][0] >= 1
    finally:
        db.close()
