from datetime import datetime
from typing import Optional

from sqlalchemy import String, Integer, DateTime, JSON, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class MonitorRule(Base):
    """数据监控规则。

    rule_type 与 params 约定：
    - row_count_fluctuation: {pct: 30}            行数较基线波动超 pct%（基线自动建立）
    - row_count_threshold:   {min: 1, max: ...}   行数越界
    - null_rate:             {pct: 5}             column 空值率 > pct%
    - unique_check:          {}                   column 重复值数 > 0
    - freshness:             {hours: 24}          column 最大时间距今超 hours 小时
    - sql_check:             {sql: "SELECT ..."}  自定义只读 SQL 返回行数 > 0 即告警
    - task_duration:         {workflow_name, max_minutes} 最近实例时长超限
    """
    __tablename__ = "mon_rule"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    rule_type: Mapped[str] = mapped_column(String(32))
    datasource_id: Mapped[int] = mapped_column(Integer, default=0)
    table_name: Mapped[str] = mapped_column(String(128), default="")
    column_name: Mapped[str] = mapped_column(String(128), default="")
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    cron: Mapped[str] = mapped_column(String(64), default="0 6 * * *")
    severity: Mapped[str] = mapped_column(String(16), default="warning")  # warning/critical
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    group_name: Mapped[str] = mapped_column(String(64), default="")  # 规则分组
    mute_hours: Mapped[int] = mapped_column(Integer, default=24)  # 告警静默窗口(小时)，0=每次都通知
    last_alerted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    baseline_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    last_status: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)  # passed/failed/error
    last_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_message: Mapped[str] = mapped_column(String(512), default="")
    owner_id: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class MonitorCheck(Base):
    __tablename__ = "mon_check"
    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[int] = mapped_column(Integer, index=True)
    rule_name: Mapped[str] = mapped_column(String(128), default="")
    rule_type: Mapped[str] = mapped_column(String(32), default="")
    status: Mapped[str] = mapped_column(String(16), default="passed")  # passed/failed/error
    metric: Mapped[str] = mapped_column(String(256), default="")
    message: Mapped[str] = mapped_column(Text, default="")
    severity: Mapped[str] = mapped_column(String(16), default="warning")
    cost_ms: Mapped[int] = mapped_column(Integer, default=0)
    ts: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
