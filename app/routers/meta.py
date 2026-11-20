from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user, require_role, client_ip
from ..models import User, MetaTable, MetaColumn, MetaChange, GlossaryTerm, FewShot, DataSource
from ..services import audit as audit_svc
from ..services import meta_service, meta_vec_service

router = APIRouter(prefix="/api/meta", tags=["meta"])


@router.get("/tables")
def list_tables(datasource_id: int = 0, keyword: str = "", page: int = 1, size: int = 50,
                user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = db.query(MetaTable)
    if datasource_id:
        q = q.filter(MetaTable.datasource_id == datasource_id)
    if keyword:
        like = f"%{keyword}%"
        q = q.filter(MetaTable.name.like(like) | MetaTable.comment.like(like) |
                     MetaTable.manual_desc.like(like))
    total = q.count()
    rows = (q.order_by(MetaTable.id).offset((page - 1) * size).limit(size).all())
    ds_map = {d.id: (d.name, d.type) for d in db.query(DataSource).all()}
    return {"total": total, "items": [{
        "id": t.id, "name": t.name, "datasource_id": t.datasource_id,
        "datasource": ds_map.get(t.datasource_id, ("?", ""))[0],
        "comment": t.comment, "manual_desc": t.manual_desc,
        "owner_name": t.owner_name, "row_count": t.row_count,
        "last_collected_at": str(t.last_collected_at or "")} for t in rows]}


@router.get("/tables/{table_id}")
def get_table(table_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    mt = db.get(MetaTable, table_id)
    if not mt:
        raise HTTPException(404, "表不存在")
    cols = (db.query(MetaColumn).filter(MetaColumn.table_id == mt.id)
              .order_by(MetaColumn.ordinal).all())
    ds = db.get(DataSource, mt.datasource_id)
    return {"id": mt.id, "name": mt.name, "datasource_id": mt.datasource_id,
            "datasource": ds.name if ds else "?", "comment": mt.comment,
            "manual_desc": mt.manual_desc, "owner_name": mt.owner_name,
            "row_count": mt.row_count,
            "columns": [{"id": c.id, "name": c.name, "type": c.data_type,
                         "comment": c.comment, "manual_desc": c.manual_desc,
                         "is_pk": c.is_pk, "samples": (c.sample_values or {}).get("values", [])}
                        for c in cols]}


@router.get("/schema/{datasource_id}")
def get_schema(datasource_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """SQL 编辑器智能补全数据：表 + 字段（名称/类型/注释）。"""
    d = db.get(DataSource, datasource_id)
    if not d:
        raise HTTPException(404, "数据源不存在")
    tables = (db.query(MetaTable)
                .filter(MetaTable.datasource_id == datasource_id)
                .order_by(MetaTable.id).limit(200).all())
    out = []
    for t in tables:
        cols = (db.query(MetaColumn)
                  .filter(MetaColumn.table_id == t.id)
                  .order_by(MetaColumn.ordinal).limit(60).all())
        out.append({
            "name": t.name, "comment": t.comment or t.manual_desc or "",
            "columns": [{"name": c.name, "type": c.data_type,
                         "comment": c.comment or c.manual_desc or ""} for c in cols],
        })
    return {"datasource": d.name, "tables": out}


class TableUpdateReq(BaseModel):
    manual_desc: Optional[str] = None
    owner_name: Optional[str] = None


@router.put("/tables/{table_id}")
def update_table(table_id: int, req: TableUpdateReq, request: Request,
                 user: User = Depends(require_role("engineer")), db: Session = Depends(get_db)):
    mt = db.get(MetaTable, table_id)
    if not mt:
        raise HTTPException(404, "表不存在")
    if req.manual_desc is not None:
        mt.manual_desc = req.manual_desc
    if req.owner_name is not None:
        mt.owner_name = req.owner_name
    db.commit()
    meta_service.meta_index.invalidate()
    meta_vec_service.refresh_tables(db, [table_id])  # 补录描述影响向量，增量重算（未配置 embedder 时 no-op）
    audit_svc.log(db, user, "meta.table.update", "meta_table", table_id,
                  req.dict(exclude_none=True), ip=client_ip(request))
    return {"ok": True}


# ---------------- 资产变更巡检 ----------------

DANGER_CHANGE_TYPES = {"table_removed", "column_removed", "column_altered"}


@router.get("/changes")
def list_changes(datasource_id: int = 0, table_id: int = 0, change_type: str = "",
                 page: int = 1, size: int = 50,
                 user: User = Depends(current_user), db: Session = Depends(get_db)):
    """资产变更巡检记录（元数据采集时自动 diff 生成）。"""
    q = db.query(MetaChange)
    if datasource_id:
        q = q.filter(MetaChange.datasource_id == datasource_id)
    if table_id:
        q = q.filter(MetaChange.table_id == table_id)
    if change_type:
        q = q.filter(MetaChange.change_type == change_type)
    total = q.count()
    rows = (q.order_by(MetaChange.detected_at.desc(), MetaChange.id.desc())
              .offset((page - 1) * size).limit(size).all())
    ds_names = {d.id: d.name for d in db.query(DataSource).all()}
    return {"total": total, "items": [{
        "id": c.id, "datasource_id": c.datasource_id,
        "datasource": ds_names.get(c.datasource_id, ""),
        "table_id": c.table_id, "schema_name": c.schema_name,
        "table_name": c.table_name, "change_type": c.change_type,
        "danger": c.change_type in DANGER_CHANGE_TYPES,
        "detail": c.detail, "detected_at": str(c.detected_at),
    } for c in rows]}


# ---------------- 术语表 ----------------

class GlossaryReq(BaseModel):
    term: str
    synonyms: str = ""
    definition: str = ""


@router.get("/glossary")
def list_glossary(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [{"id": g.id, "term": g.term, "synonyms": g.synonyms, "definition": g.definition}
            for g in db.query(GlossaryTerm).all()]


@router.post("/glossary")
def create_glossary(req: GlossaryReq, user: User = Depends(require_role("engineer")),
                    db: Session = Depends(get_db)):
    g = GlossaryTerm(term=req.term, synonyms=req.synonyms, definition=req.definition,
                     created_by=user.username)
    db.add(g)
    db.commit()
    meta_vec_service.refresh_glossary(db)  # RAG ③ 增量刷新（未配置 embedder 时 no-op）
    audit_svc.log(db, user, "glossary.create", "glossary", g.id, {"term": req.term})
    return {"id": g.id}


@router.put("/glossary/{gid}")
def update_glossary(gid: int, req: GlossaryReq, user: User = Depends(require_role("engineer")),
                    db: Session = Depends(get_db)):
    g = db.get(GlossaryTerm, gid)
    if not g:
        raise HTTPException(404, "术语不存在")
    g.term, g.synonyms, g.definition = req.term, req.synonyms, req.definition
    db.commit()
    meta_vec_service.refresh_glossary(db)
    return {"ok": True}


@router.delete("/glossary/{gid}")
def delete_glossary(gid: int, user: User = Depends(require_role("engineer")),
                    db: Session = Depends(get_db)):
    g = db.get(GlossaryTerm, gid)
    if g:
        db.delete(g)
        db.commit()
        meta_vec_service.invalidate_ref(db, "glossary", gid)
    return {"ok": True}


# ---------------- 问数样例 ----------------

class FewShotReq(BaseModel):
    question: str
    sql: str
    datasource_id: int = 0
    enabled: bool = True


@router.get("/fewshot")
def list_fewshot(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [{"id": f.id, "question": f.question, "sql": f.sql,
             "datasource_id": f.datasource_id, "enabled": f.enabled}
            for f in db.query(FewShot).all()]


@router.post("/fewshot")
def create_fewshot(req: FewShotReq, user: User = Depends(require_role("engineer")),
                   db: Session = Depends(get_db)):
    f = FewShot(question=req.question, sql=req.sql,
                datasource_id=req.datasource_id, enabled=req.enabled)
    db.add(f)
    db.commit()
    meta_vec_service.refresh_fewshots(db)  # RAG ③ 增量刷新（未配置 embedder 时 no-op）
    audit_svc.log(db, user, "fewshot.create", "fewshot", f.id, {"q": req.question[:80]})
    return {"id": f.id}


@router.put("/fewshot/{fid}")
def update_fewshot(fid: int, req: FewShotReq, user: User = Depends(require_role("engineer")),
                   db: Session = Depends(get_db)):
    f = db.get(FewShot, fid)
    if not f:
        raise HTTPException(404, "样例不存在")
    f.question, f.sql, f.datasource_id, f.enabled = \
        req.question, req.sql, req.datasource_id, req.enabled
    db.commit()
    meta_vec_service.refresh_fewshots(db)
    return {"ok": True}


@router.delete("/fewshot/{fid}")
def delete_fewshot(fid: int, user: User = Depends(require_role("engineer")),
                   db: Session = Depends(get_db)):
    f = db.get(FewShot, fid)
    if f:
        db.delete(f)
        db.commit()
        meta_vec_service.invalidate_ref(db, "fewshot", fid)
    return {"ok": True}
