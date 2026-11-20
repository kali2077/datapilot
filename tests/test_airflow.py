"""Airflow 3.x 集成测试：/api/v2 客户端与代理路由、节点级回调（factory DAG 回调）、运行矩阵。

不依赖真实 Airflow：外部调用全部 monkeypatch；节点级回调走平台自身 HTTP 端点（模拟 factory 任务）。
"""
import json
import time
import types

import pytest

from conftest import H, wait_instance

from app import config
from app.services import airflow_client


@pytest.fixture(autouse=True)
def _reset_token_cache():
    airflow_client.reset_token_cache()
    yield
    airflow_client.reset_token_cache()


# ---------------------------------------------------------------- 工具

def _mk_wf(client, tok, name, nodes, depends_on=None, cron="0 2 * * *", enabled=True):
    payload = {"name": name, "schedule_cron": cron, "enabled": enabled,
               "depends_on": depends_on or [], "nodes": nodes}
    r = client.post("/api/sched/workflows", headers=H(tok), json=payload)
    assert r.status_code == 200, r.text
    return r.json()


def _wf_nodes(client, tok, wf_id):
    r = client.get(f"/api/sched/workflows/{wf_id}", headers=H(tok))
    assert r.status_code == 200, r.text
    return r.json()["nodes"]


def _af_run(client, tok, wf_id, node_id, dag_run_id, logical_date="2026-11-15T03:00:00"):
    return client.post("/api/sched/airflow/run", headers=H(tok),
                       json={"workflow_id": wf_id, "node_id": node_id,
                             "dag_run_id": dag_run_id, "logical_date": logical_date})


# ---------------------------------------------------------------- 节点级回调 e2e

def test_airflow_node_run_success_and_idempotent(client, tokens):
    """两个节点各自回调（模拟 Airflow 按 DAG 顺序执行）→ 实例 success，且同一 dag_run_id 幂等复用。"""
    tok = tokens["admin"]
    wf = _mk_wf(client, tok, "af-node-ok", [
        {"name": "n1", "node_type": "python", "config": {"script": "print('af-n1')"}, "upstream": []},
        {"name": "n2", "node_type": "python", "config": {"script": "print('af-n2')"}, "upstream": []},
    ])
    nodes = _wf_nodes(client, tok, wf["id"])
    run_id = f"scheduled__datapilot_wf_{wf['id']}__2026-11-15T03:00:00+00:00"
    inst_ids = set()
    for n in nodes:
        r = _af_run(client, tok, wf["id"], n["id"], run_id)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "submitted"
        inst_ids.add(r.json()["instance_id"])
    assert len(inst_ids) == 1  # 同一 dag_run_id 复用实例，不重复建
    inst = wait_instance(client, tok, inst_ids.pop(), timeout=30)
    assert inst["status"] == "success", inst.get("error")
    assert all(nd["status"] == "success" for nd in inst["nodes"])
    assert inst["triggered_by"] == "airflow"
    # 再次回调同节点（Airflow 重试语义）→ 仍复用同一实例
    r = _af_run(client, tok, wf["id"], nodes[0]["id"], run_id)
    assert r.json()["instance_id"] == inst["id"]


def test_airflow_node_run_failure(client, tokens):
    """节点失败 → 实例 failed（由全部 NodeRun 汇总），错误带失败日志。"""
    tok = tokens["admin"]
    wf = _mk_wf(client, tok, "af-node-fail", [
        {"name": "bad", "node_type": "python",
         "config": {"script": "raise RuntimeError('af-boom')"}, "upstream": []},
    ])
    node = _wf_nodes(client, tok, wf["id"])[0]
    r = _af_run(client, tok, wf["id"], node["id"], "manual__af_fail__01")
    assert r.status_code == 200
    inst = wait_instance(client, tok, r.json()["instance_id"], timeout=30)
    assert inst["status"] == "failed"
    assert "af-boom" in (inst.get("error") or "")
    assert inst["nodes"][0]["status"] == "failed"


def test_airflow_fail_converges_running_instance(client, tokens):
    """节点失败 + fail 回调：未执行的下游节点记 skipped，实例收敛 failed（不卡 running）。"""
    tok = tokens["admin"]
    wf = _mk_wf(client, tok, "af-fail-conv", [
        {"name": "fa", "node_type": "python",
         "config": {"script": "raise RuntimeError('conv-boom')"}, "upstream": []},
        {"name": "fb", "node_type": "python", "config": {"script": "print('b')"}, "upstream": []},
    ])
    nodes = _wf_nodes(client, tok, wf["id"])
    fa = next(n for n in nodes if n["name"] == "fa")
    run_id = "manual__af_fail_conv__01"
    r = _af_run(client, tok, wf["id"], fa["id"], run_id)
    inst_id = r.json()["instance_id"]
    # 等 fa 执行完（此时 fb 未回调，实例应为 running——正是会卡住的中间态）
    for _ in range(30):
        inst = client.get(f"/api/sched/instances/{inst_id}", headers=H(tok)).json()
        fa_run = next((n for n in inst["nodes"] if n["node_id"] == fa["id"]), None)
        if fa_run and fa_run["status"] == "failed":
            break
        time.sleep(0.5)
    assert fa_run and fa_run["status"] == "failed"
    # factory 任务失败回调收敛端点
    r = client.post("/api/sched/airflow/fail", headers=H(tok),
                    json={"workflow_id": wf["id"], "dag_run_id": run_id,
                          "reason": "节点执行失败，下游不会执行"})
    assert r.status_code == 200 and r.json()["status"] == "failed"
    inst = client.get(f"/api/sched/instances/{inst_id}", headers=H(tok)).json()
    assert inst["status"] == "failed"
    st = {n["name"]: n["status"] for n in inst["nodes"]}
    assert st == {"fa": "failed", "fb": "skipped"}
    assert "下游不会执行" in (inst.get("error") or "")  # fail 回调的收敛原因
    fa_log = next(n for n in inst["nodes"] if n["node_id"] == fa["id"])["log"]
    assert "conv-boom" in fa_log  # 失败根因在节点日志
    # 幂等：重复回调不再改已终态实例
    r = client.post("/api/sched/airflow/fail", headers=H(tok),
                    json={"workflow_id": wf["id"], "dag_run_id": run_id, "reason": "again"})
    assert r.json()["status"] == "failed"


def test_airflow_fail_on_waiting_instance(client, tokens):
    """依赖等待超时回调：waiting 实例收敛 failed，未执行节点记 skipped。"""
    tok = tokens["admin"]
    up = _mk_wf(client, tok, "af-failwait-up", [
        {"name": "u", "node_type": "python", "config": {"script": "print('up')"}, "upstream": []},
    ], enabled=False)
    down = _mk_wf(client, tok, "af-failwait-down", [
        {"name": "d", "node_type": "python", "config": {"script": "print('d')"}, "upstream": []},
    ], depends_on=[up["id"]])
    node = _wf_nodes(client, tok, down["id"])[0]
    r = _af_run(client, tok, down["id"], node["id"], "scheduled__af_failwait__01")
    assert r.json()["status"] == "waiting"
    inst_id = r.json()["instance_id"]
    r = client.post("/api/sched/airflow/fail", headers=H(tok),
                    json={"workflow_id": down["id"], "dag_run_id": "scheduled__af_failwait__01",
                          "reason": "Airflow 等待跨工作流依赖超时"})
    assert r.status_code == 200 and r.json()["status"] == "failed"
    inst = client.get(f"/api/sched/instances/{inst_id}", headers=H(tok)).json()
    assert inst["status"] == "failed"
    assert inst["nodes"][0]["status"] == "skipped"


def test_airflow_run_waiting_on_cross_deps(client, tokens):
    """跨工作流依赖未满足 → 返回 waiting（factory 任务原地等待），不执行。"""
    tok = tokens["admin"]
    up = _mk_wf(client, tok, "af-wait-up", [
        {"name": "u", "node_type": "python", "config": {"script": "print('up')"}, "upstream": []},
    ], enabled=False)  # 上游从未成功
    down = _mk_wf(client, tok, "af-wait-down", [
        {"name": "d", "node_type": "python", "config": {"script": "print('down')"}, "upstream": []},
    ], depends_on=[up["id"]])
    node = _wf_nodes(client, tok, down["id"])[0]
    r = _af_run(client, tok, down["id"], node["id"], "scheduled__af_wait__01")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "waiting" and body["instance_id"]
    inst = client.get(f"/api/sched/instances/{body['instance_id']}", headers=H(tok)).json()
    assert inst["status"] == "waiting"  # 未被本地 poller 抢跑（external_run_id 非空）


# ---------------------------------------------------------------- 运行矩阵

def test_matrix_shape(client, tokens):
    tok = tokens["admin"]
    wf = _mk_wf(client, tok, "af-matrix", [
        {"name": "m1", "node_type": "python", "config": {"script": "print(1)"}, "upstream": []},
        {"name": "m2", "node_type": "python", "config": {"script": "print(2)"}, "upstream": []},
    ])
    nodes = _wf_nodes(client, tok, wf["id"])
    for n in nodes:
        _af_run(client, tok, wf["id"], n["id"], "scheduled__af_matrix__01")
    r = client.get(f"/api/sched/instances", headers=H(tok)).json()
    inst_id = max(i["id"] for i in r["items"] if i["workflow_id"] == wf["id"])
    wait_instance(client, tok, inst_id, timeout=30)
    r = client.get(f"/api/sched/matrix", headers=H(tok),
                   params={"workflow_id": wf["id"], "size": 10})
    assert r.status_code == 200
    m = r.json()
    assert m["workflow"]["id"] == wf["id"]
    assert [n["name"] for n in m["nodes"]] == ["m1", "m2"]
    assert m["rows"] and m["rows"][0]["instance_id"] == inst_id  # 最新在前
    cells = m["rows"][0]["cells"]
    # JSON 序列化会把 int 键转为字符串（前端 JS 数字属性访问等价）
    assert set(cells) == {str(n["id"]) for n in m["nodes"]}
    assert all(c["status"] == "success" for c in cells.values())
    # 404
    r = client.get("/api/sched/matrix", headers=H(tok), params={"workflow_id": 99999})
    assert r.status_code == 404


# ---------------------------------------------------------------- /api/airflow 代理

def _fake_request(routes):
    """按 path 返回固定响应的 request 替身；未匹配路径返回 ok=False。"""
    def fake(method, path, params=None, json_body=None, timeout=None):
        for p, resp in routes.items():
            if path.startswith(p):
                out = dict(resp)
                out.setdefault("ok", True)
                return out
        return {"ok": False, "error": f"no route for {path}"}
    return fake


def test_proxy_status_and_dags(client, tokens, monkeypatch):
    monkeypatch.setattr(airflow_client, "request", _fake_request({
        "/api/v2/monitor/health": {"metadatabase": {"status": "healthy"}},
        "/api/v2/version": {"version": "3.4.0"},
        "/api/v2/dags": {"dags": [
            {"dag_id": "datapilot_wf_7", "is_paused": True, "is_active": True,
             "has_import_errors": False, "last_parsed_time": "2026-11-15T03:00:00+00:00",
             "tags": [{"name": "datapilot"}]},
            {"dag_id": "other_dag", "is_paused": False, "has_import_errors": True},
        ]},
    }))
    h = H(tokens["viewer"])
    r = client.get("/api/airflow/status", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["reachable"] is True and body["version"] == "3.4.0"
    assert body["engine"] == config.SCHEDULER_ENGINE
    r = client.get("/api/airflow/dags", headers=h)
    dags = {d["dag_id"]: d for d in r.json()["dags"]}
    assert dags["datapilot_wf_7"]["workflow_id"] == 7
    assert dags["datapilot_wf_7"]["is_paused"] is True
    assert dags["other_dag"]["workflow_id"] == 0
    assert dags["other_dag"]["has_import_errors"] is True


def test_proxy_unreachable_no_500(client, tokens, monkeypatch):
    def dead(*a, **kw):
        return {"ok": False, "error": "connect refused"}
    monkeypatch.setattr(airflow_client, "request", dead)
    r = client.get("/api/airflow/status", headers=H(tokens["engineer"]))
    assert r.status_code == 200
    body = r.json()
    assert body["reachable"] is False and "connect refused" in body["error"]
    r = client.get("/api/airflow/dags", headers=H(tokens["engineer"]))
    assert r.status_code == 200 and r.json()["ok"] is False
    # 写操作 → 502 明确报错
    r = client.patch("/api/airflow/dags/datapilot_wf_1", headers=H(tokens["engineer"]),
                     json={"is_paused": True})
    assert r.status_code == 502


def test_proxy_patch_permissions_and_ok(client, tokens, monkeypatch):
    calls = []

    def fake(method, path, params=None, json_body=None, timeout=None):
        calls.append((method, path, params, json_body))
        return {"ok": True}
    monkeypatch.setattr(airflow_client, "request", fake)
    # viewer 无权写
    r = client.patch("/api/airflow/dags/datapilot_wf_1", headers=H(tokens["viewer"]),
                     json={"is_paused": True})
    assert r.status_code == 403
    # engineer 走 v2 PATCH + update_mask
    r = client.patch("/api/airflow/dags/datapilot_wf_1", headers=H(tokens["engineer"]),
                     json={"is_paused": False})
    assert r.status_code == 200
    method, path, params, body = calls[-1]
    assert method == "PATCH" and path == "/api/v2/dags/datapilot_wf_1"
    assert params == {"update_mask": "is_paused"} and body == {"is_paused": False}
    # 引擎侧触发
    r = client.post("/api/airflow/dags/datapilot_wf_1/trigger", headers=H(tokens["engineer"]))
    assert r.status_code == 200
    assert calls[-1][0] == "POST" and calls[-1][1].endswith("/dagRuns")


def test_proxy_runs_and_tasks_normalization(client, tokens, monkeypatch):
    monkeypatch.setattr(airflow_client, "request", _fake_request({
        "/api/v2/dags/wf/dagRuns/": {"task_instances": [
            {"task_id": "b", "state": "success", "try_number": 1},
            {"task_id": "a", "state": "failed", "try_number": 2},
        ]},
        "/api/v2/dags/wf/dagRuns": {"dag_runs": [
            {"dag_run_id": "r2", "state": "success", "start_date": "2026-11-15T05:00:00+00:00",
             "logical_date": "2026-11-15T04:00:00+00:00"},
            {"dag_run_id": "r1", "state": "failed", "start_date": "2026-11-15T03:00:00+00:00",
             "logical_date": "2026-11-15T02:00:00+00:00"},
        ]},
    }))
    h = H(tokens["viewer"])
    r = client.get("/api/airflow/dags/wf/runs", headers=h)
    runs = r.json()["runs"]
    assert [x["dag_run_id"] for x in runs] == ["r2", "r1"]  # 按开始时间倒序
    r = client.get("/api/airflow/dags/wf/runs/r2/tasks", headers=h)
    tasks = r.json()["tasks"]
    assert [t["task_id"] for t in tasks] == ["a", "b"]
    assert tasks[0]["state"] == "failed" and tasks[0]["try_number"] == 2


# ---------------------------------------------------------------- token 取用

def test_token_bootstrap_and_cache(monkeypatch, tmp_path):
    calls = []

    class Resp:
        status_code = 200
        def raise_for_status(self):
            pass
        def json(self):
            return {"access_token": "tok-abc"}

    def fake_post(url, json=None, timeout=None):
        calls.append(url)
        return Resp()

    monkeypatch.setattr(config, "AIRFLOW_TOKEN", "")
    monkeypatch.setattr(config, "AIRFLOW_TOKEN_FILE", "")
    monkeypatch.setattr(config, "AIRFLOW_USER", "admin")
    monkeypatch.setattr(config, "AIRFLOW_PASSWORD", "pw")
    monkeypatch.setattr(airflow_client, "httpx", types.SimpleNamespace(post=fake_post))

    assert airflow_client.get_token() == "tok-abc"
    airflow_client.get_token()  # 命中缓存
    assert len(calls) == 1
    airflow_client.get_token(force=True)  # 强制重签
    assert len(calls) == 2

    # token 文件优先于 user/password（不再发起请求）
    f = tmp_path / "airflow_token.txt"
    f.write_text("tok-from-file")
    monkeypatch.setattr(config, "AIRFLOW_TOKEN_FILE", str(f))
    assert airflow_client.get_token(force=True) == "tok-from-file"
    assert len(calls) == 2
    # AIRFLOW_TOKEN 直填最高优先
    monkeypatch.setattr(config, "AIRFLOW_TOKEN", "tok-static")
    assert airflow_client.get_token() == "tok-static"


def test_request_without_auth_config(monkeypatch):
    monkeypatch.setattr(config, "AIRFLOW_TOKEN", "")
    monkeypatch.setattr(config, "AIRFLOW_TOKEN_FILE", "")
    monkeypatch.setattr(config, "AIRFLOW_USER", "")
    monkeypatch.setattr(config, "AIRFLOW_PASSWORD", "")
    out = airflow_client.request("GET", "/api/v2/dags")
    assert out["ok"] is False and "未配置认证" in out["error"]


# ---------------------------------------------------------------- apply_schedule（airflow 引擎分支）

def test_apply_schedule_airflow_branch(client, tokens, monkeypatch, tmp_path):
    """airflow 模式：工作流渲染 JSON 落盘 + 走 v2 PATCH 暂停恢复。"""
    monkeypatch.setattr(config, "SCHEDULER_ENGINE", "airflow")
    monkeypatch.setattr(config, "AIRFLOW_DAGS_DIR", str(tmp_path))
    patched = []

    def fake_patch(dag_id, is_paused):
        patched.append((dag_id, is_paused))
        return {"ok": True}
    from app.services import sched_service
    monkeypatch.setattr(airflow_client, "patch_dag", fake_patch)

    wf = _mk_wf(client, tokens["engineer"], "af-apply", [
        {"name": "a", "node_type": "python", "config": {"script": "print(1)"}, "upstream": []},
    ], cron="0 5 * * *")
    cfg_path = tmp_path / "workflows" / f"wf_{wf['id']}.json"
    assert cfg_path.exists()
    cfg = json.loads(cfg_path.read_text())
    assert cfg["cron"] == "0 5 * * *" and cfg["nodes"][0]["name"] == "a"
    assert patched == [(f"datapilot_wf_{wf['id']}", False)]  # 启用 → 未暂停
    # 暂停 → PATCH is_paused=True
    r = client.put(f"/api/sched/workflows/{wf['id']}", headers=H(tokens["engineer"]),
                   json={"enabled": False})
    assert r.status_code == 200
    assert patched[-1] == (f"datapilot_wf_{wf['id']}", True)


def test_factory_config_json_schema(client, tokens, monkeypatch, tmp_path):
    """渲染出的 JSON 与 factory_dag 期望的结构一致（节点 id/name/type/upstream/config）。"""
    monkeypatch.setattr(config, "SCHEDULER_ENGINE", "airflow")
    monkeypatch.setattr(config, "AIRFLOW_DAGS_DIR", str(tmp_path))
    monkeypatch.setattr(airflow_client, "patch_dag", lambda *a: {"ok": True})
    wf = _mk_wf(client, tokens["engineer"], "af-schema", [
        {"name": "s1", "node_type": "python", "config": {"script": "print(1)"}, "upstream": []},
    ])
    cfg = json.loads((tmp_path / "workflows" / f"wf_{wf['id']}.json").read_text())
    assert set(cfg) >= {"workflow_id", "name", "cron", "depends_on", "nodes"}
    assert cfg["enabled"] is True  # factory 以 is_paused_upon_creation 消费此字段
    node = cfg["nodes"][0]
    assert set(node) >= {"id", "name", "type", "upstream", "config"}


def test_delete_workflow_removes_airflow_config(client, tokens, monkeypatch, tmp_path):
    """airflow 模式删除工作流 → factory 配置文件同步移除（防孤儿 DAG）。"""
    monkeypatch.setattr(config, "SCHEDULER_ENGINE", "airflow")
    monkeypatch.setattr(config, "AIRFLOW_DAGS_DIR", str(tmp_path))
    monkeypatch.setattr(airflow_client, "patch_dag", lambda *a: {"ok": True})
    wf = _mk_wf(client, tokens["engineer"], "af-del", [
        {"name": "d1", "node_type": "python", "config": {"script": "print(1)"}, "upstream": []},
    ])
    cfg_path = tmp_path / "workflows" / f"wf_{wf['id']}.json"
    assert cfg_path.exists()
    r = client.delete(f"/api/sched/workflows/{wf['id']}?force=true", headers=H(tokens["engineer"]))
    assert r.status_code == 200, r.text
    assert not cfg_path.exists()
