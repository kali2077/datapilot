"""SQLAlchemy 基础设施。"""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from . import config

connect_args = {}
if config.DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(
    config.DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
    future=True,
)

if config.DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# 轻量迁移：create_all 只建新表，不会给已存在的表加列；此处补齐增量列。
MIGRATIONS = {
    "mon_rule": [
        ("mute_hours", "INTEGER DEFAULT 24"),
        ("last_alerted_at", "DATETIME"),
        ("group_name", "VARCHAR(64) DEFAULT ''"),
    ],
}


def run_migrations():
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    with engine.begin() as conn:
        for table, cols in MIGRATIONS.items():
            if not insp.has_table(table):
                continue
            existing = {c["name"] for c in insp.get_columns(table)}
            for col, ddl in cols:
                if col not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
