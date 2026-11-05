"""数据查询工作台：只读即席查询 + 历史 + 保存查询。

权限：所有登录角色可执行只读查询（与智能问数 query_data 一致）；
写入类 SQL 由 sqlguard 白名单拦截。
"""
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import delete, desc, func
from sqlalchemy.orm import Session

from .. import config
from ..db import get_db
from ..deps import current_user, client_ip
from ..models import User, DataSource, SavedQuery, QueryHistory
from ..services import audit as audit_svc
from ..services import sqlguard
from ..services.engine_registry import run_query

router = APIRouter(prefix="/api/query", tags=["query"])

HISTORY_KEEP = 200


class RunReq(BaseModel):
    datasource_id: int
    sql: str
    limit: int = 500


class SaveReq(BaseModel):
    name: str
    datasource_id: int
    sql: str


def _record_history(db: Session, user_id: int, ds: DataSource, sql: str,
                    status: str, cost_ms: int, row_count: int):
    db.add(QueryHistory(user_id=user_id, datasource_id=ds.id if ds else 0,
                        datasource_name=ds.name if ds else "", sql=sql[:8000],
                        status=status, cost_ms=cost_ms, row_count=row_count))
    db.commit()
    # 每用户只保留最近 HISTORY_KEEP 条
    cnt = db.query(func.count(QueryHistory.id)).filter(QueryHistory.user_id == user_id).scalar()
    if cnt and cnt > HISTORY_KEEP:
        sub = (db.query(QueryHistory.id)
                 .filter(QueryHistory.user_id == user_id)
                 .order_by(desc(QueryHistory.id))
                 .offset(HISTORY_KEEP).subquery())
        db.execute(delete(QueryHistory)
                   .where(QueryHistory.id.in_(db.query(sub.c.id))))
        db.commit()


@router.post("/run")
def run(req: RunReq, request: Request, user: User = Depends(current_user),
        db: Session = Depends(get_db)):
    ds = db.get(DataSource, req.datasource_id)
    if not ds:
        raise HTTPException(404, "数据源不存在")
    dialect = sqlguard.dialect_of(ds.type)
    ok, msg = sqlguard.check_readonly(req.sql, dialect)
    if not ok:
        _record_history(db, user.id, ds, req.sql, "error", 0, 0)
        raise HTTPException(400, msg)
    limit = max(1, min(int(req.limit or 500), config.QUERY_MAX_ROWS))
    wrapped = sqlguard.ensure_limit(req.sql.strip().rstrip(";"), limit, dialect)
    t0 = time.time()
    try:
        cols, rows, truncated = run_query(ds, wrapped, max_rows=limit,
                                          timeout_s=config.QUERY_TIMEOUT_S)
        from ..services import mask_service
        cols, rows, masked = mask_service.apply(db, ds, cols, rows)
    except Exception as e:
        _record_history(db, user.id, ds, req.sql, "error", int((time.time() - t0) * 1000), 0)
        raise HTTPException(400, f"查询执行失败: {e}")
    cost_ms = int((time.time() - t0) * 1000)
    _record_history(db, user.id, ds, req.sql, "ok", cost_ms, len(rows))
    audit_svc.log(db, user, "query.run", "datasource", ds.id,
                  {"rows": len(rows), "cost_ms": cost_ms, "sql": req.sql[:200]},
                  ip=client_ip(request))
    return {"columns": cols, "rows": rows, "row_count": len(rows),
            "truncated": truncated, "cost_ms": cost_ms, "sql": wrapped,
            "datasource": ds.name, "masked_columns": masked}


@router.get("/history")
def history(limit: int = 30, user: User = Depends(current_user),
            db: Session = Depends(get_db)):
    rows = (db.query(QueryHistory).filter(QueryHistory.user_id == user.id)
              .order_by(QueryHistory.id.desc()).limit(min(limit, 100)).all())
    return [{"id": r.id, "datasource_id": r.datasource_id,
             "datasource": r.datasource_name, "sql": r.sql, "status": r.status,
             "cost_ms": r.cost_ms, "row_count": r.row_count, "ts": str(r.ts)}
            for r in rows]


@router.delete("/history")
def clear_history(user: User = Depends(current_user), db: Session = Depends(get_db)):
    db.query(QueryHistory).filter(QueryHistory.user_id == user.id).delete()
    db.commit()
    return {"ok": True}


@router.get("/saved")
def list_saved(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = (db.query(SavedQuery).filter(SavedQuery.user_id == user.id)
              .order_by(SavedQuery.id.desc()).all())
    return [{"id": r.id, "name": r.name, "datasource_id": r.datasource_id,
             "sql": r.sql, "created_at": str(r.created_at)} for r in rows]


@router.post("/saved")
def save(req: SaveReq, request: Request, user: User = Depends(current_user),
         db: Session = Depends(get_db)):
    if not db.get(DataSource, req.datasource_id):
        raise HTTPException(400, "数据源不存在")
    s = SavedQuery(user_id=user.id, name=req.name.strip()[:128],
                   datasource_id=req.datasource_id, sql=req.sql)
    db.add(s)
    db.commit()
    audit_svc.log(db, user, "query.save", "saved_query", s.id, {"name": s.name},
                  ip=client_ip(request))
    return {"id": s.id}


@router.delete("/saved/{sid}")
def delete_saved(sid: int, user: User = Depends(current_user),
                 db: Session = Depends(get_db)):
    s = db.get(SavedQuery, sid)
    if not s or s.user_id != user.id:
        raise HTTPException(404, "保存的查询不存在")
    db.delete(s)
    db.commit()
    return {"ok": True}
