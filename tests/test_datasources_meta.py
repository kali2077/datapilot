"""数据源与元数据测试（含资产变更自动巡检）。"""
import sqlite3

from conftest import H, make_sqlite_ds


def _changes(client, tok, ds_id=0, table_id=0, change_type=""):
    params = {"size": 100}
    if ds_id:
        params["datasource_id"] = ds_id
    if table_id:
        params["table_id"] = table_id
    if change_type:
        params["change_type"] = change_type
    r = client.get("/api/meta/changes", headers=H(tok), params=params)
    assert r.status_code == 200
    return r.json()["items"]


def _types(items):
    return [c["change_type"] for c in items]


def test_datasource_lifecycle(client, tokens):
    ds = make_sqlite_ds(
        client, tokens["engineer"], "t_ds_orders",
        {"CREATE TABLE orders (id INTEGER PRIMARY KEY, amt REAL, dt TEXT, status TEXT)":
         [(i, i * 1.5, f"2026-10-{(i % 20) + 1:02d}", "paid") for i in range(1, 31)]})
    assert ds["type"] == "sqlite"
    ds_id = ds["id"]
    # 测试连接
    r = client.post(f"/api/datasources/{ds_id}/test", headers=H(tokens["engineer"]))
    assert r.json()["ok"] is True
    # 采集
    r = client.post(f"/api/datasources/{ds_id}/collect", headers=H(tokens["engineer"]))
    assert r.status_code == 200 and r.json()["tables"] >= 1
    # 元数据可查
    r = client.get(f"/api/meta/tables?datasource_id={ds_id}", headers=H(tokens["engineer"]))
    tables = r.json()["items"]
    assert any(t["name"] == "orders" for t in tables)
    # 表详情含字段
    tid = next(t["id"] for t in tables if t["name"] == "orders")
    r = client.get(f"/api/meta/tables/{tid}", headers=H(tokens["engineer"]))
    cols = r.json()["columns"]
    assert {c["name"] for c in cols} == {"id", "amt", "dt", "status"}
    # 人工补录
    r = client.put(f"/api/meta/tables/{tid}", headers=H(tokens["engineer"]),
                   json={"manual_desc": "订单测试表", "owner_name": "邵工"})
    assert r.status_code == 200
    r = client.get(f"/api/meta/tables/{tid}", headers=H(tokens["engineer"]))
    assert r.json()["manual_desc"] == "订单测试表"
    # 删除
    r = client.delete(f"/api/datasources/{ds_id}", headers=H(tokens["engineer"]))
    assert r.status_code == 200


def test_glossary_and_fewshot(client, tokens):
    r = client.post("/api/meta/glossary", headers=H(tokens["engineer"]),
                    json={"term": "ARPU", "synonyms": "客单价", "definition": "人均收入"})
    assert r.status_code == 200
    gid = r.json()["id"]
    r = client.put(f"/api/meta/glossary/{gid}", headers=H(tokens["engineer"]),
                   json={"term": "ARPU", "synonyms": "", "definition": "每用户平均收入"})
    assert r.status_code == 200
    r = client.get("/api/meta/glossary", headers=H(tokens["viewer"]))
    assert any(g["term"] == "ARPU" for g in r.json())

    r = client.post("/api/meta/fewshot", headers=H(tokens["engineer"]),
                    json={"question": "查ARPU", "sql": "SELECT AVG(amt) FROM t"})
    assert r.status_code == 200
    fid = r.json()["id"]
    r = client.delete(f"/api/meta/fewshot/{fid}", headers=H(tokens["engineer"]))
    assert r.status_code == 200
    # analyst 不能写术语
    r = client.post("/api/meta/glossary", headers=H(tokens["analyst"]),
                    json={"term": "x", "definition": "y"})
    assert r.status_code == 403


def test_meta_change_inspection(client, tokens):
    """资产变更自动巡检：首次建基线 → 加列/删表/行数变化 → 变更记录与防重。"""
    import os
    ds = make_sqlite_ds(
        client, tokens["engineer"], "t_ds_chg",
        {"CREATE TABLE fact (id INTEGER PRIMARY KEY, amt REAL)":
         [(i, i * 1.0) for i in range(1, 401)],  # 400 行
         "CREATE TABLE dim (id INTEGER PRIMARY KEY)": [(1,)]})
    ds_id = ds["id"]
    # make_sqlite_ds 建数据源时已首次采集（建基线）→ 无变更记录
    assert _types(_changes(client, tokens["admin"], ds_id=ds_id)) == []
    # 无变化再采集 → 无变更
    client.post(f"/api/datasources/{ds_id}/collect", headers=H(tokens["engineer"]))
    assert _types(_changes(client, tokens["admin"], ds_id=ds_id)) == []

    # 造变更：加列 + 行数变化 + 删表（sqlite3 直接改源库文件）
    path = ds["params"]["path"]
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE fact ADD COLUMN region TEXT DEFAULT 'cn'")
    conn.execute("INSERT INTO fact (id, amt) VALUES (900, 9.9)")  # 400 -> 401 不够阈值
    for i in range(1000, 1200):  # 401 -> 601（+50%）
        conn.execute("INSERT INTO fact (id, amt) VALUES (?, ?)", (i, i * 1.0))
    conn.execute("DROP TABLE dim")
    conn.commit()
    conn.close()
    r = client.post(f"/api/datasources/{ds_id}/collect", headers=H(tokens["engineer"]))
    assert r.status_code == 200
    body = r.json()
    assert body.get("changes", 0) >= 3
    items = _changes(client, tokens["admin"], ds_id=ds_id)
    types = _types(items)
    assert "column_added" in types and "table_removed" in types and "row_count_changed" in types
    added = next(c for c in items if c["change_type"] == "column_added")
    assert added["detail"]["column"] == "region"
    assert added["danger"] is False
    removed = next(c for c in items if c["change_type"] == "table_removed")
    assert removed["danger"] is True and removed["table_name"] == "dim"
    rc = next(c for c in items if c["change_type"] == "row_count_changed")
    # old=上次采集的 400（插入 900 与 1000-1199 都发生在本次采集前）
    assert rc["detail"]["old"] == 400 and rc["detail"]["new"] == 601

    # 防重：删表后再采集（表已 dropped 标志）→ 不重复记 table_removed
    client.post(f"/api/datasources/{ds_id}/collect", headers=H(tokens["engineer"]))
    assert _types(_changes(client, tokens["admin"], ds_id=ds_id)).count("table_removed") == 1

    # 危险变更告警：webhook 通道（不可达地址也落记录）→ 删表再采集触发聚合告警
    r = client.post("/api/sched/alert-channels", headers=H(tokens["admin"]),
                    json={"name": "巡检告警", "type": "webhook",
                          "config": {"url": "http://127.0.0.1:1/nope"}, "enabled": True})
    assert r.status_code == 200
    conn = sqlite3.connect(path)
    conn.execute("DROP TABLE fact")
    conn.commit()
    conn.close()
    client.post(f"/api/datasources/{ds_id}/collect", headers=H(tokens["engineer"]))
    r = client.get("/api/sched/alert-records", headers=H(tokens["admin"]))
    assert any("资产变更巡检" in a["title"] and a["instance_id"] == 0
               for a in r.json()["items"])

    # 表级过滤
    tid = removed["table_id"]
    assert all(c["table_id"] == tid for c in _changes(client, tokens["admin"], table_id=tid))
    client.delete(f"/api/datasources/{ds_id}", headers=H(tokens["engineer"]))


def test_meta_change_column_removed_and_altered(client, tokens):
    """column_removed / column_altered：DROP COLUMN（sqlite>=3.35）与同名表重建。"""
    import os
    ds = make_sqlite_ds(
        client, tokens["engineer"], "t_ds_drift",
        {"CREATE TABLE tgt (id INTEGER PRIMARY KEY, legacy TEXT, keep REAL)": [(1, "x", 2.0)]})
    ds_id = ds["id"]
    path = ds["params"]["path"]
    if sqlite3.sqlite_version_info >= (3, 35, 0):
        conn = sqlite3.connect(path)
        conn.execute("ALTER TABLE tgt DROP COLUMN legacy")
        conn.commit()
        conn.close()
        client.post(f"/api/datasources/{ds_id}/collect", headers=H(tokens["engineer"]))
        items = _changes(client, tokens["admin"], ds_id=ds_id, change_type="column_removed")
        assert any(c["detail"]["column"] == "legacy" and c["danger"] is True for c in items)
        # 防重：再采集不重复记
        client.post(f"/api/datasources/{ds_id}/collect", headers=H(tokens["engineer"]))
        items = _changes(client, tokens["admin"], ds_id=ds_id, change_type="column_removed")
        assert len([c for c in items if c["detail"]["column"] == "legacy"]) == 1
    else:
        client.delete(f"/api/datasources/{ds_id}", headers=H(tokens["engineer"]))
        return

    # column_altered + 同名表重建（跨批次）：DROP → 采集记 table_removed →
    # 再 CREATE（类型变化）→ 采集记 table_added(recreated) + column_altered
    conn = sqlite3.connect(path)
    conn.execute("DROP TABLE tgt")
    conn.commit()
    conn.close()
    client.post(f"/api/datasources/{ds_id}/collect", headers=H(tokens["engineer"]))
    assert "table_removed" in _types(_changes(client, tokens["admin"], ds_id=ds_id))

    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE tgt (id INTEGER PRIMARY KEY, legacy TEXT, keep TEXT)")
    conn.commit()
    conn.close()
    client.post(f"/api/datasources/{ds_id}/collect", headers=H(tokens["engineer"]))
    added = [c for c in _changes(client, tokens["admin"], ds_id=ds_id, change_type="table_added")]
    assert any(c["detail"].get("recreated") is True and c["table_name"] == "tgt" for c in added)
    alt = {c["detail"]["column"]: c["detail"]
           for c in _changes(client, tokens["admin"], ds_id=ds_id, change_type="column_altered")}
    assert alt["keep"]["old_type"] == "REAL" and alt["keep"]["new_type"] == "TEXT"
    client.delete(f"/api/datasources/{ds_id}", headers=H(tokens["engineer"]))


def test_column_diff_kind_unit(client, tokens):
    """列级 diff 纯函数：新列/复活列/类型与主键变更判定。"""
    from app.services.meta_service import _column_diff_kind
    from app.models import MetaColumn

    def mk(dtype, is_pk=False, dropped=False):
        return MetaColumn(table_id=0, name="x", data_type=dtype, is_pk=is_pk, dropped=dropped)

    assert _column_diff_kind(None, "TEXT", False) == "column_added"
    assert _column_diff_kind(mk("TEXT", dropped=True), "TEXT", False) == "column_added"
    assert _column_diff_kind(mk("INTEGER"), "BIGINT", False) == "column_altered"
    assert _column_diff_kind(mk("INTEGER", is_pk=False), "INTEGER", True) == "column_altered"
    assert _column_diff_kind(mk("INTEGER", True), "INTEGER", True) is None
