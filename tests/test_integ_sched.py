"""数据集成 + 调度 e2e 测试（M1 核心）。"""
import time

from conftest import H, make_sqlite_ds, wait_instance

ORDERS_DDL = "CREATE TABLE orders (id INTEGER PRIMARY KEY, amt REAL, dt TEXT, status TEXT)"


def _setup(client, tokens):
    src = make_sqlite_ds(client, tokens["engineer"], "i_src",
                         {ORDERS_DDL: [(i, i * 2.0, f"2026-10-{(i % 20) + 1:02d}", "paid")
                                       for i in range(1, 51)]})
    dst = make_sqlite_ds(client, tokens["engineer"], "i_dst",
                         {"CREATE TABLE marker (x INTEGER)": [(1,)]})
    return src["id"], dst["id"]


def test_sync_job_full_lifecycle(client, tokens):
    src_id, dst_id = _setup(client, tokens)
    h = H(tokens["engineer"])
    # 创建草稿
    r = client.post("/api/integ/jobs", headers=h, json={
        "name": "sync_orders_e2e", "source_datasource_id": src_id, "source_table": "orders",
        "target_datasource_id": dst_id, "target_table": "ods_orders",
        "config": {"write_mode": "truncate_insert"}})
    assert r.status_code == 200, r.text
    job_id = r.json()["id"]
    assert r.json()["status"] == "draft"
    # 校验（应自动补列）
    r = client.post(f"/api/integ/jobs/{job_id}/validate", headers=h)
    body = r.json()
    assert body["ok"], body["issues"]
    assert "CREATE TABLE" in body["ddl"]
    # 试跑
    r = client.post(f"/api/integ/jobs/{job_id}/trial", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["stats"]["written_rows"] == 50
    assert len(r.json()["rows"]) == 10
    # 发布（带 cron）
    r = client.post(f"/api/integ/jobs/{job_id}/publish", headers=h,
                    json={"cron": "0 2 * * *"})
    assert r.status_code == 200, r.text
    wf_id = r.json()["workflow_id"]
    assert r.json()["version"] == 1
    # 工作流已注册
    r = client.get("/api/sched/workflows", headers=h)
    wf = next(w for w in r.json() if w["id"] == wf_id)
    assert wf["schedule_cron"] == "0 2 * * *" and wf["enabled"] is True
    assert wf["next_fire"]
    # 版本与 DataX JSON
    r = client.get(f"/api/integ/jobs/{job_id}/versions", headers=h)
    assert r.json()[0]["version"] == 1
    vid = r.json()[0]["id"]
    r = client.get(f"/api/integ/jobs/{job_id}/versions/{vid}/datax", headers=h)
    assert "reader" in r.json()["datax_json"] and "writer" in r.json()["datax_json"]
    # 手动触发 -> 成功
    r = client.post(f"/api/sched/workflows/{wf_id}/trigger", headers=h)
    assert r.status_code == 200
    inst = wait_instance(client, tokens["engineer"], r.json()["instance_id"])
    assert inst["status"] == "success", inst["nodes"] and inst["nodes"][0]["log"]
    assert inst["nodes"][0]["status"] == "success"
    # 数据确实写入
    from app.db import SessionLocal
    from app.models import DataSource
    from app.services.engine_registry import run_query
    db = SessionLocal()
    try:
        ds = db.get(DataSource, dst_id)
        cols, rows, _ = run_query(ds, "SELECT COUNT(*) FROM ods_orders")
        assert rows[0][0] == 50
    finally:
        db.close()
    # 下线
    r = client.post(f"/api/integ/jobs/{job_id}/offline", headers=h)
    assert r.status_code == 200
    r = client.get("/api/integ/jobs?status=offline", headers=h)
    assert any(j["id"] == job_id for j in r.json())


def test_etl_workflow_with_dependency(client, tokens):
    """M2：SQL 工作流 + 跨工作流依赖 + 血缘。"""
    src_id, dst_id = _setup(client, tokens)
    h = H(tokens["engineer"])
    # 先建一个同步任务作为上游
    r = client.post("/api/integ/jobs", headers=h, json={
        "name": "sync_up_dep", "source_datasource_id": src_id, "source_table": "orders",
        "target_datasource_id": dst_id, "target_table": "ods_orders_dep",
        "config": {}})
    job_id = r.json()["id"]
    client.post(f"/api/integ/jobs/{job_id}/validate", headers=h)
    r = client.post(f"/api/integ/jobs/{job_id}/publish", headers=h, json={"cron": ""})
    up_wf = r.json()["workflow_id"]
    # 触发上游到成功
    r = client.post(f"/api/sched/workflows/{up_wf}/trigger", headers=h)
    inst = wait_instance(client, tokens["engineer"], r.json()["instance_id"])
    assert inst["status"] == "success"
    # 建 ETL 工作流（依赖上游工作流）
    r = client.post("/api/sched/workflows", headers=h, json={
        "name": "etl_dep_e2e", "schedule_cron": "0 3 * * *",
        "depends_on": [up_wf],
        "nodes": [{"name": "dwd_sql", "node_type": "sql",
                   "config": {"datasource_id": dst_id,
                              "sql": "INSERT INTO dwd_dep SELECT * FROM ods_orders_dep",
                              "target_table": "dwd_dep"}}]})
    assert r.status_code == 200, r.text
    assert r.json()["problems"] == []
    wf_id = r.json()["id"]
    # 触发 -> 上游已有成功实例 -> 应成功；并产生 SQL 血缘
    r = client.post(f"/api/sched/workflows/{wf_id}/trigger", headers=h)
    inst = wait_instance(client, tokens["engineer"], r.json()["instance_id"])
    assert inst["status"] == "success", inst.get("nodes")
    # 血缘图包含三层
    r = client.get(f"/api/asset/map/graph?datasource_id={dst_id}&table=dwd_dep&depth=2",
                   headers=h)
    g = r.json()
    tables = {n["table"] for n in g["nodes"]}
    assert {"ods_orders_dep", "dwd_dep"} <= tables
    # 表详情带上下游
    r = client.get("/api/asset/search?keyword=dwd_dep", headers=h)
    tid = r.json()["tables"][0]["id"]
    r = client.get(f"/api/asset/tables/{tid}", headers=h)
    assert any(u["table"] == "ods_orders_dep" for u in r.json()["upstream"])


def test_workflow_ops(client, tokens):
    h = H(tokens["engineer"])
    r = client.post("/api/sched/workflows", headers=h, json={
        "name": "etl_ops", "schedule_cron": "0 4 * * *",
        "nodes": [{"name": "py", "node_type": "python",
                   "config": {"script": "print('hello ops')"}}]})
    wf_id = r.json()["id"]
    # 暂停/恢复
    r = client.put(f"/api/sched/workflows/{wf_id}", headers=h, json={"enabled": False})
    assert r.json()["enabled"] is False
    r = client.put(f"/api/sched/workflows/{wf_id}", headers=h, json={"enabled": True})
    assert r.json()["enabled"] is True
    # 触发并成功
    r = client.post(f"/api/sched/workflows/{wf_id}/trigger", headers=h)
    inst = wait_instance(client, tokens["engineer"], r.json()["instance_id"])
    assert inst["status"] == "success"
    assert "hello ops" in inst["nodes"][0]["log"]
    # 重跑
    r = client.post(f"/api/sched/instances/{inst['id']}/rerun", headers=h,
                    params={"only_failed": "false"})
    assert r.status_code == 200
    # 非法 cron
    r = client.put(f"/api/sched/workflows/{wf_id}", headers=h, json={"schedule_cron": "bad"})
    assert r.status_code == 400


def test_failure_alert_fires(client, tokens):
    """M2：任务失败 -> 告警通道触发 -> 告警记录落库。"""
    h = H(tokens["engineer"])
    # webhook 通道（地址不可达也应生成告警记录，status=failed）
    r = client.post("/api/sched/alert-channels", headers=H(tokens["admin"]),
                    json={"name": "测试告警", "type": "webhook",
                          "config": {"url": "http://127.0.0.1:1/nope"}, "enabled": True})
    assert r.status_code == 200
    r = client.post("/api/sched/workflows", headers=h, json={
        "name": "etl_alert_fail", "enabled": True,
        "nodes": [{"name": "boom", "node_type": "python",
                   "config": {"script": "raise RuntimeError('boom')"}}]})
    wf_id = r.json()["id"]
    r = client.post(f"/api/sched/workflows/{wf_id}/trigger", headers=h)
    inst = wait_instance(client, tokens["engineer"], r.json()["instance_id"])
    assert inst["status"] == "failed"
    assert "boom" in inst["error"]
    r = client.get("/api/sched/alert-records", headers=h)
    found = any(a["instance_id"] == inst["id"] for a in r.json()["items"])
    for _ in range(10):  # 告警异步发送，轮询等待
        if found:
            break
        time.sleep(0.5)
        r = client.get("/api/sched/alert-records", headers=h)
        found = any(a["instance_id"] == inst["id"] for a in r.json()["items"])
    assert found, r.json()


def test_cron_validation_and_permissions(client, tokens):
    h = H(tokens["engineer"])
    r = client.post("/api/sched/workflows", headers=h, json={
        "name": "etl_badcron", "schedule_cron": "xx",
        "nodes": []})
    assert r.status_code == 400
    # analyst 不能建工作流
    r = client.post("/api/sched/workflows", headers=H(tokens["analyst"]),
                    json={"name": "x", "nodes": []})
    assert r.status_code == 403
