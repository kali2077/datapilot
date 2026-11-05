"""LLM 适配层：OpenAI 兼容 Chat Completions（DeepSeek/Qwen 等均可）+ 内置规则引擎兜底。

统一接口：chat(messages, tools) -> ChatResult(content | tool_calls)
未配置 AI_API_KEY 时自动降级为 RuleLLM（确定性意图解析，保证离线可用）。
"""
import json
import re
from dataclasses import dataclass, field
from typing import List, Optional

from .. import config


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class ChatResult:
    content: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)


def provider_name() -> str:
    if config.AI_API_KEY:
        return f"openai-compat:{config.AI_MODEL}"
    return "rule-engine"


# ---------------------------------------------------------------- OpenAI 兼容

class OpenAICompatLLM:
    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    def chat(self, messages: List[dict], tools: List[dict]) -> ChatResult:
        import httpx
        payload = {"model": self.model, "messages": messages, "temperature": 0.2}
        if tools:
            payload["tools"] = [{"type": "function", "function": t} for t in tools]
            payload["tool_choice"] = "auto"
        resp = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload, timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        msg = data["choices"][0]["message"]
        result = ChatResult(content=msg.get("content") or "")
        for tc in (msg.get("tool_calls") or []):
            fn = tc.get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except Exception:
                args = {}
            result.tool_calls.append(ToolCall(id=tc.get("id", ""), name=fn.get("name", ""),
                                              arguments=args))
        return result


# ---------------------------------------------------------------- 规则引擎（离线兜底）

_TIME_PATTERNS = [
    (r"每小时", "0 * * * *"), (r"每(\d+)小时", r"0 */\1 * * *"),
    (r"每分钟", "* * * * *"),
    (r"凌晨\s*(\d{1,2})\s*点", r"0 \1 * * *"),
    (r"每天\s*(\d{1,2})\s*点", r"0 \1 * * *"),
    (r"每天|每日|每日凌晨", "0 2 * * *"),
]


def _extract_cron(text: str) -> str:
    for pat, repl in _TIME_PATTERNS:
        m = re.search(pat, text)
        if m:
            if "\\1" in repl:
                return repl.replace("\\1", m.group(1))
            return repl
    return ""


def _num(text: str, default=10) -> int:
    m = re.search(r"(?:top|前)\s*(\d+)", text.lower())
    return int(m.group(1)) if m else default


class RuleLLM:
    """确定性意图解析：覆盖建同步/建ETL/查数/找表/运维/发布API 六类高频意图。"""

    def __init__(self, context_provider):
        # context_provider() -> {"datasources": [name...], "tables": [{"name","comment","ds_name"}],
        #                        "glossary": [...], "last_sql": str}
        self.ctx = context_provider

    def chat(self, messages: List[dict], tools: List[dict]) -> ChatResult:
        user_text = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                user_text = m.get("content") or ""
                break
        ctx = self.ctx()
        call = self._route(user_text, ctx)
        if call:
            return call
        return ChatResult(content=self._help_text())

    def _help_text(self) -> str:
        return (
            "当前未配置 LLM（规则引擎模式）。我可以理解这些指令：\n"
            "1. 建同步任务：「把 source_ds 的 orders 表每天凌晨2点同步到 warehouse 的 ods_orders」\n"
            "2. 建ETL任务：「基于 ods_orders 做 dwd 订单明细，剔除测试账号，每天跑」\n"
            "3. 查数：「查 warehouse 的 orders 前10行」/「统计每天订单量Top10」\n"
            "4. 找表/看结构：「有哪些表」/「找订单相关的表」/「介绍一下表 orders」\n"
            "5. 运维：「重跑 xxx」「暂停 xxx」「恢复 xxx」「诊断实例 123」\n"
            "6. 发布API：「把上一条查询发布成API」\n"
            "7. 监控：「看看监控情况」「立即检查 规则名」\n"
            "配置 AI_BASE_URL/AI_API_KEY/AI_MODEL 后启用大模型自由对话。")

    # ---------------- 意图路由 ----------------

    def _match_table(self, text: str, ctx) -> Optional[dict]:
        from .meta_service import tokenize
        t_low = text.lower()
        # 同义词展开（②）：问题命中术语族（术语/同义词/定义）→ 整族词元并入匹配
        base = set(tokenize(text))
        toks = set(base)
        for g in ctx.get("glossary", []):
            fam = set(tokenize(f"{g.get('term', '')} {g.get('synonyms', '')} {g.get('definition', '')}"))
            if base & fam:
                toks |= fam
        best, best_score = None, 0
        for tb in ctx.get("tables", []):
            name = (tb.get("name") or "").lower()
            if name and name in t_low:
                score = 100 + len(name)
                if score > best_score:
                    best, best_score = tb, score
                continue
            # 中文语义匹配：注释/人工描述与提问（含术语族展开）的 token 重叠
            desc = tb.get("desc") or ""
            if desc:
                ov = len(toks & set(tokenize(name + " " + desc)))
                if ov > best_score:
                    best, best_score = tb, ov
        return best

    def _match_datasource(self, text: str, ctx) -> Optional[str]:
        for name in ctx.get("datasources", []):
            if name.lower() in text.lower():
                return name
        return None

    def _route(self, text: str, ctx) -> Optional[ChatResult]:
        t = text.strip()

        # --- 帮助 ---
        if t in ("帮助", "help", "Help", "你能做什么", "你会什么", "指令", "指令示范"):
            return ChatResult(content=self._help_text())

        # --- 数据监控：触发检查（优先于概览，"立即检查 xxx"不必含"监控"字样） ---
        if re.search(r"立即检查|执行检查|触发检查|跑一次检查", t):
            m = re.search(r"(?:立即检查|执行检查|触发检查|跑一次检查)\s*[:：]?\s*(\S+)", t)
            return ChatResult(tool_calls=[ToolCall("r1", "run_monitor_check",
                                                   {"rule_name": (m.group(1) if m else "")})])
        if re.search(r"监控|巡检|异常规则|告警情况", t):
            return ChatResult(tool_calls=[ToolCall("r1", "monitor_overview", {})])

        # --- 实例诊断 ---
        m = re.search(r"实例\s*[#＃]?(\d+)", t)
        if m and re.search(r"诊断|详情|状态|日志|看看|查看|分析", t):
            return ChatResult(tool_calls=[ToolCall("r1", "get_instance_detail",
                                                   {"instance_id": int(m.group(1))})])

        # --- 表结构介绍：「介绍一下表 ods_orders」/「ods_orders 的字段结构」 ---
        if re.search(r"介绍|字段|结构", t):
            m = (re.search(r"介绍(?:一下)?\s*表?\s*[:：]?\s*([a-zA-Z0-9_\u4e00-\u9fa5]+)", t)
                 or re.search(r"([a-zA-Z0-9_]+)\s*表\s*(?:的)?(?:字段|结构|介绍)", t))
            if m and m.group(1) not in ("的", "表"):
                tname = m.group(1)
                tb = next((x for x in ctx.get("tables", [])
                           if x["name"].lower() == tname.lower()), None)
                return ChatResult(tool_calls=[ToolCall("r1", "get_table_schema", {
                    "datasource": (tb or {}).get("ds_name", ""), "table": tname})])

        # --- 找表/元数据 ---
        if re.search(r"(有哪些|找|搜索|查找).{0,6}(表|数据)", t) and not re.search(r"查一下|统计|多少", t):
            kw = re.sub(r"(有哪些|找一下|找|搜索|查找|相关|的|表|数据|请|帮我|\?|？)", "", t).strip()
            return ChatResult(tool_calls=[ToolCall("r1", "search_metadata",
                                                   {"keyword": kw or "订单", "limit": 10})])

        # --- 运维 ---
        if re.search(r"重跑|重新跑", t):
            name = self._extract_name(t)
            return ChatResult(tool_calls=[ToolCall("r1", "rerun_job", {"workflow": name or ""})])
        if re.search(r"暂停|下线|停掉", t):
            return ChatResult(tool_calls=[ToolCall("r1", "pause_job",
                                                   {"workflow": self._extract_name(t) or ""})])
        if re.search(r"恢复|启用|上线", t):
            return ChatResult(tool_calls=[ToolCall("r1", "resume_job",
                                                   {"workflow": self._extract_name(t) or ""})])

        # --- 发布 API ---
        if re.search(r"发布.*(API|api|接口)", t):
            last_sql = ctx.get("last_sql") or ""
            if not last_sql:
                return ChatResult(content="会话里还没有可发布的查询，请先在对话里执行一次查数，再发布 API。")
            name = re.sub(r"[^0-9a-zA-Z_\u4e00-\u9fa5]", "_", t)[:40] or "chat_api"
            return ChatResult(tool_calls=[ToolCall("r1", "publish_api",
                                                   {"name": name, "sql_template": last_sql})])

        # --- ETL ---
        m = re.search(r"基于\s*(\S+?)\s*做\s*(.+?)[，,。；;]?\s*(?:每天|每日|每)?", t)
        if m and re.search(r"做|生成|加工", t):
            src = self._match_table(m.group(1), ctx) or {"name": m.group(1)}
            src_name = src["name"]
            target = src_name
            if src_name.startswith("ods_"):
                target = "dwd_" + src_name[4:]
            elif not src_name.startswith(("dwd_", "dws_", "ads_")):
                target = "dwd_" + src_name
            ds_name = src.get("ds_name") or self._match_datasource(t, ctx) or ""
            where = ""
            fm = re.search(r"(剔除|排除|过滤|去掉)([^，,。；;]*)", t)
            if fm:
                where = (f"WHERE 1=1 /* TODO: 依据[{fm.group(0)}]补充过滤条件，"
                         f"示例: is_test = 0 */")
            sql = f"INSERT INTO {target}\nSELECT *\nFROM {src_name}\n{where}".replace("\n\n", "\n")
            cron = _extract_cron(t) or "0 3 * * *"
            return ChatResult(tool_calls=[ToolCall("r1", "create_etl_job", {
                "name": f"etl_{target}", "datasource": ds_name, "source_table": src_name,
                "target_table": target, "sql": sql, "schedule_cron": cron})])

        # --- 同步 ---
        if re.search(r"同步|导入|抽取|采集到|搬", t):
            table_m = re.search(r"(\w+?)?的?\s*(\w+?)表", t)
            src_ds = self._match_datasource(t, ctx)
            table = None
            for tb in ctx.get("tables", []):
                if tb["name"].lower() in t.lower():
                    table = tb["name"]
                    break
            if table is None and table_m:
                table = table_m.group(2) if table_m.group(2) not in ("源", "数据") else None
            # 目标
            tds = None
            after = t.split("同步到")[-1] if "同步到" in t else t
            for name in ctx.get("datasources", []):
                if name.lower() in after.lower():
                    tds = name
                    break
            # 目标表：取"同步到"之后的 token，剔除数据源名与助词，优先分层命名（ods_/dwd_/...）
            ttable = None
            tokens = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", after)
            ds_lower = {n.lower() for n in ctx.get("datasources", [])}
            layer_pref = [x for x in tokens
                          if x.lower().split("_")[0] in ("ods", "dwd", "dws", "ads", "dim", "dw")]
            if layer_pref:
                ttable = layer_pref[-1]
            else:
                cands = [x for x in tokens if x.lower() not in ds_lower and len(x) > 1]
                if len(cands) >= 2:
                    ttable = cands[-1]  # 最后一个非数据源 token
            if not ttable and table:
                ttable = ("ods_" + table) if not table.startswith("ods_") else table
            if not table:
                return ChatResult(content="请告诉我源表名，例如：「把 mysql_demo 的 orders 表每天同步到 warehouse 的 ods_orders」")
            args = {
                "source_datasource": src_ds or "",
                "source_table": table,
                "target_datasource": tds or "",
                "target_table": ttable or f"ods_{table}",
                "schedule_cron": _extract_cron(t),
            }
            if re.search(r"全量|覆盖", t):
                args["write_mode"] = "truncate_insert"
            if re.search(r"增量", t):
                args["increment_enabled"] = True
            return ChatResult(tool_calls=[ToolCall("r1", "create_sync_job", args)])

        # --- 查数 ---
        m_tab = self._match_table(t, ctx)
        if re.search(r"^(查|看|统计|多少|哪些|展示|给我)|top\s*\d+|前\s*\d+行|多少", t.lower()) and \
           (re.search(r"表|行|数量|多少|top|统计|订单|用户|金额", t.lower()) or m_tab):
            tb = m_tab
            if tb is None:
                names = [x["name"] for x in ctx.get("tables", [])][:5]
                return ChatResult(content=f"没找到匹配的表。当前可查询的表（部分）：{ '、'.join(names) }。"
                                          "也可以说「查 warehouse 的 orders 前10行」。")
            ds_name = tb.get("ds_name") or self._match_datasource(t, ctx) or ""
            sql = self._gen_query_sql(t, tb["name"])
            return ChatResult(tool_calls=[ToolCall("r1", "query_data", {
                "datasource": ds_name, "sql": sql})])
        return None

    def _extract_name(self, text: str) -> str:
        for pat in (r"[「'\"](.+?)[」'\"]", r"(?:任务|工作流)\s*[:：]?\s*(\S+)"):
            m = re.search(pat, text)
            if m:
                return m.group(1)
        # 动词后直接跟名字：「暂停 sync_orders_to_ods_orders」
        m = re.search(r"(?:暂停|恢复|重跑|重新跑|触发|上线|下线)\s+([a-zA-Z0-9_\u4e00-\u9fa5-]+)", text)
        if m:
            return m.group(1)
        return ""

    def _gen_query_sql(self, text: str, table: str) -> str:
        n = _num(text)
        t = text.lower()
        if re.search(r"前\s*\d+行|前\s*\d+条|样例|示例", t):
            return f"SELECT * FROM {table} LIMIT {n}"
        if re.search(r"多少(行|条|个|单|订单|用户)|总数|总量|count", t):
            return f"SELECT COUNT(*) AS cnt FROM {table}"
        if re.search(r"top\s*\d+|前\s*\d+名", t):
            return (f"SELECT * FROM {table} LIMIT {n} -- 规则引擎建议："
                    f"请补充分组列与指标列，如 SELECT group_col, SUM(metric) FROM {table} GROUP BY 1 ORDER BY 2 DESC LIMIT {n}")
        m = re.search(r"每天|按天|每日", t)
        if m:
            return (f"SELECT dt, COUNT(*) AS cnt FROM {table} GROUP BY dt ORDER BY dt DESC LIMIT {n} "
                    f"-- 规则引擎建议：请确认日期列名（示例用 dt）")
        return f"SELECT * FROM {table} LIMIT {n}"


def get_llm():
    if config.AI_API_KEY and config.AI_BASE_URL:
        return OpenAICompatLLM(config.AI_BASE_URL, config.AI_API_KEY, config.AI_MODEL)
    return None  # agent 层将自动降级 RuleLLM
