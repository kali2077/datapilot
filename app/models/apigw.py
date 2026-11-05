from datetime import datetime
from typing import Optional

from sqlalchemy import String, Integer, DateTime, JSON, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class ApiDef(Base):
    __tablename__ = "apigw_api"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(String(256), default="")
    datasource_id: Mapped[int] = mapped_column(Integer)
    sql_template: Mapped[str] = mapped_column(Text)  # :param 绑定
    params: Mapped[list] = mapped_column(JSON, default=list)
    # [{"name":"start_date","type":"string","required":true,"default":"","validation":""}]
    cache_ttl: Mapped[int] = mapped_column(Integer, default=0)
    row_limit: Mapped[int] = mapped_column(Integer, default=1000)
    timeout_ms: Mapped[int] = mapped_column(Integer, default=5000)
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft/published/offline
    owner_id: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class ApiApp(Base):
    __tablename__ = "apigw_app"
    id: Mapped[int] = mapped_column(primary_key=True)
    app_name: Mapped[str] = mapped_column(String(64))
    app_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    app_secret_enc: Mapped[str] = mapped_column(String(512))  # Fernet 加密存储（验签时解密）
    rate_limit: Mapped[int] = mapped_column(Integer, default=10)  # QPS
    ip_whitelist: Mapped[str] = mapped_column(String(512), default="")  # 逗号分隔，空=不限
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class ApiGrant(Base):
    __tablename__ = "apigw_grant"
    id: Mapped[int] = mapped_column(primary_key=True)
    app_id: Mapped[int] = mapped_column(Integer, index=True)
    api_id: Mapped[int] = mapped_column(Integer, index=True)
    expire_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class ApiLog(Base):
    __tablename__ = "apigw_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    api_id: Mapped[int] = mapped_column(Integer, index=True)
    app_id: Mapped[int] = mapped_column(Integer, default=0)
    app_key: Mapped[str] = mapped_column(String(64), default="")
    params: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    cost_ms: Mapped[int] = mapped_column(Integer, default=0)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="ok")
    error: Mapped[str] = mapped_column(Text, default="")
    ts: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
