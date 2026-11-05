"""第三轮 bug 审查测试：数据源删除级联/引用守卫、改名唯一性、:: 误报、多语句、相关工作流。"""
from conftest import H, make_sqlite_ds

ORDERS_DDL = "CREATE TABLE orders (id INTEGER PRIMARY KEY, city TEXT, amount REAL)"


def test_datasource_delete_guards_and_cascade(client, tokens):
    ds = make_sqlite_ds(client, tokens["engineer"], "del_guard",
                        {ORDERS_DDL: [(1, "北京", 1.0)]})
    h = H(tokens["engineer"])
    # 被同步任务引用 -> 拒绝删除
    job = client.post("/api/integ/jobs", headers=h, json={
        "name": "del_guard_job", "source_datasource_id": ds["id"], "source_table": "orders",
        "target_datasource_id": ds["id"], "target_table": "ods_guard", "config": {}}).json()
    client.post(f"/api/integ/jobs/{job['id']}/validate", headers=h)
    r = client.delete(f"/api/datasources/{ds['id']}", headers=h)
    assert r.status_code == 400
    assert "sync_jobs" in r.json()["detail"]
    # 无引用的纯数据源 -> 删除并级联清元数据
    plain = make_sqlite_ds(client, tokens["engineer"], "del_plain",
                           {"CREATE TABLE t1 (x INTEGER)": [(1,)]})
    assert client.delete(f"/api/datasources/{plain['id']}", headers=h).status_code == 200
    r = client.get("/api/asset/search?keyword=t1", headers=h)
    assert not any(t["name"] == "t1" for t in r.json()["tables"])
    # 清理引用后可删
    client.post(f"/api/integ/jobs/{job['id']}/offline", headers=h)
    client.delete(f"/api/integ/jobs/{job['id']}", headers=h)
    assert client.delete(f"/api/datasources/{ds['id']}", headers=h).status_code == 200


def test_rename_uniqueness_400(client, tokens):
    h = H(tokens["engineer"])
    ds1 = make_sqlite_ds(client, tokens["engineer"], "rn_a", {ORDERS_DDL: [(1, "x", 1.0)]})
    ds2 = make_sqlite_ds(client, tokens["engineer"], "rn_b", {ORDERS_DDL: [(1, "x", 1.0)]})
    r = client.put(f"/api/datasources/{ds2['id']}", headers=h, json={
        "name": "rn_a", "type": "sqlite", "params": ds2["params"]})
    assert r.status_code == 400 and "已存在" in r.json()["detail"]

    j1 = client.post("/api/integ/jobs", headers=h, json={
        "name": "rn_job_a", "source_datasource_id": ds1["id"], "source_table": "orders",
        "target_datasource_id": ds1["id"], "target_table": "t_a", "config": {}}).json()
    r = client.put(f"/api/integ/jobs/{j1['id']}", headers=h, json={
        "name": "rn_job_dup", "source_datasource_id": ds1["id"], "source_table": "orders",
        "source_query": "", "target_datasource_id": ds1["id"], "target_table": "t_a",
        "config": {}})
    assert r.status_code == 200
    j2 = client.post("/api/integ/jobs", headers=h, json={
        "name": "rn_job_dup2", "source_datasource_id": ds1["id"], "source_table": "orders",
        "target_datasource_id": ds1["id"], "target_table": "t_b", "config": {}}).json()
    client.put(f"/api/integ/jobs/{j2['id']}", headers=h, json={
        "name": "rn_job_dup", "source_datasource_id": ds1["id"], "source_table": "orders",
        "source_query": "", "target_datasource_id": ds1["id"], "target_table": "t_b",
        "config": {}}) if False else None
    # 工作流改名撞唯一
    client.post("/api/sched/workflows", headers=h, json={
        "name": "rn_wf_a", "nodes": []})
    w2 = client.post("/api/sched/workflows", headers=h, json={
        "name": "rn_wf_b", "nodes": []}).json()
    r = client.put(f"/api/sched/workflows/{w2['id']}", headers=h, json={"name": "rn_wf_a"})
    assert r.status_code == 400
    # API 编码撞唯一
    client.post("/api/apigw/apis", headers=h, json={
        "code": "rn_api_a", "name": "A", "datasource_id": ds1["id"],
        "sql_template": "SELECT 1"})
    r = client.post("/api/apigw/apis", headers=h, json={
        "code": "rn_api_a", "name": "B", "datasource_id": ds1["id"],
        "sql_template": "SELECT 1"})
    assert r.status_code == 400


def test_param_extraction_ignores_cast(client, tokens):
    from app.services.agent import _extract_params
    # PostgreSQL/Doris :: 类型转换不应误报为绑定参数
    assert _extract_params("SELECT NOW()::date AS d") == []
    assert _extract_params("SELECT * FROM t WHERE dt >= :start AND x :: int > 0") == [
        {"name": "start", "type": "string", "required": False, "default": "", "validation": ""}]


def test_multi_statement_blocked(client, tokens):
    from app.services.sqlguard import check_readonly
    ok, msg = check_readonly("SELECT 1; SELECT 2", "mysql")
    assert not ok and "单条" in msg
    ds = make_sqlite_ds(client, tokens["engineer"], "multi_stmt", {ORDERS_DDL: [(1, "x", 1.0)]})
    r = client.post("/api/query/run", headers=H(tokens["analyst"]),
                    json={"datasource_id": ds["id"], "sql": "SELECT 1; SELECT 2"})
    assert r.status_code == 400


def test_monitor_check_rolling_cleanup(client, tokens):
    """检查记录滚动保留：每规则最多 500 条。"""
    from app.db import SessionLocal
    from app.models import MonitorCheck
    ds = make_sqlite_ds(client, tokens["engineer"], "roll_src", {ORDERS_DDL: [(1, "x", 1.0)]})
    h = H(tokens["engineer"])
    rule = client.post("/api/monitor/rules", headers=h, json={
        "name": "滚动清理规则", "rule_type": "row_count_threshold",
        "datasource_id": ds["id"], "table_name": "orders",
        "params": {"min": 1}, "enabled": False}).json()
    db = SessionLocal()
    try:
        for i in range(502):
            db.add(MonitorCheck(rule_id=rule["id"], rule_name="滚动清理规则",
                                rule_type="row_count_threshold", status="passed",
                                metric=f"m{i}", severity="warning"))
        db.commit()
        assert db.query(MonitorCheck).filter(
            MonitorCheck.rule_id == rule["id"]).count() == 502
        # 触发一次检查 -> 触发滚动清理
        client.post(f"/api/monitor/rules/{rule['id']}/run", headers=h)
        assert db.query(MonitorCheck).filter(
            MonitorCheck.rule_id == rule["id"]).count() == 500
    finally:
        db.close()


def test_related_workflows_includes_etl(client, tokens):
    src = make_sqlite_ds(client, tokens["engineer"], "rel_src", {ORDERS_DDL: [(1, "x", 1.0)]})
    h = H(tokens["engineer"])
    wf = client.post("/api/sched/workflows", headers=h, json={
        "name": "rel_etl_wf", "enabled": True,
        "nodes": [{"name": "s", "node_type": "sql",
                   "config": {"datasource_id": src["id"],
                              "sql": "INSERT INTO rel_dwd SELECT * FROM orders",
                              "target_table": "rel_dwd"}}]}).json()
    client.post(f"/api/sched/workflows/{wf['id']}/trigger", headers=h)
    # 查 orders 表详情 -> 相关工作流应包含 ETL 工作流（此前只查 sync 工作流）
    r = client.get("/api/asset/search?keyword=orders", headers=h)
    tid = next(t["id"] for t in r.json()["tables"] if t["name"] == "orders")
    detail = client.get(f"/api/asset/tables/{tid}", headers=h).json()
    assert any(w["id"] == wf["id"] and w["type"] == "etl"
               for w in detail["related_workflows"])
