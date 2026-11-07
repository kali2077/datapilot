from datetime import datetime
from typing import Optional

from sqlalchemy import String, Integer, DateTime, Text, JSON, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class User(Base):
    __tablename__ = "sys_user"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    display_name: Mapped[str] = mapped_column(String(64), default="")
    role: Mapped[str] = mapped_column(String(16), default="viewer")  # admin/engineer/analyst/viewer
    status: Mapped[str] = mapped_column(String(16), default="active")  # active/disabled
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class AuditLog(Base):
    __tablename__ = "sys_audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, default=0)
    username: Mapped[str] = mapped_column(String(64), default="")
    action: Mapped[str] = mapped_column(String(64), index=True)
    resource_type: Mapped[str] = mapped_column(String(32), default="")
    resource_id: Mapped[str] = mapped_column(String(64), default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    source: Mapped[str] = mapped_column(String(8), default="ui")  # ui/api/ai
    ip: Mapped[str] = mapped_column(String(64), default="")
    ts: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)


class ChatSession(Base):
    __tablename__ = "ai_chat_session"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    title: Mapped[str] = mapped_column(String(128), default="新对话")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class ChatMessage(Base):
    __tablename__ = "ai_chat_message"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(Integer, index=True)
    role: Mapped[str] = mapped_column(String(16))  # user/assistant/tool
    content: Mapped[str] = mapped_column(Text, default="")
    tool_calls: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    meta: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # 卡片/图表/SQL/附件等结构化信息
    confirm_token: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    confirm_status: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)  # pending/confirmed/expired
    llm_provider: Mapped[str] = mapped_column(String(32), default="")
    ts: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class ChatAttachment(Base):
    __tablename__ = "ai_chat_attachment"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(Integer, index=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    mime: Mapped[str] = mapped_column(String(128), default="")
    size: Mapped[int] = mapped_column(Integer, default=0)
    path: Mapped[str] = mapped_column(String(512), default="")
    extract: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 表格/文档类上传时解析的文本
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
