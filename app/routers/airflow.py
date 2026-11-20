"""Airflow 引擎代理：平台「调度引擎」面板的数据源（Airflow 3.x /api/v2 经服务端转发，统一平台 JWT 鉴权与审计）。"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import config
from ..db import get_db
from ..deps import current_user, require_role, client_ip
from ..models import User
from ..services import audit as audit_svc
from ..services import airflow_client

router = APIRouter(prefix="/api/airflow", tags=["airflow"])


class DagPatchReq(BaseModel):
    is_paused: bool


def _wf_id_of(dag_id: str) -> int:
    """datapilot_wf_<id> -> 平台工作流 id（非工厂 DAG 返回 0）。"""
    if dag_id.startswith("datapilot_wf_"):
        tail = dag_id.rsplit("_", 1)[-1]
        if tail.isdigit():
            return int(tail)
    return 0


@router.get("/status")
def engine_status(user: User = Depends(current_user)):
    """引擎健康度：模式/可达性/版本（airflow 不可达时 reachable=False，不抛错）。"""
    health = airflow_client.health()
    version = airflow_client.version() if health.get("ok") else {}
    return {
        "engine": config.SCHEDULER_ENGINE,
        "reachable": bool(health.get("ok")),
        "error": "" if health.get("ok") else health.get("error", ""),
        "version": version.get("version", "") if version.get("ok") else "",
        "health": {k: v for k, v in health.items() if k != "ok"} if health.get("ok") else {},
    }


@router.get("/dags")
def list_dags(user: User = Depends(current_user)):
    r = airflow_client.dags()
    if not r.get("ok"):
        return {"ok": False, "error": r.get("error", ""), "dags": []}
    dags = []
    for d in r.get("dags", []):
        dag_id = d.get("dag_id", "")
        dags.append({
            "dag_id": dag_id,
            "workflow_id": _wf_id_of(dag_id),
            "is_paused": bool(d.get("is_paused", False)),
            "is_active": bool(d.get("is_active", True)),
            "has_import_errors": bool(d.get("has_import_errors", False)),
            "last_parsed_time": d.get("last_parsed_time") or "",
            "last_schedule_time": d.get("last_schedule_time") or "",
            "next_dagrun": d.get("next_dagrun") or "",
            "description": d.get("description") or "",
            "owners": d.get("owners") or [],
            "tags": [t.get("name", "") for t in (d.get("tags") or [])],
        })
    dags.sort(key=lambda x: x["dag_id"])
    return {"ok": True, "dags": dags}


@router.patch("/dags/{dag_id}")
def patch_dag(dag_id: str, req: DagPatchReq, request: Request,
              user: User = Depends(require_role("engineer")), db: Session = Depends(get_db)):
    r = airflow_client.patch_dag(dag_id, req.is_paused)
    if not r.get("ok"):
        raise HTTPException(502, f"Airflow 调用失败: {r.get('error', '')}")
    audit_svc.log(db, user, "airflow.dag.patch", "dag", 0,
                  {"dag_id": dag_id, "is_paused": req.is_paused}, ip=client_ip(request))
    return {"ok": True}


@router.post("/dags/{dag_id}/trigger")
def trigger(dag_id: str, request: Request,
            user: User = Depends(require_role("engineer")), db: Session = Depends(get_db)):
    r = airflow_client.trigger_dag(dag_id)
    if not r.get("ok"):
        raise HTTPException(502, f"Airflow 调用失败: {r.get('error', '')}")
    audit_svc.log(db, user, "airflow.dag.trigger", "dag", 0,
                  {"dag_id": dag_id}, ip=client_ip(request))
    return {"ok": True, "dag_run_id": r.get("dag_run_id", "")}


@router.get("/dags/{dag_id}/runs")
def list_runs(dag_id: str, limit: int = 25, user: User = Depends(current_user)):
    r = airflow_client.dag_runs(dag_id, limit=limit)
    if not r.get("ok"):
        return {"ok": False, "error": r.get("error", ""), "runs": []}
    runs = r.get("dag_runs", [])
    runs.sort(key=lambda x: x.get("start_date") or x.get("logical_date") or "", reverse=True)
    return {"ok": True, "runs": [{
        "dag_run_id": x.get("dag_run_id", ""),
        "run_type": x.get("run_type") or "",
        "state": x.get("state") or "",
        "logical_date": x.get("logical_date") or "",
        "start_date": x.get("start_date") or "",
        "end_date": x.get("end_date") or "",
        "note": x.get("note") or "",
    } for x in runs]}


@router.get("/dags/{dag_id}/runs/{dag_run_id}/tasks")
def list_task_instances(dag_id: str, dag_run_id: str, user: User = Depends(current_user)):
    r = airflow_client.task_instances(dag_id, dag_run_id)
    if not r.get("ok"):
        return {"ok": False, "error": r.get("error", ""), "tasks": []}
    tasks = r.get("task_instances", [])
    tasks.sort(key=lambda x: x.get("task_id") or "")
    return {"ok": True, "tasks": [{
        "task_id": x.get("task_id", ""),
        "state": x.get("state") or "",
        "try_number": x.get("try_number") or 0,
        "start_date": x.get("start_date") or "",
        "end_date": x.get("end_date") or "",
        "duration": x.get("duration") or 0,
    } for x in tasks]}
