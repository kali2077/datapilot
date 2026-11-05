from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user, require_role, client_ip
from ..models import User, Tag
from ..services import audit as audit_svc
from ..services import asset_service

router = APIRouter(prefix="/api/asset", tags=["asset"])


@router.get("/search")
def search(keyword: str = "", limit: int = 20, user: User = Depends(current_user),
           db: Session = Depends(get_db)):
    return asset_service.search_assets(db, keyword, limit)


@router.get("/tags")
def list_tags(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [{"id": t.id, "name": t.name, "color": t.color}
            for t in db.query(Tag).all()]


@router.post("/tags")
def create_tag(req: dict, request: Request, user: User = Depends(require_role("engineer")),
               db: Session = Depends(get_db)):
    name = (req.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "标签名不能为空")
    if db.query(Tag).filter(Tag.name == name).first():
        raise HTTPException(400, "标签已存在")
    t = Tag(name=name, color=req.get("color", "#409EFF"))
    db.add(t)
    db.commit()
    audit_svc.log(db, user, "asset.tag.create", "tag", t.id, {"name": name},
                  ip=client_ip(request))
    return {"id": t.id}


@router.delete("/tags/{tag_id}")
def delete_tag(tag_id: int, request: Request, user: User = Depends(require_role("engineer")),
               db: Session = Depends(get_db)):
    from ..models import TableTag
    t = db.get(Tag, tag_id)
    if t:
        db.query(TableTag).filter(TableTag.tag_id == tag_id).delete()
        db.delete(t)
        db.commit()
        from ..services import meta_service
        meta_service.meta_index.invalidate()
    audit_svc.log(db, user, "asset.tag.delete", "tag", tag_id, ip=client_ip(request))
    return {"ok": True}


@router.post("/tables/{table_id}/tags")
def toggle_tag(table_id: int, req: dict, user: User = Depends(require_role("engineer")),
               db: Session = Depends(get_db)):
    tag_id = req.get("tag_id")
    on = bool(req.get("on", True))
    if not tag_id:
        raise HTTPException(400, "缺少 tag_id")
    asset_service.assign_tag(db, table_id, int(tag_id), on)
    return {"ok": True}


class TableAssetReq(BaseModel):
    manual_desc: Optional[str] = None
    owner_name: Optional[str] = None


@router.put("/tables/{table_id}")
def update_asset(table_id: int, req: TableAssetReq, user: User = Depends(require_role("engineer")),
                 db: Session = Depends(get_db)):
    from ..models import MetaTable
    from ..services import meta_service
    mt = db.get(MetaTable, table_id)
    if not mt:
        raise HTTPException(404, "表不存在")
    if req.manual_desc is not None:
        mt.manual_desc = req.manual_desc
    if req.owner_name is not None:
        mt.owner_name = req.owner_name
    db.commit()
    meta_service.meta_index.invalidate()
    return {"ok": True}


@router.get("/tables/{table_id}")
def detail(table_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    out = asset_service.table_detail(db, table_id)
    if not out:
        raise HTTPException(404, "表不存在")
    return out


@router.get("/map/graph")
def graph(datasource_id: int, table: str, depth: int = 2,
          user: User = Depends(current_user), db: Session = Depends(get_db)):
    return asset_service.graph(db, datasource_id, table, depth)
