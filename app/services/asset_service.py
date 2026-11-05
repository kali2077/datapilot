"""资产与地图服务：目录、标签、Owner、搜索（表+API）、血缘图。"""
from typing import List, Optional

from sqlalchemy.orm import Session

from ..models import (MetaTable, MetaColumn, Tag, TableTag, LineageEdge,
                      DataSource, ApiDef, Workflow)


def assign_tag(db: Session, table_id: int, tag_id: int, on: bool = True):
    q = db.query(TableTag).filter(TableTag.table_id == table_id, TableTag.tag_id == tag_id)
    if on and not q.first():
        db.add(TableTag(table_id=table_id, tag_id=tag_id))
    elif not on:
        q.delete()
    db.commit()
    from . import meta_service
    meta_service.meta_index.invalidate()


def table_detail(db: Session, table_id: int) -> Optional[dict]:
    mt = db.get(MetaTable, table_id)
    if not mt:
        return None
    ds = db.get(DataSource, mt.datasource_id)
    cols = (db.query(MetaColumn).filter(MetaColumn.table_id == mt.id)
              .order_by(MetaColumn.ordinal).all())
    tags = (db.query(Tag).join(TableTag, TableTag.tag_id == Tag.id)
              .filter(TableTag.table_id == mt.id).all())
    up = _lineage(db, mt, direction="up")
    down = _lineage(db, mt, direction="down")
    related_ids = set()
    for e in db.query(LineageEdge).filter(
            (LineageEdge.source_table == mt.name) | (LineageEdge.target_table == mt.name)).all():
        if e.workflow_id:
            related_ids.add(e.workflow_id)
    related = [db.get(Workflow, wid) for wid in list(related_ids)[:10]]
    sample = _sample(db, mt)
    return {
        "id": mt.id, "name": mt.name, "schema": mt.schema_name,
        "datasource": {"id": ds.id, "name": ds.name, "type": ds.type} if ds else None,
        "comment": mt.comment, "manual_desc": mt.manual_desc,
        "owner_name": mt.owner_name, "row_count": mt.row_count,
        "last_collected_at": mt.last_collected_at.isoformat() if mt.last_collected_at else None,
        "columns": [{"name": c.name, "type": c.data_type, "comment": c.comment or c.manual_desc,
                     "is_pk": c.is_pk, "samples": (c.sample_values or {}).get("values", [])[:3]}
                    for c in cols],
        "tags": [{"id": t.id, "name": t.name, "color": t.color} for t in tags],
        "upstream": up, "downstream": down,
        "related_workflows": [{"id": w.id, "name": w.name, "type": w.job_type,
                               "cron": w.schedule_cron, "enabled": w.enabled}
                              for w in related if w],
        "sample": sample,
    }


def _touches(db: Session, wf: Workflow, mt: MetaTable) -> bool:
    edges = (db.query(LineageEdge)
               .filter(LineageEdge.workflow_id == wf.id).all())
    return any(e.source_table == mt.name or e.target_table == mt.name for e in edges)


def _sample(db: Session, mt: MetaTable):
    ds = db.get(DataSource, mt.datasource_id)
    if not ds:
        return None
    try:
        from .engine_registry import run_query
        cols, rows, _ = run_query(ds, f'SELECT * FROM "{mt.name}" LIMIT 10',
                                  max_rows=10, timeout_s=10)
        from . import mask_service
        cols, rows, _m = mask_service.apply(db, ds, cols, rows)
        return {"columns": cols, "rows": [[str(v) if v is not None else None for v in r] for r in rows]}
    except Exception as e:
        return {"error": str(e)}


def _lineage(db: Session, mt: MetaTable, direction: str) -> List[dict]:
    out = []
    ds_map = {d.id: (d.name, d.type) for d in db.query(DataSource).all()}
    if direction == "up":
        edges = (db.query(LineageEdge)
                   .filter(LineageEdge.target_table == mt.name,
                           LineageEdge.target_datasource_id == mt.datasource_id).all())
        for e in edges:
            s = ds_map.get(e.source_datasource_id, ("?", ""))
            out.append({"table": e.source_table, "datasource": s[0], "job_type": e.job_type,
                        "workflow_id": e.workflow_id})
    else:
        edges = (db.query(LineageEdge)
                   .filter(LineageEdge.source_table == mt.name,
                           LineageEdge.source_datasource_id == mt.datasource_id).all())
        for e in edges:
            t = ds_map.get(e.target_datasource_id, ("?", ""))
            out.append({"table": e.target_table, "datasource": t[0], "job_type": e.job_type,
                        "workflow_id": e.workflow_id})
    return out


def search_assets(db: Session, keyword: str, limit: int = 20) -> dict:
    from . import meta_service
    tables = meta_service.meta_index.search(db, keyword, limit=limit)
    ds_map = {d.id: (d.name, d.type) for d in db.query(DataSource).all()}
    tag_map = {}
    for tt in db.query(TableTag).all():
        tag_map.setdefault(tt.table_id, []).append(tt.tag_id)
    all_tags = {t.id: t for t in db.query(Tag).all()}
    apis = []
    if keyword:
        apis = (db.query(ApiDef)
                  .filter(ApiDef.status == "published")
                  .filter(ApiDef.name.contains(keyword) | ApiDef.code.contains(keyword))
                  .limit(5).all())
    return {
        "tables": [{"id": t.id, "name": t.name,
                    "datasource": ds_map.get(t.datasource_id, ("?", ""))[0],
                    "comment": t.comment or t.manual_desc,
                    "owner": t.owner_name, "rows": t.row_count,
                    "tags": [{"id": x, "name": all_tags[x].name, "color": all_tags[x].color}
                             for x in tag_map.get(t.id, [])]} for t in tables],
        "apis": [{"id": a.id, "code": a.code, "name": a.name} for a in apis],
    }


def graph(db: Session, datasource_id: int, table: str, depth: int = 2) -> dict:
    """以指定表为中心展开血缘图（上/下游 depth 跳）。"""
    ds_map = {d.id: d.name for d in db.query(DataSource).all()}
    nodes, edges = [], []
    seen_nodes = set()

    def node_key(dsid, tname):
        return f"{dsid}:{tname}"

    def add_node(dsid, tname):
        k = node_key(dsid, tname)
        if k not in seen_nodes:
            seen_nodes.add(k)
            nodes.append({"id": k, "datasource": ds_map.get(dsid, "?"), "table": tname})
        return k

    center = add_node(datasource_id, table)
    frontier = [(datasource_id, table)]
    d = 0
    while frontier and d < depth:
        nxt = []
        for dsid, tname in frontier:
            ups = (db.query(LineageEdge)
                     .filter(LineageEdge.target_table == tname,
                             LineageEdge.target_datasource_id == dsid).all())
            downs = (db.query(LineageEdge)
                       .filter(LineageEdge.source_table == tname,
                               LineageEdge.source_datasource_id == dsid).all())
            for e in ups + downs:
                sk, tk = add_node(e.source_datasource_id, e.source_table), \
                    add_node(e.target_datasource_id, e.target_table)
                if {"source": sk, "target": tk, "workflow_id": e.workflow_id} not in edges:
                    edges.append({"source": sk, "target": tk, "workflow_id": e.workflow_id,
                                  "job_type": e.job_type})
                nxt.append((e.source_datasource_id, e.source_table))
                nxt.append((e.target_datasource_id, e.target_table))
        frontier = list({(a, b) for a, b in nxt if node_key(a, b) != center})
        d += 1
    return {"nodes": nodes, "edges": edges, "center": center}
