from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user, require_role, ensure_can_manage, client_ip
from ..models import (User, SyncJob, SyncJobVersion, TypeMapping, DataSource,
                      Workflow, TaskInstance, NodeRun)
from ..services import audit as audit_svc
from ..services import integ_service, sched_service

router = APIRouter(prefix="/api/integ", tags=["integ"])


class JobReq(BaseModel):
    name: str
    source_datasource_id: int
    source_table: str = ""
    source_query: str = ""
    target_datasource_id: int
    target_table: str
    config: dict = {}


class PublishReq(BaseModel):
    cron: str = ""
    depends_on: list = []


class MappingReq(BaseModel):
    source_engine: str = "*"
    target_engine: str = "*"
    source_dtype: str
    target_dtype: str
    transform: str = ""
    note: str = ""


def _ser(job: SyncJob, db: Session) -> dict:
    src = db.get(DataSource, job.source_datasource_id)
    dst = db.get(DataSource, job.target_datasource_id)
    return {"id": job.id, "name": job.name, "status": job.status,
            "current_version": job.current_version, "owner_id": job.owner_id,
            "source": {"id": src.id, "name": src.name, "type": src.type,
                       "table": job.source_table, "query": job.source_query} if src else None,
            "target": {"id": dst.id, "name": dst.name, "type": dst.type,
                       "table": job.target_table} if dst else None,
            "config": job.config, "updated_at": str(job.updated_at)}


@router.get("/jobs")
def list_jobs(status: str = "", user: User = Depends(current_user),
              db: Session = Depends(get_db)):
    q = db.query(SyncJob)
    if status:
        q = q.filter(SyncJob.status == status)
    jobs = q.order_by(SyncJob.id.desc()).all()
    # 最近运行状态：job -> 发布时生成的单节点工作流 -> 最新实例（列表页直接可见跑没跑成）
    job_ids = [j.id for j in jobs]
    wf_map = {}
    if job_ids:
        for w in db.query(Workflow).filter(Workflow.sync_job_id.in_(job_ids)).all():
            wf_map[w.sync_job_id] = w.id
    last_run = {}
    if wf_map:
        # 每工作流取最新一条实例（max-id 分组，避免大实例量下全行加载）
        from sqlalchemy import func
        max_ids = [r[0] for r in (db.query(func.max(TaskInstance.id))
                                  .filter(TaskInstance.workflow_id.in_(set(wf_map.values())))
                                  .group_by(TaskInstance.workflow_id).all())]
        for i in (db.query(TaskInstance).filter(TaskInstance.id.in_(max_ids)).all()
                  if max_ids else []):
            last_run[i.workflow_id] = {"instance_id": i.id, "status": i.status,
                                       "finished_at": str(i.finished_at or ""),
                                       "error": (i.error or "")[:200]}
    out = []
    for j in jobs:
        item = _ser(j, db)
        item["workflow_id"] = wf_map.get(j.id)
        item["last_run"] = last_run.get(wf_map.get(j.id))
        out.append(item)
    return out


@router.post("/jobs")
def create_job(req: JobReq, request: Request, user: User = Depends(require_role("engineer")),
               db: Session = Depends(get_db)):
    if db.query(SyncJob).filter(SyncJob.name == req.name).first():
        raise HTTPException(400, "任务名已存在")
    job = SyncJob(name=req.name, source_datasource_id=req.source_datasource_id,
                  source_table=req.source_table, source_query=req.source_query,
                  target_datasource_id=req.target_datasource_id,
                  target_table=req.target_table, config=req.config, owner_id=user.id)
    db.add(job)
    db.commit()
    audit_svc.log(db, user, "integ.job.create", "sync_job", job.id, {"name": req.name},
                  ip=client_ip(request))
    return _ser(job, db)


@router.get("/jobs/{job_id}")
def get_job(job_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    job = db.get(SyncJob, job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    out = _ser(job, db)
    out["issues"] = integ_service.validate_job(db, job)
    out["ddl"] = integ_service.suggest_target_ddl(db, job)
    return out


@router.put("/jobs/{job_id}")
def update_job(job_id: int, req: JobReq, request: Request,
               user: User = Depends(require_role("engineer")), db: Session = Depends(get_db)):
    job = db.get(SyncJob, job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    ensure_can_manage(user, job.owner_id, "该任务")
    if job.status == "published":
        raise HTTPException(400, "已发布任务不可直接修改，请先下线")
    if req.name != job.name and db.query(SyncJob).filter(SyncJob.name == req.name).first():
        raise HTTPException(400, "任务名已存在")
    job.name = req.name
    job.source_datasource_id = req.source_datasource_id
    job.source_table = req.source_table
    job.source_query = req.source_query
    job.target_datasource_id = req.target_datasource_id
    job.target_table = req.target_table
    job.config = req.config
    db.commit()
    audit_svc.log(db, user, "integ.job.update", "sync_job", job.id, {"name": req.name},
                  ip=client_ip(request))
    return _ser(job, db)


@router.post("/jobs/{job_id}/validate")
def validate(job_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    job = db.get(SyncJob, job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    issues = integ_service.validate_job(db, job)
    return {"issues": issues, "ddl": integ_service.suggest_target_ddl(db, job),
            "ok": not any(i["level"] == "error" for i in issues)}


@router.post("/jobs/{job_id}/trial")
def trial(job_id: int, request: Request, dry_run: bool = True,
          user: User = Depends(require_role("engineer")), db: Session = Depends(get_db)):
    """试跑。默认校验模式（只读源不写目标，不污染增量断点）；dry_run=false 真实写入。"""
    job = db.get(SyncJob, job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    try:
        result = integ_service.trial_run(db, job, limit=100, dry_run=dry_run)
    except Exception as e:
        raise HTTPException(400, f"试跑失败: {e}")
    audit_svc.log(db, user, "integ.job.trial", "sync_job", job.id,
                  dict(result.get("stats", {}), dry_run=dry_run), ip=client_ip(request))
    return result


@router.get("/jobs/{job_id}/trend")
def job_trend(job_id: int, limit: int = 12,
              user: User = Depends(current_user), db: Session = Depends(get_db)):
    """同步趋势：最近 N 次实例的状态/耗时/写入行数（断流检测与趋势图数据源）。"""
    job = db.get(SyncJob, job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    wf = db.query(Workflow).filter(Workflow.sync_job_id == job.id).first()
    if not wf:
        return {"items": []}
    insts = (db.query(TaskInstance)
               .filter(TaskInstance.workflow_id == wf.id)
               .order_by(TaskInstance.id.desc())
               .limit(max(1, min(limit, 50))).all())
    items = []
    for i in insts:
        rows = -1
        for r in db.query(NodeRun).filter(NodeRun.instance_id == i.id,
                                          NodeRun.rows_written >= 0).all():
            rows = max(rows, r.rows_written)
        dur_ms = 0
        if i.started_at and i.finished_at:
            dur_ms = int((i.finished_at - i.started_at).total_seconds() * 1000)
        items.append({"instance_id": i.id, "status": i.status,
                      "trigger_type": i.trigger_type,
                      "finished_at": str(i.finished_at or ""),
                      "duration_ms": dur_ms, "rows_written": rows})
    return {"workflow_id": wf.id, "items": list(reversed(items))}


@router.post("/increment/resolve")
def resolve_increment(expr: str = "", user: User = Depends(require_role("engineer"))):
    """增量表达式试算：${bizdate}/${bizdate_nodash} 按当前逻辑日期解析预览。"""
    import datetime
    resolved = integ_service._resolve_increment(expr, datetime.datetime.now())
    return {"expr": expr, "resolved": resolved}


@router.post("/jobs/{job_id}/reset_target")
def reset_target(job_id: int, request: Request, drop: bool = False,
                 user: User = Depends(require_role("engineer")), db: Session = Depends(get_db)):
    """重置目标表（清空数据）→ 下次同步重新全量、重建 max_value 断点。

    drop=true 连表结构一起删（列映射变更后建议用）；默认 TRUNCATE（结构保留）。
    """
    job = db.get(SyncJob, job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    ensure_can_manage(user, job.owner_id, "该任务")  # 破坏性操作：与其他写端点同款属主校验
    dst = db.get(DataSource, job.target_datasource_id)
    if not dst or not job.target_table:
        raise HTTPException(400, "任务无目标表，无需重置")
    from ..services.engine_registry import get_engine
    from sqlalchemy import text as _t
    eng = get_engine(dst)
    q = integ_service._qi(dst.type, job.target_table)
    mode = "drop" if drop else "truncate"
    try:
        stmt = (f"DROP TABLE IF EXISTS {q}" if drop else f"TRUNCATE TABLE {q}")
        with eng.begin() as conn:
            conn.execute(_t(stmt))
    except Exception:
        # TRUNCATE 不可用时退化为 DELETE（sqlite 等不支持 TRUNCATE 的引擎）
        try:
            with eng.begin() as conn:
                conn.execute(_t(f"DELETE FROM {q}"))
            mode = "delete"
        except Exception as e:
            raise HTTPException(400, f"重置目标表失败: {e}")
    audit_svc.log(db, user, "integ.reset_target", "sync_job", job.id,
                  {"mode": mode, "table": job.target_table,
                   "datasource": dst.name}, ip=client_ip(request))
    return {"ok": True, "mode": mode, "table": job.target_table}


@router.post("/jobs/{job_id}/publish")
def publish(job_id: int, req: PublishReq, request: Request,
            user: User = Depends(require_role("engineer")), db: Session = Depends(get_db)):
    job = db.get(SyncJob, job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    ensure_can_manage(user, job.owner_id, "该任务")
    if req.cron:
        ok, msg = sched_service.validate_cron(req.cron)
        if not ok:
            raise HTTPException(400, msg)
    issues = integ_service.validate_job(db, job)
    if any(i["level"] == "error" for i in issues):
        raise HTTPException(400, "存在未解决的 error 级问题，无法发布: " +
                            "；".join(i["message"] for i in issues if i["level"] == "error"))
    wf = integ_service.publish_job(db, job, user, cron=req.cron, depends_on=req.depends_on)
    audit_svc.log(db, user, "integ.job.publish", "sync_job", job.id,
                  {"cron": req.cron, "workflow": wf.name}, ip=client_ip(request))
    return {"ok": True, "workflow_id": wf.id, "workflow": wf.name, "version": job.current_version}


@router.post("/jobs/{job_id}/offline")
def offline(job_id: int, request: Request, user: User = Depends(require_role("engineer")),
            db: Session = Depends(get_db)):
    job = db.get(SyncJob, job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    ensure_can_manage(user, job.owner_id, "该任务")
    integ_service.offline_job(db, job)
    audit_svc.log(db, user, "integ.job.offline", "sync_job", job.id, ip=client_ip(request))
    return {"ok": True}


@router.post("/jobs/{job_id}/versions/{vid}/rollback")
def rollback(job_id: int, vid: int, request: Request,
             user: User = Depends(require_role("engineer")), db: Session = Depends(get_db)):
    job = db.get(SyncJob, job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    ensure_can_manage(user, job.owner_id, "该任务")
    ver = db.get(SyncJobVersion, vid)
    if not ver or ver.job_id != job_id:
        raise HTTPException(404, "版本不存在")
    try:
        result = integ_service.rollback_job(db, job, ver.version)
    except ValueError as e:
        raise HTTPException(400, str(e))
    audit_svc.log(db, user, "integ.job.rollback", "sync_job", job.id,
                  {"to_version": ver.version}, ip=client_ip(request))
    return result


@router.delete("/jobs/{job_id}")
def delete_job(job_id: int, request: Request,
               user: User = Depends(require_role("engineer")), db: Session = Depends(get_db)):
    job = db.get(SyncJob, job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    ensure_can_manage(user, job.owner_id, "该任务")
    try:
        result = integ_service.delete_job(db, job)
    except ValueError as e:
        raise HTTPException(400, str(e))
    audit_svc.log(db, user, "integ.job.delete", "sync_job", job_id,
                  {"name": result.get("name")}, ip=client_ip(request))
    return result


@router.get("/jobs/{job_id}/versions")
def versions(job_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [{"id": v.id, "version": v.version, "created_by": v.created_by,
             "created_at": str(v.created_at), "config": v.config}
            for v in db.query(SyncJobVersion).filter(SyncJobVersion.job_id == job_id)
            .order_by(SyncJobVersion.version.desc()).all()]


@router.get("/jobs/{job_id}/versions/{vid}/datax")
def version_datax(job_id: int, vid: int, user: User = Depends(current_user),
                  db: Session = Depends(get_db)):
    v = db.get(SyncJobVersion, vid)
    if not v or v.job_id != job_id:
        raise HTTPException(404, "版本不存在")
    import json
    return {"version": v.version, "datax_json": v.rendered_datax_json,
            "config": v.config}


# ---------------- 类型映射知识库 ----------------

@router.get("/typemappings")
def list_mappings(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [{"id": m.id, "source_engine": m.source_engine, "target_engine": m.target_engine,
             "source_dtype": m.source_dtype, "target_dtype": m.target_dtype,
             "transform": m.transform, "note": m.note}
            for m in db.query(TypeMapping).all()]


@router.post("/typemappings")
def create_mapping(req: MappingReq, user: User = Depends(require_role("admin")),
                   db: Session = Depends(get_db)):
    m = TypeMapping(**req.dict())
    db.add(m)
    db.commit()
    audit_svc.log(db, user, "typemapping.create", "type_mapping", m.id, req.dict())
    return {"id": m.id}


@router.delete("/typemappings/{mid}")
def delete_mapping(mid: int, user: User = Depends(require_role("admin")),
                   db: Session = Depends(get_db)):
    m = db.get(TypeMapping, mid)
    if m:
        db.delete(m)
        db.commit()
    return {"ok": True}
