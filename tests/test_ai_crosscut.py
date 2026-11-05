"""AI 横切层测试：页面上下文注入 + 实例诊断/监控概览/触发检查工具。"""
import time

from conftest import H, make_sqlite_ds, wait_instance

ORDERS_DDL = "CREATE TABLE orders (id INTEGER PRIMARY KEY, city TEXT, amount REAL)"


def test_page_context_send(client, tokens):
    """带 page 的对话发送：后端正常处理且 system 注入页面提示（功能冒烟）。"""
    make_sqlite_ds(client, tokens["engineer"], "xc_src", {ORDERS_DDL: [(1, "北京", 1.0)]})
    h = H(tokens["analyst"])
    sid = client.post("/api/chat/sessions", headers=h).json()["id"]
    r = client.post(f"/api/chat/sessions/{sid}/send", headers=h,
                    json={"text": "查 xc_src 的 orders 有多少行", "page": "/query"})
    assert r.status_code == 200, r.text
    assert r.json()["card"]["type"] == "table"


def test_tool_get_instance_detail(client, tokens):
    h = H(tokens["engineer"])
    # 造一个失败实例
    r = client.post("/api/sched/workflows", headers=h, json={
        "name": "xc_fail_wf", "enabled": True,
        "nodes": [{"name": "boom", "node_type": "python",
                   "config": {"script": "raise RuntimeError('boom!')"}}]})
    wf_id = r.json()["id"]
    tr = client.post(f"/api/sched/workflows/{wf_id}/trigger", headers=h).json()
    inst = wait_instance(client, tokens["engineer"], tr["instance_id"])
    assert inst["status"] == "failed"
    # 对话触发诊断工具
    sid = client.post("/api/chat/sessions", headers=h).json()["id"]
    r = client.post(f"/api/chat/sessions/{sid}/send", headers=h,
                    json={"text": f"诊断一下实例 {inst['id']}", "page": "/ops"})
    body = r.json()
    assert body["card"]["type"] == "instance", body["content"]
    assert body["card"]["status"] == "failed"
    # 规则引擎摘要含失败节点与实例号
    assert f"#{inst['id']}" in body["content"] and "boom" in body["content"]


def test_tool_monitor_overview_and_run_check(client, tokens):
    ds = make_sqlite_ds(client, tokens["engineer"], "xc_mon", {ORDERS_DDL: ROWS4()})
    h = H(tokens["engineer"])
    rule = client.post("/api/monitor/rules", headers=h, json={
        "name": "xc_行数阈值", "rule_type": "row_count_threshold",
        "datasource_id": ds["id"], "table_name": "orders",
        "params": {"min": 1}, "enabled": False}).json()
    # 概览
    sid = client.post("/api/chat/sessions", headers=h).json()["id"]
    r = client.post(f"/api/chat/sessions/{sid}/send", headers=h,
                    json={"text": "看看监控情况", "page": "/monitoring"})
    body = r.json()
    assert body["card"]["type"] == "monitor"
    assert body["card"]["rules_total"] >= 1
    # 触发检查（按名字）
    r = client.post(f"/api/chat/sessions/{sid}/send", headers=h,
                    json={"text": "立即检查 xc_行数阈值", "page": "/monitoring"})
    body = r.json()
    assert "✅" in body["content"] and "xc_行数阈值" in body["content"]
    # 不存在的规则给出提示
    r = client.post(f"/api/chat/sessions/{sid}/send", headers=h,
                    json={"text": "立即检查 不存在的规则", "page": "/monitoring"})
    assert "不存在" in r.json()["content"]


def test_tool_permission_for_analyst(client, tokens):
    """监控触发是 engineer 工具：analyst 对话触发被拒。"""
    h = H(tokens["analyst"])
    sid = client.post("/api/chat/sessions", headers=h).json()["id"]
    r = client.post(f"/api/chat/sessions/{sid}/send", headers=h,
                    json={"text": "立即检查 任意规则", "page": "/monitoring"})
    body = r.json()
    assert body.get("error") or "权限不足" in body["content"]


def test_intro_table_and_help(client, tokens):
    """「介绍一下表 X」→ schema 卡片；「你能做什么」→ 帮助文案。"""
    make_sqlite_ds(client, tokens["engineer"], "intro_src", {ORDERS_DDL: ROWS4()})
    h = H(tokens["viewer"])
    sid = client.post("/api/chat/sessions", headers=h).json()["id"]
    r = client.post(f"/api/chat/sessions/{sid}/send", headers=h,
                    json={"text": "介绍一下表 orders", "page": "/assets"})
    body = r.json()
    assert body["card"]["type"] == "schema", body["content"]
    assert body["card"]["table"] == "orders"
    r = client.post(f"/api/chat/sessions/{sid}/send", headers=h,
                    json={"text": "你能做什么"})
    assert "规则引擎模式" in r.json()["content"] and "介绍一下表" in r.json()["content"]


def test_synonym_expansion_recall(client, tokens):
    """②同义词进检索链：「流水」(GMV 同义词) 应召回 orders 表（此前字面匹配找不到）。"""
    make_sqlite_ds(client, tokens["engineer"], "syn_src", {ORDERS_DDL: ROWS4()})
    h = H(tokens["analyst"])
    sid = client.post("/api/chat/sessions", headers=h).json()["id"]
    r = client.post(f"/api/chat/sessions/{sid}/send", headers=h,
                    json={"text": "统计流水Top10", "page": "/chat"})
    body = r.json()
    # 同义词展开命中 GMV 术语族 → orders（用户列）被召回，出表格卡
    assert body["card"]["type"] == "table", body["content"]
    assert "orders" in (body["card"].get("sql") or "")

    # 直接验证 RAG 上下文：问题含同义词时 system 注入「命中术语」
    from app.db import SessionLocal
    from app.models import User as U, ChatSession
    from app.services.agent import build_context
    db = SessionLocal()
    try:
        u = db.query(U).filter(U.username == "analyst").first()
        s = db.query(ChatSession).filter(ChatSession.user_id == u.id).first()
        system, ctx = build_context(db, u, "查一下流水", s.id)
        assert "命中术语" in system and "GMV" in system
        assert any(g["term"] == "GMV" and "流水" in g["synonyms"] for g in ctx["glossary"])
    finally:
        db.close()


def ROWS4():
    return [(i, ["北京", "上海"][i % 2], i * 1.0) for i in range(1, 11)]
