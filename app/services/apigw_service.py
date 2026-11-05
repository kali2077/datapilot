"""数据服务（apigw）：API 执行链路 —— 鉴权/授权/限流/参数校验/缓存/超时/审计。"""
import datetime
import re
import threading
import time
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from ..models import ApiDef, ApiApp, ApiGrant, ApiLog, DataSource
from . import sqlguard
from .engine_registry import run_query

# 进程内限流：{app_key: (window_start, count)}
_rate: dict = {}
_rate_lock = threading.Lock()
# 进程内结果缓存：{cache_key: (value, expire_at)}
_cache: dict = {}
_cache_lock = threading.Lock()


def check_api_sql(sql: str, dialect: str = "mysql") -> Tuple[bool, str]:
    return sqlguard.check_readonly(sql, dialect)


def _cache_get(key):
    with _cache_lock:
        item = _cache.get(key)
        if not item:
            return None
        value, expire_at = item
        if time.time() > expire_at:
            _cache.pop(key, None)
            return None
        return value


def _cache_set(key, value, ttl: int):
    with _cache_lock:
        if len(_cache) > 512:
            _cache.clear()
        _cache[key] = (value, time.time() + ttl)


def check_rate_limit(app: ApiApp) -> bool:
    now = int(time.time())
    with _rate_lock:
        win, cnt = _rate.get(app.app_key, (now, 0))
        if win != now:
            win, cnt = now, 0
        cnt += 1
        _rate[app.app_key] = (win, cnt)
        return cnt <= max(1, app.rate_limit)


def verify_signature(app: ApiApp, app_key: str, timestamp: str, sign: str, params: dict) -> Tuple[bool, str]:
    """签名算法：sign = HMAC-SHA256(secret, sorted_json({app_key, timestamp, params}))，时间窗 ±300s。"""
    import hmac as _hmac
    from ..security import sign_payload, decrypt_secret
    if abs(time.time() - (int(timestamp) if str(timestamp).isdigit() else 0)) > 300:
        return False, "时间戳过期（±300s）"
    secret = decrypt_secret(app.app_secret_enc)
    expect = sign_payload({"app_key": app_key, "timestamp": str(timestamp), "params": params or {}},
                          secret)
    if not _hmac.compare_digest(str(sign), str(expect)):
        return False, "签名错误"
    return True, ""


def validate_params(api: ApiDef, params: dict) -> Tuple[bool, str]:
    for p in (api.params or []):
        name = p.get("name")
        val = params.get(name)
        if val in (None, ""):
            if p.get("required"):
                return False, f"缺少必填参数: {name}"
            d = p.get("default")
            if d not in (None, ""):
                params[name] = d
            continue
        t = p.get("type", "string")
        if t == "int":
            try:
                params[name] = int(val)
            except Exception:
                return False, f"参数 {name} 需为整数"
        elif t == "float":
            try:
                params[name] = float(val)
            except Exception:
                return False, f"参数 {name} 需为数字"
        elif t == "date":
            try:
                datetime.date.fromisoformat(str(val))
            except Exception:
                return False, f"参数 {name} 需为 YYYY-MM-DD 日期"
        rule = p.get("validation")
        if rule and not (str(val) == rule or (rule.startswith("^") and re.match(rule, str(val)))):
            return False, f"参数 {name} 未通过校验规则 {rule}"
    return True, ""


def execute_api(db: Session, api: ApiDef, app: Optional[ApiApp], params: dict,
                ip: str = "") -> dict:
    """核心执行链路。app=None 时为平台内调用（管理员试执行）。"""
    t0 = time.time()
    status, err, rows_out, cols_out, truncated = "ok", "", [], [], False

    def _log(row_count=0):
        db.add(ApiLog(api_id=api.id, app_id=app.id if app else 0,
                      app_key=app.app_key if app else "internal", params=params or {},
                      cost_ms=int((time.time() - t0) * 1000), row_count=row_count,
                      status=status, error=err[:2000]))
        db.commit()

    # 参数校验
    ok, msg = validate_params(api, params or {})
    if not ok:
        status, err = "error", msg
        _log()
        raise ValueError(msg)
    # SQL 校验
    ok, msg = check_api_sql(api.sql_template, sqlguard.dialect_of(_ds_type(db, api)))
    if not ok:
        status, err = "error", msg
        _log()
        raise ValueError(msg)
    # 缓存
    cache_key = f"{api.id}:{sorted((params or {}).items(), key=lambda x: str(x))}"
    if api.cache_ttl > 0:
        cached = _cache_get(cache_key)
        if cached is not None:
            cols_out, rows_out, truncated = cached
            _log(len(rows_out))
            return {"columns": cols_out, "rows": rows_out, "row_count": len(rows_out),
                    "truncated": truncated, "cached": True}
    # 执行（强制 LIMIT + 超时）
    ds = db.get(DataSource, api.datasource_id)
    dialect = sqlguard.dialect_of(ds.type)
    wrapped = sqlguard.ensure_limit(api.sql_template, api.row_limit, dialect)
    try:
        cols_out, rows_out, truncated = run_query(ds, wrapped, params=params or {},
                                                  max_rows=api.row_limit,
                                                  timeout_s=max(1, api.timeout_ms // 1000))
        from . import mask_service
        cols_out, rows_out, _ = mask_service.apply(db, ds, cols_out, rows_out)
    except Exception as e:
        status, err = "error", str(e)
        _log()
        raise
    if api.cache_ttl > 0:
        _cache_set(cache_key, (cols_out, rows_out, truncated), api.cache_ttl)
    _log(len(rows_out))
    return {"columns": cols_out, "rows": rows_out, "row_count": len(rows_out),
            "truncated": truncated, "cached": False}


def _ds_type(db: Session, api: ApiDef) -> str:
    ds = db.get(DataSource, api.datasource_id)
    return ds.type if ds else "mysql"


def check_app_access(db: Session, app: ApiApp, api: ApiDef, ip: str) -> Tuple[bool, str]:
    if app.status != "active":
        return False, "应用已停用"
    if app.ip_whitelist:
        allow = [x.strip() for x in app.ip_whitelist.split(",") if x.strip()]
        if ip not in allow:
            return False, f"IP {ip} 不在白名单"
    g = (db.query(ApiGrant)
           .filter(ApiGrant.app_id == app.id, ApiGrant.api_id == api.id).first())
    if not g:
        return False, "未授权该 API"
    if g.expire_at and g.expire_at < datetime.datetime.now():
        return False, "授权已过期"
    if not check_rate_limit(app):
        return False, f"超过限流阈值 {app.rate_limit} QPS"
    return True, ""
