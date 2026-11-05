from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user, client_ip
from ..models import User, ChatSession, ChatMessage
from ..services import audit as audit_svc
from ..services.agent import chat_once, execute_confirmation

router = APIRouter(prefix="/api/chat", tags=["chat"])


class SendReq(BaseModel):
    text: str
    page: str = ""


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
    db.delete(s)
    db.commit()
    return {"ok": True}


@router.get("/sessions/{sid}/messages")
def get_messages(sid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = db.get(ChatSession, sid)
    if not s or (s.user_id != user.id and user.role != "admin"):
        raise HTTPException(404, "会话不存在")
    msgs = (db.query(ChatMessage).filter(ChatMessage.session_id == sid)
              .order_by(ChatMessage.id).all())
    return [{"id": m.id, "role": m.role, "content": m.content,
             "card": (m.meta or {}).get("card"),
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
    try:
        result = chat_once(db, user, sid, req.text.strip(), page=req.page or "")
    except Exception as e:
        raise HTTPException(500, f"Agent 执行失败: {e}")
    audit_svc.log(db, user, "chat.send", "session", sid,
                  {"text": req.text[:200], "page": req.page or ""}, ip=client_ip(request))
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
