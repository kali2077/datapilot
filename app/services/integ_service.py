"""数据集成服务：校验链、类型映射知识库、DataX JSON 渲染、内置同步执行器、试跑。"""
import datetime
import json
import re
import time
from typing import List, Optional

from sqlalchemy import text as _text
from sqlalchemy.orm import Session

from ..models import DataSource, SyncJob, SyncJobVersion, TypeMapping, Workflow, WorkflowNode, LineageEdge
from .. import config
from .engine_registry import get_engine, execute_write
from . import sqlguard

# ---------------------------------------------------------------- 类型映射

# family -> 各引擎目标类型（{n}/{p}/{s} 按源类型括号参数替换）
GENERIC_MAP = {
    "int":      {"mysql": "INT", "doris": "INT", "starrocks": "INT", "sqlite": "INTEGER",
                 "postgres": "INTEGER", "oracle": "NUMBER(10)", "clickhouse": "Int32", "mssql": "INT"},
    "bigint":   {"mysql": "BIGINT", "doris": "BIGINT", "starrocks": "BIGINT", "sqlite": "INTEGER",
                 "postgres": "BIGINT", "oracle": "NUMBER(19)", "clickhouse": "Int64", "mssql": "BIGINT"},
    "smallint": {"mysql": "SMALLINT", "doris": "SMALLINT", "starrocks": "SMALLINT", "sqlite": "INTEGER",
                 "postgres": "SMALLINT", "oracle": "NUMBER(5)", "clickhouse": "Int16", "mssql": "SMALLINT"},
    "tinyint":  {"mysql": "TINYINT", "doris": "TINYINT", "starrocks": "TINYINT", "sqlite": "INTEGER",
                 "postgres": "SMALLINT", "oracle": "NUMBER(3)", "clickhouse": "UInt8", "mssql": "TINYINT"},
    "varchar":  {"mysql": "VARCHAR({n})", "doris": "VARCHAR({n})", "starrocks": "VARCHAR({n})",
                 "sqlite": "TEXT", "postgres": "VARCHAR({n})", "oracle": "VARCHAR2({n})",
                 "clickhouse": "String"},
    "char":     {"mysql": "CHAR({n})", "doris": "CHAR({n})", "starrocks": "CHAR({n})",
                 "sqlite": "TEXT", "postgres": "CHAR({n})", "oracle": "CHAR({n})",
                 "clickhouse": "String"},
    "decimal":  {"mysql": "DECIMAL({p},{s})", "doris": "DECIMAL({p},{s})", "starrocks": "DECIMAL({p},{s})",
                 "sqlite": "NUMERIC", "postgres": "NUMERIC({p},{s})", "oracle": "NUMBER({p},{s})",
                 "clickhouse": "Decimal({p},{s})"},
    "float":    {"mysql": "DOUBLE", "doris": "DOUBLE", "starrocks": "DOUBLE", "sqlite": "REAL",
                 "postgres": "DOUBLE PRECISION", "oracle": "BINARY_DOUBLE", "clickhouse": "Float64", "mssql": "FLOAT"},
    "double":   {"mysql": "DOUBLE", "doris": "DOUBLE", "starrocks": "DOUBLE", "sqlite": "REAL",
                 "postgres": "DOUBLE PRECISION", "oracle": "BINARY_DOUBLE", "clickhouse": "Float64", "mssql": "FLOAT"},
    "datetime": {"mysql": "DATETIME", "doris": "DATETIME", "starrocks": "DATETIME", "sqlite": "TEXT",
                 "postgres": "TIMESTAMP", "oracle": "DATE", "clickhouse": "DateTime", "mssql": "DATETIME2"},
    "date":     {"mysql": "DATE", "doris": "DATE", "starrocks": "DATE", "sqlite": "TEXT",
                 "postgres": "DATE", "oracle": "DATE", "clickhouse": "Date", "mssql": "DATE"},
    "timestamp": {"mysql": "TIMESTAMP", "doris": "DATETIME", "starrocks": "DATETIME", "sqlite": "TEXT",
                  "postgres": "TIMESTAMP", "oracle": "TIMESTAMP", "clickhouse": "DateTime", "mssql": "DATETIME2"},
    "text":     {"mysql": "TEXT", "doris": "STRING", "starrocks": "STRING", "sqlite": "TEXT",
                 "postgres": "TEXT", "oracle": "CLOB", "clickhouse": "String", "mssql": "NVARCHAR(MAX)"},
    "json":     {"mysql": "TEXT", "doris": "STRING", "starrocks": "STRING", "sqlite": "TEXT",
                 "postgres": "TEXT", "oracle": "CLOB", "clickhouse": "String", "mssql": "NVARCHAR(MAX)"},
    "boolean":  {"mysql": "TINYINT", "doris": "BOOLEAN", "starrocks": "BOOLEAN", "sqlite": "INTEGER",
                 "postgres": "BOOLEAN", "oracle": "NUMBER(1)", "clickhouse": "UInt8", "mssql": "BIT"},
    "unknown":  {"mysql": "TEXT", "doris": "STRING", "starrocks": "STRING", "sqlite": "TEXT",
                 "postgres": "TEXT", "oracle": "VARCHAR2(4000)", "clickhouse": "String", "mssql": "NVARCHAR(4000)"},
}
HIVE_FAMILY = {
    "int": {"hive": "INT", "spark": "INT", "maxcompute": "INT"},
    "bigint": {"hive": "BIGINT", "spark": "BIGINT", "maxcompute": "BIGINT"},
    "smallint": {"hive": "SMALLINT", "spark": "SMALLINT", "maxcompute": "SMALLINT"},
    "tinyint": {"hive": "TINYINT", "spark": "TINYINT", "maxcompute": "TINYINT"},
    "varchar": {"hive": "VARCHAR({n})", "spark": "VARCHAR({n})", "maxcompute": "STRING"},
    "char": {"hive": "CHAR({n})", "spark": "CHAR({n})", "maxcompute": "STRING"},
    "decimal": {"hive": "DECIMAL({p},{s})", "spark": "DECIMAL({p},{s})",
                "maxcompute": "DECIMAL({p},{s})"},
    "float": {"hive": "FLOAT", "spark": "FLOAT", "maxcompute": "DOUBLE"},
    "double": {"hive": "DOUBLE", "spark": "DOUBLE", "maxcompute": "DOUBLE"},
    "datetime": {"hive": "TIMESTAMP", "spark": "TIMESTAMP", "maxcompute": "DATETIME"},
    "date": {"hive": "DATE", "spark": "DATE", "maxcompute": "DATETIME"},
    "timestamp": {"hive": "TIMESTAMP", "spark": "TIMESTAMP", "maxcompute": "DATETIME"},
    "text": {"hive": "STRING", "spark": "STRING", "maxcompute": "STRING"},
    "json": {"hive": "STRING", "spark": "STRING", "maxcompute": "STRING"},
    "boolean": {"hive": "BOOLEAN", "spark": "BOOLEAN", "maxcompute": "BOOLEAN"},
    "unknown": {"hive": "STRING", "spark": "STRING", "maxcompute": "STRING"},
}
for _fam, _m in HIVE_FAMILY.items():
    GENERIC_MAP.setdefault(_fam, {}).update(_m)

TRINO_FAMILY = {
    "int": {"trino": "INTEGER"}, "bigint": {"trino": "BIGINT"},
    "smallint": {"trino": "SMALLINT"}, "tinyint": {"trino": "TINYINT"},
    "varchar": {"trino": "VARCHAR({n})", "mssql": "NVARCHAR({n})"}, "char": {"trino": "CHAR({n})", "mssql": "NCHAR({n})"},
    "decimal": {"trino": "DECIMAL({p},{s})", "mssql": "DECIMAL({p},{s})"}, "float": {"trino": "REAL"},
    "double": {"trino": "DOUBLE"}, "datetime": {"trino": "TIMESTAMP"},
    "date": {"trino": "DATE"}, "timestamp": {"trino": "TIMESTAMP"},
    "text": {"trino": "VARCHAR"}, "json": {"trino": "JSON"},
    "boolean": {"trino": "BOOLEAN"}, "unknown": {"trino": "VARCHAR"},
}
for _fam, _m in TRINO_FAMILY.items():
    GENERIC_MAP.setdefault(_fam, {}).update(_m)

FAMILY_PATTERNS = [
    (r"^(bigint|long)", "bigint"), (r"^(tinyint)", "tinyint"), (r"^(smallint|short)", "smallint"),
    (r"^(int|integer)", "int"), (r"^(decimal|numeric)", "decimal"), (r"^(double)", "double"),
    (r"^(float|real)", "float"), (r"^(datetime)", "datetime"), (r"^(timestamp)", "timestamp"),
    (r"^(date)", "date"), (r"^(n?varchar|character varying)", "varchar"), (r"^(n?char)", "char"),
    (r"^(text|string)", "text"), (r"^(json)", "json"), (r"^(bool|boolean)", "boolean"),
]


def dtype_family(dtype: str) -> str:
    d = (dtype or "").lower()
    for pat, fam in FAMILY_PATTERNS:
        if re.match(pat, d):
            return fam
    return "unknown"


def _extract_paren(dtype: str):
    m = re.search(r"\(([^)]*)\)", dtype or "")
    if not m:
        return {}
    parts = [p.strip() for p in m.group(1).split(",")]
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        return {"p": parts[0], "s": parts[1]}
    if parts and parts[0].isdigit():
        return {"n": parts[0], "p": parts[0], "s": 0}
    return {}


def map_dtype(db: Session, source_dtype: str, target_engine: str) -> str:
    """知识库优先（前缀匹配），否则内置通用映射；模板参数 {n}/{p}/{s} 按源类型替换。
    starrocks 复用 doris 知识库行（MySQL 协议同族）。"""
    fam = dtype_family(source_dtype)
    kb_engine = "doris" if target_engine == "starrocks" else target_engine
    rows = (db.query(TypeMapping)
              .filter(TypeMapping.target_engine == kb_engine).all())
    d = source_dtype.lower()
    out = None
    for r in rows:
        if d.startswith(r.source_dtype.lower()):
            out = r.target_dtype
            break
    if out is None:
        fam_map = GENERIC_MAP.get(fam, GENERIC_MAP["unknown"])
        out = fam_map.get(target_engine) or fam_map.get("mysql", "TEXT")
    params = _extract_paren(source_dtype)
    for k, v in params.items():
        out = out.replace("{%s}" % k, str(v))
    return out.replace("{n}", "255").replace("{p}", "20").replace("{s}", "0")


def auto_columns(db: Session, job: SyncJob) -> List[dict]:
    """未显式指定列时，从源库实时拉取列并生成映射。"""
    ds = db.get(DataSource, job.source_datasource_id)
    eng = get_engine(ds)
    cols = []
    if job.source_query:
        rs = eng.execute(_text(f"SELECT * FROM ({job.source_query}) _probe LIMIT 1"))
        for c in rs.keys():
            cols.append({"source": c, "target": c, "source_type": "unknown"})
    else:
        from sqlalchemy import inspect as sa_inspect
        insp = sa_inspect(eng)
        for col in insp.get_columns(job.source_table):
            cols.append({"source": col["name"], "target": col["name"],
                         "source_type": str(col.get("type"))})
    target_ds = db.get(DataSource, job.target_datasource_id)
    for c in cols:
        c["target_type"] = map_dtype(db, c.get("source_type", ""), target_ds.type)
    return cols


# ---------------------------------------------------------------- 校验链

def validate_job(db: Session, job: SyncJob, trial: bool = False) -> List[dict]:
    """返回问题列表；level=error 会阻止发布，warning 仅提示。"""
    issues: List[dict] = []
    src = db.get(DataSource, job.source_datasource_id)
    dst = db.get(DataSource, job.target_datasource_id)
    if not src:
        issues.append({"level": "error", "message": "源数据源不存在"})
        return issues
    if not dst:
        issues.append({"level": "error", "message": "目标数据源不存在"})
        return issues
    if src.id == dst.id and job.source_table == job.target_table and not job.source_query:
        issues.append({"level": "error", "message": "源表与目标表相同"})
    for ds, tag in ((src, "源"), (dst, "目标")):
        try:
            from .engine_registry import test_connection
            test_connection(ds)
        except Exception as e:
            issues.append({"level": "error", "message": f"{tag}数据源连接失败: {e}"})
    # 列
    cols = (job.config or {}).get("columns") or []
    if not cols:
        try:
            if src.type == "maxcompute":
                from . import mc_client
                cols = [{"source": n, "target": n, "source_type": tp,
                         "target_type": map_dtype(db, tp, dst.type)}
                        for n, tp in mc_client.columns(src, job.source_table)]
            else:
                cols = auto_columns(db, job)
            job.config = dict(job.config or {})
            job.config["columns"] = cols
            db.commit()
            issues.append({"level": "info", "message": f"未指定列映射，已自动读取 {len(cols)} 列"})
        except Exception as e:
            issues.append({"level": "error", "message": f"读取源表结构失败: {e}"})
            return issues
    if not job.source_query:
        try:
            if src.type == "maxcompute":
                from . import mc_client
                live = {n for n, _ in mc_client.columns(src, job.source_table)}
            else:
                live = {c["name"] for c in __import__("sqlalchemy").inspect(
                    get_engine(src)).get_columns(job.source_table)}
            missing = [c["source"] for c in cols if c["source"] not in live]
            if missing:
                issues.append({"level": "error", "message": f"源表缺少列: {', '.join(missing)}"})
        except Exception as e:
            issues.append({"level": "error", "message": f"读取源表失败: {e}"})
    if not job.target_table:
        issues.append({"level": "error", "message": "目标表未填写"})
    # 目标表存在性
    if job.target_table:
        try:
            if dst.type == "maxcompute":
                from . import mc_client
                exists = job.target_table in mc_client.table_names(dst)
            else:
                from sqlalchemy import inspect as sa_inspect
                insp = sa_inspect(get_engine(dst))
                exists = job.target_table in (
                    insp.get_table_names() + insp.get_view_names())
            if not exists:
                issues.append({"level": "warning",
                               "message": f"目标表 {job.target_table} 不存在，执行时将按映射自动创建"})
        except Exception:
            pass
    # 类型映射检查
    for c in cols:
        st = c.get("source_type", "")
        tt = c.get("target_type") or map_dtype(db, st, dst.type)
        if dtype_family(st) == "unknown" and st:
            issues.append({"level": "warning",
                           "message": f"列 {c['source']} 类型 {st} 未收录，将按 TEXT 映射"})
        c["target_type"] = tt
    # 增量配置
    inc = (job.config or {}).get("increment") or {}
    if inc.get("enabled") and not job.source_query:
        if not inc.get("column"):
            issues.append({"level": "error", "message": "启用增量但未指定增量列"})
    wm = (job.config or {}).get("write_mode", "append")
    if wm not in ("append", "truncate_insert", "replace"):
        issues.append({"level": "error", "message": f"未知写入模式: {wm}"})
    # cron 在调度层校验
    return issues


def suggest_target_ddl(db: Session, job: SyncJob, cols: Optional[List[dict]] = None) -> str:
    """目标表不存在时的自动建表 DDL 建议（按目标引擎方言生成）。"""
    dst = db.get(DataSource, job.target_datasource_id)
    cols = cols if cols else ((job.config or {}).get("columns") or [])
    if not cols:
        return ""
    t = dst.type if dst else "mysql"
    q = '"' if t in ("trino", "postgres", "oracle") else "`"
    defs = []
    for c in cols:
        tt = c.get("target_type") or ("STRING" if t in ("hive", "spark", "maxcompute") else "TEXT")
        defs.append(f"  {q}{c['target']}{q} {tt}")
    name = f"{q}{job.target_table}{q}"
    ddl = f"CREATE TABLE IF NOT EXISTS {name} (\n" + ",\n".join(defs) + "\n)"
    if t == "mysql":
        ddl += " DEFAULT CHARSET=utf8mb4"
    if t == "clickhouse":
        ddl += " ENGINE = MergeTree ORDER BY tuple()"
    if t in ("hive", "spark"):
        ddl += " STORED AS PARQUET"
    if t == "maxcompute":
        ddl += " LIFECYCLE 36500"
    if t == "oracle":
        ddl = ddl.replace(" IF NOT EXISTS", "")  # Oracle 无该语义
    if (job.config or {}).get("partition_col"):
        ddl += f"\n-- 建议分区列: {job.config['partition_col']}（Doris/Hive 建议按天分区）"
    return ddl


# ---------------------------------------------------------------- DataX 渲染

def _jdbc(ds: DataSource) -> str:
    if ds.type == "mysql":
        return f"jdbc:mysql://{ds.host}:{ds.port or 3306}/{ds.database}?useUnicode=true&characterEncoding=utf8"
    if ds.type == "doris":
        return f"jdbc:mysql://{ds.host}:{ds.port or 9030}/{ds.database}?useUnicode=true&characterEncoding=utf8"
    if ds.type == "starrocks":
        return f"jdbc:mysql://{ds.host}:{ds.port or 9030}/{ds.database}?useUnicode=true&characterEncoding=utf8"
    if ds.type == "oracle":
        return f"jdbc:oracle:thin:@//{ds.host}:{ds.port or 1521}/{ds.database}"
    if ds.type == "clickhouse":
        return f"jdbc:clickhouse://{ds.host}:{ds.port or 8123}/{ds.database}"
    if ds.type == "postgres":
        return f"jdbc:postgresql://{ds.host}:{ds.port or 5432}/{ds.database}"
    if ds.type == "trino":
        cat = (ds.params or {}).get("catalog", "")
        return f"jdbc:trino://{ds.host}:{ds.port or 8080}" + (f"/{cat}" if cat else "")
    if ds.type in ("hive", "spark"):
        return f"jdbc:hive2://{ds.host}:{ds.port or (10000 if ds.type == 'hive' else 10001)}/{ds.database}"
    if ds.type == "mssql":
        return f"jdbc:sqlserver://{ds.host}:{ds.port or 1433};databaseName={ds.database};encrypt=false"
    return f"jdbc:sqlite:{(ds.params or {}).get('path', '')}"


def _driver(ds: DataSource) -> str:
    return {
        "mysql": "com.mysql.cj.jdbc.Driver",
        "doris": "com.mysql.cj.jdbc.Driver",
        "starrocks": "com.mysql.cj.jdbc.Driver",
        "oracle": "oracle.jdbc.OracleDriver",
        "clickhouse": "com.clickhouse.jdbc.ClickHouseDriver",
        "postgres": "org.postgresql.Driver",
        "trino": "io.trino.jdbc.TrinoDriver",
        "hive": "org.apache.hive.jdbc.HiveDriver",
        "spark": "org.apache.hive.jdbc.HiveDriver",
        "mssql": "com.microsoft.sqlserver.jdbc.SQLServerDriver",
    }.get(ds.type, "")


def _pwd(ds: DataSource) -> str:
    """DataX JSON 里的明文口令（库内为 Fernet 密文，渲染时解密）。"""
    from ..security import decrypt_secret
    if not ds.password_enc:
        return ""
    try:
        return decrypt_secret(ds.password_enc)
    except Exception:
        return ""


def render_datax_json(job: SyncJob, src: DataSource, dst: DataSource, cols: List[dict]) -> str:
    # MaxCompute：阿里官方 odpsreader/odpswriter（非 JDBC 结构）
    if src.type == "maxcompute" or dst.type == "maxcompute":
        def _odps_plugin(ds: DataSource, direction: str) -> dict:
            base = {
                "accessId": ds.username,
                "accessKey": _pwd(ds),
                "project": ds.database,
                "endpoint": (ds.params or {}).get("endpoint", ""),
                "column": [c["source" if direction == "reader" else "target"] for c in cols],
            }
            tbl = job.source_table if direction == "reader" else job.target_table
            base["table"] = tbl
            part = (ds.params or {}).get("partition", "")
            if part:
                base["partition"] = part
            return {"name": f"odps{direction}", "parameter": base}
        return json.dumps({"job": {
            "setting": {"speed": {"channel": 2}, "errorLimit": {"record": 100}},
            "content": [{"reader": _odps_plugin(src, "reader"), "writer": _odps_plugin(dst, "writer")}],
        }}, ensure_ascii=False, indent=2)

    columns = [c["source"] for c in cols]
    tcolumns = [c["target"] for c in cols]
    if src.type == "mysql":
        reader = {"name": "mysqlreader", "parameter": {
            "username": src.username, "password": _pwd(src),
            "column": columns,
            "connection": [{"jdbcUrl": [_jdbc(src)], "table": [job.source_table]}]}}
    elif src.type == "doris":
        reader = {"name": "rdbmsreader", "parameter": {
            "username": src.username, "password": _pwd(src),
            "column": columns, "driverClassName": "com.mysql.cj.jdbc.Driver",
            "connection": [{"jdbcUrl": _jdbc(src), "table": [job.source_table]}]}}
    else:
        reader = {"name": "rdbmsreader", "parameter": {
            "username": src.username or "", "password": _pwd(src),
            "column": columns, "driverClassName": _driver(src),
            "connection": [{"jdbcUrl": [_jdbc(src)], "table": [job.source_table]}]}}
    q = job.source_query
    if q:
        reader["parameter"]["querySql"] = [q]
        reader["parameter"].pop("column", None)
        reader["parameter"].pop("connection", None)
        reader["parameter"]["connection"] = [{"jdbcUrl": [_jdbc(src)]}]

    if dst.type == "doris":
        writer = {"name": "doriswriter", "parameter": {
            "username": dst.username, "password": _pwd(dst),
            "column": tcolumns,
            "connection": [{"jdbcUrl": _jdbc(dst), "table": [job.target_table],
                            "database": dst.database}]}}
    elif dst.type == "mysql":
        writer = {"name": "mysqlwriter", "parameter": {
            "username": dst.username, "password": _pwd(dst),
            "column": tcolumns,
            "connection": [{"jdbcUrl": _jdbc(dst), "table": [job.target_table]}]}}
    else:
        writer = {"name": "rdbmswriter", "parameter": {
            "username": dst.username or "", "password": _pwd(dst),
            "column": tcolumns, "driverClassName": _driver(dst),
            "connection": [{"jdbcUrl": [_jdbc(dst)], "table": [job.target_table]}]}}
    jobjson = {
        "job": {
            "setting": {"speed": {"channel": 2}, "errorLimit": {"record": 100}},
            "content": [{"reader": reader, "writer": writer}],
        }
    }
    return json.dumps(jobjson, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------- 内置执行器

def _resolve_increment(expr: str, logical_date: datetime.datetime) -> str:
    return (expr or "").replace("${bizdate}", logical_date.strftime("%Y-%m-%d")) \
                       .replace("${bizdate_nodash}", logical_date.strftime("%Y%m%d"))


def ensure_target_table(db: Session, job: SyncJob, cols: List[dict], log) -> bool:
    dst = db.get(DataSource, job.target_datasource_id)
    if dst.type == "maxcompute":
        from . import mc_client
        if job.target_table not in mc_client.table_names(dst):
            ddl = suggest_target_ddl(db, job, cols=cols).split("\n--")[0]
            log(f"[ddl] 目标表不存在，自动建表:\n{ddl}")
            mc_client.create_table(dst, ddl)
            return True
        return False
    from sqlalchemy import inspect as sa_inspect
    insp = sa_inspect(get_engine(dst))
    if job.target_table in insp.get_table_names():
        return False
    if dst.type == "oracle":
        raise ValueError("Oracle 目标表不存在——Oracle 不支持 IF NOT EXISTS 自动建表，请先手工建表")
    ddl = suggest_target_ddl(db, job, cols=cols).split("\n--")[0]
    log(f"[ddl] 目标表不存在，自动建表:\n{ddl}")
    eng = get_engine(dst)
    with eng.begin() as conn:
        conn.execute(_text(ddl))
    return True


def build_source_sql(db: Session, job: SyncJob, limit: Optional[int] = None,
                     logical_date: Optional[datetime.datetime] = None):
    ds = db.get(DataSource, job.source_datasource_id)
    cfg = job.config or {}
    cols = cfg.get("columns") or auto_columns(db, job)
    collist = ", ".join(f'`{c["source"]}`' for c in cols)
    if job.source_query:
        sql = f"SELECT * FROM ({job.source_query}) _src"
        params = {}
    else:
        sql = f"SELECT {collist} FROM `{job.source_table}`"
        params = {}
        inc = cfg.get("increment") or {}
        if inc.get("enabled") and inc.get("column"):
            col = inc["column"]
            mode = inc.get("mode", "max_value")
            if mode == "time_range" and inc.get("expr"):
                sql += f" WHERE `{col}` >= :_inc_expr"
                params["_inc_expr"] = _resolve_increment(inc["expr"], logical_date or datetime.datetime.now())
            else:  # max_value
                dst = db.get(DataSource, job.target_datasource_id)
                try:
                    with get_engine(dst).connect() as conn:
                        mx = conn.execute(_text(f'SELECT MAX(`{col}`) FROM `{job.target_table}`')).scalar()
                    if mx is not None:
                        sql += f" WHERE `{col}` > :_inc_max"
                        params["_inc_max"] = str(mx)
                except Exception:
                    pass
    if limit:
        if ds.type == "sqlite":
            sql += f" LIMIT {int(limit)}"
        else:
            sql += f" LIMIT {int(limit)}"
    return ds, sql, params, cols


def run_sync(db: Session, job: SyncJob, limit: Optional[int] = None, log=print,
             logical_date: Optional[datetime.datetime] = None) -> dict:
    """内置执行器：SQLAlchemy 跨源复制（增量/全量/写入模式/自动建表）。MaxCompute 走 pyodps 通道。"""
    src0 = db.get(DataSource, job.source_datasource_id)
    dst0 = db.get(DataSource, job.target_datasource_id)
    if (src0 and src0.type == "maxcompute") or (dst0 and dst0.type == "maxcompute"):
        return _run_sync_mc(db, job, limit=limit, log=log, logical_date=logical_date)
    t0 = time.time()
    src, sql, params, cols = build_source_sql(db, job, limit=limit, logical_date=logical_date)
    # 自动读取的列映射落库，保证后续（DDL/发布/重跑）一致
    job.config = dict(job.config or {})
    if job.config.get("columns") != cols:
        job.config["columns"] = cols
        db.commit()
    dst = db.get(DataSource, job.target_datasource_id)
    cfg = job.config or {}
    write_mode = cfg.get("write_mode", "append")
    log(f"[job] {job.name} 源={src.name} 目标={dst.name}.{job.target_table} 模式={write_mode}")
    if config.DATAX_HOME and limit is None:
        log("[engine] 已配置 DATAX_HOME —— 生产执行应走 DataX；本路径为内置执行器（见任务详情 JSON）")
    src_eng = get_engine(src)
    created = ensure_target_table(db, job, cols, log)
    dst_eng = get_engine(dst)
    total = 0
    # pre_sql：写入前在目标库执行（如清史/建中间表）
    if (cfg.get("pre_sql") or "").strip():
        with dst_eng.begin() as dconn:
            dconn.execute(_text(cfg["pre_sql"]))
        log("[pre_sql] 执行完成")

    with src_eng.connect().execution_options(stream_results=True) as sconn:
        result = sconn.execute(_text(sql), params)
        # 目标列映射：按 columns 配置对齐
        tgt_cols = [c["target"] for c in cols]

        def chunk_iter(size=2000):
            while True:
                rows = result.fetchmany(size)
                if not rows:
                    break
                yield rows

        with dst_eng.begin() as dconn:
            if write_mode == "truncate_insert" and limit is None:
                dconn.execute(_text(f'DELETE FROM `{job.target_table}`'))
                log("[write] truncate_insert: 已清空目标表")
            if write_mode == "replace" and limit is None:
                pcol = cfg.get("partition_col")
                if pcol:
                    dconn.execute(_text(f'DELETE FROM `{job.target_table}` WHERE `{pcol}` = :_p'),
                                  {"_p": _resolve_increment("${bizdate}", logical_date or datetime.datetime.now())})
                    log(f"[write] replace: 已清理分区 {_resolve_increment('${bizdate}', logical_date or datetime.datetime.now())}")
                else:
                    dconn.execute(_text(f'DELETE FROM `{job.target_table}`'))
                    log("[write] replace: 已清空目标表（未配置分区列）")
            ph = ", ".join(f":c{i}" for i in range(len(tgt_cols)))
            ins_sql = (f'INSERT INTO `{job.target_table}` (' +
                       ", ".join(f'`{c}`' for c in tgt_cols) + f") VALUES ({ph})")
            for rows in chunk_iter():
                batch = []
                for r in rows:
                    d = {}
                    for i, v in enumerate(r):
                        d[f"c{i}"] = None if v is None else (str(v) if isinstance(v, (dict, list)) else v)
                    batch.append(d)
                dconn.execute(_text(ins_sql), batch)
                total += len(batch)
                if total % 20000 < len(batch):
                    log(f"[progress] 已写入 {total} 行")
    # pre/post SQL
    for key in ("post_sql",):
        if (cfg.get(key) or "").strip():
            with dst_eng.begin() as dconn:
                dconn.execute(_text(cfg[key]))
            log(f"[{key}] 执行完成")
    dur = int((time.time() - t0) * 1000)
    log(f"[done] 写入 {total} 行，耗时 {dur}ms，自动建表={created}")
    return {"read_rows": total, "written_rows": total, "duration_ms": dur, "auto_created": created}


def _run_sync_mc(db: Session, job: SyncJob, limit=None, log=print,
                 logical_date: Optional[datetime.datetime] = None) -> dict:
    """含 MaxCompute 端的同步：pyodps 读/写 + 非 MC 端走 SQLAlchemy。当前 MC 目标仅支持 append。"""
    from . import mc_client
    from .engine_registry import run_query as _rq, get_engine as _ge
    t0 = time.time()
    src = db.get(DataSource, job.source_datasource_id)
    dst = db.get(DataSource, job.target_datasource_id)
    cfg = job.config or {}
    cols = cfg.get("columns") or []
    # 补列：mc 源用 pyodps schema，非 mc 源沿用实时探测
    if not cols:
        if src.type == "maxcompute":
            cols = [{"source": n, "target": n, "source_type": tp,
                     "target_type": map_dtype(db, tp, dst.type)}
                    for n, tp in mc_client.columns(src, job.source_table)]
        else:
            cols = auto_columns(db, job)
        cfg["columns"] = cols
        db.commit()
    for c in cols:
        c["target_type"] = c.get("target_type") or map_dtype(db, c.get("source_type", ""), dst.type)
    log(f"[job] {job.name} 源={src.name}({src.type}) 目标={dst.name}({dst.type}).{job.target_table} "
        f"模式={cfg.get('write_mode', 'append')}")
    write_mode = cfg.get("write_mode", "append")
    if dst.type == "maxcompute" and write_mode in ("truncate_insert", "replace"):
        raise ValueError("MaxCompute 目标当前仅支持 append 写入模式（truncate/replace 请使用 INSERT OVERWRITE 脚本节点）")

    # 1) 读全部行
    if src.type == "maxcompute":
        collist = ", ".join(c["source"] for c in cols)
        sql = f"SELECT {collist} FROM {job.source_table}"
        inc = cfg.get("increment") or {}
        if inc.get("enabled") and inc.get("column") and not job.source_query:
            sql += f" WHERE {inc['column']} > " + mc_client._sql_lit(
                _resolve_increment(inc.get("expr") or "", logical_date or datetime.datetime.now()))
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = mc_client.fetch_rows(src, sql)
        read_n = len(rows)
    else:
        if job.source_query:
            sql = f"SELECT * FROM ({job.source_query}) _src"
        else:
            collist = ", ".join(f'`{c["source"]}`' for c in cols) \
                if src.type not in ("trino", "postgres", "oracle") \
                else ", ".join(f'"{c["source"]}"' for c in cols)
            sql = f"SELECT {collist} FROM {_qi(src.type, job.source_table)}"
            inc = cfg.get("increment") or {}
            if inc.get("enabled") and inc.get("column") and not job.source_query:
                sql += f" WHERE {inc['column']} > '{_resolve_increment(inc.get('expr') or '', logical_date or datetime.datetime.now())}'"
        if limit:
            sql += f" LIMIT {int(limit)}"
        _, _rows, _ = _rq(src, sql, max_rows=limit or 10_000_000, timeout_s=600)
        rows = [tuple(r) for r in _rows]
        read_n = len(rows)
    log(f"[read] 读取 {read_n} 行")

    # 2) 自动建表（目标端）
    if dst.type == "maxcompute":
        if job.target_table not in mc_client.table_names(dst):
            ddl = suggest_target_ddl(db, job, cols=cols).split("\n--")[0]
            log(f"[ddl] 自动建表:\n{ddl}")
            mc_client.create_table(dst, ddl)
            log("[ddl] 完成")
    else:
        from sqlalchemy import inspect as _sa_i
        if job.target_table not in _sa_i(_ge(dst)).get_table_names():
            ddl = suggest_target_ddl(db, job, cols=cols).split("\n--")[0]
            log(f"[ddl] 自动建表:\n{ddl}")
            execute_write(dst, ddl)

    # 3) 写入
    tgt_cols = [c["target"] for c in cols]
    written = 0
    if dst.type == "maxcompute":
        written = mc_client.insert_rows(dst, job.target_table, tgt_cols, rows)
    else:
        dst_eng = _ge(dst)
        with dst_eng.begin() as dconn:
            if write_mode in ("truncate_insert", "replace") and limit is None:
                dconn.execute(_text(f'DELETE FROM `{job.target_table}`'))
                log("[write] 已清空目标表")
            ph = ", ".join(f":c{i}" for i in range(len(tgt_cols)))
            ins_sql = (f'INSERT INTO `{job.target_table}` (' +
                       ", ".join(f'`{c}`' for c in tgt_cols) + f") VALUES ({ph})")
            for i in range(0, len(rows), 2000):
                chunk = rows[i:i + 2000]
                batch = [{f"c{j}": v for j, v in enumerate(r)} for r in chunk]
                dconn.execute(_text(ins_sql), batch)
                written += len(chunk)
    if (cfg.get("post_sql") or "").strip():
        if dst.type == "maxcompute":
            mc_client.execute_write(dst, cfg["post_sql"])
        else:
            execute_write(dst, cfg["post_sql"])
        log("[post_sql] 执行完成")
    dur = int((time.time() - t0) * 1000)
    log(f"[done] 写入 {written} 行，耗时 {dur}ms")
    return {"read_rows": read_n, "written_rows": written, "duration_ms": dur,
            "auto_created": False}


def _qi(ds_type: str, name: str) -> str:
    return f'"{name}"' if ds_type in ("trino", "postgres", "oracle") else f"`{name}`"


def trial_run(db: Session, job: SyncJob, limit: int = 100, log=print) -> dict:
    """试跑 N 行，返回样本与统计；不改动状态。"""
    from .engine_registry import run_query
    stats = run_sync(db, job, limit=limit, log=log)
    dst = db.get(DataSource, job.target_datasource_id)
    try:
        sample_cols, sample_rows, _ = run_query(dst, f'SELECT * FROM {_qi(dst.type, job.target_table)} LIMIT 10',
                                                max_rows=10, timeout_s=15)
        return {"stats": stats, "columns": sample_cols,
                "rows": [[str(v) if v is not None else None for v in r] for r in sample_rows]}
    except Exception:
        # 部分引擎（如 oracle 无 LIMIT 语法）sample 失败不影响试跑结论
        return {"stats": stats, "columns": [], "rows": []}


# ---------------------------------------------------------------- 发布（联动调度）

def publish_job(db: Session, job: SyncJob, user, cron: str = "", depends_on=None) -> Workflow:
    """草稿 -> 发布：固化版本 + 渲染 DataX JSON + 创建/更新单节点工作流。"""
    from . import sched_service
    cols = (job.config or {}).get("columns") or auto_columns(db, job)
    job.config = dict(job.config or {})
    job.config["columns"] = cols
    src = db.get(DataSource, job.source_datasource_id)
    dst = db.get(DataSource, job.target_datasource_id)
    rendered = render_datax_json(job, src, dst, cols)
    job.current_version += 1
    ver = SyncJobVersion(job_id=job.id, version=job.current_version,
                         config=json.loads(json.dumps(job.config, ensure_ascii=False)),
                         rendered_datax_json=rendered,
                         created_by=getattr(user, "username", "system"))
    db.add(ver)
    job.status = "published"
    wf = db.query(Workflow).filter(Workflow.sync_job_id == job.id).first()
    if not wf:
        wf = Workflow(name=job.name, job_type="sync", sync_job_id=job.id)
        db.add(wf)
        db.flush()
        node = WorkflowNode(workflow_id=wf.id, name=f"{job.name}_datax",
                            node_type="datax", config={"sync_job_id": job.id}, ordinal=0)
        db.add(node)
        db.flush()
    wf.schedule_cron = cron or wf.schedule_cron or ""
    wf.enabled = True
    wf.depends_on = depends_on or wf.depends_on or []
    db.commit()
    sched_service.apply_schedule(db, wf)
    # 血缘
    db.query(LineageEdge).filter(LineageEdge.job_type == "sync", LineageEdge.job_id == job.id).delete()
    db.add(LineageEdge(source_datasource_id=job.source_datasource_id, source_table=job.source_table,
                       target_datasource_id=job.target_datasource_id, target_table=job.target_table,
                       job_type="sync", job_id=job.id, workflow_id=wf.id))
    db.commit()
    # 发布即刷新目标数据源元数据（试跑自动建的表立刻进资产/地图）
    try:
        from . import meta_service
        meta_service.collect_datasource(db, dst)
        meta_service.meta_index.invalidate()
    except Exception:
        pass
    return wf


def offline_job(db: Session, job: SyncJob):
    from . import sched_service
    job.status = "offline"
    wf = db.query(Workflow).filter(Workflow.sync_job_id == job.id).first()
    if wf:
        wf.enabled = False
        db.commit()
        sched_service.apply_schedule(db, wf)
    db.commit()


def rollback_job(db: Session, job: SyncJob, version: int) -> dict:
    """回滚到指定版本配置。仅 draft/offline 任务可回滚（published 需先下线）。"""
    if job.status == "published":
        raise ValueError("已发布任务不可回滚，请先下线")
    ver = (db.query(SyncJobVersion)
             .filter(SyncJobVersion.job_id == job.id, SyncJobVersion.version == version)
             .first())
    if not ver:
        raise ValueError(f"版本 v{version} 不存在")
    import json as _json
    job.config = _json.loads(_json.dumps(ver.config, ensure_ascii=False))
    db.commit()
    return {"ok": True, "version": version, "config": job.config}


def delete_job(db: Session, job: SyncJob) -> dict:
    """删除任务：仅 draft/offline；随任务清理版本/血缘；无实例的伴随工作流一并删除。"""
    if job.status == "published":
        raise ValueError("已发布任务不可删除，请先下线")
    from ..models import TaskInstance
    wf = db.query(Workflow).filter(Workflow.sync_job_id == job.id).first()
    wf_info = None
    if wf:
        has_instances = db.query(TaskInstance).filter(
            TaskInstance.workflow_id == wf.id).count() > 0
        if has_instances:
            wf.enabled = False
            wf_info = {"workflow_id": wf.id, "kept": "已有运行实例，工作流保留并停用"}
        else:
            db.query(WorkflowNode).filter(WorkflowNode.workflow_id == wf.id).delete()
            db.delete(wf)
            wf_info = {"workflow_id": wf.id, "deleted": True}
    db.query(SyncJobVersion).filter(SyncJobVersion.job_id == job.id).delete()
    db.query(LineageEdge).filter(LineageEdge.job_type == "sync",
                                 LineageEdge.job_id == job.id).delete()
    name = job.name
    db.delete(job)
    db.commit()
    return {"ok": True, "name": name, "workflow": wf_info}
