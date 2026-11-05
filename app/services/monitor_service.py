"""数据监控服务：7 类规则检查器 + 调度注册 + 告警联动。

检查失败 → 复用告警通道（email/webhook）→ AlertRecord（instance_id=0，标题 [DataPilot] 监控告警）。
"""
import datetime
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import MonitorRule, MonitorCheck, DataSource, Workflow, TaskInstance, AlertChannel, AlertRecord
from . import sqlguard
from .engine_registry import run_query


def _q(ident: str, ds_type: str) -> str:
    """标识符引用：postgres/oracle 用双引号，其余（mysql/doris/starrocks/clickhouse/sqlite）用反引号。"""
    return f'"{ident}"' if ds_type in ("postgres", "oracle") else f"`{ident}`"


def _parse_dt(v) -> Optional[datetime.datetime]:
    if v is None:
        return None
    s = str(v).strip().rstrip("Z")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(s[:19], fmt)
        except Exception:
            continue
    return None


def _scalar(db: Session, ds: DataSource, sql: str, params=None):
    cols, rows, _ = run_query(ds, sql, params=params, max_rows=2,
                              timeout_s=30)
    if not rows:
        return None
    return rows[0][0]


# ---------------------------------------------------------------- 检查器

def check_rule(db: Session, rule: MonitorRule) -> MonitorCheck:
    t0 = datetime.datetime.now()
    status, metric, message = "passed", "", ""
    try:
        if rule.rule_type == "task_duration":
            status, metric, message = _CHECKERS[rule.rule_type](db, rule, None)
        else:
            ds = db.get(DataSource, rule.datasource_id)
            if not ds:
                raise ValueError("数据源不存在")
            status, metric, message = _CHECKERS[rule.rule_type](db, rule, ds)
    except Exception as e:
        status, metric, message = "error", "", f"检查执行异常: {e}"

    cost_ms = int((datetime.datetime.now() - t0).total_seconds() * 1000)
    check = MonitorCheck(rule_id=rule.id, rule_name=rule.name, rule_type=rule.rule_type,
                         status=status, metric=metric[:250], message=message[:2000],
                         severity=rule.severity, cost_ms=cost_ms)
    db.add(check)
    rule.last_status = status
    rule.last_checked_at = datetime.datetime.now()
    rule.last_message = message[:500]
    db.commit()
    # 每规则滚动保留最近 500 条检查记录
    from sqlalchemy import desc as _desc, delete as _delete
    cnt = db.query(func.count(MonitorCheck.id)).filter(
        MonitorCheck.rule_id == rule.id).scalar() or 0
    if cnt > 500:
        sub = (db.query(MonitorCheck.id)
                 .filter(MonitorCheck.rule_id == rule.id)
                 .order_by(_desc(MonitorCheck.id))
                 .offset(500).subquery())
        db.execute(_delete(MonitorCheck).where(
            MonitorCheck.id.in_(db.query(sub.c.id))))
        db.commit()
    if status == "failed":
        suppressed = _fire_alerts(db, rule, check)
        if suppressed:
            check.message = (check.message or "") + "（静默期内，未重复通知）"
            db.commit()
    return check


def _check_row_count_fluctuation(db: Session, rule: MonitorRule, ds: DataSource):
    cur = int(_scalar(db, ds, f"SELECT COUNT(*) FROM {_q(rule.table_name, ds.type)}") or 0)
    base = rule.baseline_count
    metric = f"当前 {cur} 行 / 基线 {base if base is not None else '未建立'}"
    if base is None:
        rule.baseline_count = cur
        return "passed", metric, "首次检查，基线已建立"
    pct = float((rule.params or {}).get("pct", 50))
    if base > 0:
        change = abs(cur - base) / base * 100
        if change > pct:
            return "failed", metric, f"行数波动 {change:.1f}% 超过阈值 {pct}%（基线 {base} → 当前 {cur}）"
    else:  # 基线为 0：从空到有数据视为正常
        rule.baseline_count = cur
        return "passed", metric, "基线为 0，已更新基线"
    rule.baseline_count = cur
    return "passed", metric, f"波动 {change:.1f}% 在阈值内"


def _check_row_count_threshold(db: Session, rule: MonitorRule, ds: DataSource):
    cur = int(_scalar(db, ds, f"SELECT COUNT(*) FROM {_q(rule.table_name, ds.type)}") or 0)
    p = rule.params or {}
    lo, hi = p.get("min"), p.get("max")
    metric = f"当前 {cur} 行（阈值 [{lo if lo is not None else '-∞'}, {hi if hi is not None else '∞'}]）"
    if lo is not None and cur < int(lo):
        return "failed", metric, f"行数 {cur} 低于下限 {lo}"
    if hi is not None and cur > int(hi):
        return "failed", metric, f"行数 {cur} 超过上限 {hi}"
    return "passed", metric, ""


def _check_null_rate(db: Session, rule: MonitorRule, ds: DataSource):
    col = _q(rule.column_name, ds.type)
    cols, rows, _ = run_query(ds, f"SELECT COUNT(*), COUNT({col}) FROM {_q(rule.table_name, ds.type)}",
                              max_rows=1, timeout_s=30)
    total, nonnull = (rows[0][0] or 0), (rows[0][1] or 0)
    if not total:
        return "passed", "0 行", "空表跳过空值率检查"
    rate = (total - nonnull) / total * 100
    pct = float((rule.params or {}).get("pct", 5))
    metric = f"{rule.column_name} 空值率 {rate:.2f}%"
    if rate > pct:
        return "failed", metric, f"空值率 {rate:.2f}% 超过阈值 {pct}%（{total - nonnull}/{total}）"
    return "passed", metric, ""


def _check_unique(db: Session, rule: MonitorRule, ds: DataSource):
    col = _q(rule.column_name, ds.type)
    dup = int(_scalar(db, ds, f"SELECT COUNT(*) - COUNT(DISTINCT {col}) FROM {_q(rule.table_name, ds.type)}") or 0)
    metric = f"{rule.column_name} 重复值 {dup} 个"
    if dup > 0:
        return "failed", metric, f"唯一性校验失败：{rule.column_name} 有 {dup} 个重复值"
    return "passed", metric, ""


def _check_freshness(db: Session, rule: MonitorRule, ds: DataSource):
    col = _q(rule.column_name, ds.type)
    mx = _scalar(db, ds, f"SELECT MAX({col}) FROM {_q(rule.table_name, ds.type)}")
    hours = float((rule.params or {}).get("hours", 24))
    metric = f"{rule.column_name} 最新值: {mx}"
    dt = _parse_dt(mx)
    if dt is None:
        return "error", metric, f"无法解析时间值: {mx}（需 YYYY-MM-DD[ HH:MM:SS]）"
    age_h = (datetime.datetime.now() - dt).total_seconds() / 3600
    if age_h > hours:
        return "failed", metric, f"数据延迟 {age_h:.1f} 小时超过阈值 {hours} 小时（最新: {mx}）"
    return "passed", metric, f"延迟 {age_h:.1f} 小时"


def _check_sql(db: Session, rule: MonitorRule, ds: DataSource):
    sql = (rule.params or {}).get("sql") or ""
    if not sql.strip():
        raise ValueError("sql_check 规则缺少 params.sql")
    dialect = sqlguard.dialect_of(ds.type)
    ok, msg = sqlguard.check_readonly(sql, dialect)
    if not ok:
        raise ValueError(f"监控 SQL 非只读: {msg}")
    cols, rows, _ = run_query(ds, sql, max_rows=20, timeout_s=30)
    metric = f"命中 {len(rows)} 行"
    if rows:
        sample = "; ".join(str(tuple(r))[:80] for r in rows[:3])
        return "failed", metric, f"自定义校验命中 {len(rows)} 行问题数据: {sample}"
    return "passed", metric, ""


def _check_task_duration(db: Session, rule: MonitorRule, ds: DataSource):
    """任务时长监控：最近实例运行时长超阈值即告警（ds 不使用，保持签名统一）。"""
    p = rule.params or {}
    wf_name = p.get("workflow_name") or ""
    max_min = float(p.get("max_minutes", 30))
    wf = db.query(Workflow).filter(Workflow.name == wf_name).first()
    if not wf:
        return "error", "", f"工作流不存在: {wf_name}"
    inst = (db.query(TaskInstance)
              .filter(TaskInstance.workflow_id == wf.id)
              .order_by(TaskInstance.id.desc()).first())
    if not inst:
        return "passed", "暂无实例", "工作流尚无运行实例"
    dur_ms = 0
    if inst.started_at and inst.finished_at:
        dur_ms = int((inst.finished_at - inst.started_at).total_seconds() * 1000)
    dur_min = dur_ms / 60000
    metric = f"最近实例 #{inst.id} 时长 {dur_ms} ms（状态 {inst.status}）"
    if inst.status in ("running", "waiting"):
        return "passed", metric, "实例仍在运行，跳过时长判定"
    if dur_min > max_min:
        return "failed", metric, f"任务时长 {dur_min:.1f} 分钟超过阈值 {max_min} 分钟"
    return "passed", metric, ""


_CHECKERS = {
    "row_count_fluctuation": _check_row_count_fluctuation,
    "row_count_threshold": _check_row_count_threshold,
    "null_rate": _check_null_rate,
    "unique_check": _check_unique,
    "freshness": _check_freshness,
    "sql_check": _check_sql,
    "task_duration": _check_task_duration,
}

RULE_TYPES = list(_CHECKERS.keys())


# ---------------------------------------------------------------- 告警联动

def _fire_alerts(db: Session, rule: MonitorRule, check: MonitorCheck) -> bool:
    """失败告警（带静默窗口）。返回 True 表示本次处于静默期未发送。"""
    # 静默窗口：mute_hours 内同一规则不重复通知（检查记录照常落库）
    if rule.mute_hours and rule.mute_hours > 0 and rule.last_alerted_at:
        elapsed = (datetime.datetime.now() - rule.last_alerted_at).total_seconds() / 3600
        if elapsed < rule.mute_hours:
            return True
    channels = db.query(AlertChannel).filter(AlertChannel.enabled == True).all()  # noqa: E712
    if not channels:
        return False
    title = f"[DataPilot] 监控告警[{rule.severity}]: {rule.name}"
    group_tag = f"（分组: {rule.group_name}）" if rule.group_name else ""
    content = (f"规则: {rule.name}（{rule.rule_type}）{group_tag}\n"
               f"对象: {rule.table_name or '-'}{('.' + rule.column_name) if rule.column_name else ''}\n"
               f"指标: {check.metric}\n说明: {check.message}\n时间: {check.ts}")
    from . import alert as alert_svc
    for ch in channels:
        status = "ok"
        try:
            if ch.type == "email":
                alert_svc._send_email(ch.config or {}, title, content)
            elif ch.type == "webhook":
                alert_svc._send_webhook(ch.config or {}, title, content, inst=None)
            else:
                status = "failed"
        except Exception:
            status = "failed"
        db.add(AlertRecord(channel_id=ch.id, channel_name=ch.name, instance_id=0,
                           title=title, content=content, status=status))
    rule.last_alerted_at = datetime.datetime.now()
    db.commit()
    return False


# ---------------------------------------------------------------- 调度注册

def _scheduled_check(rule_id: int):
    from ..db import SessionLocal
    db = SessionLocal()
    try:
        rule = db.get(MonitorRule, rule_id)
        if rule and rule.enabled:
            check_rule(db, rule)
    finally:
        db.close()


def apply_schedule(db: Session, rule: MonitorRule):
    from . import sched_service
    sched = sched_service._get_scheduler()
    job_id = f"mon_{rule.id}"
    if sched.get_job(job_id):
        sched.remove_job(job_id)
    if not rule.enabled:
        return
    ok, _ = sched_service.validate_cron(rule.cron)
    if not ok or not rule.cron.strip():
        return
    from apscheduler.triggers.cron import CronTrigger
    sched.add_job(_scheduled_check, trigger=CronTrigger.from_crontab(rule.cron.strip()),
                  args=[rule.id], id=job_id, name=job_id, replace_existing=True,
                  job_defaults={"coalesce": True, "misfire_grace_time": 3600})


def register_all(db: Session):
    """启动时重建全部监控调度。"""
    for rule in db.query(MonitorRule).filter(MonitorRule.enabled == True).all():  # noqa: E712
        try:
            apply_schedule(db, rule)
        except Exception:
            pass
