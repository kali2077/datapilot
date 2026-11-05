"""失败告警：邮件（SMTP）与 Webhook 双通道，含告警记录。"""
import json
import smtplib
from email.mime.text import MIMEText
from email.header import Header

from sqlalchemy.orm import Session

from ..models import AlertChannel, AlertRecord, TaskInstance


def fire_failure_alerts(db: Session, inst: TaskInstance):
    channels = db.query(AlertChannel).filter(AlertChannel.enabled == True).all()  # noqa: E712
    title = f"[DataPilot] 任务失败: {inst.workflow_name} #{inst.id}"
    content = (f"工作流: {inst.workflow_name}\n实例: #{inst.id}\n"
               f"触发: {inst.trigger_type}\n时间: {inst.finished_at}\n错误:\n{(inst.error or '')[:2000]}")
    for ch in channels:
        status = "ok"
        try:
            if ch.type == "email":
                _send_email(ch.config or {}, title, content)
            elif ch.type == "webhook":
                _send_webhook(ch.config or {}, title, content, inst)
            else:
                status = "failed"
        except Exception:
            status = "failed"
        db.add(AlertRecord(channel_id=ch.id, channel_name=ch.name, instance_id=inst.id,
                           title=title, content=content, status=status))
    db.commit()


def _send_email(cfg: dict, title: str, content: str):
    host = cfg.get("smtp_host")
    if not host:
        raise ValueError("email 通道缺少 smtp_host")
    port = int(cfg.get("smtp_port", 465))
    username = cfg.get("username", "")
    password = cfg.get("password", "")
    to = [x.strip() for x in (cfg.get("to") or "").split(",") if x.strip()]
    msg = MIMEText(content, "plain", "utf-8")
    msg["Subject"] = Header(title, "utf-8")
    msg["From"] = username
    msg["To"] = ",".join(to)
    if port == 465:
        server = smtplib.SMTP_SSL(host, port, timeout=15)
    else:
        server = smtplib.SMTP(host, port, timeout=15)
        server.starttls()
    try:
        if username:
            server.login(username, password)
        server.sendmail(username, to, msg.as_string())
    finally:
        server.quit()


def _send_webhook(cfg: dict, title: str, content: str, inst=None):
    import httpx
    url = cfg.get("url")
    if not url:
        raise ValueError("webhook 通道缺少 url")
    payload = {"title": title, "content": content,
               "instance_id": inst.id if inst else 0,
               "workflow": inst.workflow_name if inst else "",
               "status": inst.status if inst else "monitor"}
    headers = {"Content-Type": "application/json"}
    if cfg.get("secret"):
        import hashlib, hmac as _hmac, time
        ts = str(int(time.time()))
        sign = _hmac.new(cfg["secret"].encode(), f"{ts}\n{json.dumps(payload)}".encode(),
                         hashlib.sha256).hexdigest()
        headers["X-Sign"] = sign
        headers["X-Timestamp"] = ts
    resp = httpx.post(url, json=payload, headers=headers, timeout=15)
    resp.raise_for_status()
