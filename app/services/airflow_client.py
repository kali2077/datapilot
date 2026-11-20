"""Airflow 3.x 客户端：JWT Bearer 认证 + /api/v2 稳定 API 封装。

Airflow 3 移除了 /api/v1 与逐请求 basic_auth，稳定 API 全部位于 /api/v2，
认证改为 JWT Bearer（SimpleAuthManager：POST /auth/token，JSON body）。

token 取用优先级（与 config 注释一致）：
1. AIRFLOW_TOKEN 直填（已签发的长期 token）
2. AIRFLOW_TOKEN_FILE（dags 共享卷里 airflow 容器签发的 token 文件）
3. AIRFLOW_USER/AIRFLOW_PASSWORD 自动调 /auth/token 签发并缓存

所有方法不向上抛异常：不可达/鉴权失败返回 {"ok": False, "error": ...}，
沿用「airflow 不可达不阻塞平台操作」的原则（见 sched_service.apply_schedule）。
"""
import base64
import json
import os
import threading
import time

import httpx

from .. import config

_token_cache = {"token": "", "exp": 0.0}
_lock = threading.Lock()


def _jwt_exp(token: str) -> float:
    """无校验解析 JWT exp（仅用于缓存过期，不做鉴权）。"""
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        payload = json.loads(base64.urlsafe_b64decode(part))
        return float(payload.get("exp") or 0)
    except Exception:
        return 0.0


def _static_token() -> str:
    if config.AIRFLOW_TOKEN:
        return config.AIRFLOW_TOKEN
    if config.AIRFLOW_TOKEN_FILE and os.path.isfile(config.AIRFLOW_TOKEN_FILE):
        try:
            with open(config.AIRFLOW_TOKEN_FILE) as f:
                tok = f.read().strip()
            if tok:
                return tok
        except OSError:
            pass
    return ""


def _fetch_token() -> str:
    """user/password 换 JWT（SimpleAuthManager POST /auth/token，JSON body）。"""
    if not (config.AIRFLOW_USER and config.AIRFLOW_PASSWORD):
        return ""
    try:
        resp = httpx.post(f"{config.AIRFLOW_URL.rstrip('/')}/auth/token",
                          json={"username": config.AIRFLOW_USER,
                                "password": config.AIRFLOW_PASSWORD}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data.get("access_token") or data.get("token") or ""
    except Exception:
        return ""


def get_token(force: bool = False) -> str:
    with _lock:
        tok = _static_token()
        if tok:
            return tok
        if not force and _token_cache["token"] and time.time() < _token_cache["exp"]:
            return _token_cache["token"]
        tok = _fetch_token()
        if tok:
            exp = _jwt_exp(tok)
            _token_cache["token"] = tok
            _token_cache["exp"] = (exp - 60) if exp > 0 else time.time() + 1200
        return tok


def reset_token_cache():
    with _lock:
        _token_cache.update({"token": "", "exp": 0.0})


def request(method: str, path: str, params: dict = None, json_body: dict = None,
            timeout: float = 10) -> dict:
    """调用 Airflow REST API。成功返回 {"ok": True, ...响应JSON}；失败返回 {"ok": False, "error"}。"""
    base = config.AIRFLOW_URL.rstrip("/")
    tok = get_token()
    if not tok:
        return {"ok": False, "error": "Airflow 未配置认证（AIRFLOW_TOKEN / AIRFLOW_TOKEN_FILE / AIRFLOW_USER+AIRFLOW_PASSWORD）"}
    headers = {"Authorization": f"Bearer {tok}"}
    for attempt in (1, 2):
        try:
            resp = httpx.request(method, f"{base}{path}", params=params, json=json_body,
                                 headers=headers, timeout=timeout)
            if resp.status_code == 401 and attempt == 1:
                tok = get_token(force=True)  # token 过期则重签一次
                if not tok:
                    break
                headers["Authorization"] = f"Bearer {tok}"
                continue
            resp.raise_for_status()
            data = resp.json() if resp.content else {}
            data["ok"] = True
            return data
        except httpx.HTTPError as e:
            detail = ""
            resp = getattr(e, "response", None)
            if resp is not None:
                detail = f" HTTP {resp.status_code}: {resp.text[:200]}"
            return {"ok": False, "error": f"{e}{detail}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    return {"ok": False, "error": "Airflow 认证失败（401）"}


# ---------------------------------------------------------------- 常用封装

def health() -> dict:
    r = request("GET", "/api/v2/monitor/health", timeout=5)
    if not r.get("ok"):
        r = request("GET", "/health", timeout=5)  # 网关/旧路径兜底
    return r


def version() -> dict:
    return request("GET", "/api/v2/version", timeout=5)


def dags() -> dict:
    """平台工厂生成的 DAG（tag=datapilot），按 offset 自动翻页（上限 500）。"""
    out, offset = [], 0
    while True:
        r = request("GET", "/api/v2/dags",
                    params={"limit": 100, "offset": offset, "tags": "datapilot"})
        if not r.get("ok"):
            return r
        page = r.get("dags", [])
        out.extend(page)
        offset += len(page)
        if not page or offset >= 500:
            break
        total = r.get("total_entries")
        if isinstance(total, int) and offset >= total:
            break
    return {"ok": True, "dags": out}


def patch_dag(dag_id: str, is_paused: bool) -> dict:
    """暂停/恢复 DAG（v2 语义为 PATCH；update_mask 限定只改 is_paused）。"""
    return request("PATCH", f"/api/v2/dags/{dag_id}",
                   params={"update_mask": "is_paused"},
                   json_body={"is_paused": bool(is_paused)})


def dag_runs(dag_id: str, limit: int = 25) -> dict:
    return request("GET", f"/api/v2/dags/{dag_id}/dagRuns", params={"limit": limit})


def task_instances(dag_id: str, dag_run_id: str) -> dict:
    return request("GET", f"/api/v2/dags/{dag_id}/dagRuns/{dag_run_id}/taskInstances",
                   params={"limit": 200})


def trigger_dag(dag_id: str, logical_date: str = "") -> dict:
    body = {"logical_date": logical_date} if logical_date else {}
    return request("POST", f"/api/v2/dags/{dag_id}/dagRuns", json_body=body)
