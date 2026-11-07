import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session

from .. import config
from ..db import get_db
from ..deps import current_user, current_user_query, client_ip
from ..models import User, ChatSession, ChatMessage, ChatAttachment
from ..services import audit as audit_svc
from ..services.agent import chat_once, execute_confirmation
from ..services.attach_parse import kind_of, parse_attachment, TEXT_EXTS

router = APIRouter(prefix="/api/chat", tags=["chat"])


def _att_out(a: ChatAttachment, meta: Optional[dict] = None) -> dict:
    return {"id": a.id, "name": a.name, "type": kind_of(a.name, a.mime or ""),
            "size": a.size, "url": f"/api/chat/attachments/{a.id}", "meta": meta or {}}


def _load_attachments(db: Session, sid: int, user: User, ids: list) -> list:
    ids = list(dict.fromkeys(ids))  # 去重,防重复注入
    if len(ids) > 6:
        raise HTTPException(400, "单条消息最多 6 个附件")
    rows = db.query(ChatAttachment).filter(ChatAttachment.id.in_(ids)).all()
    by_id = {r.id: r for r in rows}
    atts = []
    for aid in ids:
        a = by_id.get(aid)
        if not a or a.session_id != sid or a.user_id != user.id:
            raise HTTPException(400, "附件无效或不属于当前会话")
        atts.append(a)
    return atts


class SendReq(BaseModel):
    text: str
    page: str = ""
    attachments: list[int] = []


class ConfirmReq(BaseModel):
    message_id: int
    token: str


@router.get("/sessions")
def list_sessions(user: User = Depends(current_user), db: Session = Depends(get_db)):
    sessions = (db.query(ChatSession).filter(ChatSession.user_id == user.id)
                  .order_by(ChatSession.id.desc()).limit(50).all())
    return [{"id": s.id, "title": s.title, "created_at": str(s.created_at)} for s in sessions]


@router.post("/sessions")
def create_session(user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = ChatSession(user_id=user.id)
    db.add(s)
    db.commit()
    return {"id": s.id, "title": s.title}


@router.delete("/sessions/{sid}")
def delete_session(sid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = db.get(ChatSession, sid)
    if not s or s.user_id != user.id:
        raise HTTPException(404, "会话不存在")
    db.query(ChatMessage).filter(ChatMessage.session_id == sid).delete()
    for a in db.query(ChatAttachment).filter(ChatAttachment.session_id == sid).all():
        if a.path and os.path.exists(a.path):
            try:
                os.unlink(a.path)
            except OSError:
                pass
        db.delete(a)
    db.delete(s)
    db.commit()
    return {"ok": True}


@router.post("/sessions/{sid}/attachments")
async def upload_attachment(sid: int, file: UploadFile = File(...),
                            user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = db.get(ChatSession, sid)
    if not s or s.user_id != user.id:
        raise HTTPException(404, "会话不存在")
    filename = file.filename or "file"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    mime = file.content_type or ""
    kind = kind_of(filename, mime)
    if kind == "text" and ext not in TEXT_EXTS and not mime.startswith("text/"):
        raise HTTPException(400, "不支持的附件类型（图片 png/jpg/webp/gif；表格 xlsx/xls；文档 pdf/docx；"
                                 "文本 txt/md/csv/tsv/sql/py/sh/yaml/json/xml…）")
    data = await file.read()
    # 大小分级:纯文本 2MB 足够;图片/表格/文档 10MB
    limit_mb = config.ATTACH_MAX_MB if kind != "text" else 2
    if len(data) > limit_mb * 1024 * 1024:
        raise HTTPException(400, f"附件不能超过 {limit_mb}MB")
    # 表格/文档上传时解析一次抽取文本;失败直接拒绝(内容对 AI 无用)
    extract, parse_meta = None, {}
    if kind in ("table", "doc"):
        try:
            parsed = parse_attachment(filename, data)
        except ValueError as e:
            raise HTTPException(400, str(e))
        extract, parse_meta = parsed["text"], parsed["meta"]
    att = ChatAttachment(session_id=sid, user_id=user.id, name=filename[:200],
                         mime=mime or ("image/png" if kind == "image" else "text/plain"),
                         size=len(data), path="", extract=extract)
    db.add(att)
    db.commit()
    fpath = os.path.join(config.ATTACH_DIR, f"{att.id}_{uuid.uuid4().hex[:8]}.{ext or 'bin'}")
    with open(fpath, "wb") as f:
        f.write(data)
    att.path = fpath
    db.commit()
    audit_svc.log(db, user, "chat.attachment", "session", sid,
                  {"name": att.name, "size": att.size, "kind": kind})
    return _att_out(att, parse_meta)


@router.get("/attachments/{aid}")
def get_attachment(aid: int, user: User = Depends(current_user_query), db: Session = Depends(get_db)):
    a = db.get(ChatAttachment, aid)
    if not a:
        raise HTTPException(404, "附件不存在")
    s = db.get(ChatSession, a.session_id)
    if not s or (s.user_id != user.id and user.role != "admin"):
        raise HTTPException(404, "附件不存在")
    if not a.path or not os.path.exists(a.path):
        raise HTTPException(404, "附件文件已丢失")
    return FileResponse(a.path, media_type=a.mime or "application/octet-stream", filename=a.name)


@router.get("/sessions/{sid}/messages")
def get_messages(sid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = db.get(ChatSession, sid)
    if not s or (s.user_id != user.id and user.role != "admin"):
        raise HTTPException(404, "会话不存在")
    msgs = (db.query(ChatMessage).filter(ChatMessage.session_id == sid)
              .order_by(ChatMessage.id).all())
    return [{"id": m.id, "role": m.role, "content": m.content,
             "card": (m.meta or {}).get("card"),
             "attachments": (m.meta or {}).get("attachments") or [],
             "confirm_token": m.confirm_token if m.confirm_status == "pending" else None,
             "confirm_status": m.confirm_status, "provider": m.llm_provider,
             "ts": str(m.ts)} for m in msgs]


@router.post("/sessions/{sid}/send")
def send(sid: int, req: SendReq, request: Request, user: User = Depends(current_user),
         db: Session = Depends(get_db)):
    s = db.get(ChatSession, sid)
    if not s or s.user_id != user.id:
        raise HTTPException(404, "会话不存在")
    if not req.text.strip():
        raise HTTPException(400, "消息不能为空")
    if s.title == "新对话":
        s.title = req.text.strip()[:40]
        db.commit()
    atts = _load_attachments(db, sid, user, req.attachments) if req.attachments else []
    try:
        result = chat_once(db, user, sid, req.text.strip(), page=req.page or "", attachments=atts)
    except Exception as e:
        raise HTTPException(500, f"Agent 执行失败: {e}")
    audit_svc.log(db, user, "chat.send", "session", sid,
                  {"text": req.text[:200], "page": req.page or "",
                   "attachments": [a.name for a in atts]}, ip=client_ip(request))
    return result


@router.post("/sessions/{sid}/confirm")
def confirm(sid: int, req: ConfirmReq, request: Request, user: User = Depends(current_user),
            db: Session = Depends(get_db)):
    s = db.get(ChatSession, sid)
    if not s or s.user_id != user.id:
        raise HTTPException(404, "会话不存在")
    msg = db.get(ChatMessage, req.message_id)
    if not msg or msg.session_id != sid:
        raise HTTPException(404, "消息不存在")
    if msg.confirm_status != "pending" or msg.confirm_token != req.token:
        raise HTTPException(400, "确认链接无效或已处理")
    # 找到该消息对应卡片的 action/payload
    card = (msg.meta or {}).get("card") or {}
    action, payload = card.get("action"), card.get("payload", {})
    if not action:
        raise HTTPException(400, "消息缺少确认信息")
    try:
        result = execute_confirmation(db, user, action, payload)
    except Exception as e:
        raise HTTPException(400, f"执行失败: {e}")
    msg.confirm_status = "confirmed" if not result.get("error") else "expired"
    db.commit()
    reply = ChatMessage(session_id=sid, role="assistant",
                        content=result.get("summary", "操作完成。"),
                        meta={"card": result.get("card")},
                        llm_provider="confirm")
    db.add(reply)
    db.commit()
    audit_svc.log(db, user, f"chat.confirm:{action}", "session", sid, payload,
                  source="ui", ip=client_ip(request))
    return {"ok": True, "summary": result.get("summary"), "card": result.get("card"),
            "message_id": reply.id}


@router.post("/sessions/{sid}/cancel")
def cancel(sid: int, req: ConfirmReq, user: User = Depends(current_user),
           db: Session = Depends(get_db)):
    s = db.get(ChatSession, sid)
    if not s or s.user_id != user.id:
        raise HTTPException(404, "会话不存在")
    msg = db.get(ChatMessage, req.message_id)
    if not msg or msg.confirm_token != req.token:
        raise HTTPException(404, "消息不存在")
    msg.confirm_status = "expired"
    db.commit()
    return {"ok": True}
