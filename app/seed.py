"""种子数据：默认账号、演示数据源（SQLite 双库）、类型映射知识库、术语、样例。"""
import os
import random

from sqlalchemy.orm import Session

from .db import SessionLocal, engine
from .models import (User, DataSource, TypeMapping, GlossaryTerm, FewShot,
                     MetaTable, MetaColumn)
from .security import hash_password, encrypt_secret
from . import config


def run(  # noqa: C901
):
    db = SessionLocal()
    try:
        if not db.query(User).first():
            for uname, pwd, role, dn in [
                ("admin", "admin123", "admin", "平台管理员"),
                ("engineer", "engineer123", "engineer", "数据工程师"),
                ("analyst", "analyst123", "analyst", "数据分析师"),
                ("viewer", "viewer123", "viewer", "只读访客"),
            ]:
                db.add(User(username=uname, password_hash=hash_password(pwd),
                            display_name=dn, role=role))
            db.commit()
            print("[seed] 默认账号已创建: admin/admin123 engineer/engineer123 "
                  "analyst/analyst123 viewer/viewer123")

        if not db.query(DataSource).filter(DataSource.type == "sqlite").first():
            _make_demo(db)
        _make_type_mappings(db)
        _make_glossary(db)
        _make_fewshot(db)
        db.commit()
    finally:
        db.close()


def _make_demo(db: Session):
    src_path = os.path.join(config.DATA_DIR, "demo_source.db")
    wh_path = os.path.join(config.DATA_DIR, "demo_warehouse.db")
    if not os.path.exists(src_path):
        _build_demo_source(src_path)
    for name, path, desc in [("demo_source", src_path, "演示业务库（SQLite）"),
                             ("demo_warehouse", wh_path, "演示数仓（SQLite，模拟 Doris）")]:
        if not db.query(DataSource).filter(DataSource.name == name).first():
            db.add(DataSource(name=name, type="sqlite", description=desc,
                              params={"path": path}, owner_id=1))
    db.commit()

    # 自动采集演示数据源元数据
    from .services import meta_service
    for ds in db.query(DataSource).filter(DataSource.type == "sqlite").all():
        try:
            meta_service.collect_datasource(db, ds)
        except Exception as e:
            print(f"[seed] 采集 {ds.name} 失败: {e}")
    # 演示表补充人工描述（中文问数依赖元数据注释质量）
    desc_map = {"orders": "订单明细表：订单ID、用户、金额、状态、渠道、日期",
                "customers": "客户表：客户ID、姓名、城市、等级"}
    for mt in db.query(MetaTable).all():
        if mt.name in desc_map and not mt.manual_desc:
            mt.manual_desc = desc_map[mt.name]
    db.commit()


def _build_demo_source(path: str):
    import sqlite3
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute("""CREATE TABLE orders (
        order_id INTEGER PRIMARY KEY,
        user_id INTEGER,
        user_name TEXT,
        amount REAL,
        status TEXT,
        channel TEXT,
        dt TEXT,
        update_time TEXT,
        is_test INTEGER DEFAULT 0)""")
    cur.execute("""CREATE TABLE customers (
        customer_id INTEGER PRIMARY KEY,
        name TEXT,
        city TEXT,
        level TEXT)""")
    random.seed(42)
    cities = ["北京", "上海", "广州", "深圳", "杭州"]
    channels = ["app", "web", "mini_program"]
    levels = ["normal", "vip", "svip"]
    for i in range(1, 61):
        day = 20 - (i % 20)
        cur.execute("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?)", (
            i, (i % 12) + 1, f"用户{i%12+1}",
            round(random.uniform(20, 2000), 2),
            random.choice(["paid", "paid", "refunded", "pending"]),
            random.choice(channels),
            f"2026-10-{day:02d}",
            f"2026-10-{day:02d} 1{i%10}:23:45",
            1 if i % 17 == 0 else 0))
    for i in range(1, 13):
        cur.execute("INSERT INTO customers VALUES (?,?,?,?)", (
            i, f"客户{i}", random.choice(cities), random.choice(levels)))
    conn.commit()
    conn.close()


def _make_type_mappings(db: Session):
    if db.query(TypeMapping).first():
        return
    doris_map = {"int": "INT", "bigint": "BIGINT", "tinyint": "TINYINT", "smallint": "SMALLINT",
                 "varchar": "VARCHAR({n})", "char": "CHAR({n})", "decimal": "DECIMAL({p},{s})",
                 "double": "DOUBLE", "float": "DOUBLE", "datetime": "DATETIME",
                 "timestamp": "DATETIME", "date": "DATE", "text": "STRING", "json": "STRING"}
    mysql_map = {"int": "INT", "bigint": "BIGINT", "tinyint": "TINYINT", "smallint": "SMALLINT",
                 "varchar": "VARCHAR({n})", "char": "CHAR({n})", "decimal": "DECIMAL({p},{s})",
                 "double": "DOUBLE", "float": "DOUBLE", "datetime": "DATETIME",
                 "timestamp": "TIMESTAMP", "date": "DATE", "text": "TEXT", "json": "TEXT",
                 "boolean": "TINYINT"}
    sqlite_map = {"int": "INTEGER", "bigint": "INTEGER", "varchar": "TEXT", "char": "TEXT",
                  "decimal": "NUMERIC", "double": "REAL", "float": "REAL", "datetime": "TEXT",
                  "date": "TEXT", "text": "TEXT", "json": "TEXT", "boolean": "INTEGER"}
    for target, mapping in [("doris", doris_map), ("mysql", mysql_map), ("sqlite", sqlite_map)]:
        for src, tgt in mapping.items():
            db.add(TypeMapping(source_engine="*", target_engine=target,
                               source_dtype=src, target_dtype=tgt))
    db.commit()


def _make_glossary(db: Session):
    if db.query(GlossaryTerm).first():
        return
    for term, syn, dfn in [
        ("GMV", "成交总额,流水", "已支付订单的金额总和（status=paid 的 amount 求和）"),
        ("测试账号", "test账号,脏数据", "is_test=1 的订单/用户，统计口径需剔除"),
        ("ods", "", "操作数据层：从业务库原样同步的贴源表，命名 ods_xxx"),
        ("dwd", "", "明细数据层：清洗后的明细宽表，命名 dwd_xxx"),
    ]:
        db.add(GlossaryTerm(term=term, synonyms=syn, definition=dfn, created_by="seed"))


def _make_fewshot(db: Session):
    if db.query(FewShot).first():
        return
    for q, sql in [
        ("查一下GMV", "SELECT SUM(amount) AS gmv FROM orders WHERE status = 'paid' AND is_test = 0"),
        ("订单量Top10", "SELECT user_name, COUNT(*) AS cnt FROM orders WHERE is_test = 0 "
                        "GROUP BY user_name ORDER BY cnt DESC LIMIT 10"),
    ]:
        db.add(FewShot(question=q, sql=sql))
