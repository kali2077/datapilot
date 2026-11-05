"""数据源与元数据测试。"""
from conftest import H, make_sqlite_ds


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
