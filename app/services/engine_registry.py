"""数据源引擎注册表：按 DataSource 构建/缓存 SQLAlchemy Engine。

Doris 走 MySQL 协议（FE 默认 9030），因此 mysql/doris 共用 pymysql 方言。
"""
import threading
import urllib.parse
from typing import Dict

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ..models import DataSource

_engines: Dict[str, Engine] = {}
_lock = threading.Lock()


def build_url(ds: DataSource) -> str:
    pwd = urllib.parse.quote_plus(ds.password_enc or "")
    if ds.type == "sqlite":
        path = (ds.params or {}).get("path", "")
        if not path:
            raise ValueError("SQLite 数据源缺少 path 参数")
        import os
        if not os.path.isabs(path):
            import os.path as osp
            path = osp.join(os.getcwd(), path)
        return f"sqlite:///{path}"
    if ds.type == "maxcompute":
        raise ValueError("MaxCompute 使用 pyodps 专用通道（无 JDBC URL），平台将自动分派")
    if ds.type == "trino":
        if not ds.port:
            ds.port = 8080
        user = urllib.parse.quote_plus(ds.username or "datapilot")
        pw = f":{pwd}" if ds.password_enc else ""
        catalog = (ds.params or {}).get("catalog", "")
        schema = (ds.params or {}).get("schema", "")
        return f"trino://{user}{pw}@{ds.host}:{ds.port}/{catalog}/{schema}".rstrip("/")
    if ds.type in ("hive", "spark"):
        if not ds.port:
            ds.port = 10000 if ds.type == "hive" else 10001
        user = urllib.parse.quote_plus(ds.username or "")
        pw = f":{pwd}" if ds.password_enc else ""
        db = f"/{ds.database}" if ds.database else ""
        q = {k: v for k, v in (ds.params or {}).items() if k in ("auth", "transport")}
        qs = ("?" + urllib.parse.urlencode(q)) if q else ""
        return f"hive://{user}{pw}@{ds.host}:{ds.port}{db}{qs}"
    if ds.type in ("mysql", "doris", "starrocks"):
        port = {3306: 3306, 9030: 9030}.get(ds.port or 0, ds.port) or \
            {"mysql": 3306, "doris": 9030, "starrocks": 9030}[ds.type]
        return f"mysql+pymysql://{ds.username}:{pwd}@{ds.host}:{port}/{ds.database}?charset=utf8mb4"
    if ds.type == "oracle":
        if not ds.port:
            ds.port = 1521
        # oracledb thin 模式：无需安装 Oracle 客户端；database 字段作为 service_name
        return (f"oracle+oracledb://{ds.username}:{pwd}@{ds.host}:{ds.port}"
                f"/?service_name={ds.database}")
    if ds.type == "clickhouse":
        if not ds.port:
            ds.port = 8123
        return f"clickhouse+http://{ds.username}:{pwd}@{ds.host}:{ds.port}/{ds.database}"
    if ds.type == "mssql":
        if not ds.port:
            ds.port = 1433
        return f"mssql+pymssql://{ds.username}:{pwd}@{ds.host}:{ds.port}/{ds.database}"
    if ds.type == "postgres":
        if not ds.port:
            ds.port = 5432
        return f"postgresql+psycopg2://{ds.username}:{pwd}@{ds.host}:{ds.port}/{ds.database}"
    raise ValueError(f"不支持的数据源类型: {ds.type}")


def get_engine(ds: DataSource) -> Engine:
    if ds.type == "maxcompute":
        raise ValueError("MaxCompute 使用 pyodps 专用通道（engine_registry 自动分派，勿直接取 SQLAlchemy 引擎）")
    if ds.type == "mssql":
        try:
            import pymssql  # noqa: F401
        except ImportError:
            raise ValueError("SQL Server 驱动未安装：pip install pymssql（mac 编译失败可用 conda install pymssql 或预编译 wheel）")
    with _lock:
        eng = _engines.get(str(ds.id))
        if eng is not None and eng.url.render_as_string() != build_url(ds):
            eng.dispose()
            eng = None
        if eng is None:
            eng = _create(build_url(ds))
            _engines[str(ds.id)] = eng
    return eng


def _create(url: str) -> Engine:
    from sqlalchemy import create_engine
    return create_engine(url, pool_pre_ping=True, pool_recycle=1800, future=True)


def test_connection(ds: DataSource) -> dict:
    if ds.type == "maxcompute":
        from . import mc_client
        return mc_client.test_connection(ds)
    eng = get_engine(ds)
    with eng.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"ok": True, "message": "连接成功"}


def run_query(ds: DataSource, sql: str, params: dict = None, max_rows: int = 1000, timeout_s: int = 30):
    """只读查询执行：线程级超时熔断 + 行数上限。返回 (columns, rows, truncated)。"""
    if ds.type == "maxcompute":
        from . import mc_client
        return mc_client.run_query(ds, sql, max_rows=max_rows)
    import concurrent.futures as cf

    eng = get_engine(ds)

    def _run():
        with eng.connect() as conn:
            result = conn.execute(text(sql), params or {})
            cols = list(result.keys())
            rows = result.fetchmany(max_rows + 1)
            truncated = len(rows) > max_rows
            return cols, rows[:max_rows], truncated

    with cf.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(_run)
        try:
            cols, rows, truncated = fut.result(timeout=timeout_s)
        except cf.TimeoutError:
            raise TimeoutError(f"查询超时（>{timeout_s}s）")
    return cols, [list(r) for r in rows], truncated


def execute_write(ds: DataSource, sql: str, params: dict = None):
    if ds.type == "maxcompute":
        from . import mc_client
        return mc_client.execute_write(ds, sql)
    eng = get_engine(ds)
    with eng.begin() as conn:
        result = conn.execute(text(sql), params or {})
        return {"rowcount": result.rowcount}


def table_names(ds: DataSource, schema: str = None):
    if ds.type == "maxcompute":
        from . import mc_client
        return mc_client.table_names(ds)
    from sqlalchemy import inspect
    insp = inspect(get_engine(ds))
    return insp.get_table_names(schema=schema or None)
