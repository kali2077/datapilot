from datetime import datetime
from typing import Optional

from sqlalchemy import String, Integer, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class SavedQuery(Base):
    """已保存的查询（个人工作台）。"""
    __tablename__ = "query_saved"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(128))
    datasource_id: Mapped[int] = mapped_column(Integer)
    sql: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class QueryHistory(Base):
    """查询历史（每用户保留最近 200 条）。"""
    __tablename__ = "query_history"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    datasource_id: Mapped[int] = mapped_column(Integer, default=0)
    datasource_name: Mapped[str] = mapped_column(String(64), default="")
    sql: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="ok")  # ok/error
    cost_ms: Mapped[int] = mapped_column(Integer, default=0)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    ts: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
