"""MaxCompute（ODPS）专用通道：pyodps 实现，替代 SQLAlchemy 引擎。

覆盖：连接测试 / 只读查询 / 写入执行 / 表清单 / 元数据采集 / 同步行读取与写入。
AC/SK 存储约定：username=AccessKeyId，password_enc=Fernet(AccessKeySecret)，
params.endpoint=服务地址，database=project。
"""
from sqlalchemy import text as _text  # noqa: F401  占位对齐其他模块 import 风格

from ..models import DataSource
from ..security import decrypt_secret


def get_odps(ds: DataSource):
    from odps import ODPS
    if not (ds.params or {}).get("endpoint"):
        raise ValueError("MaxCompute 数据源缺少 params.endpoint（如 http://service.cn-hangzhou.maxcompute.aliyun.com/api）")
    if not ds.database:
        raise ValueError("MaxCompute 数据源缺少 project（填在 database 字段）")
    if not ds.username or not ds.password_enc:
        raise ValueError("MaxCompute 需要 AccessKeyId（用户名）与 AccessKeySecret（密码）")
    return ODPS(ds.username, decrypt_secret(ds.password_enc),
                project=ds.database, endpoint=ds.params["endpoint"])


def test_connection(ds: DataSource) -> dict:
    get_odps(ds).list_tables(limit=1)  # 探活
    return {"ok": True, "message": f"连接成功（project={ds.database}）"}


def run_query(ds: DataSource, sql: str, max_rows: int = 1000):
    """只读查询：open_reader(tunnel=True)。不支持绑定参数。"""
    if sql.strip().lower().count(";") > 1:
        raise ValueError("仅允许单条语句")
    o = get_odps(ds)
    ins = o.execute_sql(sql.rstrip(";"))
    with ins.open_reader(tunnel=True) as reader:
        cols = [c.name for c in reader.schema.columns]
        rows = [[_safe(v) for v in rec.values] for rec in reader[:max_rows]]
        truncated = len(rows) >= max_rows
    return cols, rows, truncated


def _safe(v):
    import datetime
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, (datetime.datetime, datetime.date)):
        return str(v)
    return str(v)


def execute_write(ds: DataSource, sql: str):
    o = get_odps(ds)
    ins = o.execute_sql(sql.rstrip(";"))
    try:
        return {"rowcount": ins.logs and -1 or -1}
    except Exception:
        return {"rowcount": -1}


def table_names(ds: DataSource):
    o = get_odps(ds)
    return [t.name for t in o.list_tables()]


def columns(ds: DataSource, table: str):
    """[(name, type_str), ...]"""
    o = get_odps(ds)
    t = o.get_table(table)
    return [(c.name, str(c.type)) for c in t.schema.simple_columns]


def count(ds: DataSource, table: str) -> int:
    o = get_odps(ds)
    try:
        return int(o.get_table(table).count)
    except Exception:
        return -1


def fetch_rows(ds: DataSource, sql: str):
    """全量读取源行（同步用），返回 [tuple,...]。"""
    o = get_odps(ds)
    ins = o.execute_sql(sql.rstrip(";"))
    with ins.open_reader(tunnel=True) as reader:
        return [tuple(_safe(v) for v in rec.values) for rec in reader]


def insert_rows(ds: DataSource, table: str, cols, rows, batch: int = 200):
    """INSERT INTO ... VALUES 批量写入（append 语义）。"""
    if not rows:
        return 0
    o = get_odps(ds)
    total = 0
    collist = ", ".join(cols)
    for i in range(0, len(rows), batch):
        chunk = rows[i:i + batch]
        values = ", ".join("(" + ", ".join(_sql_lit(v) for v in r) + ")" for r in chunk)
        o.execute_sql(f"INSERT INTO {table} ({collist}) VALUES {values}")
        total += len(chunk)
    return total


def create_table(ds: DataSource, ddl: str):
    get_odps(ds).execute_sql(ddl.rstrip(";"))


def _sql_lit(v):
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return repr(v)
    s = str(v).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{s}'"
