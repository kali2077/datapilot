from datetime import datetime
from typing import Optional

from sqlalchemy import String, Integer, DateTime, JSON, Boolean, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class HadoopCluster(Base):
    """Hadoop 平台（集群注册表）：一次注册，多组件派生数据源。"""
    __tablename__ = "ds_hadoop_cluster"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    hdfs_uri: Mapped[str] = mapped_column(String(256), default="")     # WebHDFS http://nn:9870
    hs2_uri: Mapped[str] = mapped_column(String(256), default="")      # HiveServer2 thrift://h:10000
    metastore_uri: Mapped[str] = mapped_column(String(256), default="")
    spark_uri: Mapped[str] = mapped_column(String(256), default="")    # Spark thrift://h:10001
    trino_uri: Mapped[str] = mapped_column(String(256), default="")    # http://trino:8080/hive
    trino_catalog: Mapped[str] = mapped_column(String(64), default="")
    kerberos: Mapped[dict] = mapped_column(JSON, default=dict)  # {enabled,realm,principal,keytab_name}
    description: Mapped[str] = mapped_column(String(256), default="")
    owner_id: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class MaskRule(Base):
    """列级数据脱敏规则：作用于查询工作台/智能问数/数据服务出参（同步链路不受影响）。"""
    __tablename__ = "ds_mask_rule"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    datasource_id: Mapped[int] = mapped_column(Integer, default=0)  # 0=全部数据源
    column_name: Mapped[str] = mapped_column(String(128), index=True)
    strategy: Mapped[str] = mapped_column(String(16), default="phone")  # phone/id_card/email/custom
    custom_regex: Mapped[str] = mapped_column(String(256), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    owner_id: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class SysSetting(Base):
    __tablename__ = "sys_setting"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class DataSource(Base):
    __tablename__ = "ds_datasource"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    type: Mapped[str] = mapped_column(String(16))  # mysql/doris/starrocks/oracle/clickhouse/postgres/sqlite
    host: Mapped[str] = mapped_column(String(128), default="")
    port: Mapped[int] = mapped_column(Integer, default=0)
    database: Mapped[str] = mapped_column(String(128), default="")
    username: Mapped[str] = mapped_column(String(64), default="")
    password_enc: Mapped[str] = mapped_column(String(512), default="")
    params: Mapped[dict] = mapped_column(JSON, default=dict)  # sqlite: {"path": "..."}
    description: Mapped[str] = mapped_column(String(256), default="")
    owner_id: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)
