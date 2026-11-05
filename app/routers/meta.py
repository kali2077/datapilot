from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user, require_role, client_ip
from ..models import User, MetaTable, MetaColumn, GlossaryTerm, FewShot, DataSource
from ..services import audit as audit_svc
from ..services import meta_service

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
    audit_svc.log(db, user, "meta.table.update", "meta_table", table_id,
                  req.dict(exclude_none=True), ip=client_ip(request))
    return {"ok": True}


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
    return {"ok": True}


@router.delete("/glossary/{gid}")
def delete_glossary(gid: int, user: User = Depends(require_role("engineer")),
                    db: Session = Depends(get_db)):
    g = db.get(GlossaryTerm, gid)
    if g:
        db.delete(g)
        db.commit()
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
    return {"ok": True}


@router.delete("/fewshot/{fid}")
def delete_fewshot(fid: int, user: User = Depends(require_role("engineer")),
                   db: Session = Depends(get_db)):
    f = db.get(FewShot, fid)
    if f:
        db.delete(f)
        db.commit()
    return {"ok": True}
