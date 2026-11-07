"""FastAPI 依赖：登录态、角色权限、资源所有权。"""
from typing import Optional

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .db import get_db
from .models import User
from .security import decode_token

ROLE_LEVEL = {"viewer": 1, "analyst": 2, "engineer": 3, "admin": 4}


def _extract_token(request: Request) -> Optional[str]:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return None


def get_optional_user(request: Request, db: Session = Depends(get_db)) -> Optional[User]:
    token = _extract_token(request)
    if not token:
        return None
    payload = decode_token(token)
    if not payload:
        return None
    user = db.get(User, int(payload["sub"]))
    if not user or user.status != "active":
        return None
    return user


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = get_optional_user(request, db)
    if not user:
        raise HTTPException(401, "未登录或登录已过期")
    return user


def current_user_query(token: str = "", db: Session = Depends(get_db)) -> User:
    """URL 直接引用场景（如 <img src> 无法携带 Authorization 头）的鉴权：token 走 query 参数。"""
    payload = decode_token(token) if token else None
    if not payload:
        raise HTTPException(401, "未登录或登录已过期")
    user = db.get(User, int(payload["sub"]))
    if not user or user.status != "active":
        raise HTTPException(401, "未登录或登录已过期")
    return user


def require_role(min_role: str):
    def dep(user: User = Depends(current_user)) -> User:
        if ROLE_LEVEL.get(user.role, 0) < ROLE_LEVEL.get(min_role, 99):
            raise HTTPException(403, f"需要 {min_role} 及以上权限")
        return user
    return dep


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(403, "需要管理员权限")
    return user


def can_manage(user: User, owner_id: int) -> bool:
    """admin 或资源 Owner 可管理。"""
    return user.role == "admin" or user.id == owner_id


def ensure_can_manage(user: User, owner_id: int, what: str = "该资源"):
    if not can_manage(user, owner_id):
        raise HTTPException(403, f"无权管理{what}（仅 Owner 或管理员）")


def client_ip(request: Request) -> str:
    fwd = request.headers.get("X-Forwarded-For")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else ""
