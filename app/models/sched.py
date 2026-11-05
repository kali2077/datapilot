from datetime import datetime
from typing import Optional

from sqlalchemy import String, Integer, DateTime, JSON, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class Workflow(Base):
    """调度工作流：sync 发布时自动生成单节点 datax 工作流；ETL 为多节点。"""
    __tablename__ = "sched_workflow"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    description: Mapped[str] = mapped_column(String(256), default="")
    schedule_cron: Mapped[str] = mapped_column(String(64), default="")  # 5段 cron；空=不调度
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    depends_on: Mapped[list] = mapped_column(JSON, default=list)  # 上游 workflow id 列表
    job_type: Mapped[str] = mapped_column(String(16), default="sync")  # sync/etl
    sync_job_id: Mapped[int] = mapped_column(Integer, default=0)
    owner_id: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class WorkflowNode(Base):
    __tablename__ = "sched_workflow_node"
    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_id: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    node_type: Mapped[str] = mapped_column(String(16), default="sql")  # datax/sql/python
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    # datax: {"sync_job_id": n}
    # sql:   {"datasource_id": n, "sql": "...", "target_table": "dwd_x"}
    # python:{"script": "..."}
    upstream: Mapped[list] = mapped_column(JSON, default=list)  # 上游 node id 列表
    ordinal: Mapped[int] = mapped_column(Integer, default=0)


class TaskInstance(Base):
    __tablename__ = "sched_instance"
    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_id: Mapped[int] = mapped_column(Integer, index=True)
    workflow_name: Mapped[str] = mapped_column(String(128), default="")
    trigger_type: Mapped[str] = mapped_column(String(16), default="manual")  # scheduled/manual/rerun
    logical_date: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    status: Mapped[str] = mapped_column(String(16), default="waiting", index=True)
    # waiting/running/success/failed/canceled
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    triggered_by: Mapped[str] = mapped_column(String(64), default="scheduler")


class NodeRun(Base):
    __tablename__ = "sched_node_run"
    id: Mapped[int] = mapped_column(primary_key=True)
    instance_id: Mapped[int] = mapped_column(Integer, index=True)
    node_id: Mapped[int] = mapped_column(Integer, default=0)
    node_name: Mapped[str] = mapped_column(String(128), default="")
    node_type: Mapped[str] = mapped_column(String(16), default="")
    status: Mapped[str] = mapped_column(String(16), default="waiting")
    log: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    attempt: Mapped[int] = mapped_column(Integer, default=1)


class AlertChannel(Base):
    __tablename__ = "sched_alert_channel"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    type: Mapped[str] = mapped_column(String(16), default="webhook")  # email/webhook
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    # email: {"to": "a@b.com,c@d.com", "smtp_host": "", "smtp_port": 465, "username": "", "password": ""}
    # webhook: {"url": "https://..."}
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class AlertRecord(Base):
    __tablename__ = "sched_alert_record"
    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(Integer, default=0)
    channel_name: Mapped[str] = mapped_column(String(64), default="")
    instance_id: Mapped[int] = mapped_column(Integer, default=0)
    title: Mapped[str] = mapped_column(String(256), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="ok")  # ok/failed
    ts: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
