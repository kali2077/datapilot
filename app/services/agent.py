"""AI Agent：Function Calling 工具注册、Agent Loop、RAG 注入、HITL 确认流。

原则（与 06 文档一致）：
- 工具 = service 层方法 + JSON Schema；AI 以登录用户身份执行（权限在代码层）
- create 类工具只产草稿 + 返回确认卡片，用户确认后才上线（HITL 红线）
- 只读工具免确认直接执行
"""
import datetime
import json
import re
import secrets as _secrets
from typing import List, Optional

from sqlalchemy.orm import Session

from ..models import (DataSource, MetaTable, MetaColumn, GlossaryTerm, FewShot,
                      SyncJob, Workflow, WorkflowNode, TaskInstance, NodeRun,
                      ApiDef, ChatMessage, AuditLog, MonitorRule, MonitorCheck)
from .. import config
from . import sqlguard, meta_service, integ_service, sched_service, llm as llm_mod
from .engine_registry import run_query

MAX_ROUNDS = 8

_ROLE_LEVEL = {"viewer": 1, "analyst": 2, "engineer": 3, "admin": 4}


def _tool_allowed(user, min_role: str) -> bool:
    return _ROLE_LEVEL.get(user.role, 0) >= _ROLE_LEVEL.get(min_role, 99)


# ---------------------------------------------------------------- 工具注册

TOOLS: List[dict] = []


def tool(name: str, description: str, parameters: dict, confirm: bool = False, min_role: str = "viewer"):
    def deco(fn):
        TOOLS.append({"name": name, "description": description, "parameters": parameters,
                      "handler": fn, "confirm": confirm, "min_role": min_role})
        return fn
    return deco


def _get_ds(db: Session, name_or_id):
    ds = None
    if isinstance(name_or_id, int) or (isinstance(name_or_id, str) and name_or_id.isdigit()):
        ds = db.get(DataSource, int(name_or_id))
    if ds is None and name_or_id:
        ds = db.query(DataSource).filter(DataSource.name == name_or_id).first()
    if ds is None:  # 唯一数据源时自动兜底
        all_ds = db.query(DataSource).all()
        if len(all_ds) == 1:
            ds = all_ds[0]
    return ds


def _json_safe(v):
    """JSON 列安全化：Decimal/datetime 等转为可序列化类型。"""
    import decimal
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (datetime.datetime, datetime.date)):
        return str(v)
    if isinstance(v, bytes):
        try:
            return v.decode("utf-8", "replace")
        except Exception:
            return str(v)
    return str(v)


@tool("query_data", "在指定数据源上执行只读 SQL 查询并返回结果。仅允许 SELECT/WITH。",
      {"type": "object", "properties": {
          "datasource": {"type": "string", "description": "数据源名称"},
          "sql": {"type": "string", "description": "只读 SQL"},
          "limit": {"type": "integer", "description": "行数上限，默认200"}},
       "required": ["datasource", "sql"]})
def t_query_data(db: Session, user, session_id: int, datasource="", sql="", limit=None):
    ds = _get_ds(db, datasource)
    if not ds:
        return {"summary": f"数据源不存在: {datasource}", "error": True}
    dialect = sqlguard.dialect_of(ds.type)
    ok, msg = sqlguard.check_readonly(sql, dialect)
    if not ok:
        return {"summary": f"SQL 校验失败: {msg}", "error": True}
    try:
        limit = int(limit or config.CHAT_MAX_ROWS)
        limit = min(limit, config.QUERY_MAX_ROWS)
        wrapped = sqlguard.ensure_limit(sql, limit, dialect)
        cols, rows, truncated = run_query(ds, wrapped, max_rows=limit,
                                          timeout_s=config.QUERY_TIMEOUT_S)
        rows = [[_json_safe(v) for v in r] for r in rows]
        from . import mask_service
        cols, rows, masked = mask_service.apply(db, ds, cols, rows)
        chart = _chart_hint(cols, rows)
        note = f"（敏感列已脱敏: {', '.join(masked)}）" if masked else ""
        return {"summary": f"查询成功，返回 {len(rows)} 行{'（已截断）' if truncated else ''}{note}。",
                "card": {"type": "table", "columns": cols, "rows": rows[:config.CHAT_MAX_ROWS],
                         "sql": wrapped, "chart": chart, "datasource": ds.name,
                         "masked": masked},
                "last_sql": wrapped}
    except Exception as e:
        return {"summary": f"查询执行失败: {e}", "error": True}


def _chart_hint(cols, rows) -> Optional[dict]:
    """简单图表推断：1 个字符串列 + >=1 个数值列 -> bar。"""
    if not rows or len(cols) < 2:
        return None
    num_idx, str_idx = [], []
    for i, v in enumerate(rows[0]):
        if isinstance(v, (int, float)):
            num_idx.append(i)
        elif isinstance(v, str) and re.fullmatch(r"-?\d+(\.\d+)?", v or ""):
            num_idx.append(i)
        else:
            str_idx.append(i)
    if str_idx and num_idx:
        return {"type": "bar", "x": cols[str_idx[0]], "y": [cols[i] for i in num_idx]}
    return None


@tool("search_metadata", "按关键词搜索平台元数据（表名/注释/标签）。",
      {"type": "object", "properties": {
          "keyword": {"type": "string"}, "limit": {"type": "integer"}},
       "required": ["keyword"]})
def t_search_metadata(db: Session, user, session_id: int, keyword="", limit=10):
    tables = meta_service.meta_index.search(db, keyword or "", limit=int(limit or 10))
    out = [{"datasource_id": t.datasource_id, "table": t.name,
            "comment": t.comment or t.manual_desc, "rows": t.row_count} for t in tables]
    if not out:
        return {"summary": f"未找到与「{keyword}」相关的表。", "card": {"type": "text"}}
    return {"summary": f"找到 {len(out)} 张相关表：" + "、".join(x["table"] for x in out),
            "card": {"type": "tables", "tables": out}}


@tool("get_table_schema", "查看表结构（字段/类型/注释/样例值）。",
      {"type": "object", "properties": {
          "datasource": {"type": "string"}, "table": {"type": "string"}},
       "required": ["datasource", "table"]})
def t_get_schema(db: Session, user, session_id: int, datasource="", table=""):
    ds = _get_ds(db, datasource)
    if not ds:
        return {"summary": f"数据源不存在: {datasource}", "error": True}
    mt = (db.query(MetaTable)
            .filter(MetaTable.datasource_id == ds.id, MetaTable.name == table).first())
    if not mt:
        return {"summary": f"元数据中未收录表 {table}，请先在数据源页面执行采集。", "error": True}
    cols = (db.query(MetaColumn).filter(MetaColumn.table_id == mt.id)
              .order_by(MetaColumn.ordinal).all())
    col_list = [{"name": c.name, "type": c.data_type,
                 "comment": c.comment or c.manual_desc,
                 "samples": (c.sample_values or {}).get("values", [])[:3]} for c in cols]
    return {"summary": f"表 {table} 共 {len(col_list)} 个字段。",
            "card": {"type": "schema", "table": table, "datasource": ds.name, "columns": col_list}}


@tool("create_sync_job", "创建数据同步任务（草稿）：源表 -> 目标表，含调度与增量策略。返回试跑结果与确认卡片，需用户确认后才上线。",
      {"type": "object", "properties": {
          "source_datasource": {"type": "string"}, "source_table": {"type": "string"},
          "target_datasource": {"type": "string"}, "target_table": {"type": "string"},
          "schedule_cron": {"type": "string", "description": "5段cron，如 0 2 * * *；空=手动"},
          "write_mode": {"type": "string", "enum": ["append", "truncate_insert", "replace"]},
          "increment_enabled": {"type": "boolean"},
          "increment_column": {"type": "string"},
          "partition_col": {"type": "string"}},
       "required": ["source_table", "target_table"]},
      confirm=True, min_role="engineer")
def t_create_sync_job(db: Session, user, session_id: int, source_datasource="",
                      source_table="", target_datasource="", target_table="",
                      schedule_cron="", write_mode="append", increment_enabled=False,
                      increment_column="", partition_col=""):
    src = _get_ds(db, source_datasource)
    dst = _get_ds(db, target_datasource)
    if not src:
        return {"summary": f"源数据源不存在: {source_datasource}。现有: " +
                           "、".join(d.name for d in db.query(DataSource).all()), "error": True}
    if not dst:
        return {"summary": f"目标数据源不存在: {target_datasource}。现有: " +
                           "、".join(d.name for d in db.query(DataSource).all()), "error": True}
    name = f"sync_{source_table}_to_{target_table}"
    existed = db.query(SyncJob).filter(SyncJob.name == name).first()
    job = existed or SyncJob(name=name)
    job.source_datasource_id = src.id
    job.source_table = source_table
    job.target_datasource_id = dst.id
    job.target_table = target_table
    job.status = "draft"
    job.owner_id = user.id
    cfg = {"write_mode": write_mode or "append",
           "columns": [],
           "increment": {"enabled": bool(increment_enabled), "column": increment_column or "",
                         "mode": "max_value" if not increment_column else "max_value"},
           "partition_col": partition_col or ""}
    if existed:
        cfg["columns"] = existed.config.get("columns", []) if existed.config else []
        job.config = cfg
    else:
        job.config = cfg
        db.add(job)
    db.commit()
    # 校验链
    issues = integ_service.validate_job(db, job)
    errors = [i for i in issues if i["level"] == "error"]
    if errors:
        return {"summary": "任务配置存在问题，未生成草稿：" + "；".join(i["message"] for i in errors),
                "card": {"type": "issues", "issues": issues}, "error": True}
    # 试跑 100 行
    log_lines = []
    try:
        trial = integ_service.trial_run(db, job, limit=100, log=log_lines.append)
    except Exception as e:
        return {"summary": f"试跑失败: {e}", "card": {"type": "issues", "issues": issues}, "error": True}
    ddl = integ_service.suggest_target_ddl(db, job)
    cron = (schedule_cron or "").strip()
    if cron:
        ok, msg = sched_service.validate_cron(cron)
        if not ok:
            return {"summary": msg, "error": True}
    token = _secrets.token_hex(16)
    desc = (f"已创建草稿任务 [{name}] 并试跑 100 行成功（{trial['stats']['written_rows']} 行，"
            f"{trial['stats']['duration_ms']}ms）。目标表{'已自动建表' if trial['stats']['auto_created'] else '已存在'}。")
    if cron:
        desc += f"确认后将注册调度：{cron}。"
    else:
        desc += "未配置调度，确认后仅上线任务（可手动触发）。"
    return {"summary": desc,
            "card": {"type": "confirm", "action": "publish_sync_job",
                     "title": f"同步任务 {name}",
                     "display": {"源": f"{src.name}.{source_table}",
                                 "目标": f"{dst.name}.{target_table}",
                                 "写入模式": cfg["write_mode"],
                                 "增量": "开" if increment_enabled else "关",
                                 "调度": cron or "手动"},
                     "payload": {"job_id": job.id, "cron": cron},
                     "trial": trial, "issues": issues, "ddl": ddl},
            "confirm_token": token, "job_id": job.id}


@tool("create_etl_job", "创建 ETL/SQL 加工任务（草稿）：校验 SQL -> EXPLAIN -> 依赖推荐 -> 确认卡片，需用户确认后才上线。",
      {"type": "object", "properties": {
          "name": {"type": "string"},
          "datasource": {"type": "string"},
          "target_table": {"type": "string"},
          "sql": {"type": "string", "description": "INSERT INTO target SELECT ..."},
          "schedule_cron": {"type": "string"},
          "depends_on": {"type": "array", "items": {"type": "string"},
                          "description": "上游工作流名称列表，留空则按血缘自动推荐"}},
       "required": ["name", "datasource", "target_table", "sql"]},
      confirm=True, min_role="engineer")
def t_create_etl_job(db: Session, user, session_id: int, name="", datasource="",
                     target_table="", sql="", schedule_cron="0 3 * * *", depends_on=None,
                     source_table=""):
    ds = _get_ds(db, datasource)
    if not ds:
        return {"summary": f"数据源不存在: {datasource}", "error": True}
    dialect = sqlguard.dialect_of(ds.type)
    ok, msg = sqlguard.check_etl_sql(sql or "", target_table or "", dialect)
    if not ok:
        return {"summary": f"SQL 校验失败: {msg}", "error": True}
    try:
        import sqlglot as _sg
        from sqlglot import expressions as _exp
        st = _sg.parse_one(sql, read=dialect)
        select_part = st.expression.sql(dialect=dialect) if isinstance(st, _exp.Insert) else sql
        plan = sqlguard.explain_sql(ds, select_part)
    except Exception as e:
        return {"summary": f"EXPLAIN 失败: {e}", "error": True}
    if db.query(Workflow).filter(Workflow.name == name).first():
        return {"summary": f"工作流 {name} 已存在，请换一个名字。", "error": True}
    wf = Workflow(name=name, description=f"ETL: {target_table}", schedule_cron="",
                  enabled=False, job_type="etl", owner_id=user.id)
    db.add(wf)
    db.flush()
    node = WorkflowNode(workflow_id=wf.id, name=f"{target_table}_sql", node_type="sql",
                        config={"datasource_id": ds.id, "sql": sql, "target_table": target_table},
                        ordinal=0)
    db.add(node)
    db.commit()
    # 血缘依赖推荐
    auto_deps = []
    if not depends_on:
        from ..models import LineageEdge
        srcs, _tgt = sqlguard.extract_sql_lineage(sql, dialect)
        for s in srcs:
            edges = (db.query(LineageEdge)
                       .filter(LineageEdge.target_table == s).all())
            for e in edges:
                w = db.get(Workflow, e.workflow_id)
                if w and w.enabled and w.id != wf.id and w.name not in auto_deps:
                    auto_deps.append(w.name)
    wf.depends_on = [d for d in (depends_on or auto_deps)]
    cron = (schedule_cron or "").strip()
    if cron:
        okc, msgc = sched_service.validate_cron(cron)
        if not okc:
            db.delete(wf)
            db.commit()
            return {"summary": msgc, "error": True}
    db.commit()
    token = _secrets.token_hex(16)
    desc = (f"SQL 校验通过、EXPLAIN 正常。已创建草稿工作流 [{name}]。"
            + (f"血缘检测到上游 [{'], ['.join(wf.depends_on)}]，已自动挂依赖。" if wf.depends_on else "")
            + (f"确认后启用调度 {cron}。" if cron else "未配置调度，确认后手动触发。"))
    return {"summary": desc,
            "card": {"type": "confirm", "action": "publish_etl_job",
                     "title": f"ETL 工作流 {name}",
                     "display": {"目标表": target_table, "数据源": ds.name,
                                 "调度": cron or "手动", "上游依赖": "、".join(wf.depends_on) or "无"},
                     "payload": {"workflow_id": wf.id, "cron": cron},
                     "sql": sql, "plan": plan.get("plan", [])[:5]},
            "confirm_token": token, "workflow_id": wf.id}


@tool("run_job", "立即触发一次工作流。", {"type": "object", "properties": {"workflow": {"type": "string"}},
                                      "required": ["workflow"]}, confirm=True, min_role="engineer")
def t_run_job(db: Session, user, session_id: int, workflow=""):
    wf = _find_wf(db, workflow)
    if not wf:
        return {"summary": f"工作流不存在: {workflow}", "error": True}
    token = _secrets.token_hex(16)
    return {"summary": f"是否立即触发 [{wf.name}]？",
            "card": {"type": "confirm", "action": "run_workflow", "title": f"触发 {wf.name}",
                     "display": {"工作流": wf.name, "当前调度": wf.schedule_cron or "手动"},
                     "payload": {"workflow_id": wf.id}},
            "confirm_token": token}


@tool("pause_job", "暂停工作流调度。", {"type": "object", "properties": {"workflow": {"type": "string"}},
                                       "required": ["workflow"]}, confirm=True, min_role="engineer")
def t_pause_job(db: Session, user, session_id: int, workflow=""):
    wf = _find_wf(db, workflow)
    if not wf:
        return {"summary": f"工作流不存在: {workflow}", "error": True}
    token = _secrets.token_hex(16)
    return {"summary": f"是否暂停 [{wf.name}] 的调度？",
            "card": {"type": "confirm", "action": "pause_workflow", "title": f"暂停 {wf.name}",
                     "display": {"工作流": wf.name}, "payload": {"workflow_id": wf.id}},
            "confirm_token": token}


@tool("resume_job", "恢复工作流调度。", {"type": "object", "properties": {"workflow": {"type": "string"}},
                                        "required": ["workflow"]}, confirm=True, min_role="engineer")
def t_resume_job(db: Session, user, session_id: int, workflow=""):
    wf = _find_wf(db, workflow)
    if not wf:
        return {"summary": f"工作流不存在: {workflow}", "error": True}
    token = _secrets.token_hex(16)
    return {"summary": f"是否恢复 [{wf.name}] 的调度？",
            "card": {"type": "confirm", "action": "resume_workflow", "title": f"恢复 {wf.name}",
                     "display": {"工作流": wf.name}, "payload": {"workflow_id": wf.id}},
            "confirm_token": token}


@tool("rerun_job", "重跑工作流最近一个失败实例（无失败实例则重跑最近实例）。",
      {"type": "object", "properties": {"workflow": {"type": "string"}}, "required": ["workflow"]},
      confirm=True, min_role="engineer")
def t_rerun_job(db: Session, user, session_id: int, workflow=""):
    wf = _find_wf(db, workflow)
    if not wf:
        return {"summary": f"工作流不存在: {workflow}", "error": True}
    inst = (db.query(TaskInstance).filter(TaskInstance.workflow_id == wf.id)
              .order_by(TaskInstance.id.desc()).first())
    if not inst:
        return {"summary": f"[{wf.name}] 还没有运行实例。", "error": True}
    token = _secrets.token_hex(16)
    return {"summary": f"是否重跑 [{wf.name}] 的实例 #{inst.id}（当前 {inst.status}）？",
            "card": {"type": "confirm", "action": "rerun_instance",
                     "title": f"重跑 {wf.name} #{inst.id}",
                     "display": {"实例": f"#{inst.id}", "状态": inst.status},
                     "payload": {"instance_id": inst.id}},
            "confirm_token": token}


@tool("publish_api", "把一条 SELECT 查询发布为数据服务 API（草稿），确认后生效。",
      {"type": "object", "properties": {
          "name": {"type": "string"}, "datasource": {"type": "string"},
          "sql_template": {"type": "string"}},
       "required": ["name", "sql_template"]}, confirm=True, min_role="engineer")
def t_publish_api(db: Session, user, session_id: int, name="", datasource="", sql_template=""):
    from .apigw_service import check_api_sql
    ds = _get_ds(db, datasource)
    if not ds:
        return {"summary": f"数据源不存在: {datasource}", "error": True}
    ok, msg = check_api_sql(sql_template, sqlguard.dialect_of(ds.type))
    if not ok:
        return {"summary": f"SQL 校验失败: {msg}", "error": True}
    code = re.sub(r"[^a-z0-9_]", "_", (name or "api").lower())[:48] or f"api_{_secrets.token_hex(4)}"
    if db.query(ApiDef).filter(ApiDef.code == code).first():
        code = f"{code}_{_secrets.token_hex(3)}"
    api = ApiDef(code=code, name=name or code, datasource_id=ds.id,
                 sql_template=sql_template, params=_extract_params(sql_template),
                 status="draft", owner_id=user.id)
    db.add(api)
    db.commit()
    token = _secrets.token_hex(16)
    return {"summary": f"已创建 API 草稿 [{code}]，确认后发布生效。",
            "card": {"type": "confirm", "action": "publish_api",
                     "title": f"发布 API {code}",
                     "display": {"编码": code, "数据源": ds.name, "参数": _param_names(api.params) or "无"},
                     "payload": {"api_id": api.id}},
            "confirm_token": token, "api_id": api.id}


def _param_names(params):
    return "、".join(p["name"] for p in params or [])


def _extract_params(sql: str):
    """提取 :param 绑定参数（忽略 :: 类型转换，如 PostgreSQL/Doris 的 NOW()::date）。"""
    params = []
    for m in dict.fromkeys(re.findall(r"(?<!:):([a-zA-Z_]\w*)", sql or "")):
        params.append({"name": m, "type": "string", "required": False, "default": "", "validation": ""})
    return params


@tool("get_instance_detail", "查看调度实例详情（各节点状态与日志尾部），用于失败诊断。",
      {"type": "object", "properties": {"instance_id": {"type": "integer"}},
       "required": ["instance_id"]})
def t_get_instance_detail(db: Session, user, session_id: int, instance_id: int = 0):
    inst = db.get(TaskInstance, int(instance_id or 0))
    if not inst:
        return {"summary": f"实例 #{instance_id} 不存在", "error": True}
    runs = (db.query(NodeRun).filter(NodeRun.instance_id == inst.id)
              .order_by(NodeRun.id).all())
    nodes = [{"name": r.node_name, "type": r.node_type, "status": r.status,
              "duration_ms": r.duration_ms,
              "log_tail": (r.log or "")[-800:]} for r in runs]
    card = {"type": "instance", "instance_id": inst.id, "workflow": inst.workflow_name,
            "status": inst.status, "trigger_type": inst.trigger_type,
            "error": (inst.error or "")[:500],
            "nodes": [{"name": n["name"], "status": n["status"],
                       "duration_ms": n["duration_ms"]} for n in nodes]}
    if inst.status == "failed":
        bad = next((n for n in nodes if n["status"] == "failed"), None)
        hint = ""
        if bad:
            tail = (bad["log_tail"] or "")[-400:]
            if "no such table" in tail:
                hint = "疑似目标表缺失，可先检查上游任务是否成功。"
            elif "syntax error" in tail or "ParseError" in tail:
                hint = "疑似 SQL 语法问题，可在数据开发页编辑节点 SQL。"
            elif "Timeout" in tail or "timeout" in tail:
                hint = "疑似执行超时，可检查数据量或超时配置后重跑。"
        return {"summary": (f"实例 #{inst.id} [{inst.workflow_name}] 状态 {inst.status}。"
                            + (f"失败节点: {bad['name']}。{hint}" if bad else inst.error[:200])),
                "card": card, "nodes_detail": nodes}
    return {"summary": f"实例 #{inst.id} [{inst.workflow_name}] 状态 {inst.status}，共 {len(nodes)} 个节点。",
            "card": card, "nodes_detail": nodes}


@tool("monitor_overview", "查看数据监控概览：规则数、近24h检查/失败、异常规则列表。",
      {"type": "object", "properties": {}})
def t_monitor_overview(db: Session, user, session_id: int):
    import datetime as _dt
    from sqlalchemy import func as _func
    from ..models import AlertRecord
    since = _dt.datetime.now() - _dt.timedelta(hours=24)
    rules_total = db.query(_func.count(MonitorRule.id)).scalar() or 0
    failing = (db.query(MonitorRule)
                 .filter(MonitorRule.enabled == True,  # noqa: E712
                         MonitorRule.last_status.in_(["failed", "error"]))
                 .all())
    checks_24h = db.query(_func.count(MonitorCheck.id)).filter(
        MonitorCheck.ts >= since).scalar() or 0
    failed_24h = db.query(_func.count(MonitorCheck.id)).filter(
        MonitorCheck.ts >= since, MonitorCheck.status == "failed").scalar() or 0
    out = {"rules_total": rules_total, "checks_24h": checks_24h, "failed_24h": failed_24h,
           "failing": [{"id": r.id, "name": r.name, "type": r.rule_type,
                        "message": (r.last_message or "")[:120]} for r in failing]}
    if not rules_total:
        return {"summary": "平台还没有配置监控规则。可在【数据监控告警】页面创建（支持行数波动/阈值/空值率/唯一性/延迟/自定义SQL/任务时长 7 类）。",
                "card": {"type": "text"}}
    lines = [f"共 {rules_total} 条规则，近24h检查 {checks_24h} 次、失败 {failed_24h} 次。"]
    if failing:
        lines.append("异常规则：" + "；".join(
            f"{f['name']}（{f['message']}）" for f in out["failing"][:5]))
        return {"summary": "\n".join(lines), "card": {"type": "monitor", **out}}
    return {"summary": "\n".join(lines) + " 全部正常。", "card": {"type": "monitor", **out}}


@tool("run_monitor_check", "立即执行一次指定数据监控规则的检查。",
      {"type": "object", "properties": {"rule_name": {"type": "string"}},
       "required": ["rule_name"]}, min_role="engineer")
def t_run_monitor_check(db: Session, user, session_id: int, rule_name=""):
    rule = db.query(MonitorRule).filter(MonitorRule.name == rule_name).first()
    if not rule:
        names = "、".join(r.name for r in db.query(MonitorRule).limit(10))
        return {"summary": f"监控规则不存在: {rule_name}。现有规则: {names or '（无）'}", "error": True}
    from . import monitor_service
    check = monitor_service.check_rule(db, rule)
    label = {"passed": "✅ 通过", "failed": "❌ 失败", "error": "⚠️ 异常"}[check.status]
    return {"summary": f"[{label}] {rule.name}：{check.metric}"
                       + (f" —— {check.message}" if check.message and check.status != "passed" else ""),
            "card": {"type": "monitor_check", "rule": rule.name, "status": check.status,
                     "metric": check.metric, "message": check.message}}


def _find_wf(db: Session, name_or_id) -> Optional[Workflow]:
    if isinstance(name_or_id, int) or (isinstance(name_or_id, str) and name_or_id.isdigit()):
        wf = db.get(Workflow, int(name_or_id))
        if wf:
            return wf
    if name_or_id:
        return db.query(Workflow).filter(Workflow.name == name_or_id).first()
    return None


TOOL_MAP = {t["name"]: t for t in TOOLS}


# ---------------------------------------------------------------- 确认执行器

def execute_confirmation(db: Session, user, action: str, payload: dict) -> dict:
    if action == "publish_sync_job":
        job = db.get(SyncJob, payload["job_id"])
        if not job:
            return {"summary": "任务不存在", "error": True}
        wf = integ_service.publish_job(db, job, user, cron=payload.get("cron", ""))
        return {"summary": f"✅ 同步任务 [{job.name}] 已上线，工作流 [{wf.name}] 已注册调度。",
                "card": {"type": "job_link", "workflow_id": wf.id, "job_id": job.id}}
    if action == "publish_etl_job":
        wf = db.get(Workflow, payload["workflow_id"])
        if not wf:
            return {"summary": "工作流不存在", "error": True}
        wf.enabled = True
        wf.schedule_cron = payload.get("cron", "")
        db.commit()
        sched_service.apply_schedule(db, wf)
        return {"summary": f"✅ ETL 工作流 [{wf.name}] 已上线" +
                           (f"，调度 {wf.schedule_cron}" if wf.schedule_cron else "（手动触发）") + "。",
                "card": {"type": "job_link", "workflow_id": wf.id}}
    if action == "run_workflow":
        wf = db.get(Workflow, payload["workflow_id"])
        inst = sched_service.trigger_workflow(db, wf, "manual", wait_on_deps=True,
                                              triggered_by=f"ai:{user.username}")
        return {"summary": f"✅ 已触发 [{wf.name}]，实例 #{inst.id} 执行中。",
                "card": {"type": "instance_link", "instance_id": inst.id}}
    if action == "pause_workflow":
        wf = db.get(Workflow, payload["workflow_id"])
        wf.enabled = False
        db.commit()
        sched_service.apply_schedule(db, wf)
        return {"summary": f"⏸ [{wf.name}] 调度已暂停。"}
    if action == "resume_workflow":
        wf = db.get(Workflow, payload["workflow_id"])
        wf.enabled = True
        db.commit()
        sched_service.apply_schedule(db, wf)
        return {"summary": f"▶ [{wf.name}] 调度已恢复。"}
    if action == "rerun_instance":
        inst = db.get(TaskInstance, payload["instance_id"])
        sched_service.rerun_instance(db, inst, only_failed=(inst.status == "failed"))
        return {"summary": f"✅ 实例 #{inst.id} 已重新提交执行。"}
    if action == "publish_api":
        api = db.get(ApiDef, payload["api_id"])
        api.status = "published"
        db.commit()
        return {"summary": f"✅ API [{api.code}] 已发布。调用方式见数据服务页面。",
                "card": {"type": "api_link", "api_id": api.id, "code": api.code}}
    return {"summary": f"未知确认动作: {action}", "error": True}


# ---------------------------------------------------------------- RAG 注入

PAGE_HINTS = {
    "/ai-assistant": "用户在【AI 助手】中心（平台全能力对话入口），请直接响应指令并主动推荐可用能力。",
    "/integration": "用户正在【数据集成】页面，可引导使用 create_sync_job 创建同步任务。",
    "/workflows": "用户正在【工作流开发】页面，可引导使用 create_etl_job 创建 ETL 任务。",
    "/ops": "用户正在【调度运维】页面，可用 get_instance_detail 诊断实例、用 run/pause/resume/rerun_job 运维。",
    "/monitoring": "用户正在【数据监控告警】页面，可用 monitor_overview 查看异常规则、run_monitor_check 触发检查。",
    "/assets": "用户正在【数据资产地图】页面，可用 search_metadata/get_table_schema 探查表与血缘。",
    "/query": "用户正在【数据查询】页面，可用 query_data 帮其写并执行只读查询。",
    "/apis": "用户正在【数据服务】页面，可用 publish_api 把查询发布为 API。",
    "/datasources": "用户正在【数据源管理】页面，新建数据源/采集元数据请在页面上操作，AI 可解释后续步骤。",
    "/chat": "用户正在【智能问数】页面。",
}


def _expand_with_glossary(base_toks: set, glossary):
    """②同义词进检索链：问题词元命中术语族（术语/同义词/定义）→ 整族并入。

    返回 (展开后的词元集合, 命中的术语列表)。纯代码实现，零部署依赖。
    """
    toks = set(base_toks)
    hits = []
    for g in glossary:
        fam = set(meta_service.tokenize(
            f"{g.term} {(g.synonyms or '').replace(',', ' ').replace('，', ' ')} {(g.definition or '')}"))
        if base_toks & fam:
            toks |= fam
            hits.append(g)
    return toks, hits


def build_context(db: Session, user, user_text: str, session_id: int, page: str = "") -> (str, dict):
    ds_list = db.query(DataSource).all()
    ds_names = [d.name for d in ds_list]
    ds_map = {d.id: d.name for d in ds_list}
    tables = db.query(MetaTable).all()
    scored = []
    glossary = db.query(GlossaryTerm).limit(50).all()
    # 同义词展开（②）：问题词元命中任一术语族（术语/同义词/定义）→ 整族词元并入检索
    base_toks = set(meta_service.tokenize(user_text or ""))
    toks, hit_terms = _expand_with_glossary(base_toks, glossary)
    for t in tables:
        t_tokens = set(meta_service.tokenize(t.name + " " + (t.comment or "") + " " + (t.manual_desc or "")))
        overlap = len(toks & t_tokens)
        scored.append((overlap, t))
    scored.sort(key=lambda x: -x[0])
    top = [t for s, t in scored if s > 0][:8] or [t for _, t in scored[:5]]
    schema_txt = []
    for t in top:
        cols = (db.query(MetaColumn).filter(MetaColumn.table_id == t.id)
                  .order_by(MetaColumn.ordinal).limit(30).all())
        col_str = ", ".join(f"{c.name}({c.data_type})" for c in cols)
        schema_txt.append(f"- {ds_map.get(t.datasource_id, '?')}.{t.name}: {(t.comment or t.manual_desc or '')} 字段: {col_str}")
    gl_hits = "；".join(f"{g.term}={g.definition}" for g in hit_terms if g.definition)
    gl = "；".join(f"{g.term}={g.definition}" for g in glossary if g.definition)
    fewshots = db.query(FewShot).filter(FewShot.enabled == True).all()  # noqa: E712
    fs_scored = []
    for fs in fewshots:
        ov = len(toks & set(meta_service.tokenize(fs.question)))
        fs_scored.append((ov, fs))
    fs_scored.sort(key=lambda x: -x[0])
    fs_txt = "\n".join(f"Q: {fs.question}\nSQL: {fs.sql}" for _, fs in fs_scored[:3] if fs_scored and fs_scored[0][0] > 0)

    last_sql = ""
    last_msg = (db.query(ChatMessage)
                  .filter(ChatMessage.session_id == session_id,
                          ChatMessage.role == "assistant")
                  .order_by(ChatMessage.id.desc()).first())
    if last_msg and last_msg.meta:
        last_sql = last_msg.meta.get("sql") or ""

    system = (
        "你是 DataPilot 数据平台的 AI 助手，可以操作：数据查询、元数据检索、创建数据同步任务(DataX)、"
        "创建 ETL 任务(SQL 工作流)、调度运维(触发/暂停/恢复/重跑/诊断实例)、数据监控(查看/触发检查)、发布数据 API。\n"
        f"今天日期: {datetime.date.today().isoformat()}。当前用户: {user.username}（角色 {user.role}）。\n"
        + (f"用户当前所在页面: {PAGE_HINTS.get(page, page)}\n" if page else "")
        + f"平台数据源: {json.dumps(ds_names, ensure_ascii=False)}。\n"
        + "相关表元数据:\n" + ("\n".join(schema_txt) or "（暂无）") +
        ("\n命中术语(与问题相关,务必遵循其口径): " + gl_hits if gl_hits else "") +
        ("\n术语表: " + gl if gl else "") +
        ("\n参考样例:\n" + fs_txt if fs_txt else "") +
        "\n规则：\n"
        "1. 创建任务类操作会先产出草稿与试跑结果，用户确认后才上线；不要承诺未确认的变更。\n"
        "2. 查询只能只读 SQL；ETL 只允许 INSERT INTO 目标表 SELECT ...。\n"
        "3. 需要表结构时先调 get_table_schema，不要凭空猜测字段。\n"
        "4. 用中文回答，简洁、给出结论与下一步。")
    ctx = {"datasources": ds_names,
           "tables": [{"name": t.name, "ds_name": ds_map.get(t.datasource_id, ""),
                       "desc": (t.comment or "") + " " + (t.manual_desc or "")} for t in tables],
           "glossary": [{"term": g.term, "synonyms": g.synonyms or "",
                         "definition": g.definition or ""} for g in glossary],
           "last_sql": last_sql}
    return system, ctx


# ---------------------------------------------------------------- Agent Loop

def chat_once(db: Session, user, session_id: int, text: str, page: str = "") -> dict:
    start_user_msg = ChatMessage(session_id=session_id, role="user", content=text)
    db.add(start_user_msg)
    db.commit()

    system, ctx = build_context(db, user, text, session_id, page=page)
    provider = llm_mod.get_llm()
    rule_mode = provider is None
    if rule_mode:
        provider = llm_mod.RuleLLM(lambda: ctx)

    history = (db.query(ChatMessage)
                 .filter(ChatMessage.session_id == session_id)
                 .order_by(ChatMessage.id.desc()).limit(20).all())
    history.reverse()
    messages = [{"role": "system", "content": system}]
    for m in history[:-1]:
        messages.append({"role": m.role, "content": (m.content or "")[:4000]})
    messages.append({"role": "user", "content": text})

    tool_specs = [{"name": t["name"], "description": t["description"],
                   "parameters": t["parameters"]} for t in TOOLS
                  if _tool_allowed(user, t["min_role"])]
    last_result = None
    reply = None
    for round_i in range(MAX_ROUNDS):
        if rule_mode and round_i > 0:
            break  # 规则引擎单轮：工具结果直接汇总
        try:
            result = provider.chat(messages, tool_specs)
        except Exception as e:
            reply = f"LLM 调用失败: {e}"
            break
        if not result.tool_calls:
            reply = result.content
            break
        # 执行工具（本轮所有 tool_calls 记入同一条 assistant 消息，符合 OpenAI 规范）
        assistant_tool_calls, tool_messages = [], []
        for tc in result.tool_calls:
            spec = TOOL_MAP.get(tc.name)
            if not spec:
                tool_out = {"summary": f"未知工具 {tc.name}", "error": True}
            elif not _tool_allowed(user, spec["min_role"]):
                tool_out = {"summary": f"权限不足：{tc.name} 需要 {spec['min_role']} 及以上角色",
                            "error": True}
            else:
                try:
                    tool_out = spec["handler"](db, user, session_id, **(tc.arguments or {}))
                except Exception as e:
                    tool_out = {"summary": f"工具执行异常: {e}", "error": True}
                _audit_ai(db, user, tc.name, tc.arguments, tool_out)
            last_result = tool_out
            assistant_tool_calls.append({"id": tc.id, "type": "function",
                                         "function": {"name": tc.name,
                                                      "arguments": json.dumps(tc.arguments or {}, ensure_ascii=False)}})
            tool_messages.append({"role": "tool", "tool_call_id": tc.id, "name": tc.name,
                                  "content": json.dumps(tool_out, ensure_ascii=False, default=str)[:6000]})
        messages.append({"role": "assistant", "content": "",
                         "tool_calls": assistant_tool_calls})
        messages.extend(tool_messages)
        if not rule_mode:
            continue
        break

    if reply is None:
        # 用工具结果汇总作为回复
        if last_result:
            reply = last_result.get("summary", "操作完成。")
        else:
            reply = "未能理解该请求，请换个说法或查看帮助。"

    msg = ChatMessage(session_id=session_id, role="assistant", content=reply,
                      llm_provider=llm_mod.provider_name())
    if last_result:
        meta = {"card": last_result.get("card")}
        if last_result.get("last_sql"):
            meta["sql"] = last_result["last_sql"]
        if last_result.get("error"):
            meta["error"] = True
        msg.meta = meta
    if last_result and last_result.get("confirm_token"):
        msg.confirm_token = last_result["confirm_token"]
        msg.confirm_status = "pending"
    db.add(msg)
    db.commit()
    return {"message_id": msg.id, "content": reply, "card": (msg.meta or {}).get("card"),
            "confirm_token": msg.confirm_token, "confirm_status": msg.confirm_status,
            "provider": msg.llm_provider, "error": (msg.meta or {}).get("error", False)}


def _audit_ai(db: Session, user, tool_name: str, args: dict, result: dict):
    db.add(AuditLog(user_id=user.id, username=user.username, action=f"ai:{tool_name}",
                    resource_type="tool", resource_id=str(args.get("workflow") or args.get("name") or ""),
                    detail={"args": {k: str(v)[:200] for k, v in (args or {}).items()},
                            "error": result.get("error", False)},
                    source="ai"))
    db.commit()
