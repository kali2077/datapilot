"""新增数据源类型测试：Oracle / StarRocks / ClickHouse（URL 构建/方言/映射/DataX 渲染，无需真实服务器）。"""
from app.models import DataSource
from app.services import sqlguard
from app.services.engine_registry import build_url
from app.services.integ_service import (GENERIC_MAP, _jdbc, _driver,
                                        map_dtype, suggest_target_ddl)


def _ds(**kw):
    base = dict(name="t", type="oracle", host="10.0.0.1", port=0, database="orcl",
                username="scott", password_enc="tiger", params={})
    base.update(kw)
    return DataSource(**base)


def test_build_url_new_types():
    assert build_url(_ds()) == "oracle+oracledb://scott:tiger@10.0.0.1:1521/?service_name=orcl"
    sr = _ds(type="starrocks", database="dw")
    assert build_url(sr).startswith("mysql+pymysql://scott:tiger@10.0.0.1:9030/dw")
    ch = _ds(type="clickhouse", database="logs")
    assert build_url(ch).startswith("clickhouse+http://scott:tiger@10.0.0.1:8123/logs")
    my = _ds(type="mysql", database="biz")
    assert ":3306/" in build_url(my)


def test_dialect_of_new_types():
    assert sqlguard.dialect_of("starrocks") == "mysql"
    assert sqlguard.dialect_of("oracle") == "oracle"
    assert sqlguard.dialect_of("clickhouse") == "clickhouse"


def test_readonly_guard_new_dialects(client):
    # oracle 方言解析
    ok, _ = sqlguard.check_readonly("SELECT 1 FROM DUAL", "oracle")
    assert ok
    ok, _ = sqlguard.check_readonly("DELETE FROM t", "oracle")
    assert not ok
    # clickhouse 方言解析 + 多语句拦截
    ok, _ = sqlguard.check_readonly("SELECT 1", "clickhouse")
    assert ok
    ok, _ = sqlguard.check_readonly("SELECT 1; DROP TABLE t", "clickhouse")
    assert not ok


def test_ensure_limit_new_dialects():
    out = sqlguard.ensure_limit("SELECT * FROM t", 10, "oracle")
    assert "10" in out
    out = sqlguard.ensure_limit("SELECT * FROM t", 10, "clickhouse")
    assert "10" in out


def test_map_dtype_new_targets(client):
    from app.db import SessionLocal
    db = SessionLocal()
    try:
        # starrocks 复用 doris 知识库
        assert map_dtype(db, "VARCHAR(64)", "starrocks") == "VARCHAR(64)"
        assert map_dtype(db, "TEXT", "starrocks") == "STRING"
        # 内置通用映射
        assert map_dtype(db, "VARCHAR(128)", "oracle") == "VARCHAR2(128)"
        assert map_dtype(db, "BIGINT", "oracle") == "NUMBER(19)"
        assert map_dtype(db, "BIGINT", "clickhouse") == "Int64"
        assert map_dtype(db, "DATETIME", "clickhouse") == "DateTime"
        assert map_dtype(db, "DECIMAL(10,2)", "oracle") == "NUMBER(10,2)"
        assert map_dtype(db, "DECIMAL(10,2)", "clickhouse") == "Decimal(10,2)"
        assert GENERIC_MAP["int"]["starrocks"] == "INT"
    finally:
        db.close()


def test_jdbc_and_driver():
    assert _jdbc(_ds()) == "jdbc:oracle:thin:@//10.0.0.1:1521/orcl"
    assert _jdbc(_ds(type="starrocks", database="dw")) == \
        "jdbc:mysql://10.0.0.1:9030/dw?useUnicode=true&characterEncoding=utf8"
    assert _jdbc(_ds(type="clickhouse", database="logs")) == "jdbc:clickhouse://10.0.0.1:8123/logs"
    assert _driver(_ds()) == "oracle.jdbc.OracleDriver"
    assert _driver(_ds(type="clickhouse")) == "com.clickhouse.jdbc.ClickHouseDriver"
    assert _driver(_ds(type="starrocks")) == "com.mysql.cj.jdbc.Driver"


def test_datax_render_oracle_source(client, tokens):
    """oracle 源渲染 rdbmsreader + 正确驱动。"""
    import json
    from app.services.integ_service import render_datax_json
    src = _ds(name="o_src", type="oracle")
    dst = _ds(name="d_dst", type="doris", database="ods")
    job = type("J", (), {"source_table": "EMP", "source_query": "",
                         "target_table": "ods_emp"})()
    j = json.loads(render_datax_json(job, src, dst, [{"source": "ID", "target": "id"}]))
    reader = j["job"]["content"][0]["reader"]
    assert reader["name"] == "rdbmsreader"
    assert reader["parameter"]["driverClassName"] == "oracle.jdbc.OracleDriver"
    writer = j["job"]["content"][0]["writer"]
    assert writer["name"] == "doriswriter"


def test_clickhouse_ddl_has_engine(client, tokens):
    """clickhouse 自动建表 DDL 必须带 ENGINE。"""
    import time

    from app.db import SessionLocal
    from app.models import DataSource, SyncJob
    db = SessionLocal()
    try:
        ds = DataSource(name=f"ch_ddl_test_{int(time.time())}", type="clickhouse",
                        host="1.2.3.4", port=8123, database="logs")
        db.add(ds)
        db.commit()
        job = SyncJob(name="ch_ddl_tmp", source_datasource_id=ds.id,
                      target_datasource_id=ds.id, target_table="ods_x",
                      config={"columns": [{"source": "id", "target": "id",
                                           "source_type": "BIGINT", "target_type": "Int64"}]})
        ddl = suggest_target_ddl(db, job)
        assert "ENGINE = MergeTree" in ddl, ddl
    finally:
        db.close()


def test_create_ds_new_types_validated(client, tokens):
    """类型白名单放行三种新类型（连接失败按 400 提示，而非'不支持的数据源类型'）。"""
    r = client.post("/api/datasources", headers=H(tokens["engineer"]),
                    json={"name": "fake_oracle", "type": "oracle", "host": "127.0.0.1",
                          "port": 1521, "database": "FREE", "username": "u", "password": "p"})
    assert r.status_code == 400
    assert "不支持的数据源类型" not in r.json()["detail"]


from conftest import H  # noqa: E402
