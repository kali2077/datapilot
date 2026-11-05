from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user, require_admin, client_ip
from ..models import User
from ..security import hash_password, verify_password, create_token
from ..services import audit as audit_svc

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginReq(BaseModel):
    username: str
    password: str


class UserReq(BaseModel):
    username: str
    password: str = ""
    display_name: str = ""
    role: str = "viewer"


class UserUpdateReq(BaseModel):
    display_name: Optional[str] = None
    role: Optional[str] = None
    status: Optional[str] = None
    password: Optional[str] = None


@router.post("/login")
def login(req: LoginReq, request: Request, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == req.username).first()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(401, "用户名或密码错误")
    if user.status != "active":
        raise HTTPException(403, "账号已停用")
    token = create_token(user.id, user.username, user.role)
    audit_svc.log(db, user, "login", source="ui", ip=client_ip(request))
    return {"token": token, "user": {"id": user.id, "username": user.username,
                                     "display_name": user.display_name, "role": user.role}}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return {"id": user.id, "username": user.username,
            "display_name": user.display_name, "role": user.role}


@router.get("/users")
def list_users(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return [{"id": u.id, "username": u.username, "display_name": u.display_name,
             "role": u.role, "status": u.status, "created_at": str(u.created_at)}
            for u in db.query(User).all()]


@router.post("/users")
def create_user(req: UserReq, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    if db.query(User).filter(User.username == req.username).first():
        raise HTTPException(400, "用户名已存在")
    if req.role not in ("admin", "engineer", "analyst", "viewer"):
        raise HTTPException(400, "非法角色")
    if len(req.password) < 6:
        raise HTTPException(400, "密码至少 6 位")
    u = User(username=req.username, password_hash=hash_password(req.password),
             display_name=req.display_name or req.username, role=req.role)
    db.add(u)
    db.commit()
    audit_svc.log(db, admin, "user.create", "user", u.id, {"role": req.role})
    return {"id": u.id}


@router.put("/users/{user_id}")
def update_user(user_id: int, req: UserUpdateReq, admin: User = Depends(require_admin),
                db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404, "用户不存在")
    if req.role:
        if req.role not in ("admin", "engineer", "analyst", "viewer"):
            raise HTTPException(400, "非法角色")
        u.role = req.role
    if req.status:
        if req.status not in ("active", "disabled"):
            raise HTTPException(400, "非法状态")
        if u.id == admin.id and req.status == "disabled":
            raise HTTPException(400, "不能停用自己")
        u.status = req.status
    if req.display_name is not None:
        u.display_name = req.display_name
    if req.password:
        if len(req.password) < 6:
            raise HTTPException(400, "密码至少 6 位")
        u.password_hash = hash_password(req.password)
    db.commit()
    audit_svc.log(db, admin, "user.update", "user", u.id,
                  {k: v for k, v in req.dict().items() if v is not None and k != "password"})
    return {"ok": True}
