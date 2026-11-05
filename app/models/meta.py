from datetime import datetime
from typing import Optional

from sqlalchemy import String, Integer, DateTime, JSON, UniqueConstraint, Boolean, Text, Index
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class MetaTable(Base):
    __tablename__ = "meta_table"
    __table_args__ = (UniqueConstraint("datasource_id", "schema_name", "name", name="uq_meta_table"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    datasource_id: Mapped[int] = mapped_column(Integer, index=True)
    schema_name: Mapped[str] = mapped_column(String(128), default="")
    name: Mapped[str] = mapped_column(String(128), index=True)
    table_type: Mapped[str] = mapped_column(String(16), default="table")  # table/view
    comment: Mapped[str] = mapped_column(String(512), default="")
    manual_desc: Mapped[str] = mapped_column(String(512), default="")  # 人工补录，采集不覆盖
    owner_id: Mapped[int] = mapped_column(Integer, default=0)
    owner_name: Mapped[str] = mapped_column(String(64), default="")
    row_count: Mapped[int] = mapped_column(Integer, default=-1)
    last_collected_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class MetaColumn(Base):
    __tablename__ = "meta_column"
    __table_args__ = (UniqueConstraint("table_id", "name", name="uq_meta_column"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    table_id: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(128))
    data_type: Mapped[str] = mapped_column(String(64), default="")
    comment: Mapped[str] = mapped_column(String(512), default="")
    manual_desc: Mapped[str] = mapped_column(String(512), default="")
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    is_pk: Mapped[bool] = mapped_column(Boolean, default=False)
    sample_values: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)


class LineageEdge(Base):
    """任务级血缘：sync（同步任务）/ sql（ETL任务）产生。"""
    __tablename__ = "meta_lineage"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_datasource_id: Mapped[int] = mapped_column(Integer, default=0)
    source_schema: Mapped[str] = mapped_column(String(128), default="")
    source_table: Mapped[str] = mapped_column(String(128), default="")
    target_datasource_id: Mapped[int] = mapped_column(Integer, default=0)
    target_schema: Mapped[str] = mapped_column(String(128), default="")
    target_table: Mapped[str] = mapped_column(String(128), default="")
    job_type: Mapped[str] = mapped_column(String(16), default="sync")  # sync/sql
    job_id: Mapped[int] = mapped_column(Integer, default=0)
    workflow_id: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    __table_args__ = (
        Index("idx_lineage_src", "source_datasource_id", "source_table"),
        Index("idx_lineage_dst", "target_datasource_id", "target_table"),
    )


class Tag(Base):
    __tablename__ = "meta_tag"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    color: Mapped[str] = mapped_column(String(16), default="#409EFF")


class TableTag(Base):
    __tablename__ = "meta_table_tag"
    id: Mapped[int] = mapped_column(primary_key=True)
    table_id: Mapped[int] = mapped_column(Integer, index=True)
    tag_id: Mapped[int] = mapped_column(Integer, index=True)


class GlossaryTerm(Base):
    __tablename__ = "meta_glossary"
    id: Mapped[int] = mapped_column(primary_key=True)
    term: Mapped[str] = mapped_column(String(64), index=True)
    synonyms: Mapped[str] = mapped_column(String(256), default="")  # 逗号分隔
    definition: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(64), default="")


class FewShot(Base):
    """问数样例库：NL -> SQL few-shot，用于 RAG 注入。"""
    __tablename__ = "meta_fewshot"
    id: Mapped[int] = mapped_column(primary_key=True)
    question: Mapped[str] = mapped_column(String(256))
    sql: Mapped[str] = mapped_column(Text)
    datasource_id: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
