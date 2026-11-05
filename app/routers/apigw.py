"""数据服务路由：管理端（JWT）+ 公共调用端（AK/SK 签名）。"""
import datetime
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user, require_role, ensure_can_manage, client_ip
from ..models import User, ApiDef, ApiApp, ApiGrant
from ..security import gen_secret, encrypt_secret, sign_payload
from ..services import audit as audit_svc
from ..services import apigw_service, sqlguard

router = APIRouter(prefix="/api/apigw", tags=["apigw"])


class ApiReq(BaseModel):
    code: str
    name: str
    description: str = ""
    datasource_id: int
    sql_template: str
    params: list = []
    cache_ttl: int = 0
    row_limit: int = 1000
    timeout_ms: int = 5000


class AppReq(BaseModel):
    app_name: str
    rate_limit: int = 10
    ip_whitelist: str = ""


class GrantReq(BaseModel):
    api_id: int
    expire_at: Optional[str] = None


def _api_out(a: ApiDef, db: Session) -> dict:
    from ..models import DataSource
    ds = db.get(DataSource, a.datasource_id)
    return {"id": a.id, "code": a.code, "name": a.name, "description": a.description,
            "datasource_id": a.datasource_id, "datasource": ds.name if ds else "?",
            "sql_template": a.sql_template, "params": a.params,
            "cache_ttl": a.cache_ttl, "row_limit": a.row_limit, "timeout_ms": a.timeout_ms,
            "status": a.status, "owner_id": a.owner_id, "updated_at": str(a.updated_at)}


@router.get("/apis")
def list_apis(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [_api_out(a, db) for a in db.query(ApiDef).order_by(ApiDef.id.desc()).all()]


@router.post("/apis")
def create_api(req: ApiReq, request: Request, user: User = Depends(require_role("engineer")),
               db: Session = Depends(get_db)):
    from ..models import DataSource
    if db.query(ApiDef).filter(ApiDef.code == req.code).first():
        raise HTTPException(400, "API 编码已存在")
    ds = db.get(DataSource, req.datasource_id)
    if not ds:
        raise HTTPException(400, "数据源不存在")
    ok, msg = apigw_service.check_api_sql(req.sql_template, sqlguard.dialect_of(ds.type))
    if not ok:
        raise HTTPException(400, f"SQL 校验失败: {msg}")
    a = ApiDef(code=req.code, name=req.name, description=req.description,
               datasource_id=req.datasource_id, sql_template=req.sql_template,
               params=req.params, cache_ttl=req.cache_ttl, row_limit=req.row_limit,
               timeout_ms=req.timeout_ms, owner_id=user.id)
    db.add(a)
    db.commit()
    audit_svc.log(db, user, "apigw.api.create", "api", a.id, {"code": req.code},
                  ip=client_ip(request))
    return _api_out(a, db)


@router.put("/apis/{api_id}")
def update_api(api_id: int, req: ApiReq, request: Request,
               user: User = Depends(require_role("engineer")), db: Session = Depends(get_db)):
    a = db.get(ApiDef, api_id)
    if not a:
        raise HTTPException(404, "API 不存在")
    ensure_can_manage(user, a.owner_id, "该 API")
    if a.status == "published":
        raise HTTPException(400, "已发布 API 不可修改，请先下线")
    if req.code != a.code and db.query(ApiDef).filter(ApiDef.code == req.code).first():
        raise HTTPException(400, "API 编码已存在")
    from ..models import DataSource
    ds = db.get(DataSource, req.datasource_id)
    ok, msg = apigw_service.check_api_sql(req.sql_template, sqlguard.dialect_of(ds.type))
    if not ok:
        raise HTTPException(400, f"SQL 校验失败: {msg}")
    for k, v in req.dict().items():
        setattr(a, k, v)
    db.commit()
    audit_svc.log(db, user, "apigw.api.update", "api", a.id, {"code": a.code},
                  ip=client_ip(request))
    return _api_out(a, db)


@router.delete("/apis/{api_id}")
def delete_api(api_id: int, request: Request, user: User = Depends(require_role("engineer")),
               db: Session = Depends(get_db)):
    a = db.get(ApiDef, api_id)
    if not a:
        raise HTTPException(404, "API 不存在")
    ensure_can_manage(user, a.owner_id, "该 API")
    db.query(ApiGrant).filter(ApiGrant.api_id == api_id).delete()
    audit_svc.log(db, user, "apigw.api.delete", "api", api_id, ip=client_ip(request))
    db.delete(a)
    db.commit()
    return {"ok": True}


@router.post("/apis/{api_id}/publish")
def publish_api(api_id: int, request: Request, user: User = Depends(require_role("engineer")),
                db: Session = Depends(get_db)):
    a = db.get(ApiDef, api_id)
    if not a:
        raise HTTPException(404, "API 不存在")
    ensure_can_manage(user, a.owner_id, "该 API")
    a.status = "published"
    db.commit()
    audit_svc.log(db, user, "apigw.api.publish", "api", api_id, ip=client_ip(request))
    return {"ok": True}


@router.post("/apis/{api_id}/offline")
def offline_api(api_id: int, request: Request, user: User = Depends(require_role("engineer")),
                db: Session = Depends(get_db)):
    a = db.get(ApiDef, api_id)
    if not a:
        raise HTTPException(404, "API 不存在")
    ensure_can_manage(user, a.owner_id, "该 API")
    a.status = "offline"
    db.commit()
    audit_svc.log(db, user, "apigw.api.offline", "api", api_id, ip=client_ip(request))
    return {"ok": True}


@router.post("/apis/{api_id}/test-execute")
def test_execute(api_id: int, req: dict, user: User = Depends(require_role("engineer")),
                 db: Session = Depends(get_db)):
    a = db.get(ApiDef, api_id)
    if not a:
        raise HTTPException(404, "API 不存在")
    try:
        result = apigw_service.execute_api(db, a, None, req.get("params") or {})
        result["rows"] = [[str(v) if v is not None else None for v in r] for r in result["rows"]]
        return result
    except Exception as e:
        raise HTTPException(400, str(e))


@router.get("/apis/{api_id}/doc")
def api_doc(api_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = db.get(ApiDef, api_id)
    if not a:
        raise HTTPException(404, "API 不存在")
    return {"code": a.code, "method": "POST", "path": f"/api/v1/query/{a.code}",
            "headers": {"X-App-Key": "应用 app_key",
                        "X-Timestamp": "unix 秒级时间戳（±300s）",
                        "X-Sign": "HMAC-SHA256(secret, sorted_json({app_key,timestamp,params}))"},
            "body": {"params": {p["name"]: f"({p.get('type','string')}"
                                f"{', required' if p.get('required') else ''})"
                                for p in (a.params or [])}},
            "status": a.status}


# ---------------- 应用（AK/SK） ----------------

@router.get("/apps")
def list_apps(user: User = Depends(require_role("engineer")), db: Session = Depends(get_db)):
    return [{"id": a.id, "app_name": a.app_name, "app_key": a.app_key,
             "rate_limit": a.rate_limit, "ip_whitelist": a.ip_whitelist,
             "status": a.status} for a in db.query(ApiApp).all()]


@router.post("/apps")
def create_app(req: AppReq, request: Request, user: User = Depends(require_role("admin")),
               db: Session = Depends(get_db)):
    app_key = "ak_" + gen_secret(12).replace("-", "").replace("_", "").lower()[:20]
    secret = "sk_" + gen_secret(24).replace("-", "").replace("_", "").lower()[:40]
    a = ApiApp(app_name=req.app_name, app_key=app_key,
               app_secret_enc=encrypt_secret(secret), rate_limit=req.rate_limit,
               ip_whitelist=req.ip_whitelist)
    db.add(a)
    db.commit()
    audit_svc.log(db, user, "apigw.app.create", "api_app", a.id,
                  {"app_name": req.app_name}, ip=client_ip(request))
    return {"id": a.id, "app_key": app_key, "app_secret": secret,
            "note": "secret 仅此一次展示，平台加密存储、不可再次查看"}


@router.put("/apps/{app_id}/status")
def update_app(app_id: int, req: dict, user: User = Depends(require_role("admin")),
               db: Session = Depends(get_db)):
    a = db.get(ApiApp, app_id)
    if not a:
        raise HTTPException(404, "应用不存在")
    if req.get("status") in ("active", "disabled"):
        a.status = req["status"]
    if "rate_limit" in req:
        a.rate_limit = int(req["rate_limit"])
    db.commit()
    return {"ok": True}


@router.post("/apps/{app_id}/grants")
def grant(app_id: int, req: GrantReq, request: Request,
          user: User = Depends(require_role("admin")), db: Session = Depends(get_db)):
    a = db.get(ApiApp, app_id)
    if not a:
        raise HTTPException(404, "应用不存在")
    api = db.get(ApiDef, req.api_id)
    if not api:
        raise HTTPException(404, "API 不存在")
    expire = None
    if req.expire_at:
        expire = datetime.datetime.fromisoformat(req.expire_at)
    g = (db.query(ApiGrant).filter(ApiGrant.app_id == app_id, ApiGrant.api_id == req.api_id)
           .first())
    if not g:
        g = ApiGrant(app_id=app_id, api_id=req.api_id, expire_at=expire)
        db.add(g)
    else:
        g.expire_at = expire
    db.commit()
    audit_svc.log(db, user, "apigw.grant", "api_app", app_id, {"api": api.code},
                  ip=client_ip(request))
    return {"ok": True}


@router.get("/grants/{app_id}")
def list_grants(app_id: int, user: User = Depends(require_role("engineer")),
                db: Session = Depends(get_db)):
    from ..models import ApiLog  # noqa
    grants = db.query(ApiGrant).filter(ApiGrant.app_id == app_id).all()
    out = []
    for g in grants:
        api = db.get(ApiDef, g.api_id)
        out.append({"api_id": g.api_id, "code": api.code if api else "?",
                    "name": api.name if api else "?",
                    "expire_at": str(g.expire_at) if g.expire_at else ""})
    return out


@router.get("/logs")
def logs(api_id: int = 0, page: int = 1, size: int = 50,
         user: User = Depends(require_role("engineer")), db: Session = Depends(get_db)):
    from ..models import ApiLog
    q = db.query(ApiLog)
    if api_id:
        q = q.filter(ApiLog.api_id == api_id)
    total = q.count()
    rows = q.order_by(ApiLog.id.desc()).offset((page - 1) * size).limit(size).all()
    return {"total": total, "items": [{"id": r.id, "api_id": r.api_id, "app_key": r.app_key,
                                       "params": r.params, "cost_ms": r.cost_ms,
                                       "row_count": r.row_count, "status": r.status,
                                       "error": r.error, "ts": str(r.ts)} for r in rows]}


# ---------------- 公共调用端点（AK/SK） ----------------

public_router = APIRouter(prefix="/api/v1", tags=["apigw-public"])


@public_router.post("/query/{code}")
def public_query(code: str, body: dict, request: Request, db: Session = Depends(get_db)):
    api = db.query(ApiDef).filter(ApiDef.code == code).first()
    if not api or api.status != "published":
        raise HTTPException(404, "API 不存在或未发布")
    app_key = request.headers.get("X-App-Key", "")
    ts = request.headers.get("X-Timestamp", "")
    sign = request.headers.get("X-Sign", "")
    params = body.get("params") or {}
    app = db.query(ApiApp).filter(ApiApp.app_key == app_key).first()
    if not app:
        raise HTTPException(401, "app_key 无效")
    ok, msg = apigw_service.verify_signature(app, app_key, ts, sign, params)
    if not ok:
        raise HTTPException(401, msg)
    ip = client_ip(request)
    ok, msg = apigw_service.check_app_access(db, app, api, ip)
    if not ok:
        raise HTTPException(403, msg)
    try:
        result = apigw_service.execute_api(db, api, app, params, ip=ip)
    except Exception as e:
        raise HTTPException(400, str(e))
    return {"code": 0, "data": {"columns": result["columns"], "rows": result["rows"],
                                "row_count": result["row_count"],
                                "truncated": result["truncated"], "cached": result["cached"]}}
