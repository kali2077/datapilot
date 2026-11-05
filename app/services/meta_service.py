"""元数据服务：采集（inspector）、搜索（倒排索引）、表详情。"""
import datetime
import re
from typing import List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..models import DataSource, MetaTable, MetaColumn, Tag, TableTag


def _dtype_str(col: dict) -> str:
    t = col.get("type")
    return str(t) if t is not None else ""


def collect_datasource(db: Session, ds: DataSource, with_samples: bool = False) -> dict:
    """采集指定数据源的库表结构。保留人工补录字段（manual_desc/owner）。"""
    if ds.type == "maxcompute":
        return _collect_maxcompute(db, ds)
    from .engine_registry import get_engine
    from sqlalchemy import inspect

    eng = get_engine(ds)
    insp = inspect(eng)
    now = datetime.datetime.now()
    table_cnt, column_cnt = 0, 0
    names = insp.get_table_names()
    if not names and ds.type in ("hive", "spark"):
        # Hive/Spark 方言 inspector 兼容性弱：show tables + describe 专用采集
        return _collect_hive_fallback(db, ds, now)
    for tname in names:
        schema_name = ds.database or ""
        mt = (db.query(MetaTable)
                .filter(MetaTable.datasource_id == ds.id, MetaTable.schema_name == schema_name,
                        MetaTable.name == tname).first())
        created = mt is None
        if created:
            mt = MetaTable(datasource_id=ds.id, schema_name=schema_name, name=tname)
            db.add(mt)
            db.flush()
        try:
            comment = insp.get_table_comment(tname) or {}
            comment = comment.get("text") or ""
        except Exception:
            comment = ""
        if comment:
            mt.comment = comment
        mt.table_type = "table"
        mt.last_collected_at = now
        if created:
            db.flush()
        table_cnt += 1

        pk_cols = set()
        try:
            pk = insp.get_pk_constraint(tname) or {}
            pk_cols = set(pk.get("constrained_columns") or [])
        except Exception:
            pass
        existing = {c.name: c for c in db.query(MetaColumn).filter(MetaColumn.table_id == mt.id)}
        for i, col in enumerate(insp.get_columns(tname)):
            mc = existing.get(col["name"])
            if mc is None:
                mc = MetaColumn(table_id=mt.id, name=col["name"])
                db.add(mc)
            mc.data_type = _dtype_str(col)
            mc.comment = col.get("comment") or ""
            mc.ordinal = i
            mc.is_pk = col["name"] in pk_cols
            if with_samples and mc.sample_values is None:
                try:
                    rs = eng.execute(text(f'SELECT "{col["name"]}" FROM "{tname}" LIMIT 5'))
                    vals = [str(r[0])[:100] for r in rs.fetchall()]
                    mc.sample_values = {"values": vals}
                except Exception:
                    mc.sample_values = None
            column_cnt += 1
        # 行数（尽力而为）
        try:
            rc = eng.execute(text(f'SELECT COUNT(*) FROM "{tname}"')).scalar()
            mt.row_count = int(rc) if rc is not None else -1
        except Exception:
            mt.row_count = -1
    db.commit()
    return {"tables": table_cnt, "columns": column_cnt}


def tokenize(text_: str) -> List[str]:
    """极简分词：英文按词、中文按 2-gram，用于倒排索引。"""
    if not text_:
        return []
    tokens = set(re.findall(r"[a-zA-Z0-9_]+", text_.lower()))
    cn = re.sub(r"[^一-龥]+", " ", text_)
    for chunk in cn.split():
        if len(chunk) == 1:
            tokens.add(chunk)
        else:
            for i in range(len(chunk) - 1):
                tokens.add(chunk[i:i + 2])
    return list(tokens)


class MetaIndex:
    """轻量倒排索引：token -> {table_id}，懒加载 + 失效重建。"""

    def __init__(self):
        self._index = {}
        self._built_at = None
        self._dirty = True

    def invalidate(self):
        self._dirty = True

    def _build(self, db: Session):
        idx = {}
        tables = db.query(MetaTable).all()
        tags_map = {}
        for tt in db.query(TableTag).all():
            tags_map.setdefault(tt.table_id, []).append(tt.tag_id)
        tag_names = {t.id: t.name for t in db.query(Tag).all()}
        for mt in tables:
            text_blob = " ".join([mt.name, mt.comment, mt.manual_desc, mt.schema_name,
                                  " ".join(tag_names.get(t, "") for t in tags_map.get(mt.id, []))])
            for tok in tokenize(text_blob):
                idx.setdefault(tok, set()).add(mt.id)
        self._index = idx
        self._dirty = False

    def search(self, db: Session, keyword: str, limit: int = 20) -> List[MetaTable]:
        if self._dirty:
            self._build(db)
        toks = tokenize(keyword)
        if not toks:
            return []
        score = {}
        for tok in toks:
            for tid in self._index.get(tok, ()):
                score[tid] = score.get(tid, 0) + 1
        ids = sorted(score, key=lambda t: -score[t])[:limit]
        if not ids:
            return []
        rows = db.query(MetaTable).filter(MetaTable.id.in_(ids)).all()
        rows.sort(key=lambda r: -score.get(r.id, 0))
        return rows


meta_index = MetaIndex()


def _collect_hive_fallback(db: Session, ds: DataSource, now):
    """Hive/Spark inspector 失效时的 show tables + describe 采集分支。"""
    from .engine_registry import run_query
    from ..models import MetaTable, MetaColumn
    table_cnt, column_cnt = 0, 0
    try:
        _, trows, _ = run_query(ds, "show tables", max_rows=2000, timeout_s=60)
    except Exception:
        return {"tables": 0, "columns": 0}
    schema_name = ds.database or ""
    for r in trows:
        tname = str(r[0])
        if not tname:
            continue
        mt = (db.query(MetaTable)
                .filter(MetaTable.datasource_id == ds.id, MetaTable.schema_name == schema_name,
                        MetaTable.name == tname).first())
        if not mt:
            mt = MetaTable(datasource_id=ds.id, schema_name=schema_name, name=tname)
            db.add(mt)
            db.flush()
        mt.last_collected_at = now
        table_cnt += 1
        try:
            _, crows, _ = run_query(ds, f"describe `{tname}`", max_rows=500, timeout_s=60)
            for i, cr in enumerate(crows):
                cname, ctype = str(cr[0] or ""), str(cr[1] or "")
                if not cname or cname.startswith("#"):
                    continue
                mc = db.query(MetaColumn).filter(MetaColumn.table_id == mt.id,
                                                 MetaColumn.name == cname).first()
                if not mc:
                    mc = MetaColumn(table_id=mt.id, name=cname)
                    db.add(mc)
                mc.data_type = ctype
                mc.comment = str(cr[2] or "") if len(cr) > 2 else ""
                mc.ordinal = i
                column_cnt += 1
        except Exception:
            pass
    db.commit()
    return {"tables": table_cnt, "columns": column_cnt}


def _collect_maxcompute(db: Session, ds: DataSource):
    """MaxCompute：pyodps 采集（list_tables + table schema + count）。"""
    from . import mc_client
    from ..models import MetaTable, MetaColumn
    now = datetime.datetime.now()
    o = mc_client.get_odps(ds)
    table_cnt, column_cnt = 0, 0
    for t in o.list_tables():
        schema_name = ds.database or ""
        mt = (db.query(MetaTable)
                .filter(MetaTable.datasource_id == ds.id, MetaTable.schema_name == schema_name,
                        MetaTable.name == t.name).first())
        if not mt:
            mt = MetaTable(datasource_id=ds.id, schema_name=schema_name, name=t.name)
            db.add(mt)
            db.flush()
        mt.last_collected_at = now
        mt.comment = getattr(t, "comment", "") or ""
        try:
            mt.row_count = int(t.count)
        except Exception:
            mt.row_count = -1
        existing = {c.name: c for c in db.query(MetaColumn).filter(MetaColumn.table_id == mt.id)}
        for i, sc in enumerate(t.schema.simple_columns):
            mc = existing.get(sc.name)
            if not mc:
                mc = MetaColumn(table_id=mt.id, name=sc.name)
                db.add(mc)
            mc.data_type = str(sc.type)
            mc.comment = getattr(sc, "comment", "") or ""
            mc.ordinal = i
            column_cnt += 1
        table_cnt += 1
    db.commit()
    return {"tables": table_cnt, "columns": column_cnt}
