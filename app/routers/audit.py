from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_admin
from ..models import User, AuditLog

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("/logs")
def list_logs(page: int = 1, size: int = 50, user: str = "", action: str = "",
              source: str = "", admin: User = Depends(require_admin),
              db: Session = Depends(get_db)):
    q = db.query(AuditLog)
    if user:
        q = q.filter(AuditLog.username.contains(user))
    if action:
        q = q.filter(AuditLog.action.contains(action))
    if source:
        q = q.filter(AuditLog.source == source)
    total = q.count()
    rows = q.order_by(AuditLog.id.desc()).offset((page - 1) * size).limit(size).all()
    return {"total": total, "items": [{
        "id": r.id, "username": r.username, "action": r.action,
        "resource_type": r.resource_type, "resource_id": r.resource_id,
        "detail": r.detail, "source": r.source, "ip": r.ip, "ts": str(r.ts)}
        for r in rows]}
