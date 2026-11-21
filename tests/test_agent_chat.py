"""AI 对话 e2e 测试（M1/M2 核心链路）。"""
from conftest import H, make_sqlite_ds, wait_instance

ORDERS_DDL = "CREATE TABLE orders (id INTEGER PRIMARY KEY, user_name TEXT, amount REAL, dt TEXT, is_test INTEGER)"


def _rows():
    return [(i, f"用户{i % 6}", i * 10.0, f"2026-10-{(i % 20) + 1:02d}", 1 if i % 9 == 0 else 0)
            for i in range(1, 41)]


def test_chat_query_data(client, tokens):
    make_sqlite_ds(client, tokens["engineer"], "c_src", {ORDERS_DDL: _rows()})
    h = H(tokens["analyst"])
    r = client.post("/api/chat/sessions", headers=h)
    sid = r.json()["id"]
    r = client.post(f"/api/chat/sessions/{sid}/send", headers=h,
                    json={"text": "查 c_src 的 orders 前5行"})
    assert r.status_code == 200, r.text
    body = r.json()
    card = body["card"]
    assert card["type"] == "table"
    assert len(card["rows"]) == 5
    assert card["sql"].upper().startswith("SELECT")


def test_chat_create_sync_and_confirm(client, tokens):
    src = make_sqlite_ds(client, tokens["engineer"], "c_src2", {ORDERS_DDL: _rows()})
    dst = make_sqlite_ds(client, tokens["engineer"], "c_dst2",
                         {"CREATE TABLE marker (x INTEGER)": [(1,)]})
    h = H(tokens["engineer"])
    r = client.post("/api/chat/sessions", headers=h)
    sid = r.json()["id"]
    r = client.post(f"/api/chat/sessions/{sid}/send", headers=h,
                    json={"text": "把 c_src2 的 orders 表每天凌晨2点同步到 c_dst2 的 ods_orders"})
    body = r.json()
    assert body["card"]["type"] == "confirm", body["content"]
    # 试跑为校验模式（确认前不写目标表）：只读源验证
    assert body["card"]["trial"]["stats"]["read_rows"] == 40
    assert body["card"]["trial"]["stats"]["written_rows"] == 0
    assert body["confirm_token"]    # 确认上线
    r = client.post(f"/api/chat/sessions/{sid}/confirm", headers=h,
                    json={"message_id": body["message_id"], "token": body["confirm_token"]})
    assert r.status_code == 200, r.text
    assert "已上线" in r.json()["summary"]
    # 工作流存在且启用
    r = client.get("/api/sched/workflows", headers=h)
    assert any(w["name"].endswith("ods_orders") and w["enabled"] for w in r.json())


def test_chat_create_etl_and_run(client, tokens):
    src = make_sqlite_ds(client, tokens["engineer"], "c_src3", {ORDERS_DDL: _rows()})
    dst = make_sqlite_ds(client, tokens["engineer"], "c_dst3",
                         {"CREATE TABLE ods_base (id INTEGER)": [(1,)]})
    h = H(tokens["engineer"])
    r = client.post("/api/chat/sessions", headers=h)
    sid = r.json()["id"]
    r = client.post(f"/api/chat/sessions/{sid}/send", headers=h,
                    json={"text": "基于 ods_base 做 dwd 明细，剔除测试账号，每天凌晨3点跑"})
    body = r.json()
    assert body["card"]["type"] == "confirm", body["content"]
    assert "EXPLAIN" in body["content"] or "校验通过" in body["content"]
    r = client.post(f"/api/chat/sessions/{sid}/confirm", headers=h,
                    json={"message_id": body["message_id"], "token": body["confirm_token"]})
    assert "已上线" in r.json()["summary"]
    # 触发该 ETL
    r = client.get("/api/sched/workflows", headers=h)
    wf = next(w for w in r.json() if w["name"].startswith("etl_"))
    r = client.post(f"/api/sched/workflows/{wf['id']}/trigger", headers=h)
    inst = wait_instance(client, tokens["engineer"], r.json()["instance_id"])
    assert inst["status"] == "success", inst.get("nodes")


def test_chat_permission_denied_for_analyst(client, tokens):
    make_sqlite_ds(client, tokens["engineer"], "c_src4", {ORDERS_DDL: _rows()})
    make_sqlite_ds(client, tokens["engineer"], "c_dst4",
                   {"CREATE TABLE m (x INTEGER)": [(1,)]})
    h = H(tokens["analyst"])
    r = client.post("/api/chat/sessions", headers=h)
    sid = r.json()["id"]
    r = client.post(f"/api/chat/sessions/{sid}/send", headers=h,
                    json={"text": "把 c_src4 的 orders 表同步到 c_dst4 的 ods_orders"})
    body = r.json()
    # 工具应被拒绝（错误信息而不是上线）
    assert body.get("error") or "权限不足" in body["content"]


def test_chat_search_metadata(client, tokens):
    h = H(tokens["viewer"])
    r = client.post("/api/chat/sessions", headers=h)
    sid = r.json()["id"]
    r = client.post(f"/api/chat/sessions/{sid}/send", headers=h,
                    json={"text": "找订单相关的表"})
    body = r.json()
    assert body["card"]["type"] == "tables"
    assert len(body["card"]["tables"]) >= 1


def _parse_sse(text: str):
    """解析 SSE body → 事件列表。"""
    events = []
    for part in text.split("\n\n"):
        for line in part.split("\n"):
            if line.startswith("data: "):
                import json
                events.append(json.loads(line[6:]))
    return events


def test_chat_send_stream_sse(client, tokens):
    """流式端点：SSE 事件序列完整（stage/tool_end/done），落库结果与同步版一致。"""
    make_sqlite_ds(client, tokens["engineer"], "c_stream", {ORDERS_DDL: _rows()})
    h = H(tokens["analyst"])
    r = client.post("/api/chat/sessions", headers=h)
    sid = r.json()["id"]
    r = client.post(f"/api/chat/sessions/{sid}/send_stream", headers=h,
                    json={"text": "查 c_stream 的 orders 前3行"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(r.text)
    types = [e["type"] for e in events]
    assert types[0] == "stage"                      # 上下文检索提示
    assert "stage" in types[1:-1] or "tool_begin" in types  # 思考轮/工具事件
    assert any(e["type"] == "tool_begin" and e["name"] == "query_data" for e in events)
    assert any(e["type"] == "tool_end" and e["name"] == "query_data" for e in events)
    done = events[-1]
    assert done["type"] == "done" and done["message_id"]
    assert done["card"]["type"] == "table" and len(done["card"]["rows"]) == 3
    # 落库校验：消息与同步版一致（用户+助手各一条，助手带卡片）
    r = client.get(f"/api/chat/sessions/{sid}/messages", headers=h)
    msgs = r.json()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[-1]["card"]["type"] == "table"


def test_chat_send_stream_errors(client, tokens):
    """流式端点前置校验：会话不存在 / 空文本返回 HTTP 错误而非事件流。"""
    h = H(tokens["analyst"])
    r = client.post(f"/api/chat/sessions/99999/send_stream", headers=h,
                    json={"text": "hi"})
    assert r.status_code == 404
    r = client.post("/api/chat/sessions", headers=h)
    sid = r.json()["id"]
    r = client.post(f"/api/chat/sessions/{sid}/send_stream", headers=h,
                    json={"text": "   "})
    assert r.status_code == 400


def test_openai_stream_chat_parses_sse(monkeypatch):
    """OpenAICompatLLM.stream_chat：SSE 增量解析 + 流式 tool_calls 分片聚合。"""
    import httpx
    from app.services.llm import OpenAICompatLLM
    lines = [
        'data: {"choices":[{"delta":{"content":"你"}}]}',
        '',
        'data: {"choices":[{"delta":{"content":"好"}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1",'
        '"function":{"name":"query_data","arguments":"{\\"datas"}}]}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
        '"function":{"arguments":"ource\\":\\"db\\"}"}}]}}]}',
        'data: [DONE]',
    ]

    class _FakeResp:
        def raise_for_status(self): pass
        def iter_lines(self): return iter(lines)
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(httpx, "post", lambda url, **kw: _FakeResp())
    llm = OpenAICompatLLM("http://x", "k", "m")
    deltas, result = [], None
    for ev in llm.stream_chat([{"role": "user", "content": "hi"}], []):
        if ev["type"] == "delta":
            deltas.append(ev["text"])
        else:
            result = ev["result"]
    assert "".join(deltas) == "你好"
    assert result.content == "你好"
    assert result.tool_calls[0].name == "query_data"
    assert result.tool_calls[0].arguments == {"datasource": "db"}


def test_chat_stream_with_streaming_llm(client, tokens, monkeypatch):
    """接入流式 provider 后：delta 事件逐段透传，工具链路与落库不变。"""
    from app.services import llm as llm_mod
    from app.services.llm import ChatResult, ToolCall

    class _FakeStreamLLM:
        def __init__(self): self.round = 0
        def stream_chat(self, messages, tools):
            self.round += 1
            if self.round == 1:
                yield {"type": "delta", "text": "正在查询…"}
                yield {"type": "result", "result": ChatResult(tool_calls=[
                    ToolCall("t1", "query_data",
                             {"datasource": "c_stream2", "sql": "SELECT * FROM orders LIMIT 2"})])}
                return
            yield {"type": "delta", "text": "查询完成"}
            yield {"type": "result", "result": ChatResult(content="查询完成，共 2 行。")}

    monkeypatch.setattr(llm_mod, "get_llm", lambda: _FakeStreamLLM())
    make_sqlite_ds(client, tokens["engineer"], "c_stream2", {ORDERS_DDL: _rows()})
    h = H(tokens["analyst"])
    sid = client.post("/api/chat/sessions", headers=h).json()["id"]
    r = client.post(f"/api/chat/sessions/{sid}/send_stream", headers=h,
                    json={"text": "查 c_stream2 的 orders 前2行"})
    events = _parse_sse(r.text)
    types = [e["type"] for e in events]
    assert "delta" in types
    assert [t for t in types if t == "delta"] == ["delta"] * 2  # 两轮各一段增量
    done = events[-1]
    assert done["type"] == "done"
    assert done["content"] == "查询完成，共 2 行。"
    assert done["card"]["type"] == "table" and len(done["card"]["rows"]) == 2
    assert done["card"]["sql"].upper().startswith("SELECT")


# ---------------- Agent 工具补齐（P2）：TOOL_MAP 直调 + 确认执行 ----------------

def _tool(tok_key, **kwargs):
    """以指定角色直调工具 handler（角色门控在 chat_stream 层，此处测工具本体）。"""
    from app.db import SessionLocal
    from app.models import User
    from app.services import agent
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == tok_key).first()
        spec = agent.TOOL_MAP[kwargs.pop("_name")]
        return spec["handler"](db, user, 0, **kwargs)
    finally:
        db.close()


def test_agent_create_monitor_rule(client, tokens):
    from app.db import SessionLocal
    from app.models import MonitorRule, User
    src = make_sqlite_ds(client, tokens["engineer"], "ag_mon_src",
                         {"CREATE TABLE orders (id INTEGER PRIMARY KEY, amt REAL)": [(1, 1.0)]})
    ds_name = src["name"]
    # 确认卡：datasource 名进 payload，datasource_id=-1 占位
    out = _tool("engineer", _name="create_monitor_rule", name="ag-断流", rule_type="sync_halt",
                params={"workflow_name": "wf_demo", "min_rows": 1}, cron="0 7 * * *")
    assert out["card"]["type"] == "confirm" and out["confirm_token"]
    assert out["card"]["payload"]["datasource_id"] == 0
    # cron 非法
    out = _tool("engineer", _name="create_monitor_rule", name="ag-断流2", rule_type="sync_halt",
                params={"workflow_name": "wf_demo"}, cron="bad")
    assert out.get("error")
    # viewer 权限被 chat_stream 拦（此处直接验证 _tool_allowed）
    from app.services.agent import _tool_allowed
    db = SessionLocal()
    try:
        viewer = db.query(User).filter(User.username == "viewer").first()
        assert not _tool_allowed(viewer, "engineer")
    finally:
        db.close()
    # 确认执行：ds 名解析为 id，规则创建 + 调度注册
    from app.services.agent import execute_confirmation
    db = SessionLocal()
    try:
        eng = db.query(User).filter(User.username == "engineer").first()
        payload = {"name": "ag-断流", "rule_type": "sync_halt", "datasource_id": -1,
                   "datasource_name": ds_name, "table_name": "", "column_name": "",
                   "params": {"workflow_name": "wf_demo", "min_rows": 1},
                   "cron": "0 7 * * *", "severity": "warning"}
        res = execute_confirmation(db, eng, "create_monitor_rule", payload)
        assert "已创建" in res["summary"]
        rule = db.query(MonitorRule).filter(MonitorRule.name == "ag-断流").first()
        assert rule and rule.datasource_id == 0 and rule.enabled
        assert rule.group_name == "AI 助手"
    finally:
        db.close()
    client.delete(f"/api/monitor/rules/{rule.id}", headers=H(tokens["admin"]))


def test_agent_compare_trend_changes_reset(client, tokens):
    """compare_data / job_trend / list_asset_changes / reset_target_table 四工具 + 确认执行。"""
    import sqlite3
    src = make_sqlite_ds(client, tokens["engineer"], "ag_cmp_src",
                         {"CREATE TABLE orders (id INTEGER PRIMARY KEY, amt REAL)":
                          [(i, i * 1.0) for i in range(1, 11)]})
    dst = make_sqlite_ds(client, tokens["engineer"], "ag_cmp_dst",
                         {"CREATE TABLE m (x INTEGER)": [(1,)]})
    h = H(tokens["engineer"])
    r = client.post("/api/integ/jobs", headers=h, json={
        "name": "ag_cmp_job", "source_datasource_id": src["id"], "source_table": "orders",
        "target_datasource_id": dst["id"], "target_table": "ods_ag",
        "config": {"write_mode": "append", "upsert_keys": ["id"]}})
    job_id = r.json()["id"]
    r = client.post(f"/api/integ/jobs/{job_id}/publish", headers=h, json={"cron": ""})
    assert r.status_code == 200, r.text
    wf_id = r.json()["workflow_id"]
    r = client.post(f"/api/sched/workflows/{wf_id}/trigger", headers=h)
    wait_instance(client, tokens["engineer"], r.json()["instance_id"])

    # compare_data：一致
    out = _tool("engineer", _name="compare_data", job_name="ag_cmp_job", mode="chunk")
    assert "✅ 源目一致" in out["summary"] and out["card"]["report"]["consistent"]
    # 目标删 3 行 → 差异
    conn = sqlite3.connect(dst["params"]["path"])
    conn.execute("DELETE FROM ods_ag WHERE id > 7")
    conn.commit()
    conn.close()
    out = _tool("engineer", _name="compare_data", job_name="ag_cmp_job", mode="chunk")
    assert "⚠️ 存在差异" in out["summary"]
    assert sum(c["source"] - c["target"] for c in out["card"]["report"]["chunk_diffs"]) == 3

    # job_trend：趋势 + 行数
    out = _tool("viewer", _name="job_trend", job_name="ag_cmp_job")
    assert "最近" in out["summary"] and out["card"]["items"][-1]["rows_written"] >= 7

    # list_asset_changes：先造一条变更（源加列再采集）
    conn = sqlite3.connect(src["params"]["path"])
    conn.execute("ALTER TABLE orders ADD COLUMN region TEXT DEFAULT 'cn'")
    conn.commit()
    conn.close()
    client.post(f"/api/datasources/{src['id']}/collect", headers=h)
    out = _tool("viewer", _name="list_asset_changes", datasource=src["name"])
    assert "column_added" in out["summary"]

    # reset_target_table：确认卡 → 确认执行 → 表清空
    out = _tool("engineer", _name="reset_target_table", job_name="ag_cmp_job")
    assert out["card"]["action"] == "reset_target_table" and out["confirm_token"]
    from app.db import SessionLocal
    from app.models import DataSource, User
    from app.services.agent import execute_confirmation
    from app.services.engine_registry import run_query
    db = SessionLocal()
    try:
        eng = db.query(User).filter(User.username == "engineer").first()
        res = execute_confirmation(db, eng, "reset_target_table",
                                   {"job_id": job_id, "drop": False})
        assert "已重置" in res["summary"]
        ds_obj = db.get(DataSource, dst["id"])
        _, rows, _ = run_query(ds_obj, "SELECT COUNT(*) FROM ods_ag")
        assert rows[0][0] == 0
    finally:
        db.close()
    client.post(f"/api/integ/jobs/{job_id}/offline", headers=h)
    client.delete(f"/api/integ/jobs/{job_id}?force=true", headers=h)


def test_agent_create_sync_job_new_params(client, tokens, monkeypatch):
    """fake LLM 触发 create_sync_job 新参数（time_range/upsert_keys/retries）→ config 透传。"""
    from app.services import llm as llm_mod
    from app.services.llm import ChatResult, ToolCall

    class _FakeLLM:
        def __init__(self): self.round = 0
        def chat(self, messages, tools):
            self.round += 1
            if self.round == 1:
                return ChatResult(tool_calls=[ToolCall("t1", "create_sync_job", {
                    "source_datasource": "ag_param_src", "source_table": "orders",
                    "target_datasource": "ag_param_dst", "target_table": "ods_param",
                    "increment_enabled": True, "increment_column": "dt",
                    "increment_mode": "time_range", "increment_expr": "${bizdate}",
                    "upsert_keys": ["id"], "retries": 3, "retry_delay_minutes": 10})])
            return ChatResult(content="草稿已创建，请确认。")

    monkeypatch.setattr(llm_mod, "get_llm", lambda: _FakeLLM())
    make_sqlite_ds(client, tokens["engineer"], "ag_param_src",
                   {"CREATE TABLE orders (id INTEGER PRIMARY KEY, dt TEXT)":
                    [(1, "2026-01-01")]})
    make_sqlite_ds(client, tokens["engineer"], "ag_param_dst",
                   {"CREATE TABLE m (x INTEGER)": [(1,)]})
    h = H(tokens["engineer"])
    sid = client.post("/api/chat/sessions", headers=h).json()["id"]
    r = client.post(f"/api/chat/sessions/{sid}/send", headers=h,
                    json={"text": "建一个 ag_param_src orders 到 ag_param_dst ods_param 的同步"})
    assert r.status_code == 200
    card = r.json()["card"]
    assert card["type"] == "confirm"
    assert "time_range" in (card["display"]["增量"] or "") or "按表达式" in card["display"]["增量"]
    assert "合并键: id" in card["display"]["写入模式"]
    # config 透传
    r = client.get("/api/integ/jobs", headers=h)
    job = next(j for j in r.json() if j["name"] == "sync_orders_to_ods_param")
    cfg = job["config"]
    assert cfg["increment"]["mode"] == "time_range" and cfg["increment"]["expr"] == "${bizdate}"
    assert cfg["upsert_keys"] == ["id"] and cfg["retries"] == 3 and cfg["retry_delay_minutes"] == 10
    client.delete(f"/api/integ/jobs/{job['id']}", headers=h)
