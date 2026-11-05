from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user, require_role, ensure_can_manage, client_ip
from ..models import User, Workflow, WorkflowNode, TaskInstance, NodeRun, AlertChannel, AlertRecord
from ..services import audit as audit_svc
from ..services import sched_service, sqlguard

router = APIRouter(prefix="/api/sched", tags=["sched"])


class NodeReq(BaseModel):
    name: str
    node_type: str = "sql"
    config: dict = {}
    upstream: list = []


class WorkflowReq(BaseModel):
    name: str
    description: str = ""
    schedule_cron: str = ""
    depends_on: list = []
    enabled: bool = True
    nodes: list = []


class WfUpdateReq(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    schedule_cron: Optional[str] = None
    depends_on: Optional[list] = None
    enabled: Optional[bool] = None


class ChannelReq(BaseModel):
    name: str
    type: str
    config: dict = {}
    enabled: bool = True


def _ser_wf(wf: Workflow, db: Session) -> dict:
    return {"id": wf.id, "name": wf.name, "description": wf.description,
            "schedule_cron": wf.schedule_cron, "enabled": wf.enabled,
            "depends_on": wf.depends_on, "job_type": wf.job_type,
            "sync_job_id": wf.sync_job_id, "owner_id": wf.owner_id,
            "next_fire": sched_service.next_fire_time(wf.schedule_cron).isoformat()
            if wf.enabled and sched_service.next_fire_time(wf.schedule_cron) else None}


@router.get("/workflows")
def list_wf(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [_ser_wf(w, db) for w in db.query(Workflow).order_by(Workflow.id).all()]


@router.post("/workflows")
def create_wf(req: WorkflowReq, request: Request,
              user: User = Depends(require_role("engineer")), db: Session = Depends(get_db)):
    if db.query(Workflow).filter(Workflow.name == req.name).first():
        raise HTTPException(400, "工作流名已存在")
    if req.schedule_cron:
        ok, msg = sched_service.validate_cron(req.schedule_cron)
        if not ok:
            raise HTTPException(400, msg)
    wf = Workflow(name=req.name, description=req.description,
                  schedule_cron=req.schedule_cron, depends_on=req.depends_on,
                  enabled=req.enabled, job_type="etl", owner_id=user.id)
    db.add(wf)
    db.flush()
    ids = []
    for n in req.nodes:
        node = WorkflowNode(workflow_id=wf.id, name=n.get("name", "node"),
                            node_type=n.get("node_type", "sql"),
                            config=n.get("config", {}), upstream=n.get("upstream", []),
                            ordinal=len(ids))
        db.add(node)
        db.flush()
        ids.append(node.id)
    db.commit()
    # SQL 节点校验
    problems = []
    for node in db.query(WorkflowNode).filter(WorkflowNode.workflow_id == wf.id):
        if node.node_type == "sql":
            cfg = node.config or {}
            ds_id = cfg.get("datasource_id")
            from ..models import DataSource
            ds = db.get(DataSource, ds_id) if ds_id else None
            if not ds:
                problems.append(f"节点 {node.name}: 数据源不存在")
                continue
            ok, msg = sqlguard.check_etl_sql(cfg.get("sql", ""), cfg.get("target_table", ""),
                                             sqlguard.dialect_of(ds.type))
            if not ok:
                problems.append(f"节点 {node.name}: {msg}")
    if req.enabled:
        sched_service.apply_schedule(db, wf)
    audit_svc.log(db, user, "sched.wf.create", "workflow", wf.id, {"name": req.name},
                  ip=client_ip(request))
    out = _ser_wf(wf, db)
    out["problems"] = problems
    return out


@router.get("/workflows/{wf_id}")
def get_wf(wf_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    wf = db.get(Workflow, wf_id)
    if not wf:
        raise HTTPException(404, "工作流不存在")
    nodes = (db.query(WorkflowNode).filter(WorkflowNode.workflow_id == wf.id)
               .order_by(WorkflowNode.ordinal).all())
    out = _ser_wf(wf, db)
    out["nodes"] = [{"id": n.id, "name": n.name, "node_type": n.node_type,
                     "config": n.config, "upstream": n.upstream} for n in nodes]
    return out


@router.put("/workflows/{wf_id}")
def update_wf(wf_id: int, req: WfUpdateReq, request: Request,
              user: User = Depends(require_role("engineer")), db: Session = Depends(get_db)):
    wf = db.get(Workflow, wf_id)
    if not wf:
        raise HTTPException(404, "工作流不存在")
    ensure_can_manage(user, wf.owner_id, "该工作流")
    if req.schedule_cron is not None:
        ok, msg = sched_service.validate_cron(req.schedule_cron)
        if not ok:
            raise HTTPException(400, msg)
        wf.schedule_cron = req.schedule_cron
    if req.name is not None:
        if req.name != wf.name and db.query(Workflow).filter(Workflow.name == req.name).first():
            raise HTTPException(400, "工作流名已存在")
        wf.name = req.name
    if req.description is not None:
        wf.description = req.description
    if req.depends_on is not None:
        wf.depends_on = req.depends_on
    if req.enabled is not None:
        wf.enabled = req.enabled
    db.commit()
    sched_service.apply_schedule(db, wf)
    audit_svc.log(db, user, "sched.wf.update", "workflow", wf.id,
                  req.dict(exclude_none=True), ip=client_ip(request))
    return _ser_wf(wf, db)


@router.delete("/workflows/{wf_id}")
def delete_wf(wf_id: int, request: Request, force: bool = False,
              user: User = Depends(require_role("engineer")), db: Session = Depends(get_db)):
    wf = db.get(Workflow, wf_id)
    if not wf:
        raise HTTPException(404, "工作流不存在")
    ensure_can_manage(user, wf.owner_id, "该工作流")
    inst_count = db.query(TaskInstance).filter(TaskInstance.workflow_id == wf_id).count()
    if wf.enabled and not force:
        raise HTTPException(400, "工作流启用中，请先暂停或使用 force 删除")
    if inst_count and not force:
        raise HTTPException(400, f"工作流有 {inst_count} 条运行实例，force=true 连同实例强制删除")
    db.query(WorkflowNode).filter(WorkflowNode.workflow_id == wf_id).delete()
    db.query(NodeRun).filter(NodeRun.instance_id.in_(
        db.query(TaskInstance.id).filter(TaskInstance.workflow_id == wf_id))).delete(synchronize_session=False)
    db.query(TaskInstance).filter(TaskInstance.workflow_id == wf_id).delete()
    from ..models import LineageEdge
    db.query(LineageEdge).filter(LineageEdge.workflow_id == wf_id).delete()
    audit_svc.log(db, user, "sched.wf.delete", "workflow", wf_id,
                  {"name": wf.name, "force": force}, ip=client_ip(request))
    db.delete(wf)
    db.commit()
    return {"ok": True, "deleted_instances": inst_count}


@router.post("/workflows/{wf_id}/trigger")
def trigger(wf_id: int, request: Request, user: User = Depends(require_role("engineer")),
            db: Session = Depends(get_db)):
    wf = db.get(Workflow, wf_id)
    if not wf:
        raise HTTPException(404, "工作流不存在")
    inst = sched_service.trigger_workflow(db, wf, "manual", wait_on_deps=True,
                                          triggered_by=user.username)
    audit_svc.log(db, user, "sched.wf.trigger", "workflow", wf.id,
                  {"instance": inst.id}, ip=client_ip(request))
    return {"instance_id": inst.id, "status": inst.status}


@router.get("/instances")
def list_instances(workflow_id: int = 0, status: str = "", page: int = 1, size: int = 20,
                   user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = db.query(TaskInstance)
    if workflow_id:
        q = q.filter(TaskInstance.workflow_id == workflow_id)
    if status:
        q = q.filter(TaskInstance.status == status)
    total = q.count()
    rows = (q.order_by(TaskInstance.id.desc())
              .offset((page - 1) * size).limit(size).all())
    return {"total": total, "items": [{
        "id": i.id, "workflow_id": i.workflow_id, "workflow_name": i.workflow_name,
        "trigger_type": i.trigger_type, "logical_date": str(i.logical_date),
        "status": i.status, "started_at": str(i.started_at or ""),
        "finished_at": str(i.finished_at or ""), "error": (i.error or "")[:500],
        "triggered_by": i.triggered_by} for i in rows]}


@router.get("/instances/{inst_id}")
def get_instance(inst_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    inst = db.get(TaskInstance, inst_id)
    if not inst:
        raise HTTPException(404, "实例不存在")
    runs = (db.query(NodeRun).filter(NodeRun.instance_id == inst.id)
              .order_by(NodeRun.id).all())
    return {"id": inst.id, "workflow_id": inst.workflow_id,
            "workflow_name": inst.workflow_name, "status": inst.status,
            "trigger_type": inst.trigger_type, "logical_date": str(inst.logical_date),
            "started_at": str(inst.started_at or ""), "finished_at": str(inst.finished_at or ""),
            "error": inst.error, "triggered_by": inst.triggered_by,
            "nodes": [{"id": r.id, "node_id": r.node_id, "name": r.node_name,
                       "node_type": r.node_type, "status": r.status,
                       "duration_ms": r.duration_ms,
                       "started_at": str(r.started_at or ""), "log": r.log or ""} for r in runs]}


@router.post("/instances/{inst_id}/rerun")
def rerun(inst_id: int, request: Request, only_failed: bool = False,
          user: User = Depends(require_role("engineer")), db: Session = Depends(get_db)):
    inst = db.get(TaskInstance, inst_id)
    if not inst:
        raise HTTPException(404, "实例不存在")
    sched_service.rerun_instance(db, inst, only_failed=only_failed)
    audit_svc.log(db, user, "sched.instance.rerun", "instance", inst.id,
                  {"only_failed": only_failed}, ip=client_ip(request))
    return {"ok": True}


@router.post("/nodes/{run_id}/rerun")
def rerun_node(run_id: int, request: Request, user: User = Depends(require_role("engineer")),
               db: Session = Depends(get_db)):
    sched_service.rerun_node(db, run_id)
    audit_svc.log(db, user, "sched.node.rerun", "node_run", run_id, ip=client_ip(request))
    return {"ok": True}


# ---------------- SQL 开发工具 ----------------

class SqlCheckReq(BaseModel):
    datasource_id: int
    sql: str
    target_table: str = ""


@router.post("/dev/sql/validate")
def sql_validate(req: SqlCheckReq, user: User = Depends(require_role("engineer")),
                 db: Session = Depends(get_db)):
    from ..models import DataSource
    ds = db.get(DataSource, req.datasource_id)
    if not ds:
        raise HTTPException(404, "数据源不存在")
    dialect = sqlguard.dialect_of(ds.type)
    ok, msg = sqlguard.check_etl_sql(req.sql, req.target_table, dialect)
    ro, romsg = sqlguard.check_readonly(req.sql, dialect)
    lineage = sqlguard.extract_sql_lineage(req.sql, dialect)
    return {"etl_ok": ok, "etl_msg": msg, "readonly_ok": ro, "readonly_msg": romsg,
            "sources": lineage[0], "target": lineage[1]}


@router.post("/dev/sql/explain")
def sql_explain(req: SqlCheckReq, user: User = Depends(require_role("engineer")),
                db: Session = Depends(get_db)):
    from ..models import DataSource
    ds = db.get(DataSource, req.datasource_id)
    if not ds:
        raise HTTPException(404, "数据源不存在")
    import sqlglot as _sg
    from sqlglot import expressions as _exp
    dialect = sqlguard.dialect_of(ds.type)
    try:
        st = _sg.parse_one(req.sql, read=dialect)
        part = st.expression.sql(dialect=dialect) if isinstance(st, _exp.Insert) else req.sql
        return sqlguard.explain_sql(ds, part)
    except Exception as e:
        raise HTTPException(400, f"EXPLAIN 失败: {e}")


# ---------------- 告警 ----------------

@router.get("/alert-channels")
def list_channels(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [{"id": c.id, "name": c.name, "type": c.type, "config": c.config,
             "enabled": c.enabled} for c in db.query(AlertChannel).all()]


@router.post("/alert-channels")
def create_channel(req: ChannelReq, request: Request,
                   user: User = Depends(require_role("admin")), db: Session = Depends(get_db)):
    if req.type not in ("email", "webhook"):
        raise HTTPException(400, "通道类型仅支持 email/webhook")
    c = AlertChannel(name=req.name, type=req.type, config=req.config, enabled=req.enabled)
    db.add(c)
    db.commit()
    audit_svc.log(db, user, "alert.channel.create", "alert_channel", c.id,
                  {"type": req.type}, ip=client_ip(request))
    return {"id": c.id}


@router.put("/alert-channels/{cid}")
def update_channel(cid: int, req: ChannelReq, user: User = Depends(require_role("admin")),
                   db: Session = Depends(get_db)):
    c = db.get(AlertChannel, cid)
    if not c:
        raise HTTPException(404, "通道不存在")
    c.name, c.type, c.config, c.enabled = req.name, req.type, req.config, req.enabled
    db.commit()
    return {"ok": True}


@router.delete("/alert-channels/{cid}")
def delete_channel(cid: int, user: User = Depends(require_role("admin")),
                   db: Session = Depends(get_db)):
    c = db.get(AlertChannel, cid)
    if c:
        db.delete(c)
        db.commit()
    return {"ok": True}


@router.get("/alert-records")
def alert_records(page: int = 1, size: int = 50, user: User = Depends(current_user),
                  db: Session = Depends(get_db)):
    q = db.query(AlertRecord)
    total = q.count()
    rows = (q.order_by(AlertRecord.id.desc()).offset((page - 1) * size).limit(size).all())
    return {"total": total, "items": [{"id": r.id, "channel": r.channel_name,
                                       "instance_id": r.instance_id, "title": r.title,
                                       "status": r.status, "ts": str(r.ts)} for r in rows]}
