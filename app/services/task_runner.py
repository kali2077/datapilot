"""任务执行器：工作流实例的拓扑执行、节点分发（datax/sql/python）、日志、血缘。"""
import datetime
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import List

from sqlalchemy.orm import Session

from ..models import (Workflow, WorkflowNode, TaskInstance, NodeRun,
                      SyncJob, DataSource, LineageEdge)
from .engine_registry import execute_write, get_engine
from . import sqlguard, integ_service

_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="wf-run")


def _append_log(db: Session, run_id: int, text_: str):
    from sqlalchemy import text as _t
    db.execute(_t("UPDATE sched_node_run SET log = COALESCE(log,'') || :t WHERE id = :i"),
               {"t": text_, "i": run_id})
    db.commit()


def topo_sort(nodes: List[WorkflowNode]) -> List[WorkflowNode]:
    ids = {n.id for n in nodes}
    by_id = {n.id: n for n in nodes}
    indeg = {n.id: 0 for n in nodes}
    children = {n.id: [] for n in nodes}
    for n in nodes:
        for up in (n.upstream or []):
            if up in ids:
                indeg[n.id] += 1
                children[up].append(n.id)
    order, queue = [], [nid for nid, d in indeg.items() if d == 0]
    while queue:
        nid = queue.pop(0)
        order.append(by_id[nid])
        for ch in children[nid]:
            indeg[ch] -= 1
            if indeg[ch] == 0:
                queue.append(ch)
    if len(order) != len(nodes):
        raise ValueError("工作流节点存在循环依赖")
    return order


def _deps_satisfied(db: Session, wf: Workflow, logical_date: datetime.datetime) -> (bool, str):
    """跨工作流依赖：同一逻辑日期的上游实例需 success。"""
    for dep_id in (wf.depends_on or []):
        dep_wf = db.get(Workflow, dep_id)
        if not dep_wf:
            continue
        last = (db.query(TaskInstance)
                  .filter(TaskInstance.workflow_id == dep_id)
                  .order_by(TaskInstance.id.desc()).first())
        if not last or last.status != "success":
            return False, f"上游工作流 [{dep_wf.name}] 尚无成功实例"
    return True, ""


def _exec_datax(db: Session, node: WorkflowNode, log, logical_date) -> dict:
    from .. import config as _cfg
    job_id = (node.config or {}).get("sync_job_id")
    job = db.get(SyncJob, job_id)
    if not job:
        raise ValueError(f"sync_job {job_id} 不存在")
    if _cfg.DATAX_HOME:
        log("[engine] 提示：DATAX_HOME 已配置，生产环境可用 datax.py 执行渲染 JSON（见任务版本管理）")
    return integ_service.run_sync(db, job, limit=None, log=log, logical_date=logical_date)


def _exec_sql(db: Session, node: WorkflowNode, log, logical_date) -> dict:
    import sqlglot as _sg
    from sqlglot import expressions as _exp
    cfg = node.config or {}
    ds = db.get(DataSource, cfg.get("datasource_id"))
    if not ds:
        raise ValueError("SQL 节点数据源不存在")
    sql = (cfg.get("sql") or "").strip()
    target = cfg.get("target_table") or ""
    dialect = sqlguard.dialect_of(ds.type)
    ok, msg = sqlguard.check_etl_sql(sql, target, dialect)
    if not ok:
        raise ValueError(f"SQL 校验失败: {msg}")
    log(f"[sql] 目标={target}\n{sql}")
    # 目标表不存在时自动建表（CTAS LIMIT 0：结构对齐 SELECT 输出）
    from sqlalchemy import inspect as _sa_inspect
    if target.split(".")[-1] not in _sa_inspect(get_engine(ds)).get_table_names():
        st = _sg.parse_one(sql, read=dialect)
        sel0 = st.expression.limit(0).sql(dialect=dialect)
        ctas = f"CREATE TABLE {target} AS {sel0}"
        if ds.type == "clickhouse":
            ctas += " ENGINE = MergeTree ORDER BY tuple()"
        log(f"[ddl] 目标表不存在，自动建表: {ctas[:300]}")
        execute_write(ds, ctas)
    res = execute_write(ds, sql)
    # 血缘
    sources, tgt = sqlguard.extract_sql_lineage(sql, sqlguard.dialect_of(ds.type))
    if tgt:
        db.query(LineageEdge).filter(LineageEdge.workflow_id == node.workflow_id,
                                     LineageEdge.job_type == "sql").delete()
        for s in sources:
            db.add(LineageEdge(source_datasource_id=ds.id, source_table=s,
                               target_datasource_id=ds.id, target_table=tgt,
                               job_type="sql", job_id=node.id, workflow_id=node.workflow_id))
        db.commit()
    return {"rowcount": res.get("rowcount", -1)}


def _exec_python(db: Session, node: WorkflowNode, log, logical_date) -> dict:
    import subprocess, sys, tempfile, os
    script = (node.config or {}).get("script") or ""
    if not script.strip():
        raise ValueError("python 节点脚本为空")
    fd, path = tempfile.mkstemp(suffix=".py")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(script)
        proc = subprocess.run([sys.executable, path], capture_output=True, text=True, timeout=120)
        log(f"[python] stdout:\n{proc.stdout[-4000:]}")
        if proc.returncode != 0:
            raise ValueError(f"脚本退出码 {proc.returncode}: {proc.stderr[-4000:]}")
        return {"stdout_bytes": len(proc.stdout)}
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _refresh_meta(db: Session, node: WorkflowNode, log):
    """节点成功后自动刷新目标数据源元数据（新表自动进资产/地图/血缘）。"""
    try:
        from . import meta_service
        from ..models import MetaTable
        if node.node_type == "sql":
            ds_id = (node.config or {}).get("datasource_id")
        elif node.node_type == "datax":
            job = db.get(SyncJob, (node.config or {}).get("sync_job_id"))
            ds_id = job.target_datasource_id if job else None
        else:
            return
        if not ds_id:
            return
        ds = db.get(DataSource, ds_id)
        if ds:
            meta_service.collect_datasource(db, ds)
            meta_service.meta_index.invalidate()
            log("[meta] 目标数据源元数据已刷新")
    except Exception as e:
        log(f"[meta] 刷新失败（不影响任务）: {e}")


def run_node(db: Session, run: NodeRun, node: WorkflowNode, logical_date: datetime.datetime):
    run.status = "running"
    run.started_at = datetime.datetime.now()
    db.commit()
    log = lambda s: _append_log(db, run.id, str(s) + "\n")
    try:
        if node.node_type == "datax":
            res = _exec_datax(db, node, log, logical_date)
        elif node.node_type == "sql":
            res = _exec_sql(db, node, log, logical_date)
        elif node.node_type == "python":
            res = _exec_python(db, node, log, logical_date)
        else:
            raise ValueError(f"未知节点类型 {node.node_type}")
        run.status = "success"
        log(f"[result] {res}")
        _refresh_meta(db, node, log)
    except Exception as e:
        run.status = "failed"
        log(f"[error] {e}\n{traceback.format_exc()[-3000:]}")
    run.finished_at = datetime.datetime.now()
    run.duration_ms = int((run.finished_at - run.started_at).total_seconds() * 1000)
    db.commit()


def run_instance(instance_id: int, only_node: int = None, rerun_failed: bool = False):
    """在工作线程中执行实例。"""
    from ..db import SessionLocal
    db = SessionLocal()
    try:
        inst = db.get(TaskInstance, instance_id)
        if not inst:
            return
        wf = db.get(Workflow, inst.workflow_id)
        nodes = db.query(WorkflowNode).filter(WorkflowNode.workflow_id == wf.id).all()
        inst.status = "running"
        inst.started_at = inst.started_at or datetime.datetime.now()
        inst.triggered_by = inst.triggered_by or "scheduler"
        db.commit()
        order = topo_sort(nodes)
        ok, msg = (True, "")
        if inst.trigger_type == "scheduled" and only_node is None:
            ok, msg = _deps_satisfied(db, wf, inst.logical_date)
            if not ok:
                inst.status = "waiting"
                db.commit()
                db.close()
                return
        existing = {r.node_id: r for r in db.query(NodeRun).filter(NodeRun.instance_id == inst.id)}
        failed = False
        first_error = ""
        for node in order:
            run = existing.get(node.id)
            if rerun_failed and run and run.status == "success":
                continue
            if run is None:
                run = NodeRun(instance_id=inst.id, node_id=node.id, node_name=node.name,
                              node_type=node.node_type, status="waiting")
                db.add(run)
                db.commit()
            elif only_node is not None and run.id != only_node and run.status == "success":
                continue
            elif only_node is not None and run.id != only_node:
                continue
            if failed and only_node is None:
                run.status = "skipped"
                db.commit()
                continue
            run_node(db, run, node, inst.logical_date)
            db.refresh(run)  # 日志经原生 SQL 追加，刷新后可读
            if run.status == "failed":
                failed = True
                first_error = first_error or (run.log or "")[-2000:]
        inst.status = "failed" if failed else "success"
        inst.error = first_error
        inst.finished_at = datetime.datetime.now()
        db.commit()
        if failed:
            _fire_alerts(db, inst)
    finally:
        db.close()


def _fire_alerts(db: Session, inst: TaskInstance):
    from .alert import fire_failure_alerts
    try:
        fire_failure_alerts(db, inst)
    except Exception:
        pass


def submit_instance(instance_id: int, only_node: int = None, rerun_failed: bool = False):
    _pool.submit(run_instance, instance_id, only_node, rerun_failed)
