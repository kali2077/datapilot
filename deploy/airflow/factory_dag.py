"""DataPilot 动态 DAG 工厂（配置驱动）。

部署方式：本文件与 workflows/ 目录置于 $AIRFLOW_DAGS_DIR。
平台把每个工作流渲染为 workflows/wf_<id>.json，本工厂扫描后动态生成 DAG。

配置结构（由 DataPilot sched_service._airflow_write_config 生成）:
{
  "workflow_id": 1, "name": "sync_orders", "cron": "0 2 * * *",
  "depends_on": [2],
  "nodes": [{"id": 1, "name": "node1", "type": "datax|sql|python",
             "upstream": [], "config": {...}}]
}

节点执行：回调 DataPilot 平台 API（由平台侧真正执行任务并写实例/日志/血缘），
Airflow 只负责编排与重试视图 —— 与"配置驱动动态 DAG，平台永不写 DAG 文件"的架构一致。
"""
import json
import os
import time

from airflow import DAG
from airflow.operators.python import PythonOperator
from datetime import datetime, timedelta

DAGS_DIR = os.path.dirname(os.path.abspath(__file__))
WORKFLOW_DIR = os.path.join(DAGS_DIR, "workflows")
API_BASE = os.getenv("DATAPILOT_API", "http://datapilot-app:8000")
API_TOKEN = os.getenv("DATAPILOT_TOKEN", "")


def _exec_via_platform(workflow_id: int, **_):
    """回调平台触发工作流实例并轮询完成。"""
    import httpx
    headers = {"Authorization": f"Bearer {API_TOKEN}"} if API_TOKEN else {}
    r = httpx.post(f"{API_BASE}/api/sched/workflows/{workflow_id}/trigger",
                   headers=headers, json={}, timeout=30)
    r.raise_for_status()
    inst_id = r.json()["instance_id"]
    for _ in range(3600):
        time.sleep(2)
        inst = httpx.get(f"{API_BASE}/api/sched/instances/{inst_id}",
                         headers=headers, timeout=15).json()
        if inst.get("status") in ("success", "failed"):
            if inst["status"] != "success":
                raise RuntimeError(f"实例 #{inst_id} 失败: {inst.get('error', '')[:500]}")
            return
    raise TimeoutError(f"实例 #{inst_id} 轮询超时")


def _load_configs():
    if not os.path.isdir(WORKFLOW_DIR):
        return []
    out = []
    for f in os.listdir(WORKFLOW_DIR):
        if f.endswith(".json"):
            try:
                with open(os.path.join(WORKFLOW_DIR, f), encoding="utf-8") as fp:
                    out.append(json.load(fp))
            except Exception:
                continue
    return out


def _build_dag(cfg: dict) -> DAG:
    with DAG(
        dag_id=f"datapilot_wf_{cfg['workflow_id']}",
        description=cfg.get("name", ""),
        schedule=cfg.get("cron") or None,
        start_date=datetime(2026, 1, 1),
        catchup=False,
        max_active_runs=1,
        default_args={"retries": 0, "retry_delay": timedelta(minutes=1)},
        tags=["datapilot"],
    ) as dag:
        tasks = {}
        for node in cfg.get("nodes", []):
            t = PythonOperator(
                task_id=f"{node['id']}_{node['name']}",
                python_callable=_exec_via_platform,
                op_kwargs={"workflow_id": cfg["workflow_id"]},
            )
            tasks[node["id"]] = t
            for up in node.get("upstream", []):
                if up in tasks:
                    tasks[up] >> t
        # 空工作流占位
        if not tasks:
            PythonOperator(task_id="noop", python_callable=lambda: None)
    return dag


for _cfg in _load_configs():
    try:
        globals()[f"datapilot_wf_{_cfg['workflow_id']}"] = _build_dag(_cfg)
    except Exception as e:  # noqa
        print(f"[factory_dag] 生成 {_cfg.get('name')} 失败: {e}")
