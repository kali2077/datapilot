"""数据源二期测试：Trino/Hive/Spark/MaxCompute + Hadoop 平台（渲染/映射层单测 + API e2e）。"""
import json

from conftest import H, make_sqlite_ds

from app.models import DataSource
from app.services import sqlguard
from app.services.engine_registry import build_url
from app.services.integ_service import _jdbc, _driver, map_dtype, suggest_target_ddl


def _ds(**kw):
    base = dict(name="t", type="trino", host="t1", port=0, database="",
                username="analyst", password_enc="", params={})
    base.update(kw)
    return DataSource(**base)


# ---------------- P0: URL / 方言 / 映射 / JDBC ----------------

def test_p0_build_urls():
    tr = _ds(params={"catalog": "hive", "schema": "default"})
    assert build_url(tr) == "trino://analyst@t1:8080/hive/default"
    hive = _ds(type="hive", host="h1", database="dw",
               username="u", password_enc="p", params={"auth": "NONE"})
    assert build_url(hive) == "hive://u:p@h1:10000/dw?auth=NONE"
    sp = _ds(type="spark", host="s1")
    assert build_url(sp) == "hive://analyst@s1:10001"


def test_p0_dialects_and_guard():
    assert sqlguard.dialect_of("trino") == "trino"
    assert sqlguard.dialect_of("hive") == "hive"
    assert sqlguard.dialect_of("spark") == "spark"
    assert sqlguard.dialect_of("maxcompute") == "hive"
    ok, _ = sqlguard.check_readonly("SELECT * FROM t", "trino")
    assert ok
    ok, _ = sqlguard.check_readonly("INSERT INTO t VALUES (1)", "trino")
    assert not ok
    ok, _ = sqlguard.check_readonly("SELECT * FROM t", "hive")
    assert ok
    ok, _ = sqlguard.check_readonly("DROP TABLE t", "hive")
    assert not ok
    ok = sqlguard.ensure_limit("SELECT * FROM t", 5, "hive")
    assert "5" in ok


def test_p0_mapping(client):
    from app.db import SessionLocal
    db = SessionLocal()
    try:
        assert map_dtype(db, "string", "hive") == "STRING"
        assert map_dtype(db, "decimal(10,2)", "hive") == "DECIMAL(10,2)"
        assert map_dtype(db, "string", "spark") == "STRING"
        assert map_dtype(db, "VARCHAR(32)", "trino") == "VARCHAR(32)"
        assert map_dtype(db, "bigint", "trino") == "BIGINT"
        assert map_dtype(db, "string", "maxcompute") == "STRING"
        assert map_dtype(db, "bigint", "maxcompute") == "BIGINT"
    finally:
        db.close()


def test_p0_jdbc_render():
    tr = _ds(params={"catalog": "hive"})
    assert _jdbc(tr) == "jdbc:trino://t1:8080/hive"
    assert _driver(tr) == "io.trino.jdbc.TrinoDriver"
    hv = _ds(type="hive", host="h1", database="dw")
    assert _jdbc(hv) == "jdbc:hive2://h1:10000/dw"
    assert _driver(hv) == "org.apache.hive.jdbc.HiveDriver"
    sp = _ds(type="spark", host="s1")
    assert _jdbc(sp) == "jdbc:hive2://s1:10001/"


def test_p0_datax_render_trino_source_hive_target(client, tokens):
    """trino 源 -> hive 目标渲染 + 密码解密验证。"""
    from app.security import encrypt_secret
    from app.services.integ_service import render_datax_json
    src = _ds(name="tr_src", params={"catalog": "hive"}, password_enc=encrypt_secret("secret_pw"))
    dst = _ds(name="hv_dst", type="hive", host="h1", database="dw",
              password_enc=encrypt_secret("hive_pw"))
    job = type("J", (), {"source_table": "orders", "source_query": "",
                         "target_table": "ods_orders"})()
    j = json.loads(render_datax_json(job, src, dst, [{"source": "id", "target": "id"}]))
    reader, writer = j["job"]["content"][0]["reader"], j["job"]["content"][0]["writer"]
    assert reader["parameter"]["driverClassName"] == "io.trino.jdbc.TrinoDriver"
    assert reader["parameter"]["connection"][0]["jdbcUrl"][0] == "jdbc:trino://t1:8080/hive"
    assert writer["parameter"]["driverClassName"] == "org.apache.hive.jdbc.HiveDriver"
    # 密码已解密（此前为 Fernet 密文的潜在 bug）
    assert reader["parameter"]["password"] == "secret_pw"
    assert writer["parameter"]["password"] == "hive_pw"


def test_p0_ddl_flavors(client, tokens):
    from app.db import SessionLocal
    from app.models import DataSource, SyncJob
    db = SessionLocal()
    try:
        for i, (t, expect, not_expect) in enumerate([
            ("hive", "STORED AS PARQUET", "ENGINE"),
            ("spark", "STORED AS PARQUET", "ENGINE"),
            ("trino", '"ods_x"', None),
            ("maxcompute", "LIFECYCLE", None),
        ]):
            ds = DataSource(name=f"ddl_{t}_{i}", type=t, host="h", port=0, database="d")
            db.add(ds)
            db.commit()
            job = SyncJob(name=f"ddl_{t}_{i}", source_datasource_id=ds.id,
                          target_datasource_id=ds.id, target_table="ods_x",
                          config={"columns": [{"source": "id", "target": "id",
                                               "source_type": "BIGINT",
                                               "target_type": map_dtype(db, "bigint", t)}]})
            ddl = suggest_target_ddl(db, job)
            assert expect in ddl, (t, ddl)
            if not_expect:
                assert not_expect not in ddl, (t, ddl)
    finally:
        db.close()


def test_p0_create_datasource_api(client, tokens):
    for t, port in [("trino", 8080), ("hive", 10000), ("spark", 10001)]:
        r = client.post("/api/datasources", headers=H(tokens["engineer"]),
                        json={"name": f"p0_{t}", "type": t, "host": "127.0.0.1",
                              "port": port, "database": "dw", "username": "u",
                              "password": "p", "params": {"auth": "NONE"}})
        # 连接失败（无真实集群）按 400 返回，且不再是"不支持的数据源类型"
        assert r.status_code == 400
        assert "不支持的数据源类型" not in r.json()["detail"]


# ---------------- P1: MaxCompute ----------------

def test_p1_mc_url_guard_and_render(client, tokens):
    """MC 无 JDBC URL（build_url 抛错）；渲染走 odpsreader/odpswriter 且 AK/SK 解密。"""
    from app.security import encrypt_secret
    from app.services.integ_service import render_datax_json
    try:
        build_url(_ds(type="maxcompute"))
        assert False, "should raise"
    except ValueError as e:
        assert "pyodps" in str(e)

    src = _ds(name="mc_src", type="maxcompute", database="prj",
              username="LTAI", password_enc=encrypt_secret("sk_secret"),
              params={"endpoint": "http://service.cn-hangzhou.maxcompute.aliyun.com/api"})
    dst = _ds(name="mc_dst", type="maxcompute", database="prj2",
              username="LTAI", password_enc=encrypt_secret("sk_secret"),
              params={"endpoint": "http://service.cn-hangzhou.maxcompute.aliyun.com/api"})
    job = type("J", (), {"source_table": "src_t", "source_query": "",
                         "target_table": "tgt_t"})()
    j = json.loads(render_datax_json(job, src, dst, [{"source": "id", "target": "id"}]))
    reader = j["job"]["content"][0]["reader"]
    writer = j["job"]["content"][0]["writer"]
    assert reader["name"] == "odpsreader" and writer["name"] == "odpswriter"
    assert reader["parameter"]["accessKey"] == "sk_secret"
    assert reader["parameter"]["endpoint"].startswith("http://service")


def test_p1_mc_create_and_columns_guard(client, tokens):
    """MC 数据源创建走 pyodps 连通测试（对假 endpoint 返回 400 而非 500）。"""
    r = client.post("/api/datasources", headers=H(tokens["engineer"]),
                    json={"name": "p1_mc", "type": "maxcompute", "host": "",
                          "port": 0, "database": "prj", "username": "LTAI",
                          "password": "sk", "params": {"endpoint": "http://127.0.0.1:1/api"}})
    assert r.status_code == 400
    assert "不支持的数据源类型" not in r.json()["detail"]


# ---------------- P2: Hadoop 平台 ----------------

def test_p2_hadoop_cluster_lifecycle(client, tokens):
    h = H(tokens["engineer"])
    r = client.post("/api/hadoop/clusters", headers=h, json={
        "name": "prod_hadoop", "hdfs_uri": "http://127.0.0.1:1",
        "hs2_uri": "thrift://127.0.0.1:1", "spark_uri": "thrift://127.0.0.1:1",
        "trino_uri": "http://127.0.0.1:1", "trino_catalog": "hive",
        "kerberos": {"enabled": True, "realm": "EXAMPLE.COM",
                     "principal": "hive/_HOST@EXAMPLE.COM"}})
    assert r.status_code == 200, r.text
    cid = r.json()["id"]
    assert r.json()["kerberos"]["enabled"] is True
    # 连通测试（全部不可达但优雅返回）
    r = client.post(f"/api/hadoop/clusters/{cid}/test", headers=h)
    comps = r.json()["components"]
    assert comps["hive_hs2"]["ok"] is False
    assert comps["metastore"]["ok"] is None  # 未配置
    # 派生 hive 数据源（预填 + Kerberos 认证标记）
    r = client.post(f"/api/hadoop/clusters/{cid}/derive", headers=h, json={
        "kind": "hive", "name": "prod_hive", "database": "dw"})
    assert r.status_code == 200, r.text
    ds_id = r.json()["id"]
    assert r.json()["params"]["cluster_id"] == cid
    assert r.json()["params"]["auth"] == "KERBEROS"
    # 派生数据源出现在列表
    dss = client.get("/api/datasources", headers=h).json()
    assert any(d["id"] == ds_id for d in dss)
    # 有派生引用时平台删除被拒
    r = client.delete(f"/api/hadoop/clusters/{cid}", headers=h)
    assert r.status_code == 400
    # 删除派生数据源后可删
    client.delete(f"/api/datasources/{ds_id}", headers=h)
    assert client.delete(f"/api/hadoop/clusters/{cid}", headers=h).status_code == 200


def test_p2_hadoop_permissions(client, tokens):
    r = client.post("/api/hadoop/clusters", headers=H(tokens["analyst"]),
                    json={"name": "x"})
    assert r.status_code == 403
    r = client.get("/api/hadoop/clusters", headers=H(tokens["viewer"]))
    assert r.status_code == 200
