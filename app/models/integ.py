from datetime import datetime
from typing import Optional

from sqlalchemy import String, Integer, DateTime, JSON, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class SyncJob(Base):
    """数据集成任务。config 结构：
    {
      "columns": [{"source": "id", "target": "id", "source_type": "INTEGER", "target_type": "BIGINT"}...],
      "write_mode": "append|truncate_insert|replace",
      "partition_col": "dt",           # replace 时按该列清理
      "increment": {"enabled": false, "column": "update_time",
                     "mode": "time_range", "expr": "${bizdate}"},
      "pre_sql": "", "post_sql": ""
    }
    """
    __tablename__ = "integ_sync_job"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    source_datasource_id: Mapped[int] = mapped_column(Integer)
    source_table: Mapped[str] = mapped_column(String(128), default="")
    source_query: Mapped[str] = mapped_column(Text, default="")  # 自定义查询（优先于 table）
    target_datasource_id: Mapped[int] = mapped_column(Integer)
    target_table: Mapped[str] = mapped_column(String(128), default="")
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft/published/offline
    current_version: Mapped[int] = mapped_column(Integer, default=0)
    owner_id: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class SyncJobVersion(Base):
    __tablename__ = "integ_sync_job_version"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(Integer, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    rendered_datax_json: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class TypeMapping(Base):
    """字段类型映射知识库：source_type 正则前缀匹配 -> 目标类型。"""
    __tablename__ = "integ_type_mapping"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_engine: Mapped[str] = mapped_column(String(16), default="*")   # mysql/*/doris...
    target_engine: Mapped[str] = mapped_column(String(16), default="*")
    source_dtype: Mapped[str] = mapped_column(String(32))   # 如 varchar / int / decimal / datetime
    target_dtype: Mapped[str] = mapped_column(String(64))   # 如 varchar(这里可为模板 {n}) / bigint
    transform: Mapped[str] = mapped_column(String(64), default="")  # 可选转换函数说明
    note: Mapped[str] = mapped_column(String(256), default="")
