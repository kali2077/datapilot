"""元数据服务：采集（inspector）、变更巡检（diff）、搜索（倒排索引）、表详情。"""
import datetime
import re
import threading
from typing import List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..models import DataSource, MetaTable, MetaColumn, MetaChange, Tag, TableTag
from . import meta_vec_service

# 行数变更记录阈值：变化 ≥5% 且绝对差 ≥10 才记（防小表抖动刷屏；-1=采集失败不参与）
ROW_CHANGE_PCT = 5
ROW_CHANGE_ABS = 10

# 危险结构变更（聚合告警类型；table_added/column_added/comment_changed/row_count_changed 仅落记录）
DANGER_CHANGE_TYPES = ("table_removed", "column_removed", "column_altered")

_collect_locks: dict = {}  # ds.id -> Lock：同数据源串行采集（定时与手动并发会记重复变更）
_collect_locks_guard = threading.Lock()


def _ds_lock(ds_id: int) -> threading.Lock:
    with _collect_locks_guard:
        if ds_id not in _collect_locks:
            _collect_locks[ds_id] = threading.Lock()
        return _collect_locks[ds_id]


def _dtype_str(col: dict) -> str:
    t = col.get("type")
    return str(t) if t is not None else ""


def collect_datasource(db: Session, ds: DataSource, with_samples: bool = False) -> dict:
    """采集指定数据源的库表结构。保留人工补录字段（manual_desc/owner）。

    所有采集入口（API / 定时采集 / 任务成功后 / 目标表采集 / 播种）共用本函数。
    采集时自动与上次快照对比生成资产变更记录（MetaChange，零配置巡检）；
    危险结构变更（删表/删列/改列）经已启用的告警通道聚合通知。
    向量增量刷新（RAG ③）在此统一触发；未配置 embedder 时为 no-op。
    """
    with _ds_lock(ds.id):  # 定时巡检与手动采集并发时避免重复 diff/首采撞唯一约束
        result = _collect_impl(db, ds, with_samples)
    danger = result.pop("_danger_changes", None)
    if danger:
        _alert_danger_changes(db, ds, danger)
    try:
        ids = [r.id for r in db.query(MetaTable.id).filter(MetaTable.datasource_id == ds.id)]
        meta_vec_service.refresh_tables(db, ids)
    except Exception:
        pass  # 向量刷新失败不影响采集结果（红线：不被 embedding 模型绑架）
    return result


def _column_diff_kind(mc: Optional[MetaColumn], dtype: str, is_pk: bool) -> Optional[str]:
    """列级 diff 判定（纯函数）：新列/复活列 → column_added；类型或主键变化 → column_altered；
    无变化 → None。缺失列的 column_removed 由调用方按源集合反向判断。"""
    if mc is None or mc.dropped:
        return "column_added"
    if mc.data_type != dtype or mc.is_pk != is_pk:
        return "column_altered"
    return None


def _record_changes(db: Session, ds: DataSource, changes: list) -> int:
    """批量落 MetaChange。changes 元素: {table_id, schema_name, table_name, change_type, detail}。"""
    now = datetime.datetime.now()
    for c in changes:
        db.add(MetaChange(datasource_id=ds.id, table_id=c.get("table_id", 0),
                          schema_name=c.get("schema_name", ""), table_name=c["table_name"],
                          change_type=c["change_type"], detail=c.get("detail") or {},
                          detected_at=now))
    return len(changes)


def _detect_removed_tables(db: Session, ds: DataSource, schema_name: str,
                           source_names: set, changes: list):
    """源中已删除的表：记 table_removed + dropped 标志（防重复记录，记录保留）。"""
    source = set(source_names)
    dropped = (db.query(MetaTable)
                 .filter(MetaTable.datasource_id == ds.id, MetaTable.schema_name == schema_name,
                         MetaTable.dropped == False).all())  # noqa: E712
    for mt in dropped:
        if mt.name in source:
            continue
        mt.dropped = True
        changes.append({"table_id": mt.id, "schema_name": mt.schema_name, "table_name": mt.name,
                        "change_type": "table_removed",
                        "detail": {"last_row_count": mt.row_count}})


def _alert_danger_changes(db: Session, ds: DataSource, danger: list):
    """危险结构变更聚合告警：有启用的告警通道才发（无通道只落变更记录）。"""
    try:
        from ..models import AlertChannel
        from .alert import fire_custom_alert
        if not db.query(AlertChannel).filter(AlertChannel.enabled == True).count():  # noqa: E712
            return
        lines = []
        for c in danger:
            d = c.get("detail") or {}
            if c["change_type"] == "table_removed":
                lines.append(f"- [删除表] {c['table_name']}（最后行数 {d.get('last_row_count', '-')}）")
            elif c["change_type"] == "column_removed":
                lines.append(f"- [删除字段] {c['table_name']}.{d.get('column')}（原类型 {d.get('old_type') or '-'}）")
            elif c["change_type"] == "column_altered":
                seg = f"- [变更字段] {c['table_name']}.{d.get('column')}: {d.get('old_type')} → {d.get('new_type')}"
                if d.get("old_pk") != d.get("new_pk"):
                    seg += f"（主键 {d.get('old_pk')} → {d.get('new_pk')}）"
                lines.append(seg)
        title = f"[DataPilot] 资产变更巡检: {ds.name} 发现 {len(danger)} 处结构变更"
        fire_custom_alert(db, title, "自动巡检发现以下危险结构变更：\n" + "\n".join(lines))
    except Exception:
        pass  # 告警失败不影响采集


def _collect_impl(db: Session, ds: DataSource, with_samples: bool = False) -> dict:
    if ds.type == "maxcompute":
        return _collect_maxcompute(db, ds)
    from .engine_registry import get_engine
    from sqlalchemy import inspect

    eng = get_engine(ds)
    insp = inspect(eng)
    now = datetime.datetime.now()
    schema_name = ds.database or ""
    # 变更巡检：首次采集只建基线不记变更（避免全量 added 噪音）
    first_collect = (db.query(MetaTable.id)
                       .filter(MetaTable.datasource_id == ds.id).first() is None)
    changes: list = []
    table_cnt, column_cnt = 0, 0
    names = insp.get_table_names()
    if not names and ds.type in ("hive", "spark"):
        # Hive/Spark 方言 inspector 兼容性弱：show tables + describe 专用采集
        return _collect_hive_fallback(db, ds, now)
    for tname in names:
        mt = (db.query(MetaTable)
                .filter(MetaTable.datasource_id == ds.id, MetaTable.schema_name == schema_name,
                        MetaTable.name == tname).first())
        created = mt is None
        if created:
            mt = MetaTable(datasource_id=ds.id, schema_name=schema_name, name=tname)
            db.add(mt)
            db.flush()
            if not first_collect:
                changes.append({"table_id": mt.id, "schema_name": schema_name,
                                "table_name": tname, "change_type": "table_added",
                                "detail": {}})
        elif mt.dropped:  # 同名表重建（曾记录删除）→ 视为新增
            mt.dropped = False
            if not first_collect:
                changes.append({"table_id": mt.id, "schema_name": schema_name,
                                "table_name": tname, "change_type": "table_added",
                                "detail": {"recreated": True}})
        old_comment = mt.comment or ""
        try:
            comment = insp.get_table_comment(tname) or {}
            comment = comment.get("text") or ""
        except Exception:
            comment = ""
        if comment:
            mt.comment = comment
            if not first_collect and not created and comment != old_comment:
                # created 表 old_comment 恒为 ""，有注释必触发——新增表只记 table_added
                changes.append({"table_id": mt.id, "schema_name": schema_name,
                                "table_name": tname, "change_type": "comment_changed",
                                "detail": {"old": old_comment, "new": comment}})
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
        source_cols = insp.get_columns(tname)
        for i, col in enumerate(source_cols):
            mc = existing.get(col["name"])
            dtype = _dtype_str(col)
            is_pk = col["name"] in pk_cols
            kind = _column_diff_kind(mc, dtype, is_pk)
            if mc is None:
                mc = MetaColumn(table_id=mt.id, name=col["name"])
                db.add(mc)
            elif mc.dropped:
                mc.dropped = False  # 曾记录删除的字段复活
            if kind and not first_collect:
                changes.append({"table_id": mt.id, "schema_name": schema_name,
                                "table_name": tname, "change_type": kind,
                                "detail": {"column": col["name"], "data_type": dtype}
                                if kind == "column_added" else
                                {"column": col["name"], "old_type": mc.data_type,
                                 "new_type": dtype, "old_pk": mc.is_pk, "new_pk": is_pk}})
            mc.data_type = dtype
            mc.comment = col.get("comment") or ""
            mc.ordinal = i
            mc.is_pk = is_pk
            if with_samples and mc.sample_values is None:
                try:
                    with eng.connect() as conn_:
                        rs = conn_.execute(text(f'SELECT "{col["name"]}" FROM "{tname}" LIMIT 5'))
                        vals = [str(r[0])[:100] for r in rs.fetchall()]
                    mc.sample_values = {"values": vals}
                except Exception:
                    mc.sample_values = None
            column_cnt += 1
        if not first_collect:  # 源中缺失的列（schema drift）
            for cname, mc in existing.items():
                if cname not in {c["name"] for c in source_cols} and not mc.dropped:
                    mc.dropped = True
                    changes.append({"table_id": mt.id, "schema_name": schema_name,
                                    "table_name": tname, "change_type": "column_removed",
                                    "detail": {"column": cname, "old_type": mc.data_type}})
        # 行数（尽力而为）+ 变更巡检
        # 注意：SQLAlchemy 2.0 移除 Engine.execute，须走 connect()（旧写法在此恒失败 → row_count 恒 -1）
        try:
            with eng.connect() as conn_:
                rc = conn_.execute(text(f'SELECT COUNT(*) FROM "{tname}"')).scalar()
            new_rc = int(rc) if rc is not None else -1
        except Exception:
            new_rc = -1
        old_rc = -1 if created else (mt.row_count or 0)
        if not first_collect and new_rc >= 0 and old_rc >= 0 and new_rc != old_rc:
            diff = abs(new_rc - old_rc)
            pct = diff * 100.0 / max(old_rc, 1)
            if pct >= ROW_CHANGE_PCT and diff >= ROW_CHANGE_ABS:
                changes.append({"table_id": mt.id, "schema_name": schema_name,
                                "table_name": tname, "change_type": "row_count_changed",
                                "detail": {"old": old_rc, "new": new_rc,
                                           "pct": round((new_rc - old_rc) * 100.0 / max(old_rc, 1), 1)}})
        mt.row_count = new_rc
    _detect_removed_tables(db, ds, schema_name, set(names), changes)
    n = _record_changes(db, ds, changes)
    db.commit()
    return {"tables": table_cnt, "columns": column_cnt, "changes": n,
            "_danger_changes": [c for c in changes if c["change_type"] in DANGER_CHANGE_TYPES]}


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
    """Hive/Spark inspector 失效时的 show tables + describe 采集分支（表级变更巡检）。"""
    from .engine_registry import run_query
    from ..models import MetaTable, MetaColumn
    table_cnt, column_cnt = 0, 0
    changes: list = []
    first_collect = (db.query(MetaTable.id)
                       .filter(MetaTable.datasource_id == ds.id).first() is None)
    try:
        _, trows, _ = run_query(ds, "show tables", max_rows=2000, timeout_s=60)
    except Exception:
        return {"tables": 0, "columns": 0, "changes": 0}
    schema_name = ds.database or ""
    seen = []
    for r in trows:
        tname = str(r[0])
        if not tname:
            continue
        seen.append(tname)
        mt = (db.query(MetaTable)
                .filter(MetaTable.datasource_id == ds.id, MetaTable.schema_name == schema_name,
                        MetaTable.name == tname).first())
        created = mt is None
        if not mt:
            mt = MetaTable(datasource_id=ds.id, schema_name=schema_name, name=tname)
            db.add(mt)
            db.flush()
        elif mt.dropped:
            mt.dropped = False
            created = True
        if created and not first_collect:
            changes.append({"table_id": mt.id, "schema_name": schema_name, "table_name": tname,
                            "change_type": "table_added", "detail": {}})
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
                mc.dropped = False
                column_cnt += 1
        except Exception:
            pass
    _detect_removed_tables(db, ds, schema_name, set(seen), changes)
    n = _record_changes(db, ds, changes)
    db.commit()
    return {"tables": table_cnt, "columns": column_cnt, "changes": n,
            "_danger_changes": [c for c in changes if c["change_type"] in DANGER_CHANGE_TYPES]}


def _collect_maxcompute(db: Session, ds: DataSource):
    """MaxCompute：pyodps 采集（list_tables + table schema + count，表级变更巡检）。"""
    from . import mc_client
    from ..models import MetaTable, MetaColumn
    now = datetime.datetime.now()
    o = mc_client.get_odps(ds)
    schema_name = ds.database or ""
    table_cnt, column_cnt = 0, 0
    changes: list = []
    first_collect = (db.query(MetaTable.id)
                       .filter(MetaTable.datasource_id == ds.id).first() is None)
    seen = []
    for t in o.list_tables():
        seen.append(t.name)
        mt = (db.query(MetaTable)
                .filter(MetaTable.datasource_id == ds.id, MetaTable.schema_name == schema_name,
                        MetaTable.name == t.name).first())
        created = mt is None
        if not mt:
            mt = MetaTable(datasource_id=ds.id, schema_name=schema_name, name=t.name)
            db.add(mt)
            db.flush()
        elif mt.dropped:
            mt.dropped = False
            created = True
        if created and not first_collect:
            changes.append({"table_id": mt.id, "schema_name": schema_name, "table_name": t.name,
                            "change_type": "table_added", "detail": {}})
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
            mc.dropped = False
            column_cnt += 1
        table_cnt += 1
    _detect_removed_tables(db, ds, schema_name, set(seen), changes)
    n = _record_changes(db, ds, changes)
    db.commit()
    return {"tables": table_cnt, "columns": column_cnt, "changes": n,
            "_danger_changes": [c for c in changes if c["change_type"] in DANGER_CHANGE_TYPES]}
