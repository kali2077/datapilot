"""聊天附件测试：上传/类型校验/大小限制/鉴权/send 落库/回显/删除级联。"""
import io

from conftest import H

PNG_BYTES = (b"\x89PNG\r\n\x1a\n" + b"0" * 64)  # PNG magic + 占位内容


def _mk_session(client, tok):
    r = client.post("/api/chat/sessions", headers=H(tok))
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_upload_txt_and_send_roundtrip(client, tokens):
    tok = tokens["admin"]
    sid = _mk_session(client, tok)
    files = {"file": ("note.txt", io.BytesIO("订单口径：金额为含税价".encode()), "text/plain")}
    r = client.post(f"/api/chat/sessions/{sid}/attachments", headers=H(tok), files=files)
    assert r.status_code == 200, r.text
    att = r.json()
    assert att["type"] == "text" and att["name"] == "note.txt" and att["size"] > 0

    # send 带附件（规则引擎模式：正常返回 + 降级说明）
    r2 = client.post(f"/api/chat/sessions/{sid}/send", headers=H(tok),
                     json={"text": "根据附件总结口径", "page": "", "attachments": [att["id"]]})
    assert r2.status_code == 200, r2.text
    assert "note.txt" in r2.json()["content"]

    # messages 回显附件
    r3 = client.get(f"/api/chat/sessions/{sid}/messages", headers=H(tok))
    user_msgs = [m for m in r3.json() if m["role"] == "user"]
    assert user_msgs and user_msgs[-1]["attachments"][0]["id"] == att["id"]

    # 附件回显接口：token 走 query 参数
    r4 = client.get(f"/api/chat/attachments/{att['id']}?token={tokens['admin']}")
    assert r4.status_code == 200
    assert "含税价".encode() in r4.content


def test_upload_image_and_type_reject(client, tokens):
    tok = tokens["engineer"]
    sid = _mk_session(client, tok)
    r = client.post(f"/api/chat/sessions/{sid}/attachments", headers=H(tok),
                    files={"file": ("shot.png", io.BytesIO(PNG_BYTES), "image/png")})
    assert r.status_code == 200, r.text
    assert r.json()["type"] == "image"

    # 不支持的类型
    r2 = client.post(f"/api/chat/sessions/{sid}/attachments", headers=H(tok),
                     files={"file": ("evil.exe", io.BytesIO(b"MZ"), "application/octet-stream")})
    assert r2.status_code == 400


def test_attachment_oversize_rejected(client, tokens):
    tok = tokens["analyst"]
    sid = _mk_session(client, tok)
    big = io.BytesIO(b"x" * (10 * 1024 * 1024 + 1))
    r = client.post(f"/api/chat/sessions/{sid}/attachments", headers=H(tok),
                    files={"file": ("big.txt", big, "text/plain")})
    assert r.status_code == 400
    assert "超过" in r.json()["detail"]  # 纯文本类限额 2MB,断言不绑定具体数值


def test_attachment_session_isolation(client, tokens):
    """他人会话上传/读取都被拒；跨会话引用附件被拒。"""
    sid_admin = _mk_session(client, tokens["admin"])
    files = {"file": ("a.txt", io.BytesIO(b"data"), "text/plain")}
    # engineer 不能往 admin 的会话传附件
    r = client.post(f"/api/chat/sessions/{sid_admin}/attachments", headers=H(tokens["engineer"]), files=files)
    assert r.status_code == 404
    # admin 上传后，viewer 无权读取
    r2 = client.post(f"/api/chat/sessions/{sid_admin}/attachments", headers=H(tokens["admin"]), files=files)
    aid = r2.json()["id"]
    r3 = client.get(f"/api/chat/attachments/{aid}?token={tokens['viewer']}")
    assert r3.status_code == 404
    # engineer 自己的会话引用 admin 会话的附件 → 400
    sid_eng = _mk_session(client, tokens["engineer"])
    r4 = client.post(f"/api/chat/sessions/{sid_eng}/send", headers=H(tokens["engineer"]),
                     json={"text": "hi", "attachments": [aid]})
    assert r4.status_code == 400


def test_upload_sql_and_xlsx_with_meta(client, tokens):
    import openpyxl
    tok = tokens["admin"]
    sid = _mk_session(client, tok)
    # sql → 纯文本类
    r = client.post(f"/api/chat/sessions/{sid}/attachments", headers=H(tok),
                    files={"file": ("etl.sql", io.BytesIO(b"INSERT INTO ods_t SELECT * FROM src"), "text/plain")})
    assert r.status_code == 200, r.text
    assert r.json()["type"] == "text"
    # xlsx → table + 行列元数据
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["城市", "金额"])
    ws.append(["北京", 100.0])
    ws.append(["上海", 200.0])
    buf = io.BytesIO()
    wb.save(buf)
    r2 = client.post(f"/api/chat/sessions/{sid}/attachments", headers=H(tok),
                     files={"file": ("数据.xlsx", buf.getvalue(),
                                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert r2.status_code == 200, r2.text
    att = r2.json()
    assert att["type"] == "table"
    assert att["meta"]["rows"] == 2 and att["meta"]["cols"] == 2
    # 带表格发送走通
    r3 = client.post(f"/api/chat/sessions/{sid}/send", headers=H(tok),
                     json={"text": "总结这份数据", "attachments": [att["id"]]})
    assert r3.status_code == 200, r3.text
    assert "数据.xlsx" in r3.json()["content"]


def test_upload_docx_and_invalid_pdf(client, tokens):
    import docx
    tok = tokens["engineer"]
    sid = _mk_session(client, tok)
    d = docx.Document()
    d.add_paragraph("口径说明：金额为含税价。")
    buf = io.BytesIO()
    d.save(buf)
    r = client.post(f"/api/chat/sessions/{sid}/attachments", headers=H(tok),
                    files={"file": ("口径.docx", buf.getvalue(),
                                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
    assert r.status_code == 200, r.text
    assert r.json()["type"] == "doc"
    # 损坏 pdf → 解析失败 400
    r2 = client.post(f"/api/chat/sessions/{sid}/attachments", headers=H(tok),
                     files={"file": ("broken.pdf", io.BytesIO(b"this is not a pdf"), "application/pdf")})
    assert r2.status_code == 400


def test_delete_session_cascades_attachments(client, tokens):
    tok = tokens["analyst"]
    sid = _mk_session(client, tok)
    files = {"file": ("gone.txt", io.BytesIO(b"bye"), "text/plain")}
    aid = client.post(f"/api/chat/sessions/{sid}/attachments", headers=H(tok), files=files).json()["id"]
    assert client.delete(f"/api/chat/sessions/{sid}", headers=H(tok)).status_code == 200
    r = client.get(f"/api/chat/attachments/{aid}?token={tok}")
    assert r.status_code == 404
