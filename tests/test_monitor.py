"""数据监控告警模块 e2e 测试：7 类规则 + 告警联动 + 权限。"""
import sqlite3
import time

from conftest import H, make_sqlite_ds, wait_instance

ORDERS_DDL = "CREATE TABLE orders (id INTEGER PRIMARY KEY, city TEXT, amount REAL, dt TEXT)"
ROWS = [(i, ["北京", "上海"][i % 2], i * 1.0, f"2026-10-{(i % 20) + 1:02d}")
        for i in range(1, 21)]


def _mk_rule(client, tok, name, rule_type, ds_id=0, table="", column="", params=None,
             cron="0 6 * * *", severity="warning"):
    r = client.post("/api/monitor/rules", headers=H(tok), json={
        "name": name, "rule_type": rule_type, "datasource_id": ds_id,
        "table_name": table, "column_name": column, "params": params or {},
        "cron": cron, "severity": severity})
    assert r.status_code == 200, r.text
    return r.json()


def test_row_count_threshold_fail_with_alert(client, tokens):
    ds = make_sqlite_ds(client, tokens["engineer"], "m_src1", {ORDERS_DDL: ROWS})
    # 失败告警通道（不可达也落记录）
    client.post("/api/sched/alert-channels", headers=H(tokens["admin"]),
                json={"name": "监控告警通道", "type": "webhook",
                      "config": {"url": "http://127.0.0.1:1/nope"}, "enabled": True})
    rule = _mk_rule(client, tokens["engineer"], "行数下限", "row_count_threshold",
                    ds["id"], "orders", params={"min": 100})
    assert rule["enabled"] is True
    r = client.post(f"/api/monitor/rules/{rule['id']}/run", headers=H(tokens["engineer"]))
    body = r.json()
    assert body["status"] == "failed", body
    assert "低于下限" in body["message"]
    # 告警记录落库（监控告警标题）
    for _ in range(10):
        recs = client.get("/api/sched/alert-records", headers=H(tokens["engineer"])).json()["items"]
        if any("监控告警" in a["title"] for a in recs):
            break
        time.sleep(0.5)
    assert any("监控告警" in a["title"] for a in recs)
    # 概览
    s = client.get("/api/monitor/summary", headers=H(tokens["engineer"])).json()
    assert s["failed_24h"] >= 1 and s["alerts_24h"] >= 1
    assert any(f["id"] == rule["id"] for f in s["failing_rules"])


def test_row_count_fluctuation_baseline(client, tokens):
    ds = make_sqlite_ds(client, tokens["engineer"], "m_src2", {ORDERS_DDL: ROWS})
    rule = _mk_rule(client, tokens["engineer"], "行数波动", "row_count_fluctuation",
                    ds["id"], "orders", params={"pct": 10})
    h = H(tokens["engineer"])
    r1 = client.post(f"/api/monitor/rules/{rule['id']}/run", headers=h).json()
    assert r1["status"] == "passed"  # 基线建立
    # 直接往 sqlite 文件追加 20 行（波动 100% > 10%）
    path = ds["params"]["path"]
    conn = sqlite3.connect(path)
    conn.executemany("INSERT INTO orders VALUES (?,?,?,?)",
                     [(i, "北京", 1.0, "2026-10-21") for i in range(100, 120)])
    conn.commit()
    conn.close()
    r2 = client.post(f"/api/monitor/rules/{rule['id']}/run", headers=h).json()
    assert r2["status"] == "failed" and "波动" in r2["message"]


def test_null_rate_unique_freshness_sql(client, tokens):
    ddl = ("CREATE TABLE t_mix (id INTEGER PRIMARY KEY, city TEXT, dt TEXT)",
           [(i, None if i % 3 == 0 else "北京", "2026-01-01") for i in range(1, 11)])
    ds = make_sqlite_ds(client, tokens["engineer"], "m_src3", {ddl[0]: ddl[1]})
    h = H(tokens["engineer"])
    # 空值率：city 3/10 = 30% > 20%
    rule = _mk_rule(client, tokens["engineer"], "空值率", "null_rate",
                    ds["id"], "t_mix", "city", params={"pct": 20})
    r = client.post(f"/api/monitor/rules/{rule['id']}/run", headers=h).json()
    assert r["status"] == "failed" and "空值率" in r["metric"]
    # 唯一性：city 有重复
    rule2 = _mk_rule(client, tokens["engineer"], "唯一性", "unique_check",
                     ds["id"], "t_mix", "city")
    r = client.post(f"/api/monitor/rules/{rule2['id']}/run", headers=h).json()
    assert r["status"] == "failed"
    # 新鲜度：dt 是 2026-01-01，延迟远超 24h
    rule3 = _mk_rule(client, tokens["engineer"], "新鲜度", "freshness",
                     ds["id"], "t_mix", "dt", params={"hours": 24})
    r = client.post(f"/api/monitor/rules/{rule3['id']}/run", headers=h).json()
    assert r["status"] == "failed" and "延迟" in r["message"]
    # 自定义 SQL：无问题数据 -> 通过；有问题数据 -> 失败
    rule4 = _mk_rule(client, tokens["engineer"], "自定义校验-通过", "sql_check",
                     ds["id"], "t_mix", params={"sql": "SELECT * FROM t_mix WHERE id > 999"})
    r = client.post(f"/api/monitor/rules/{rule4['id']}/run", headers=h).json()
    assert r["status"] == "passed"
    rule5 = _mk_rule(client, tokens["engineer"], "自定义校验-失败", "sql_check",
                     ds["id"], "t_mix", params={"sql": "SELECT id FROM t_mix WHERE city IS NULL"})
    r = client.post(f"/api/monitor/rules/{rule5['id']}/run", headers=h).json()
    assert r["status"] == "failed" and "问题数据" in r["message"]


def test_task_duration_rule(client, tokens):
    h = H(tokens["engineer"])
    # 造一个跑完的工作流
    r = client.post("/api/sched/workflows", headers=h, json={
        "name": "wf_mon_dur", "enabled": True,
        "nodes": [{"name": "py", "node_type": "python",
                   "config": {"script": "print('x')"}}]})
    wf_id = r.json()["id"]
    tr = client.post(f"/api/sched/workflows/{wf_id}/trigger", headers=h).json()
    wait_instance(client, tokens["engineer"], tr["instance_id"])
    # 阈值 0 分钟 -> 必失败
    rule = _mk_rule(client, tokens["engineer"], "任务时长", "task_duration",
                    params={"workflow_name": "wf_mon_dur", "max_minutes": 0})
    r = client.post(f"/api/monitor/rules/{rule['id']}/run", headers=h).json()
    assert r["status"] == "failed" and "超过阈值" in r["message"]


def test_rule_crud_permissions_and_checks(client, tokens):
    ds = make_sqlite_ds(client, tokens["engineer"], "m_src4", {ORDERS_DDL: ROWS})
    # analyst 不能管理规则
    r = client.post("/api/monitor/rules", headers=H(tokens["analyst"]),
                    json={"name": "x", "rule_type": "row_count_threshold",
                          "datasource_id": ds["id"], "table_name": "orders"})
    assert r.status_code == 403
    # 非法 cron / 非法 severity / 未知类型
    h = H(tokens["engineer"])
    assert client.post("/api/monitor/rules", headers=h, json={
        "name": "x", "rule_type": "row_count_threshold", "datasource_id": ds["id"],
        "table_name": "orders", "cron": "bad"}).status_code == 400
    assert client.post("/api/monitor/rules", headers=h, json={
        "name": "x", "rule_type": "nope", "datasource_id": ds["id"],
        "table_name": "orders"}).status_code == 400
    # 检查记录列表
    rule = _mk_rule(client, tokens["engineer"], "阈值", "row_count_threshold",
                    ds["id"], "orders", params={"min": 1})
    client.post(f"/api/monitor/rules/{rule['id']}/run", headers=h)
    r = client.get("/api/monitor/checks", headers=H(tokens["analyst"])).json()
    assert r["total"] >= 1 and any(c["rule_id"] == rule["id"] for c in r["items"])
    # 删除规则 -> 检查记录级联删除
    assert client.delete(f"/api/monitor/rules/{rule['id']}", headers=h).status_code == 200
    assert client.get("/api/monitor/checks?rule_id=" + str(rule["id"]),
                      headers=h).json()["total"] == 0


def test_alert_mute_window(client, tokens):
    """静默窗口：mute_hours 内同一规则不重复发告警，检查记录照常落库。"""
    ds = make_sqlite_ds(client, tokens["engineer"], "m_mute", {ORDERS_DDL: ROWS})
    client.post("/api/sched/alert-channels", headers=H(tokens["admin"]),
                json={"name": "静默测试通道", "type": "webhook",
                      "config": {"url": "http://127.0.0.1:1/nope"}, "enabled": True})
    h = H(tokens["engineer"])
    rule = _mk_rule(client, tokens["engineer"], "静默规则", "row_count_threshold",
                    ds["id"], "orders", params={"min": 999999}, severity="critical")
    # 设置静默 24h
    r = client.put(f"/api/monitor/rules/{rule['id']}", headers=h, json={
        "name": "静默规则", "rule_type": "row_count_threshold", "datasource_id": ds["id"],
        "table_name": "orders", "column_name": "", "params": {"min": 999999},
        "cron": "0 6 * * *", "severity": "critical", "enabled": True,
        "group_name": "数仓质量", "mute_hours": 24})
    assert r.status_code == 200 and r.json()["mute_hours"] == 24
    assert r.json()["group_name"] == "数仓质量"
    # 第一次失败 -> 发告警；第二次失败 -> 静默（只统计本规则在本通道的告警）
    def rule_alert_count():
        recs = client.get("/api/sched/alert-records", headers=h).json()["items"]
        return sum(1 for a in recs
                   if "静默规则" in a["title"] and a.get("channel") == "静默测试通道")

    client.post(f"/api/monitor/rules/{rule['id']}/run", headers=h)
    mid = rule_alert_count()
    r2 = client.post(f"/api/monitor/rules/{rule['id']}/run", headers=h).json()
    time.sleep(1)
    after = rule_alert_count()
    assert mid == 1                   # 第一次发了
    assert after == 1                 # 第二次被静默
    assert r2["status"] == "failed"
    assert "静默期内" in r2["message"]


def test_trend_and_group(client, tokens):
    ds = make_sqlite_ds(client, tokens["engineer"], "m_trend", {ORDERS_DDL: ROWS})
    h = H(tokens["engineer"])
    rule = _mk_rule(client, tokens["engineer"], "趋势规则", "row_count_threshold",
                    ds["id"], "orders", params={"min": 1})
    client.post(f"/api/monitor/rules/{rule['id']}/run", headers=h)
    r = client.get("/api/monitor/trend?days=7", headers=h).json()
    assert r["total"] >= 1
    assert any(b["passed"] >= 1 for b in r["days"])
    # 按规则过滤
    r2 = client.get(f"/api/monitor/trend?rule_id={rule['id']}", headers=h).json()
    assert r2["total"] >= 1
    # 分组字段落库并可查
    r3 = client.put(f"/api/monitor/rules/{rule['id']}", headers=h, json={
        "name": "趋势规则", "rule_type": "row_count_threshold", "datasource_id": ds["id"],
        "table_name": "orders", "column_name": "", "params": {"min": 1},
        "cron": "0 6 * * *", "severity": "warning", "enabled": True,
        "group_name": "核心表监控", "mute_hours": 0})
    assert r3.json()["group_name"] == "核心表监控"
    rules = client.get("/api/monitor/rules", headers=h).json()
    assert any(x["id"] == rule["id"] and x["group_name"] == "核心表监控" for x in rules)
