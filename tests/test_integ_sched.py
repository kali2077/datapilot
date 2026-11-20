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
    # 试跑（默认校验模式：只读源不写目标，不污染增量断点）
    r = client.post(f"/api/integ/jobs/{job_id}/trial", headers=h)
    assert r.status_code == 200
    assert r.json()["stats"]["dry_run"] is True
    assert r.json()["stats"]["read_rows"] == 50
    assert r.json()["stats"]["written_rows"] == 0
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
    for _ in range(30):  # 告警异步发送，轮询等待（全量套件高负载下 5s 不够）
        if found:
            break
        time.sleep(0.5)
        r = client.get("/api/sched/alert-records", headers=h)
        found = any(a["instance_id"] == inst["id"] for a in r.json()["items"])
    assert found, r.json()


def test_trial_write_reset_and_last_run(client, tokens):
    """第一批：真实写入试跑、重置目标表重建断点、集成列表返回最近运行。"""
    src_id, dst_id = _setup(client, tokens)
    h = H(tokens["engineer"])
    r = client.post("/api/integ/jobs", headers=h, json={
        "name": "sync_batch1", "source_datasource_id": src_id, "source_table": "orders",
        "target_datasource_id": dst_id, "target_table": "ods_batch1",
        "config": {"write_mode": "append",
                   "increment": {"enabled": True, "column": "id", "mode": "max_value"}}})
    assert r.status_code == 200, r.text
    job_id = r.json()["id"]

    # 校验模式试跑（默认）：目标零写入
    r = client.post(f"/api/integ/jobs/{job_id}/trial", headers=h)
    body = r.json()
    assert body["stats"]["dry_run"] is True and body["stats"]["written_rows"] == 0
    assert body["stats"]["read_rows"] == 50

    # 真实写入试跑：50 行全部进目标（断点 = MAX(id)）
    r = client.post(f"/api/integ/jobs/{job_id}/trial", headers=h, params={"dry_run": "false"})
    assert r.status_code == 200, r.text
    assert r.json()["stats"]["written_rows"] == 50

    # 重置目标表（truncate）→ 数据清空，断点消失
    r = client.post(f"/api/integ/jobs/{job_id}/reset_target", headers=h)
    assert r.status_code == 200 and r.json()["mode"] in ("truncate", "delete")
    from app.db import SessionLocal
    from app.models import DataSource
    from app.services.engine_registry import run_query
    db = SessionLocal()
    try:
        ds = db.get(DataSource, dst_id)
        _, rows, _ = run_query(ds, "SELECT COUNT(*) FROM ods_batch1")
        assert rows[0][0] == 0
    finally:
        db.close()

    # 发布 + 触发：目标已空 → 重新全量（50 行），append + 增量断点重建
    r = client.post(f"/api/integ/jobs/{job_id}/publish", headers=h, json={"cron": ""})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/sched/workflows/{r.json()['workflow_id']}/trigger", headers=h)
    inst = wait_instance(client, tokens["engineer"], r.json()["instance_id"])
    assert inst["status"] == "success", inst.get("error")

    # 集成列表带最近运行状态 + 工作流关联
    r = client.get("/api/integ/jobs", headers=h)
    item = next(j for j in r.json() if j["id"] == job_id)
    assert item["workflow_id"]
    assert item["last_run"] and item["last_run"]["status"] == "success"

    # drop 模式重置：连表一起删，再次触发自动重建并全量成功
    r = client.post(f"/api/integ/jobs/{job_id}/reset_target", headers=h, params={"drop": "true"})
    assert r.status_code == 200 and r.json()["mode"] == "drop"
    r = client.post(f"/api/sched/workflows/{item['workflow_id']}/trigger", headers=h)
    inst = wait_instance(client, tokens["engineer"], r.json()["instance_id"])
    assert inst["status"] == "success", inst.get("error")  # 表被删后自动重建、重新全量
    client.post(f"/api/integ/jobs/{job_id}/offline", headers=h)
    client.delete(f"/api/integ/jobs/{job_id}?force=true", headers=h)


def test_upsert_merge_keys(client, tokens):
    """合并键 upsert：同键合并不重复（sqlite 兜底=先删后插），自动建表带 PRIMARY KEY。"""
    import sqlite3
    src = make_sqlite_ds(client, tokens["engineer"], "b2_up_src",
                         {"CREATE TABLE orders (id INTEGER PRIMARY KEY, amt REAL, dt TEXT)":
                          [(i, i * 1.0, "2026-11-01") for i in range(1, 51)]})
    dst = make_sqlite_ds(client, tokens["engineer"], "b2_up_dst",
                         {"CREATE TABLE m (x INTEGER)": [(1,)]})
    h = H(tokens["engineer"])
    r = client.post("/api/integ/jobs", headers=h, json={
        "name": "sync_upsert_b2", "source_datasource_id": src["id"], "source_table": "orders",
        "target_datasource_id": dst["id"], "target_table": "ods_up",
        "config": {"write_mode": "append", "upsert_keys": ["id"]}})
    assert r.status_code == 200, r.text
    job_id = r.json()["id"]
    r = client.post(f"/api/integ/jobs/{job_id}/validate", headers=h)
    assert r.json()["ok"], r.json()["issues"]
    r = client.post(f"/api/integ/jobs/{job_id}/publish", headers=h, json={"cron": ""})
    assert r.status_code == 200, r.text
    wf_id = r.json()["workflow_id"]
    # 第一次：全量 50 行
    r = client.post(f"/api/sched/workflows/{wf_id}/trigger", headers=h)
    inst = wait_instance(client, tokens["engineer"], r.json()["instance_id"])
    assert inst["status"] == "success", inst.get("error")
    # 自动建表 DDL 含 PRIMARY KEY（合并键）
    from app.db import SessionLocal
    from app.models import DataSource
    from app.services.engine_registry import run_query
    db = SessionLocal()
    try:
        ds = db.get(DataSource, dst["id"])
        _, rows, _ = run_query(ds, "SELECT sql FROM sqlite_master WHERE name='ods_up'")
        assert "PRIMARY KEY" in (rows[0][0] or "")
        # 源端造变更：改 5 行 amt + 新增 1 行
        conn = sqlite3.connect(src["params"]["path"])
        conn.execute("UPDATE orders SET amt = 999.0 WHERE id <= 5")
        conn.execute("INSERT INTO orders VALUES (100, 100.0, '2026-11-02')")
        conn.commit()
        conn.close()
    finally:
        db.close()
    # 第二次：append + 合并键 → 50+1=51 行、无重复、被改的 amt 已更新（不是追加成 56 行）
    r = client.post(f"/api/sched/workflows/{wf_id}/trigger", headers=h)
    inst = wait_instance(client, tokens["engineer"], r.json()["instance_id"])
    assert inst["status"] == "success", inst.get("error")
    db = SessionLocal()
    try:
        ds = db.get(DataSource, dst["id"])
        _, rows, _ = run_query(ds, "SELECT COUNT(*), COUNT(DISTINCT id), "
                                   "SUM(CASE WHEN id <= 5 AND amt = 999.0 THEN 1 ELSE 0 END) FROM ods_up")
        assert rows[0][0] == 51 and rows[0][1] == 51 and rows[0][2] == 5, rows
    finally:
        db.close()
    client.post(f"/api/integ/jobs/{job_id}/offline", headers=h)
    client.delete(f"/api/integ/jobs/{job_id}?force=true", headers=h)


def test_time_range_increment(client, tokens):
    """time_range 增量：${bizdate} 表达式只同步当天分区数据。"""
    import datetime
    today = datetime.date.today().isoformat()
    src = make_sqlite_ds(client, tokens["engineer"], "b2_tr_src",
                         {"CREATE TABLE orders (id INTEGER PRIMARY KEY, amt REAL, dt TEXT)":
                          [(1, 1.0, "2026-01-01"), (2, 2.0, "2026-01-02"),
                           (3, 3.0, today), (4, 4.0, today)]})
    dst = make_sqlite_ds(client, tokens["engineer"], "b2_tr_dst",
                         {"CREATE TABLE m (x INTEGER)": [(1,)]})
    h = H(tokens["engineer"])
    r = client.post("/api/integ/jobs", headers=h, json={
        "name": "sync_tr_b2", "source_datasource_id": src["id"], "source_table": "orders",
        "target_datasource_id": dst["id"], "target_table": "ods_tr",
        "config": {"write_mode": "append",
                   "increment": {"enabled": True, "column": "dt",
                                 "mode": "time_range", "expr": "${bizdate}"}}})
    job_id = r.json()["id"]
    # time_range 缺表达式 → 校验 error（防静默降级为断点续传）
    r = client.post("/api/integ/jobs", headers=h, json={
        "name": "sync_tr_noexpr", "source_datasource_id": src["id"], "source_table": "orders",
        "target_datasource_id": dst["id"], "target_table": "ods_tr2",
        "config": {"write_mode": "append",
                   "increment": {"enabled": True, "column": "dt", "mode": "time_range", "expr": ""}}})
    bad_id = r.json()["id"]
    r = client.post(f"/api/integ/jobs/{bad_id}/validate", headers=h)
    assert r.json()["ok"] is False
    assert any("时间表达式" in i["message"] for i in r.json()["issues"])
    client.delete(f"/api/integ/jobs/{bad_id}", headers=h)
    r = client.post(f"/api/integ/jobs/{job_id}/publish", headers=h, json={"cron": ""})
    wf_id = r.json()["workflow_id"]
    r = client.post(f"/api/sched/workflows/{wf_id}/trigger", headers=h)
    inst = wait_instance(client, tokens["engineer"], r.json()["instance_id"])
    assert inst["status"] == "success", inst.get("error")
    from app.db import SessionLocal
    from app.models import DataSource
    from app.services.engine_registry import run_query
    db = SessionLocal()
    try:
        ds = db.get(DataSource, dst["id"])
        _, rows, _ = run_query(ds, "SELECT COUNT(*) FROM ods_tr")
        assert rows[0][0] == 2  # 只同步 dt >= 今天的 2 行
    finally:
        db.close()
    client.post(f"/api/integ/jobs/{job_id}/offline", headers=h)
    client.delete(f"/api/integ/jobs/{job_id}?force=true", headers=h)


def test_retries_in_factory_config(client, tokens, monkeypatch, tmp_path):
    """重试策略配置化：SyncJob.config.retries 下发进 factory DAG JSON。"""
    from app import config
    from app.services import airflow_client
    monkeypatch.setattr(config, "SCHEDULER_ENGINE", "airflow")
    monkeypatch.setattr(config, "AIRFLOW_DAGS_DIR", str(tmp_path))
    monkeypatch.setattr(airflow_client, "patch_dag", lambda *a: {"ok": True})
    src = make_sqlite_ds(client, tokens["engineer"], "b2_rt_src",
                         {"CREATE TABLE orders (id INTEGER PRIMARY KEY, amt REAL)": [(1, 1.0)]})
    dst = make_sqlite_ds(client, tokens["engineer"], "b2_rt_dst",
                         {"CREATE TABLE m (x INTEGER)": [(1,)]})
    h = H(tokens["engineer"])
    r = client.post("/api/integ/jobs", headers=h, json={
        "name": "sync_rt_b2", "source_datasource_id": src["id"], "source_table": "orders",
        "target_datasource_id": dst["id"], "target_table": "ods_rt",
        "config": {"write_mode": "append", "retries": 2, "retry_delay_minutes": 7}})
    job_id = r.json()["id"]
    r = client.post(f"/api/integ/jobs/{job_id}/publish", headers=h, json={"cron": "0 6 * * *"})
    assert r.status_code == 200, r.text
    import json as _json
    cfg = _json.loads((tmp_path / "workflows" / f"wf_{r.json()['workflow_id']}.json").read_text())
    assert cfg["retries"] == 2 and cfg["retry_delay_minutes"] == 7
    client.post(f"/api/integ/jobs/{job_id}/offline", headers=h)
    client.delete(f"/api/integ/jobs/{job_id}?force=true", headers=h)


def test_resolve_increment_endpoint(client, tokens):
    """增量表达式试算端点：${bizdate} 按当前日期解析；viewer 无权。"""
    import datetime
    h = H(tokens["engineer"])
    r = client.post("/api/integ/increment/resolve", headers=h,
                    params={"expr": "${bizdate}"})
    assert r.status_code == 200
    assert r.json()["resolved"] == datetime.date.today().isoformat()
    r = client.post("/api/integ/increment/resolve", headers=h,
                    params={"expr": "dt >= ${bizdate_nodash}"})
    assert datetime.date.today().strftime("%Y%m%d") in r.json()["resolved"]
    r = client.post("/api/integ/increment/resolve", headers=H(tokens["viewer"]),
                    params={"expr": "${bizdate}"})
    assert r.status_code == 403


def test_trend_sync_halt_and_chunk_compare(client, tokens):
    """第三批：写入行数捕获 + 趋势端点 + sync_halt 断流规则 + 分块全量比对。"""
    src = make_sqlite_ds(client, tokens["engineer"], "b3_tr_src",
                         {"CREATE TABLE orders (id INTEGER PRIMARY KEY, amt REAL, dt TEXT)":
                          [(i, i * 1.0, "2026-11-01") for i in range(1, 21)]})
    dst = make_sqlite_ds(client, tokens["engineer"], "b3_tr_dst",
                         {"CREATE TABLE m (x INTEGER)": [(1,)]})
    h = H(tokens["engineer"])
    r = client.post("/api/integ/jobs", headers=h, json={
        "name": "sync_b3", "source_datasource_id": src["id"], "source_table": "orders",
        "target_datasource_id": dst["id"], "target_table": "ods_b3",
        "config": {"write_mode": "append", "upsert_keys": ["id"]}})
    job_id = r.json()["id"]
    r = client.post(f"/api/integ/jobs/{job_id}/publish", headers=h, json={"cron": ""})
    wf_id = r.json()["workflow_id"]
    r = client.post(f"/api/sched/workflows/{wf_id}/trigger", headers=h)
    inst = wait_instance(client, tokens["engineer"], r.json()["instance_id"])
    assert inst["status"] == "success", inst.get("error")

    # 趋势端点：最近实例捕获写入行数
    r = client.get(f"/api/integ/jobs/{job_id}/trend", headers=h)
    items = r.json()["items"]
    assert items and items[-1]["rows_written"] == 20 and items[-1]["status"] == "success"

    # 分块全量比对（源目一致）
    r = client.post(f"/api/integ/jobs/{job_id}/compare", headers=h, params={"mode": "chunk"})
    rep = r.json()
    assert rep["mode"] == "chunk" and rep["chunks_ok"] == rep["chunks_total"] == 10
    assert rep["consistent"] is True

    # 目标端删 6 行（第 2 块区间）→ 分块定位差异
    import sqlite3
    conn = sqlite3.connect(dst["params"]["path"])
    conn.execute("DELETE FROM ods_b3 WHERE id > 10 AND id <= 16")
    conn.commit()
    conn.close()
    r = client.post(f"/api/integ/jobs/{job_id}/compare", headers=h, params={"mode": "chunk"})
    rep = r.json()
    assert rep["consistent"] is False
    assert rep["chunk_diffs"]  # 差异被定位到具体键区间（3 块各差 2）
    assert sum(c["source"] - c["target"] for c in rep["chunk_diffs"]) == 6

    # sync_halt 断流规则：阈值 30 行（实际 20）→ failed；调阈值 1 → passed
    r = client.post("/api/monitor/rules", headers=h, json={
        "name": "sync_b3-断流", "rule_type": "sync_halt", "datasource_id": 0,
        "table_name": "", "column_name": "",
        "params": {"workflow_name": "sync_b3", "min_rows": 30},
        "cron": "0 7 * * *", "severity": "warning", "enabled": True,
        "group_name": "", "mute_hours": 0})
    assert r.status_code == 200, r.text
    rule_id = r.json()["id"]
    r = client.post(f"/api/monitor/rules/{rule_id}/run", headers=h)
    assert r.json()["status"] == "failed", r.json()
    # 调整阈值到合理值（PUT 更新）→ 断流解除
    r = client.put(f"/api/monitor/rules/{rule_id}", headers=h, json={
        "name": "sync_b3-断流", "rule_type": "sync_halt", "datasource_id": 0,
        "table_name": "", "column_name": "",
        "params": {"workflow_name": "sync_b3", "min_rows": 1},
        "cron": "0 7 * * *", "severity": "warning", "enabled": True,
        "group_name": "", "mute_hours": 0})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/monitor/rules/{rule_id}/run", headers=h)
    assert r.json()["status"] == "passed", r.json()
    # 规则列表可见 + 清理
    r = client.get("/api/monitor/rules", headers=h)
    assert any(x["rule_type"] == "sync_halt" for x in r.json())
    client.delete(f"/api/monitor/rules/{rule_id}", headers=H(tokens["admin"]))
    client.post(f"/api/integ/jobs/{job_id}/offline", headers=h)
    client.delete(f"/api/integ/jobs/{job_id}?force=true", headers=h)


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
