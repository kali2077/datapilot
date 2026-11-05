from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user, require_role, ensure_can_manage, client_ip
from ..models import User, SyncJob, SyncJobVersion, TypeMapping, DataSource
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
    return [_ser(j, db) for j in q.order_by(SyncJob.id.desc()).all()]


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
def trial(job_id: int, request: Request, user: User = Depends(require_role("engineer")),
          db: Session = Depends(get_db)):
    job = db.get(SyncJob, job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    try:
        result = integ_service.trial_run(db, job, limit=100)
    except Exception as e:
        raise HTTPException(400, f"试跑失败: {e}")
    audit_svc.log(db, user, "integ.job.trial", "sync_job", job.id,
                  result.get("stats", {}), ip=client_ip(request))
    return result


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
