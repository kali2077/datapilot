"""Hadoop 平台（集群注册表）：注册/连通测试/一键派生数据源/Kerberos keytab。"""
import os
import re
import socket
import urllib.parse

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session

from .. import config
from ..db import get_db
from ..deps import current_user, require_role, ensure_can_manage, client_ip
from ..models import User, HadoopCluster, DataSource
from ..security import encrypt_secret
from ..services import audit as audit_svc

router = APIRouter(prefix="/api/hadoop", tags=["hadoop"])

KEYTAB_DIR = os.path.join(config.DATA_DIR, "keytabs")
os.makedirs(KEYTAB_DIR, exist_ok=True)


class ClusterReq(BaseModel):
    name: str
    hdfs_uri: str = ""
    hs2_uri: str = ""
    metastore_uri: str = ""
    spark_uri: str = ""
    trino_uri: str = ""
    trino_catalog: str = ""
    kerberos: dict = {}
    description: str = ""


class DeriveReq(BaseModel):
    kind: str  # hive / spark / trino
    name: str
    database: str = ""
    username: str = ""
    password: str = ""


def _parse(uri: str) -> tuple:
    """thrift://h:10000 / http://nn:9870 / h:10000 -> (host, port, scheme)"""
    if not uri:
        return "", 0, ""
    u = uri.strip()
    scheme = ""
    if "://" in u:
        scheme, u = u.split("://", 1)
    u = u.split("/")[0]
    host, _, port = u.partition(":")
    return host.strip(), int(port) if port.isdigit() else 0, scheme.lower()


def _tcp(host: str, port: int, timeout: float = 3.0) -> tuple:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, "TCP 可达"
    except Exception as e:
        return False, str(e)[:120]


def _ser(c: HadoopCluster) -> dict:
    ker = c.kerberos or {}
    return {"id": c.id, "name": c.name, "hdfs_uri": c.hdfs_uri, "hs2_uri": c.hs2_uri,
            "metastore_uri": c.metastore_uri, "spark_uri": c.spark_uri,
            "trino_uri": c.trino_uri, "trino_catalog": c.trino_catalog,
            "kerberos": {"enabled": bool(ker.get("enabled")), "realm": ker.get("realm", ""),
                         "principal": ker.get("principal", ""),
                         "keytab_name": ker.get("keytab_name", "")},
            "description": c.description, "owner_id": c.owner_id,
            "updated_at": str(c.updated_at)}


@router.get("/clusters")
def list_clusters(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [_ser(c) for c in db.query(HadoopCluster).order_by(HadoopCluster.id).all()]


@router.post("/clusters")
def create_cluster(req: ClusterReq, request: Request,
                   user: User = Depends(require_role("engineer")), db: Session = Depends(get_db)):
    if db.query(HadoopCluster).filter(HadoopCluster.name == req.name).first():
        raise HTTPException(400, "平台名称已存在")
    c = HadoopCluster(**req.dict(), owner_id=user.id)
    db.add(c)
    db.commit()
    audit_svc.log(db, user, "hadoop.create", "hadoop_cluster", c.id, {"name": req.name},
                  ip=client_ip(request))
    return _ser(c)


@router.put("/clusters/{cid}")
def update_cluster(cid: int, req: ClusterReq, request: Request,
                   user: User = Depends(require_role("engineer")), db: Session = Depends(get_db)):
    c = db.get(HadoopCluster, cid)
    if not c:
        raise HTTPException(404, "平台不存在")
    ensure_can_manage(user, c.owner_id, "该平台")
    if req.name != c.name and db.query(HadoopCluster).filter(HadoopCluster.name == req.name).first():
        raise HTTPException(400, "平台名称已存在")
    for k, v in req.dict().items():
        setattr(c, k, v)
    db.commit()
    audit_svc.log(db, user, "hadoop.update", "hadoop_cluster", cid, {"name": req.name},
                  ip=client_ip(request))
    return _ser(c)


@router.delete("/clusters/{cid}")
def delete_cluster(cid: int, request: Request,
                   user: User = Depends(require_role("engineer")), db: Session = Depends(get_db)):
    c = db.get(HadoopCluster, cid)
    if not c:
        raise HTTPException(404, "平台不存在")
    ensure_can_manage(user, c.owner_id, "该平台")
    derived = db.query(DataSource).filter(
        DataSource.params.contains(f'"cluster_id": {cid}')).count()
    if derived:
        raise HTTPException(400, f"有 {derived} 个派生数据源引用该平台，请先删除它们")
    audit_svc.log(db, user, "hadoop.delete", "hadoop_cluster", cid, {"name": c.name},
                  ip=client_ip(request))
    ker = c.kerberos or {}
    if ker.get("keytab_name"):
        try:
            os.unlink(os.path.join(KEYTAB_DIR, ker["keytab_name"]))
        except OSError:
            pass
    db.delete(c)
    db.commit()
    return {"ok": True}


@router.post("/clusters/{cid}/test")
def test_cluster(cid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    c = db.get(HadoopCluster, cid)
    if not c:
        raise HTTPException(404, "平台不存在")
    results = {}
    checks = {"hdfs": c.hdfs_uri, "hive_hs2": c.hs2_uri, "metastore": c.metastore_uri,
              "spark": c.spark_uri, "trino": c.trino_uri}
    for comp, uri in checks.items():
        if not uri:
            results[comp] = {"ok": None, "message": "未配置"}
            continue
        host, port, scheme = _parse(uri)
        if not host or not port:
            results[comp] = {"ok": False, "message": "URI 缺少 host:port"}
            continue
        if comp == "hdfs" and scheme in ("http", "https"):
            # WebHDFS HTTP 探活
            try:
                import httpx
                r = httpx.get(f"{scheme}://{host}:{port}/jmx?q=HadoopHttpServer", timeout=5)
                results[comp] = {"ok": r.status_code < 500,
                                 "message": f"HTTP {r.status_code}"}
            except Exception as e:
                results[comp] = {"ok": False, "message": str(e)[:120]}
        else:
            ok, msg = _tcp(host, port)
            results[comp] = {"ok": ok, "message": msg}
    return {"components": results,
            "kerberos": {"enabled": bool((c.kerberos or {}).get("enabled"))}}


@router.post("/clusters/{cid}/keytab")
async def upload_keytab(cid: int, file: UploadFile, request: Request,
                        user: User = Depends(require_role("engineer")),
                        db: Session = Depends(get_db)):
    c = db.get(HadoopCluster, cid)
    if not c:
        raise HTTPException(404, "平台不存在")
    ensure_can_manage(user, c.owner_id, "该平台")
    data = await file.read()
    if len(data) > 2 * 1024 * 1024:
        raise HTTPException(400, "keytab 文件需小于 2MB")
    ker = c.kerberos or {}
    if ker.get("keytab_name"):
        try:
            os.unlink(os.path.join(KEYTAB_DIR, ker["keytab_name"]))
        except OSError:
            pass
    fname = f"cluster_{cid}_{int(__import__('time').time())}.keytab"
    with open(os.path.join(KEYTAB_DIR, fname), "wb") as f:
        f.write(data)
    ker["keytab_name"] = fname
    c.kerberos = ker
    db.commit()
    audit_svc.log(db, user, "hadoop.keytab.upload", "hadoop_cluster", cid,
                  {"file": fname}, ip=client_ip(request))
    return {"ok": True, "keytab_name": fname}


@router.post("/clusters/{cid}/derive")
def derive_datasource(cid: int, req: DeriveReq, request: Request,
                      user: User = Depends(require_role("engineer")),
                      db: Session = Depends(get_db)):
    c = db.get(HadoopCluster, cid)
    if not c:
        raise HTTPException(404, "平台不存在")
    ensure_can_manage(user, c.owner_id, "该平台")
    if req.kind not in ("hive", "spark", "trino"):
        raise HTTPException(400, "kind 仅支持 hive/spark/trino")
    if db.query(DataSource).filter(DataSource.name == req.name).first():
        raise HTTPException(400, "数据源名称已存在")

    params = {"cluster_id": cid}
    if req.kind == "trino":
        host, port, _ = _parse(c.trino_uri)
        if not host:
            raise HTTPException(400, "平台未配置 Trino URI")
        if c.trino_catalog:
            params["catalog"] = c.trino_catalog
        port = port or 8080
    else:
        uri = c.hs2_uri if req.kind == "hive" else c.spark_uri
        host, port, _ = _parse(uri)
        if not host:
            raise HTTPException(400, f"平台未配置 {req.kind} URI")
        port = port or (10000 if req.kind == "hive" else 10001)
        ker = c.kerberos or {}
        params["auth"] = "KERBEROS" if ker.get("enabled") else "NONE"

    ds = DataSource(name=req.name, type=req.kind, host=host, port=port,
                    database=req.database or "", username=req.username or "",
                    password_enc=encrypt_secret(req.password) if req.password else "",
                    params=params, description=f"派生自 Hadoop 平台 [{c.name}]",
                    owner_id=user.id)
    db.add(ds)
    db.commit()
    audit_svc.log(db, user, "hadoop.derive", "datasource", ds.id,
                  {"kind": req.kind, "cluster": c.name}, ip=client_ip(request))
    return {"id": ds.id, "name": ds.name, "type": ds.type,
            "host": ds.host, "port": ds.port, "params": ds.params}
