from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user, require_role, ensure_can_manage, can_manage, client_ip
from ..models import User, DataSource, SyncJob, ApiDef, MonitorRule, SavedQuery, \
    MetaTable, MetaColumn, TableTag, LineageEdge
from ..security import encrypt_secret
from ..services import audit as audit_svc
from ..services import engine_registry, meta_service

router = APIRouter(prefix="/api/datasources", tags=["datasources"])


class DsReq(BaseModel):
    name: str
    type: str  # mysql/doris/postgres/sqlite
    host: str = ""
    port: int = 0
    database: str = ""
    username: str = ""
    password: str = ""
    params: dict = {}
    description: str = ""


def _serialize(ds: DataSource) -> dict:
    return {"id": ds.id, "name": ds.name, "type": ds.type, "host": ds.host, "port": ds.port,
            "database": ds.database, "username": ds.username,
            "has_password": bool(ds.password_enc),
            "params": ds.params, "description": ds.description,
            "owner_id": ds.owner_id, "status": ds.status,
            "updated_at": str(ds.updated_at)}


@router.get("")
def list_ds(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [_serialize(d) for d in db.query(DataSource).order_by(DataSource.id).all()]


@router.post("")
def create_ds(req: DsReq, request: Request, user: User = Depends(require_role("engineer")),
              db: Session = Depends(get_db)):
    if req.type not in ("mysql", "doris", "starrocks", "oracle", "clickhouse",
                        "postgres", "sqlite", "hive", "spark", "trino", "maxcompute", "mssql"):
        raise HTTPException(400, "不支持的数据源类型")
    if req.type == "maxcompute" and not req.username:
        raise HTTPException(400, "MaxCompute 需要 AccessKeyId（填在用户名）")
    if db.query(DataSource).filter(DataSource.name == req.name).first():
        raise HTTPException(400, "数据源名称已存在")
    ds = DataSource(name=req.name, type=req.type, host=req.host, port=req.port,
                    database=req.database, username=req.username,
                    password_enc=encrypt_secret(req.password) if req.password else "",
                    params=req.params, description=req.description, owner_id=user.id)
    db.add(ds)
    db.commit()
    try:
        engine_registry.test_connection(ds)
    except Exception as e:
        db.delete(ds)
        db.commit()
        raise HTTPException(400, f"连接测试失败: {e}")
    audit_svc.log(db, user, "datasource.create", "datasource", ds.id,
                  {"name": req.name, "type": req.type}, ip=client_ip(request))
    return _serialize(ds)


@router.put("/{ds_id}")
def update_ds(ds_id: int, req: DsReq, request: Request,
              user: User = Depends(require_role("engineer")), db: Session = Depends(get_db)):
    ds = db.get(DataSource, ds_id)
    if not ds:
        raise HTTPException(404, "数据源不存在")
    ensure_can_manage(user, ds.owner_id, "数据源")
    if req.name != ds.name and db.query(DataSource).filter(DataSource.name == req.name).first():
        raise HTTPException(400, "数据源名称已存在")
    ds.name, ds.type, ds.host, ds.port = req.name, req.type, req.host, req.port
    ds.database, ds.username = req.database, req.username
    ds.params, ds.description = req.params, req.description
    if req.password:
        ds.password_enc = encrypt_secret(req.password)
    db.commit()
    try:
        engine_registry.test_connection(ds)
    except Exception as e:
        raise HTTPException(400, f"已保存，但连接测试失败: {e}")
    audit_svc.log(db, user, "datasource.update", "datasource", ds.id,
                  {"name": req.name}, ip=client_ip(request))
    return _serialize(ds)


@router.delete("/{ds_id}")
def delete_ds(ds_id: int, request: Request, user: User = Depends(require_role("engineer")),
              db: Session = Depends(get_db)):
    ds = db.get(DataSource, ds_id)
    if not ds:
        raise HTTPException(404, "数据源不存在")
    ensure_can_manage(user, ds.owner_id, "数据源")
    # 引用检查：被任务/API/监控规则/保存查询引用时拒绝删除
    refs = {
        "sync_jobs": db.query(SyncJob)
            .filter((SyncJob.source_datasource_id == ds_id) |
                    (SyncJob.target_datasource_id == ds_id)).count(),
        "apis": db.query(ApiDef).filter(ApiDef.datasource_id == ds_id).count(),
        "monitor_rules": db.query(MonitorRule)
            .filter(MonitorRule.datasource_id == ds_id).count(),
        "saved_queries": db.query(SavedQuery)
            .filter(SavedQuery.datasource_id == ds_id).count(),
    }
    if any(refs.values()):
        detail = "、".join(f"{k} {v} 个" for k, v in refs.items() if v)
        raise HTTPException(400, f"数据源被引用，无法删除（{detail}），请先处理相关对象")
    # 级联清理：元数据/标签/血缘 + 搜索索引
    table_ids = [t.id for t in db.query(MetaTable).filter(MetaTable.datasource_id == ds_id)]
    if table_ids:
        db.query(MetaColumn).filter(MetaColumn.table_id.in_(table_ids)).delete(synchronize_session=False)
        db.query(TableTag).filter(TableTag.table_id.in_(table_ids)).delete(synchronize_session=False)
    db.query(MetaTable).filter(MetaTable.datasource_id == ds_id).delete(synchronize_session=False)
    db.query(LineageEdge).filter((LineageEdge.source_datasource_id == ds_id) |
                                 (LineageEdge.target_datasource_id == ds_id)) \
        .delete(synchronize_session=False)
    audit_svc.log(db, user, "datasource.delete", "datasource", ds.id,
                  {"name": ds.name}, ip=client_ip(request))
    db.delete(ds)
    db.commit()
    from ..services import meta_service
    meta_service.meta_index.invalidate()
    return {"ok": True, "cascade": {"meta_tables": len(table_ids), "lineage": True}}


@router.post("/{ds_id}/test")
def test_ds(ds_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ds = db.get(DataSource, ds_id)
    if not ds:
        raise HTTPException(404, "数据源不存在")
    try:
        return engine_registry.test_connection(ds)
    except Exception as e:
        return {"ok": False, "message": str(e)}


@router.post("/{ds_id}/collect")
def collect(ds_id: int, request: Request, user: User = Depends(require_role("engineer")),
            db: Session = Depends(get_db)):
    ds = db.get(DataSource, ds_id)
    if not ds:
        raise HTTPException(404, "数据源不存在")
    try:
        result = meta_service.collect_datasource(db, ds)
    except Exception as e:
        raise HTTPException(400, f"采集失败: {e}")
    meta_service.meta_index.invalidate()
    audit_svc.log(db, user, "datasource.collect", "datasource", ds.id, result,
                  ip=client_ip(request))
    return result
