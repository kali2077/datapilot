"""sqlguard / 类型映射 单元测试。"""
import pytest

from app.services import sqlguard
from app.services.integ_service import dtype_family, map_dtype


def test_readonly_ok():
    ok, msg = sqlguard.check_readonly("SELECT * FROM t", "mysql")
    assert ok
    ok, _ = sqlguard.check_readonly("WITH x AS (SELECT 1) SELECT * FROM x", "mysql")
    assert ok
    ok, _ = sqlguard.check_readonly("SELECT 1 UNION SELECT 2", "mysql")
    assert ok


def test_readonly_blocked():
    for sql in ["DROP TABLE t", "DELETE FROM t", "UPDATE t SET a=1",
                "INSERT INTO t VALUES (1)", "CREATE TABLE t(a int)",
                "ALTER TABLE t ADD c int", "ATTACH DATABASE 'x' AS y"]:
        ok, msg = sqlguard.check_readonly(sql, "mysql")
        assert not ok, sql


def test_etl_sql():
    ok, msg = sqlguard.check_etl_sql("INSERT INTO dwd_x SELECT * FROM ods_x", "dwd_x")
    assert ok, msg
    ok, _ = sqlguard.check_etl_sql("INSERT INTO other SELECT * FROM ods_x", "dwd_x")
    assert not ok
    ok, _ = sqlguard.check_etl_sql("INSERT INTO dwd_x SELECT * FROM dwd_x", "dwd_x")
    assert not ok  # 自写入
    ok, _ = sqlguard.check_etl_sql("DELETE FROM dwd_x", "dwd_x")
    assert not ok
    ok, _ = sqlguard.check_etl_sql("INSERT INTO dwd_x SELECT 1; DROP TABLE t", "dwd_x")
    assert not ok  # 多语句


def test_ensure_limit():
    out = sqlguard.ensure_limit("SELECT * FROM t", 10, "mysql")
    assert "10" in out and "LIMIT" in out.upper()
    out = sqlguard.ensure_limit("SELECT * FROM t LIMIT 5", 10, "mysql")
    assert "LIMIT 5" in out


def test_lineage_extraction():
    sources, target = sqlguard.extract_sql_lineage(
        "INSERT INTO dwd_orders SELECT * FROM ods_orders WHERE dt = '2026-01-01'")
    assert target == "dwd_orders" and sources == ["ods_orders"]
    sources, target = sqlguard.extract_sql_lineage("SELECT * FROM a JOIN b ON a.x=b.x")
    assert set(sources) == {"a", "b"} and target == ""


def test_dtype_family_and_mapping():
    assert dtype_family("VARCHAR(255)") == "varchar"
    assert dtype_family("BIGINT") == "bigint"
    assert dtype_family("DECIMAL(10,2)") == "decimal"
    assert dtype_family("DATETIME") == "datetime"
    assert dtype_family("WEIRD_TYPE") == "unknown"


def test_map_dtype_kb(client):
    from app.db import SessionLocal
    db = SessionLocal()
    try:
        assert map_dtype(db, "VARCHAR(64)", "doris") == "VARCHAR(64)"
        assert map_dtype(db, "TEXT", "doris") == "STRING"
        assert map_dtype(db, "BIGINT", "sqlite") == "INTEGER"
        assert map_dtype(db, "DATETIME", "mysql") == "DATETIME"
    finally:
        db.close()
