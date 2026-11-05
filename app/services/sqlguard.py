"""SQL 安全护栏（sqlglot）：只读校验 / ETL 写入校验 / LIMIT 包裹 / 方言适配。"""
from typing import List, Tuple

import sqlglot
from sqlglot import expressions as exp


def dialect_of(ds_type: str) -> str:
    return {"doris": "mysql", "starrocks": "mysql", "mysql": "mysql", "sqlite": "sqlite",
            "postgres": "postgres", "oracle": "oracle", "clickhouse": "clickhouse",
            "trino": "trino", "hive": "hive", "spark": "spark",
            "maxcompute": "hive", "mssql": "tsql"}.get(ds_type, "mysql")


def parse_one(sql: str, dialect: str = "mysql"):
    return sqlglot.parse_one(sql, read=dialect)


def check_readonly(sql: str, dialect: str = "mysql") -> Tuple[bool, str]:
    """查询类语句校验：仅允许 SELECT / WITH / UNION / EXPLAIN(DESCRIBE) / SHOW。"""
    try:
        statements = sqlglot.parse(sql, read=dialect)
    except Exception as e:
        return False, f"SQL 解析失败: {e}"
    if not statements:
        return False, "空 SQL"
    if len(statements) > 1:
        return False, "仅允许单条查询语句"
    for st in statements:
        if isinstance(st, (exp.Select, exp.Union, exp.Subquery, exp.With,
                           exp.Describe, exp.Show)):
            continue
        return False, f"仅允许只读查询，检测到: {type(st).__name__}"
    return True, ""


def check_etl_sql(sql: str, target_table: str, dialect: str = "mysql") -> Tuple[bool, str]:
    """ETL 写入校验：仅允许单条 INSERT INTO <target> SELECT ...；禁止其余 DML/DDL。"""
    try:
        statements = sqlglot.parse(sql, read=dialect)
    except Exception as e:
        return False, f"SQL 解析失败: {e}"
    if len(statements) != 1:
        return False, "ETL 任务仅允许单条语句"
    st = statements[0]
    if not isinstance(st, exp.Insert):
        return False, f"仅允许 INSERT INTO ... SELECT，检测到: {type(st).__name__}"
    if not isinstance(st.expression, (exp.Select, exp.Union)):
        return False, "INSERT 目标必须来自 SELECT 查询"
    target = st.this
    tname = target.name if hasattr(target, "name") else str(target)
    if tname.lower() != (target_table or "").lower().split(".")[-1]:
        return False, f"INSERT 目标表 {tname} 与任务声明目标 {target_table} 不一致"
    src = st.expression.find_all(exp.Table)
    for t in src:
        if (t.name or "").lower() == tname.lower():
            return False, "禁止 INSERT 目标表与源表相同（自写入）"
    return True, ""


def ensure_limit(sql: str, limit: int, dialect: str = "mysql") -> str:
    """无 LIMIT 的查询包一层外层 LIMIT。"""
    try:
        st = sqlglot.parse_one(sql, read=dialect)
        if isinstance(st, (exp.Select, exp.Union)):
            if st.args.get("limit") is None:
                return st.limit(limit).sql(dialect=dialect)
            return st.sql(dialect=dialect)
        return sql
    except Exception:
        return f"SELECT * FROM ({sql}) _t LIMIT {limit}"


def extract_sql_lineage(sql: str, dialect: str = "mysql") -> Tuple[List[str], str]:
    """从 INSERT...SELECT 提取 (源表列表, 目标表)。普通 SELECT 返回 (源表, '')。"""
    sources, target = [], ""
    try:
        st = sqlglot.parse_one(sql, read=dialect)
        if isinstance(st, exp.Insert):
            target = st.this.name if hasattr(st.this, "name") else str(st.this)
            st = st.expression
        for t in st.find_all(exp.Table):
            name = t.name
            if name and name.lower() != target.lower():
                sources.append(name)
    except Exception:
        pass
    return list(dict.fromkeys(sources)), target


def explain_sql(ds, sql: str) -> dict:
    """在目标数据源上 EXPLAIN 试跑。"""
    from .engine_registry import get_engine
    from sqlalchemy import text as _text
    if ds.type == "sqlite":
        esql = f"EXPLAIN QUERY PLAN {sql}"
    else:
        esql = f"EXPLAIN {sql}"
    eng = get_engine(ds)
    with eng.connect() as conn:
        rows = conn.execute(_text(esql)).fetchall()
    return {"ok": True, "plan": [str(r) for r in rows[:20]]}
