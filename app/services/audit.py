"""审计服务：所有变更动作（UI/API/AI）统一落库。"""
from typing import Optional

from sqlalchemy.orm import Session

from ..models import AuditLog


def log(db: Session, user, action: str, resource_type: str = "", resource_id="",
        detail: Optional[dict] = None, source: str = "ui", ip: str = ""):
    entry = AuditLog(
        user_id=getattr(user, "id", 0) or 0,
        username=getattr(user, "username", "") or "",
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id or ""),
        detail=detail or {},
        source=source,
        ip=ip or "",
    )
    db.add(entry)
    db.commit()
