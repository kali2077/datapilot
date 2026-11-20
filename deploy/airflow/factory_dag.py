"""DataPilot 动态 DAG 工厂（配置驱动，Airflow 3.x）。

部署方式：本文件与 workflows/ 目录置于 $AIRFLOW_DAGS_DIR。
平台把每个工作流渲染为 workflows/wf_<id>.json，本工厂扫描后动态生成 DAG。

节点执行（节点级回调）：每个 Airflow 任务对应平台工作流的一个节点 ——
回调 POST {DATAPILOT_API}/api/sched/airflow/run 幂等映射平台实例并执行该节点，
随后轮询节点状态到终态；Airflow 任务状态即平台节点状态，
因此 Airflow Grid 与平台「运行矩阵」逐格一致（重试也可按节点）。
平台侧真正执行任务并写实例/日志/血缘，Airflow 只负责编排 ——
与「配置驱动动态 DAG，平台永不写 DAG 文件」的架构一致。
"""
import json
import os
import time
from datetime import datetime, timedelta, timezone

from airflow import DAG
from airflow.operators.python import PythonOperator

DAGS_DIR = os.path.dirname(os.path.abspath(__file__))
WORKFLOW_DIR = os.path.join(DAGS_DIR, "workflows")
API_BASE = os.getenv("DATAPILOT_API", "http://app:8000")
API_TOKEN = os.getenv("DATAPILOT_TOKEN", "")
POLL_INTERVAL = 5
POLL_TIMEOUT = 2 * 60 * 60  # 单节点最长等待 2h

# 配置结构（由 DataPilot sched_service._airflow_write_config 生成）:
# {
#   "workflow_id": 1, "name": "sync_orders", "cron": "0 2 * * *",
#   "enabled": true,   # false 时 DAG 以暂停态创建（防平台 PATCH 暂停前的竞态触发）
#   "depends_on": [2],
#   "nodes": [{"id": 1, "name": "node1", "type": "datax|sql|python",
#              "upstream": [], "config": {...}}]
# }


def _headers():
    h = {"Content-Type": "application/json"}
    if API_TOKEN:
        h["Authorization"] = f"Bearer {API_TOKEN}"
    return h


def _notify_dead_run(workflow_id: int, dag_run_id: str, reason: str):
    """任务即将失败：通知平台把实例收敛为 failed（下游未跑节点记 skipped）。

    平台侧实例不收敛的话，节点级回调模式下下游任务（Airflow upstream_failed）
    永不执行，实例会永远停在 waiting/running。尽力而为，失败不掩盖原始异常。
    """
    if not dag_run_id:
        return
    try:
        import httpx
        httpx.post(f"{API_BASE}/api/sched/airflow/fail", headers=_headers(),
                   json={"workflow_id": workflow_id, "dag_run_id": dag_run_id,
                         "reason": reason}, timeout=30)
    except Exception:
        pass


def _exec_node(workflow_id: int, node_id: int, **context):
    """回调平台执行单节点，轮询到终态；失败 raise 让 Airflow 任务标红。"""
    import httpx
    dag_run = context.get("dag_run")
    dag_run_id = context.get("run_id") or (dag_run.run_id if dag_run else "")
    logical_date = str(context.get("logical_date") or "") if context.get("logical_date") else ""
    body = {"workflow_id": workflow_id, "node_id": node_id,
            "dag_run_id": dag_run_id, "logical_date": logical_date}
    deadline = time.time() + POLL_TIMEOUT
    inst_id = node_run_id = None
    while time.time() < deadline:
        r = httpx.post(f"{API_BASE}/api/sched/airflow/run",
                       headers=_headers(), json=body, timeout=30)
        r.raise_for_status()
        data = r.json()
        if data.get("status") == "waiting":
            time.sleep(POLL_INTERVAL)  # 跨工作流依赖未满足，原地等待（平台 poller 同语义）
            continue
        inst_id, node_run_id = data.get("instance_id"), data.get("node_run_id")
        break
    if not node_run_id:
        _notify_dead_run(workflow_id, dag_run_id, "Airflow 等待跨工作流依赖超时")
        raise TimeoutError("等待跨工作流依赖超时")
    consecutive_errors = 0
    while time.time() < deadline:
        time.sleep(POLL_INTERVAL)
        inst = None
        try:  # 平台瞬时不可达/抖动容忍，但持续失败（重启、token 失效、实例被删）快速暴露
            resp = httpx.get(f"{API_BASE}/api/sched/instances/{inst_id}",
                             headers=_headers(), timeout=15)
            resp.raise_for_status()
            inst = resp.json()
        except Exception:
            pass
        node = None
        if isinstance(inst, dict) and "nodes" in inst:
            consecutive_errors = 0
            node = next((n for n in inst["nodes"] if n.get("id") == node_run_id), None)
        else:
            consecutive_errors += 1
            if consecutive_errors >= 12:  # 连续 1 分钟查询失败
                _notify_dead_run(workflow_id, dag_run_id,
                                 f"平台实例 #{inst_id} 查询连续失败")
                raise RuntimeError(f"平台实例 #{inst_id} 查询连续失败（平台不可达或实例已删除）")
            continue
        if node and node.get("status") in ("success", "failed", "skipped"):
            if node["status"] != "success":
                _notify_dead_run(workflow_id, dag_run_id,
                                 f"节点 {node_id} 执行失败，下游不会执行")
                raise RuntimeError(f"节点 #{node_run_id} 失败: {(node.get('log') or '')[-500:]}")
            return
    _notify_dead_run(workflow_id, dag_run_id, f"平台节点 #{node_run_id} 执行轮询超时")
    raise TimeoutError(f"节点 #{node_run_id} 轮询超时")


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
        start_date=datetime(2026, 1, 1, tzinfo=timezone.utc),  # Airflow 3 要求 tz-aware
        catchup=False,
        max_active_runs=1,
        is_paused_upon_creation=not cfg.get("enabled", True),
        # 重试策略由平台任务配置下发（retries/retry_delay_minutes）；增量 max_value 幂等，重试安全
        default_args={"retries": int(cfg.get("retries", 0) or 0),
                      "retry_delay": timedelta(minutes=int(cfg.get("retry_delay_minutes", 1) or 1))},
        tags=["datapilot"],
    ) as dag:
        tasks = {}
        for node in cfg.get("nodes", []):
            t = PythonOperator(
                task_id=f"{node['id']}_{node['name']}",
                python_callable=_exec_node,
                op_kwargs={"workflow_id": cfg["workflow_id"], "node_id": node["id"]},
            )
            tasks[node["id"]] = t
            tasks[node.get("name")] = t  # 兼容旧配置：upstream 可能存节点名而非 id
            tasks[f"{node['id']}_{node['name']}"] = t
        for node in cfg.get("nodes", []):
            for up in node.get("upstream", []):
                if tasks.get(up) and tasks.get(up) is not tasks.get(node["id"]):
                    tasks[up] >> tasks[node["id"]]
        # 空工作流占位
        if not tasks:
            PythonOperator(task_id="noop", python_callable=lambda: None)
    return dag


for _cfg in _load_configs():
    try:
        globals()[f"datapilot_wf_{_cfg['workflow_id']}"] = _build_dag(_cfg)
    except Exception as e:  # noqa
        print(f"[factory_dag] 生成 {_cfg.get('name')} 失败: {e}")
