"""P0 修复回归：MaxCompute 同步路径（_run_sync_mc）假端到端——monkeypatch mc_client，
源=假 MC、目标=真 SQLite，覆盖 增量 SQL(_sql_lit)/非MC目标建表(execute_write)/写入/post_sql。"""
from unittest.mock import patch

from app.db import SessionLocal
from app.models import DataSource, SyncJob
from app.services import integ_service
from app.services import mc_client as mc_mod
from app.services.engine_registry import run_query
from conftest import H, make_sqlite_ds

MC_TABLE_COLS = [("id", "BIGINT"), ("name", "STRING"), ("amt", "DOUBLE")]
MC_ROWS = [(1, "a", 1.5), (2, "b", 2.5), (3, "c", 3.5)]


def _fake_mc_ds(db, name="fakemc"):
    ds = DataSource(name=name, type="maxcompute", database="prj",
                    username="AK", password_enc="SK",
                    params={"endpoint": "http://fake/api"})
    db.add(ds)
    db.commit()
    return ds


def test_run_sync_mc_source_to_sqlite(client, tokens):
    dst = make_sqlite_ds(client, tokens["engineer"], "mcfix_dst",
                         {"CREATE TABLE m (x INTEGER)": [(1,)]})
    db = SessionLocal()
    try:
        src = _fake_mc_ds(db)
        job = SyncJob(name="mcfix_job", source_datasource_id=src.id, source_table="src_t",
                      target_datasource_id=dst["id"], target_table="ods_mc",
                      owner_id=1, status="draft",
                      config={"write_mode": "append",
                              "increment": {"enabled": True, "column": "dt",
                                            "expr": "${bizdate}"},
                              "post_sql": "CREATE TABLE IF NOT EXISTS post_mark (t TEXT)"})
        db.add(job)
        db.commit()
        logs = []
        with patch.object(mc_mod, "columns", return_value=MC_TABLE_COLS), \
             patch.object(mc_mod, "fetch_rows",
                          side_effect=lambda ds, sql: (logs.append(sql), MC_ROWS)[1]), \
             patch.object(mc_mod, "table_names", return_value=[]), \
             patch.object(mc_mod, "create_table",
                          side_effect=lambda ds, ddl: logs.append("MC-DDL")), \
             patch.object(mc_mod, "insert_rows",
                          return_value=0):  # 目标非 MC，不应走 MC insert
            stats = integ_service.run_sync(db, job, limit=None, log=logs.append)
        # 断言：增量 SQL 用了 mc_client._sql_lit（不再 NameError），含引号字面量
        inc_sql = next(l for l in logs if l.startswith("SELECT id, name"))
        assert "WHERE dt > '" in inc_sql
        # 非 MC 目标：execute_write 建表 + 参数化批量插入（此前 NameError）
        assert stats["read_rows"] == 3
        dst_ds = db.get(DataSource, dst["id"])
        cols, rows, _ = run_query(dst_ds, "SELECT COUNT(*) FROM ods_mc")
        assert rows[0][0] == 3
        _, rows2, _ = run_query(dst_ds, "SELECT name FROM ods_mc ORDER BY id")
        assert [r[0] for r in rows2] == ["a", "b", "c"]
        # post_sql 在非 MC 目标执行成功（此前 NameError）
        run_query(dst_ds, "SELECT COUNT(*) FROM post_mark")
    finally:
        db.close()


def test_run_sync_mc_target_append_from_sqlite(client, tokens):
    """sqlite 源 → 假 MC 目标：走 mc_client 建表/insert_rows；truncate 模式被拒。"""
    src = make_sqlite_ds(client, tokens["engineer"], "mcfix_src",
                         {"CREATE TABLE s (id INTEGER, v TEXT)": [(1, "x"), (2, "y")]})
    db = SessionLocal()
    try:
        mcds = _fake_mc_ds(db, "fakemc2")
        job = SyncJob(name="mcfix_job2", source_datasource_id=src["id"], source_table="s",
                      target_datasource_id=mcds.id, target_table="tgt_t",
                      owner_id=1, status="draft", config={"write_mode": "append"})
        db.add(job)
        db.commit()
        calls = {"ddl": None, "insert": None}
        with patch.object(mc_mod, "table_names", return_value=[]), \
             patch.object(mc_mod, "create_table",
                          side_effect=lambda ds, ddl: calls.__setitem__("ddl", ddl)), \
             patch.object(mc_mod, "insert_rows",
                          side_effect=lambda ds, t, cols, rows:
                          (calls.__setitem__("insert", (t, list(cols), len(rows))), len(rows))[1]):
            stats = integ_service.run_sync(db, job, log=lambda s: None)
        assert stats["written_rows"] == 2
        assert "tgt_t" in (calls["ddl"] or "")
        assert calls["insert"][0] == "tgt_t" and calls["insert"][2] == 2
        # truncate/replace 模式在 MC 目标被明确拒绝
        job.config["write_mode"] = "truncate_insert"
        db.commit()
        try:
            integ_service.run_sync(db, job, log=lambda s: None)
            assert False, "should raise"
        except ValueError as e:
            assert "append" in str(e)
    finally:
        db.close()
