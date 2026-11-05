"""T1/T2 能力 API：文件入仓 / 整库同步 / 数据比对 / 主机指标 / 系统设置 / 脱敏规则。"""
import csv
import datetime
import io
import json as _json
import re
import time
from typing import List, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import config
from ..db import get_db
from ..deps import current_user, require_role, ensure_can_manage, client_ip
from ..models import (User, DataSource, SyncJob, MaskRule, SysSetting, MetaTable)
from ..security import hash_password
from ..services import audit as audit_svc
from ..services import integ_service, sqlguard, mask_service
from ..services.engine_registry import run_query, execute_write, get_engine, table_names
from ..services.integ_service import _qi

router = APIRouter(prefix="/api", tags=["tools"])

SETTING_KEYS = ("superset_url",)


# ================================================================ 文件入仓

def _read_table(file_bytes: bytes, filename: str) -> tuple:
    """→ (headers, rows[list[list]])。支持 csv/txt/json/xlsx。"""
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xls")):
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
        ws = wb[wb.sheetnames[0]]
        rows = [[c for c in r] for r in ws.iter_rows(values_only=True)]
        if not rows:
            raise ValueError("Excel 首个 sheet 为空")
        return [str(h) for h in rows[0]], [[("" if v is None else v) for v in r] for r in rows[1:]]
    if name.endswith(".json"):
        data = _json.loads(file_bytes.decode("utf-8-sig"))
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list) or not data or not isinstance(data[0], dict):
            raise ValueError("JSON 需为对象数组，如 [{...},{...}]")
        headers = list(dict.fromkeys(k for d in data for k in d.keys()))
        return headers, [[d.get(h) for h in headers] for d in data]
    # csv / txt
    text = file_bytes.decode("utf-8-sig", errors="replace")
    delim = "\t" if ("\t" in text.splitlines()[0] and "," not in text.splitlines()[0]) else ","
    reader = list(csv.reader(io.StringIO(text), delimiter=delim))
    if not reader:
        raise ValueError("文件为空")
    return [h.strip() for h in reader[0]], [[c for c in r] for r in reader[1:]]


_DATE_RE = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}([ T]\d{1,2}:\d{2}(:\d{2})?)?$")


def _infer(headers, rows) -> List[dict]:
    """采样推断每列语义类型 → {name, semantic}"""
    out = []
    sample = rows[:200]
    for i, h in enumerate(headers):
        vals = [r[i] for r in sample if i < len(r) and str(r[i]).strip() != ""]
        if not vals:
            out.append({"name": h, "semantic": "text"})
            continue
        def all_match(fn):
            return all(fn(v) for v in vals)
        if all_match(lambda v: re.fullmatch(r"-?\d+", str(v))):
            sem = "bigint"
        elif all_match(lambda v: re.fullmatch(r"-?\d+\.\d+", str(v))):
            sem = "double"
        elif all_match(lambda v: str(v).lower() in ("true", "false", "0", "1")):
            sem = "boolean"
        elif all_match(lambda v: _DATE_RE.fullmatch(str(v).strip())):
            sem = "datetime"
        else:
            sem = "varchar"
        out.append({"name": h, "semantic": sem})
    return out


def _quote(engine: str, name: str) -> str:
    return f'"{name}"' if engine in ("trino", "postgres", "oracle") else f"`{name}`"


def _create_import_table(db: Session, dst: DataSource, table: str, cols: List[dict],
                         if_exists: str, log=print):
    """导入建表（含 replace 的 DROP）。"""
    if dst.type == "maxcompute":
        raise ValueError("MaxCompute 目标暂不支持文件导入")
    from sqlalchemy import inspect as _sa_i
    eng = get_engine(dst)
    insp = _sa_i(eng)
    exists = table in insp.get_table_names()
    if exists and if_exists == "fail":
        raise ValueError(f"目标表 {table} 已存在（可选择追加或替换）")
    if exists and if_exists == "replace":
        log(f"[ddl] replace: 删除旧表 {table}")
        execute_write(dst, f'DROP TABLE IF EXISTS {_quote(dst.type, table)}')
        exists = False
    if not exists:
        defs = []
        for c in cols:
            tt = integ_service.map_dtype(db, c["semantic"], dst.type)
            defs.append(f"{_quote(dst.type, c['name'])} {tt}")
        ddl = f"CREATE TABLE {_quote(dst.type, table)} (\n" + ",\n".join(defs) + "\n)"
        if dst.type == "mysql":
            ddl += " DEFAULT CHARSET=utf8mb4"
        if dst.type == "clickhouse":
            ddl += " ENGINE = MergeTree ORDER BY tuple()"
        log(f"[ddl] {ddl[:200]}")
        execute_write(dst, ddl)
    # 返回实际列名集合（目标已有表时对齐交集）
    if exists:
        live = {c["name"].lower() for c in _sa_i(eng).get_columns(table)}
        return [c for c in cols if c["name"].lower() in live]
    return cols


class ImportReq(BaseModel):
    target_datasource_id: int
    table_name: str
    if_exists: str = "append"  # append/replace/fail


@router.post("/import/run")
async def import_run(request: Request, file: UploadFile,
                     target_datasource_id: int = Form(0), table_name: str = Form(""),
                     if_exists: str = Form("append"),
                     user: User = Depends(require_role("engineer")),
                     db: Session = Depends(get_db)):
    data = await file.read()
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(400, "文件需小于 20MB")
    if not target_datasource_id:
        raise HTTPException(400, "请选择目标数据源")
    dst = db.get(DataSource, target_datasource_id)
    if not dst:
        raise HTTPException(404, "目标数据源不存在")
    if dst.type == "maxcompute":
        raise HTTPException(400, "MaxCompute 目标暂不支持文件导入")
    table = re.sub(r"[^a-zA-Z0-9_]", "_", table_name or
                   (file.filename or "import").rsplit(".", 1)[0]).lower().strip("_")[:60]
    if not table:
        raise HTTPException(400, "无法确定表名，请手动填写")
    log_lines = []
    log = lambda s: log_lines.append(str(s))
    try:
        headers, rows = _read_table(data, file.filename or "")
        if not rows:
            raise ValueError("文件没有数据行")
        headers = [re.sub(r"[^a-zA-Z0-9_\u4e00-\u9fa5]", "_", h.strip()) or f"col{i}"
                   for i, h in enumerate(headers)]
        # 表头去重（同名列依次加后缀）
        seen = {}
        deduped = []
        for h in headers:
            n = seen.get(h, 0)
            seen[h] = n + 1
            deduped.append(h if n == 0 else f"{h}_{n + 1}")
        headers = deduped
        cols = _infer(headers, rows)
        cols = _create_import_table(db, dst, table, cols, if_exists, log)
        names = [c["name"] for c in cols]
        idx = [headers.index(n) for n in names]
        rows = [[r[i] if i < len(r) else None for i in idx] for r in rows]
        # 批量插入
        eng = get_engine(dst)
        from sqlalchemy import text as _t
        ph = ", ".join(f":c{i}" for i in range(len(names)))
        ins_sql = (f"INSERT INTO {_quote(dst.type, table)} (" +
                   ", ".join(_quote(dst.type, n) for n in names) + f") VALUES ({ph})")
        t0 = time.time()
        with eng.begin() as dconn:
            for i in range(0, len(rows), 500):
                batch = [{f"c{j}": (None if v == "" else v) for j, v in enumerate(r)}
                         for r in rows[i:i + 500]]
                dconn.execute(_t(ins_sql), batch)
        dur = int((time.time() - t0) * 1000)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"导入失败: {e}")
    audit_svc.log(db, user, "import.run", "datasource", dst.id,
                  {"file": file.filename, "table": table, "rows": len(rows)},
                  ip=client_ip(request))
    return {"table": table, "rows": len(rows), "columns": names,
            "duration_ms": dur, "target": dst.name, "logs": log_lines[-10:]}


# ================================================================ 整库同步

class BulkReq(BaseModel):
    source_datasource_id: int
    target_datasource_id: int
    tables: List[str]
    write_mode: str = "append"
    cron: str = ""
    publish: bool = True
    trial: bool = True


@router.post("/integ/jobs/bulk")
def bulk_sync(req: BulkReq, request: Request,
              user: User = Depends(require_role("engineer")), db: Session = Depends(get_db)):
    src = db.get(DataSource, req.source_datasource_id)
    dst = db.get(DataSource, req.target_datasource_id)
    if not src or not dst:
        raise HTTPException(404, "源/目标数据源不存在")
    if src.id == dst.id:
        raise HTTPException(400, "源与目标不能是同一数据源")
    if not req.tables:
        raise HTTPException(400, "请选择要同步的表")
    created, skipped, failed = [], [], []
    for tname in req.tables:
        name = f"sync_{tname}_to_{dst.name}"
        if db.query(SyncJob).filter(SyncJob.name == name).first():
            skipped.append({"table": tname, "reason": "同名任务已存在"})
            continue
        try:
            job = SyncJob(name=name, source_datasource_id=src.id, source_table=tname,
                          source_query="", target_datasource_id=dst.id,
                          target_table=tname, status="draft",
                          config={"write_mode": req.write_mode}, owner_id=user.id)
            db.add(job)
            db.commit()
            issues = integ_service.validate_job(db, job)
            errors = [i for i in issues if i["level"] == "error"]
            if errors:
                failed.append({"table": tname, "error": "；".join(i["message"] for i in errors)})
                continue
            if req.trial:
                integ_service.trial_run(db, job, limit=100, log=lambda s: None)
            if req.publish:
                integ_service.publish_job(db, job, user, cron=req.cron or "")
            created.append({"table": tname, "job_id": job.id,
                            "version": job.current_version, "status": job.status})
        except Exception as e:
            failed.append({"table": tname, "error": str(e)[:200]})
    audit_svc.log(db, user, "integ.bulk", "sync_job", 0,
                  {"created": len(created), "failed": len(failed)}, ip=client_ip(request))
    return {"created": created, "skipped": skipped, "failed": failed}


# ================================================================ 数据比对

@router.post("/integ/jobs/{job_id}/compare")
def compare_job(job_id: int, request: Request, sample_rows: int = 200,
                user: User = Depends(require_role("engineer")), db: Session = Depends(get_db)):
    from collections import Counter
    job = db.get(SyncJob, job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    if not job.source_table and not job.source_query:
        raise HTTPException(400, "任务无源表，无法比对")
    src = db.get(DataSource, job.source_datasource_id)
    dst = db.get(DataSource, job.target_datasource_id)
    n = max(10, min(sample_rows, 5000))

    def count(ds, table):
        cols, rows, _ = run_query(ds, f"SELECT COUNT(*) AS c FROM {_qi(ds.type, table)}",
                                  max_rows=1, timeout_s=120)
        return int(rows[0][0])

    src_cnt = count(src, job.source_table)
    dst_cnt = count(dst, job.target_table)
    # 抽样值集比对（全列元组 str 化）
    _, s_rows, _ = run_query(src, f"SELECT * FROM {_qi(src.type, job.source_table)} LIMIT {n}",
                             max_rows=n, timeout_s=120)
    _, d_rows, _ = run_query(dst, f"SELECT * FROM {_qi(dst.type, job.target_table)} LIMIT {n}",
                             max_rows=n, timeout_s=120)
    sc, dc = Counter(tuple(str(v) for v in r) for r in s_rows), \
        Counter(tuple(str(v) for v in r) for r in d_rows)
    only_src = list((sc - dc).elements())
    only_dst = list((dc - sc).elements())
    audit_svc.log(db, user, "integ.compare", "sync_job", job.id,
                  {"src": src_cnt, "dst": dst_cnt}, ip=client_ip(request))
    return {
        "source": {"datasource": src.name, "table": job.source_table, "rows": src_cnt},
        "target": {"datasource": dst.name, "table": job.target_table, "rows": dst_cnt},
        "row_diff": src_cnt - dst_cnt,
        "sample": {"rows": n, "only_in_source": len(only_src), "only_in_target": len(only_dst),
                   "src_examples": [list(r)[:6] for r in only_src[:5]],
                   "dst_examples": [list(r)[:6] for r in only_dst[:5]]},
        "consistent": src_cnt == dst_cnt and not only_src and not only_dst,
    }


# ================================================================ 主机指标

@router.get("/system/metrics")
def system_metrics(user: User = Depends(current_user)):
    import psutil
    import os as _os
    cpu = psutil.cpu_percent(interval=None)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage(config.DATA_DIR)
    proc = psutil.Process()
    return {"cpu_percent": cpu, "cpu_count": _os.cpu_count(),
            "mem_total_gb": round(mem.total / 1024**3, 1),
            "mem_used_gb": round(mem.used / 1024**3, 1),
            "mem_percent": mem.percent,
            "disk_total_gb": round(disk.total / 1024**3, 1),
            "disk_used_gb": round(disk.used / 1024**3, 1),
            "disk_percent": disk.percent,
            "proc_mem_mb": round(proc.memory_info().rss / 1024**2, 1),
            "boot_time": datetime.datetime.fromtimestamp(psutil.boot_time()).isoformat()}


# ================================================================ 系统设置

@router.get("/settings")
def get_settings(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.query(SysSetting).filter(SysSetting.key.in_(SETTING_KEYS)).all()
    out = {k: "" for k in SETTING_KEYS}
    for r in rows:
        out[r.key] = r.value
    return out


class SettingReq(BaseModel):
    superset_url: str = ""


@router.put("/settings")
def put_settings(req: SettingReq, request: Request,
                 admin: User = Depends(require_role("admin")), db: Session = Depends(get_db)):
    url = req.superset_url.strip()
    if url and not url.startswith(("http://", "https://")):
        raise HTTPException(400, "URL 需以 http(s):// 开头")
    row = db.get(SysSetting, "superset_url")
    if not row:
        row = SysSetting(key="superset_url", value=url)
        db.add(row)
    else:
        row.value = url
    db.commit()
    audit_svc.log(db, admin, "settings.update", "setting", 0,
                  {"superset_url": url}, ip=client_ip(request))
    return {"ok": True}


# ================================================================ 脱敏规则

class MaskReq(BaseModel):
    name: str = ""
    datasource_id: int = 0
    column_name: str
    strategy: str = "phone"
    custom_regex: str = ""
    enabled: bool = True


def _mask_ser(r: MaskRule) -> dict:
    return {"id": r.id, "name": r.name, "datasource_id": r.datasource_id,
            "column_name": r.column_name, "strategy": r.strategy,
            "custom_regex": r.custom_regex, "enabled": r.enabled, "owner_id": r.owner_id}


@router.get("/mask/rules")
def list_mask(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [_mask_ser(r) for r in db.query(MaskRule).order_by(MaskRule.id.desc()).all()]


@router.post("/mask/rules")
def create_mask(req: MaskReq, request: Request,
                user: User = Depends(require_role("engineer")), db: Session = Depends(get_db)):
    if req.strategy not in ("phone", "id_card", "email", "custom"):
        raise HTTPException(400, "strategy 仅支持 phone/id_card/email/custom")
    if req.strategy == "custom" and not req.custom_regex:
        raise HTTPException(400, "custom 策略需填写正则")
    if not req.column_name.strip():
        raise HTTPException(400, "列名必填")
    if req.datasource_id and not db.get(DataSource, req.datasource_id):
        raise HTTPException(404, "数据源不存在")
    r = MaskRule(**req.dict(), owner_id=user.id)
    db.add(r)
    db.commit()
    audit_svc.log(db, user, "mask.create", "mask_rule", r.id,
                  {"column": r.column_name, "strategy": r.strategy}, ip=client_ip(request))
    return _mask_ser(r)


@router.put("/mask/rules/{mid}")
def update_mask(mid: int, req: MaskReq, request: Request,
                user: User = Depends(require_role("engineer")), db: Session = Depends(get_db)):
    r = db.get(MaskRule, mid)
    if not r:
        raise HTTPException(404, "规则不存在")
    ensure_can_manage(user, r.owner_id, "该规则")
    for k, v in req.dict().items():
        setattr(r, k, v)
    db.commit()
    audit_svc.log(db, user, "mask.update", "mask_rule", r.id, {"column": r.column_name},
                  ip=client_ip(request))
    return _mask_ser(r)


@router.delete("/mask/rules/{mid}")
def delete_mask(mid: int, request: Request,
                user: User = Depends(require_role("engineer")), db: Session = Depends(get_db)):
    r = db.get(MaskRule, mid)
    if not r:
        raise HTTPException(404, "规则不存在")
    ensure_can_manage(user, r.owner_id, "该规则")
    db.delete(r)
    db.commit()
    audit_svc.log(db, user, "mask.delete", "mask_rule", mid, ip=client_ip(request))
    return {"ok": True}
