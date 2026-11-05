"""数据监控告警路由：规则 CRUD / 立即检查 / 检查记录 / 概览。"""
import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from typing import Optional
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user, require_role, ensure_can_manage, client_ip
from ..models import User, MonitorRule, MonitorCheck, DataSource, AlertRecord
from ..services import audit as audit_svc
from ..services import monitor_service, sched_service

router = APIRouter(prefix="/api/monitor", tags=["monitor"])


class RuleReq(BaseModel):
    name: str
    rule_type: str
    datasource_id: int = 0
    table_name: str = ""
    column_name: str = ""
    params: dict = {}
    cron: str = "0 6 * * *"
    severity: str = "warning"
    enabled: bool = True
    group_name: str = ""
    mute_hours: int = 24


def _ser(rule: MonitorRule, db: Session) -> dict:
    ds = db.get(DataSource, rule.datasource_id) if rule.datasource_id else None
    return {"id": rule.id, "name": rule.name, "rule_type": rule.rule_type,
            "datasource_id": rule.datasource_id, "datasource": ds.name if ds else "",
            "table_name": rule.table_name, "column_name": rule.column_name,
            "params": rule.params, "cron": rule.cron, "severity": rule.severity,
            "enabled": rule.enabled, "group_name": rule.group_name or "",
            "mute_hours": rule.mute_hours if rule.mute_hours is not None else 24,
            "last_alerted_at": str(rule.last_alerted_at or ""),
            "baseline_count": rule.baseline_count,
            "last_status": rule.last_status, "last_checked_at": str(rule.last_checked_at or ""),
            "last_message": rule.last_message, "owner_id": rule.owner_id,
            "updated_at": str(rule.updated_at)}


@router.get("/rules")
def list_rules(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [_ser(r, db) for r in db.query(MonitorRule).order_by(MonitorRule.id.desc()).all()]


@router.get("/meta")
def rule_meta(user: User = Depends(current_user)):
    return {"rule_types": monitor_service.RULE_TYPES}


@router.post("/rules")
def create_rule(req: RuleReq, request: Request,
                user: User = Depends(require_role("engineer")), db: Session = Depends(get_db)):
    if req.rule_type not in monitor_service.RULE_TYPES:
        raise HTTPException(400, f"未知规则类型: {req.rule_type}")
    if req.rule_type != "task_duration":
        if not db.get(DataSource, req.datasource_id):
            raise HTTPException(400, "数据源不存在")
    if req.cron:
        ok, msg = sched_service.validate_cron(req.cron)
        if not ok:
            raise HTTPException(400, msg)
    if req.severity not in ("warning", "critical"):
        raise HTTPException(400, "severity 仅支持 warning/critical")
    rule = MonitorRule(**req.dict(), owner_id=user.id)
    db.add(rule)
    db.commit()
    if rule.enabled:
        monitor_service.apply_schedule(db, rule)
    audit_svc.log(db, user, "monitor.rule.create", "monitor_rule", rule.id,
                  {"name": req.name, "type": req.rule_type}, ip=client_ip(request))
    return _ser(rule, db)


@router.put("/rules/{rule_id}")
def update_rule(rule_id: int, req: RuleReq, request: Request,
                user: User = Depends(require_role("engineer")), db: Session = Depends(get_db)):
    rule = db.get(MonitorRule, rule_id)
    if not rule:
        raise HTTPException(404, "规则不存在")
    ensure_can_manage(user, rule.owner_id, "该规则")
    if req.rule_type not in monitor_service.RULE_TYPES:
        raise HTTPException(400, f"未知规则类型: {req.rule_type}")
    ok, msg = sched_service.validate_cron(req.cron)
    if not ok:
        raise HTTPException(400, msg)
    for k, v in req.dict().items():
        setattr(rule, k, v)
    db.commit()
    monitor_service.apply_schedule(db, rule)
    audit_svc.log(db, user, "monitor.rule.update", "monitor_rule", rule.id,
                  {"name": req.name}, ip=client_ip(request))
    return _ser(rule, db)


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: int, request: Request,
                user: User = Depends(require_role("engineer")), db: Session = Depends(get_db)):
    rule = db.get(MonitorRule, rule_id)
    if not rule:
        raise HTTPException(404, "规则不存在")
    ensure_can_manage(user, rule.owner_id, "该规则")
    db.query(MonitorCheck).filter(MonitorCheck.rule_id == rule_id).delete()
    audit_svc.log(db, user, "monitor.rule.delete", "monitor_rule", rule_id,
                  {"name": rule.name}, ip=client_ip(request))
    db.delete(rule)
    db.commit()
    from ..services import sched_service as _ss
    sched = _ss._get_scheduler()
    job_id = f"mon_{rule_id}"
    if sched.get_job(job_id):
        sched.remove_job(job_id)
    return {"ok": True}


@router.post("/rules/{rule_id}/run")
def run_now(rule_id: int, request: Request, user: User = Depends(require_role("engineer")),
            db: Session = Depends(get_db)):
    rule = db.get(MonitorRule, rule_id)
    if not rule:
        raise HTTPException(404, "规则不存在")
    check = monitor_service.check_rule(db, rule)
    audit_svc.log(db, user, "monitor.rule.run", "monitor_rule", rule.id,
                  {"status": check.status, "metric": check.metric[:120]}, ip=client_ip(request))
    return {"id": check.id, "status": check.status, "metric": check.metric,
            "message": check.message, "cost_ms": check.cost_ms}


@router.get("/checks")
def list_checks(rule_id: int = 0, status: str = "", page: int = 1, size: int = 30,
                user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = db.query(MonitorCheck)
    if rule_id:
        q = q.filter(MonitorCheck.rule_id == rule_id)
    if status:
        q = q.filter(MonitorCheck.status == status)
    total = q.count()
    rows = (q.order_by(MonitorCheck.id.desc())
              .offset((page - 1) * size).limit(size).all())
    return {"total": total, "items": [{
        "id": c.id, "rule_id": c.rule_id, "rule_name": c.rule_name,
        "rule_type": c.rule_type, "status": c.status, "metric": c.metric,
        "message": c.message, "severity": c.severity, "cost_ms": c.cost_ms,
        "ts": str(c.ts)} for c in rows]}


@router.get("/trend")
def trend(days: int = 7, rule_id: int = 0, user: User = Depends(current_user),
          db: Session = Depends(get_db)):
    """近 N 天检查结果趋势（按天聚合 通过/失败/异常）。"""
    since = datetime.datetime.now() - datetime.timedelta(days=max(1, min(days, 90)))
    q = db.query(MonitorCheck).filter(MonitorCheck.ts >= since)
    if rule_id:
        q = q.filter(MonitorCheck.rule_id == rule_id)
    buckets = {}
    for c in q.all():
        day = c.ts.strftime("%m-%d")
        b = buckets.setdefault(day, {"day": day, "passed": 0, "failed": 0, "error": 0})
        if c.status in ("passed", "failed", "error"):
            b[c.status] += 1
    days_list = [buckets[k] for k in sorted(buckets)]
    return {"days": days_list,
            "total": sum(b["passed"] + b["failed"] + b["error"] for b in days_list)}


@router.get("/summary")
def summary(user: User = Depends(current_user), db: Session = Depends(get_db)):
    since = datetime.datetime.now() - datetime.timedelta(hours=24)
    rules_total = db.query(func.count(MonitorRule.id)).scalar() or 0
    rules_enabled = db.query(func.count(MonitorRule.id)).filter(
        MonitorRule.enabled == True).scalar() or 0  # noqa: E712
    checks_24h = db.query(func.count(MonitorCheck.id)).filter(
        MonitorCheck.ts >= since).scalar() or 0
    failed_24h = db.query(func.count(MonitorCheck.id)).filter(
        MonitorCheck.ts >= since, MonitorCheck.status == "failed").scalar() or 0
    error_24h = db.query(func.count(MonitorCheck.id)).filter(
        MonitorCheck.ts >= since, MonitorCheck.status == "error").scalar() or 0
    pass_rate = round((checks_24h - failed_24h - error_24h) / checks_24h * 100, 1) if checks_24h else None
    failing = (db.query(MonitorRule)
                 .filter(MonitorRule.enabled == True,  # noqa: E712
                         MonitorRule.last_status.in_(["failed", "error"]))
                 .order_by(MonitorRule.last_checked_at.desc()).limit(5).all())
    alerts_24h = db.query(func.count(AlertRecord.id)).filter(
        AlertRecord.ts >= since, AlertRecord.title.like("[DataPilot] 监控告警%")).scalar() or 0
    return {"rules_total": rules_total, "rules_enabled": rules_enabled,
            "checks_24h": checks_24h, "failed_24h": failed_24h, "error_24h": error_24h,
            "pass_rate": pass_rate, "alerts_24h": alerts_24h,
            "failing_rules": [{"id": r.id, "name": r.name, "status": r.last_status,
                               "message": r.last_message, "severity": r.severity} for r in failing]}
