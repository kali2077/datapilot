"""调度服务：Local（APScheduler）/ Airflow（REST+factory DAG 配置文件）双适配器。

- apply_schedule: 任务配置 -> 引擎生效（新增/更新/暂停）
- trigger_workflow: 创建实例并执行（上游依赖不满足则 waiting，由 poller 兜底启动）
- Airflow 模式：将工作流配置渲染为 JSON 放入 AIRFLOW_DAGS_DIR/workflows/，
  由 deploy/airflow/factory_dag.py 动态生成 DAG；再通过 REST 触发/启停。
"""
import datetime
import json
import os
import threading
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session

from .. import config
from ..models import Workflow, TaskInstance, DataSource
from . import task_runner

_scheduler: Optional[BackgroundScheduler] = None
_lock = threading.Lock()
_poller_started = False


# ---------------------------------------------------------------- cron 校验

def validate_cron(cron: str) -> (bool, str):
    if not cron or not cron.strip():
        return True, ""
    try:
        CronTrigger.from_crontab(cron.strip())
        return True, ""
    except Exception as e:
        return False, f"cron 表达式非法: {e}"


def next_fire_time(cron: str):
    ok, _ = validate_cron(cron)
    if not ok:
        return None
    try:
        return CronTrigger.from_crontab(cron.strip()).get_next_fire_time(None, datetime.datetime.now())
    except Exception:
        return None


# ---------------------------------------------------------------- Local 引擎

def _get_scheduler() -> BackgroundScheduler:
    global _scheduler
    with _lock:
        if _scheduler is None:
            _scheduler = BackgroundScheduler(timezone="Asia/Shanghai",
                                             job_defaults={"coalesce": True,
                                                           "misfire_grace_time": 3600,
                                                           "max_instances": 1})
            _scheduler.start()
    return _scheduler


def rebuild_jobs(db: Session):
    """启动时从配置库重建全部本地调度（幂等），并启动等待轮询器与元数据定时采集。"""
    _start_poller()
    # 监控规则调度（数据监控告警模块）：任何调度引擎模式下都由平台自身执行
    from . import monitor_service
    monitor_service.register_all(db)
    if config.SCHEDULER_ENGINE != "local":
        return
    sched = _get_scheduler()
    for wf in db.query(Workflow).filter(Workflow.enabled == True).all():  # noqa: E712
        ok, _ = validate_cron(wf.schedule_cron)
        if ok and wf.schedule_cron.strip():
            _register_local(sched, wf.id, wf.schedule_cron)
    # 元数据定时采集（PRD FR-META：新数据源定时可见）；META_COLLECT_HOURS=0 关闭
    hours = int(os.getenv("META_COLLECT_HOURS", "24"))
    if hours > 0:
        job_id = "meta_collect_all"
        if sched.get_job(job_id):
            sched.remove_job(job_id)
        sched.add_job(_collect_all_meta, trigger="interval", hours=hours,
                      id=job_id, name=job_id, next_run_time=datetime.datetime.now())


def _collect_all_meta():
    """定时采集全部数据源元数据（尽力而为，失败不影响平台）。"""
    from ..db import SessionLocal
    from . import meta_service
    db = SessionLocal()
    try:
        for ds in db.query(DataSource).all():
            try:
                meta_service.collect_datasource(db, ds)
            except Exception:
                pass
        meta_service.meta_index.invalidate()
    finally:
        db.close()


def _register_local(sched: BackgroundScheduler, wf_id: int, cron: str):
    job_id = f"wf_{wf_id}"
    trigger = CronTrigger.from_crontab(cron.strip())
    if sched.get_job(job_id):
        sched.remove_job(job_id)
    sched.add_job(_scheduled_fire, trigger=trigger, id=job_id, args=[wf_id],
                  replace_existing=True, name=job_id)


def _scheduled_fire(wf_id: int):
    from ..db import SessionLocal
    db = SessionLocal()
    try:
        wf = db.get(Workflow, wf_id)
        if not wf or not wf.enabled:
            return
        trigger_workflow(db, wf, trigger_type="scheduled", wait_on_deps=True)
    finally:
        db.close()


def _start_poller():
    """每 30s 检查 waiting 实例：依赖满足则启动。"""
    global _poller_started

    def _loop():
        import time
        from ..db import SessionLocal
        while True:
            time.sleep(30)
            db = SessionLocal()
            try:
                waiting = db.query(TaskInstance).filter(TaskInstance.status == "waiting").all()
                for inst in waiting:
                    wf = db.get(Workflow, inst.workflow_id)
                    if not wf or not wf.enabled:
                        continue
                    ok, _ = task_runner._deps_satisfied(db, wf, inst.logical_date)
                    if ok:
                        task_runner.submit_instance(inst.id)
            except Exception:
                pass
            finally:
                db.close()

    with _lock:
        if not _poller_started:
            t = threading.Thread(target=_loop, name="wf-wait-poller", daemon=True)
            t.start()
            _poller_started = True


# ---------------------------------------------------------------- Airflow 适配器

def _airflow_write_config(wf: Workflow, nodes):
    if not config.AIRFLOW_DAGS_DIR:
        raise ValueError("AIRFLOW_DAGS_DIR 未配置")
    out_dir = os.path.join(config.AIRFLOW_DAGS_DIR, "workflows")
    os.makedirs(out_dir, exist_ok=True)
    payload = {
        "workflow_id": wf.id, "name": wf.name, "cron": wf.schedule_cron,
        "depends_on": wf.depends_on or [],
        "nodes": [{"id": n.id, "name": n.name, "type": n.node_type,
                   "upstream": n.upstream or [], "config": n.config} for n in nodes],
    }
    path = os.path.join(out_dir, f"wf_{wf.id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path


def _airflow_api(method: str, path: str, payload: dict = None):
    import httpx
    headers = {"Content-Type": "application/json"}
    if config.AIRFLOW_TOKEN:
        headers["Authorization"] = f"Bearer {config.AIRFLOW_TOKEN}"
    resp = httpx.request(method, f"{config.AIRFLOW_URL.rstrip('/')}{path}",
                         json=payload, headers=headers, timeout=15)
    resp.raise_for_status()
    return resp


def _airflow_dag_id(wf: Workflow) -> str:
    return f"datapilot_wf_{wf.id}"


# ---------------------------------------------------------------- 对外接口

def apply_schedule(db: Session, wf: Workflow):
    """配置变更后调用：让调度引擎与配置一致。"""
    from ..models import WorkflowNode
    if config.SCHEDULER_ENGINE == "airflow":
        nodes = db.query(WorkflowNode).filter(WorkflowNode.workflow_id == wf.id).all()
        _airflow_write_config(wf, nodes)
        try:
            _airflow_api("POST", f"/api/v1/dags/{_airflow_dag_id(wf)}",
                         {"is_paused": not bool(wf.enabled)})
        except Exception:
            pass  # airflow 不可达时不阻塞平台操作
        return
    sched = _get_scheduler()
    _start_poller()
    job_id = f"wf_{wf.id}"
    ok, _ = validate_cron(wf.schedule_cron)
    has_cron = ok and wf.schedule_cron.strip()
    if not wf.enabled or not has_cron:
        if sched.get_job(job_id):
            sched.remove_job(job_id)
        return
    _register_local(sched, wf.id, wf.schedule_cron)


def trigger_workflow(db: Session, wf: Workflow, trigger_type: str = "manual",
                     wait_on_deps: bool = False, triggered_by: str = "") -> TaskInstance:
    inst = TaskInstance(workflow_id=wf.id, workflow_name=wf.name,
                        trigger_type=trigger_type, logical_date=datetime.datetime.now(),
                        status="waiting", triggered_by=triggered_by or trigger_type)
    db.add(inst)
    db.commit()
    if wait_on_deps:
        ok, _ = task_runner._deps_satisfied(db, wf, inst.logical_date)
        if not ok:
            _start_poller()
            return inst
    task_runner.submit_instance(inst.id)
    return inst


def rerun_instance(db: Session, inst: TaskInstance, only_failed: bool = False):
    task_runner.submit_instance(inst.id, rerun_failed=only_failed)


def rerun_node(db: Session, run_id: int):
    """重跑单个节点：仅执行该节点所在实例的该节点。"""
    from ..models import NodeRun
    run = db.get(NodeRun, run_id)
    if not run:
        raise ValueError("节点运行记录不存在")
    task_runner.submit_instance(run.instance_id, only_node=run_id)
