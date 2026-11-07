"""聊天附件解析:xlsx/pdf/docx 上传时解析一次抽取文本(落库 extract 列),发送时直接注入 prompt。

纯文本与图片不经此模块:文本 send 时读盘,图片走 vision。解析失败抛 ValueError → 路由层转 400。
"""
import io

TEXT_PARSE_CAP = 100_000   # 落库抽取文本上限(prompt 注入时另有统一截断)
XLSX_MAX_ROWS = 100        # 物化进 prompt 的数据行数
XLSX_MAX_COLS = 60
XLSX_CELL_CAP = 100        # 单元格字符截断
PDF_MAX_PAGES = 30

IMAGE_EXTS = {"png", "jpg", "jpeg", "webp", "gif"}
TEXT_EXTS = {"txt", "md", "csv", "tsv", "json", "log", "sql", "py", "sh",
             "yaml", "yml", "xml", "html", "ini", "conf", "toml"}
TABLE_EXTS = {"xlsx"}  # 不收 .xls:openpyxl 不支持 BIFF 老格式,需 xlrd(已停维),明确拒收优于报解析错误
DOC_EXTS = {"pdf", "docx"}


def kind_of(filename: str, mime: str = "") -> str:
    """附件种类:image / table / doc / text(气泡渲染与注入路径都依赖它)。"""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in IMAGE_EXTS or mime.startswith("image/"):
        return "image"
    if ext in TABLE_EXTS:
        return "table"
    if ext in DOC_EXTS:
        return "doc"
    return "text"


def parse_attachment(filename: str, data: bytes) -> dict:
    """表格/文档类 → {"text": 抽取文本, "meta": {rows/pages/...}};失败抛 ValueError。"""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in TABLE_EXTS:
        return _parse_excel(data)
    if ext == "pdf":
        return _parse_pdf(data)
    if ext == "docx":
        return _parse_docx(data)
    raise ValueError(f"不支持的解析类型 .{ext}")


def _parse_excel(data: bytes) -> dict:
    try:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as e:
        raise ValueError(f"Excel 解析失败:{e}")
    try:
        ws = wb.active
        rows = []
        for r in ws.iter_rows(max_row=XLSX_MAX_ROWS + 1, max_col=XLSX_MAX_COLS, values_only=True):
            cells = ["" if c is None else str(c)[:XLSX_CELL_CAP] for c in r]
            while cells and cells[-1] == "":  # 去行尾空单元,表宽由最宽行决定
                cells.pop()
            rows.append(cells)
            if len(rows) > XLSX_MAX_ROWS:
                break
        if not rows or not any(rows):
            return {"text": "(空表格)", "meta": {"rows": 0, "cols": 0}}
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]
        header, body = rows[0], rows[1:]
        lines = ["| " + " | ".join(header) + " |",
                 "|" + "---|" * width]
        lines += ["| " + " | ".join(r) + " |" for r in body]
        total = ws.max_row if isinstance(ws.max_row, int) and ws.max_row else len(rows)
        sheet_note = f"(工作表「{ws.title}」" + (f",共 {len(wb.sheetnames)} 个工作表)" if len(wb.sheetnames) > 1 else ")")
        text = f"{sheet_note}\n" + "\n".join(lines)
        if total - 1 > XLSX_MAX_ROWS:
            text += f"\n…(仅展示前 {XLSX_MAX_ROWS} 行,全表约 {total - 1} 行)"
        return {"text": text[:TEXT_PARSE_CAP], "meta": {"rows": max(total - 1, 0), "cols": width}}
    finally:
        wb.close()


def _parse_pdf(data: bytes) -> dict:
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
    except Exception as e:
        raise ValueError(f"PDF 解析失败:{e}")
    pages = len(reader.pages)
    text = "\n".join((p.extract_text() or "") for p in reader.pages[:PDF_MAX_PAGES])
    text = text.strip()[:TEXT_PARSE_CAP]
    if not text:
        raise ValueError("PDF 无可抽取文本(可能是扫描件,文本模型无法读取)")
    return {"text": text, "meta": {"pages": pages}}


def _parse_docx(data: bytes) -> dict:
    try:
        import docx
        d = docx.Document(io.BytesIO(data))
    except Exception as e:
        raise ValueError(f"Word 解析失败:{e}")
    parts = [p.text for p in d.paragraphs if p.text.strip()]
    for t in d.tables:
        for row in t.rows:
            parts.append(" | ".join(c.text.strip() for c in row.cells))
    text = "\n".join(parts).strip()[:TEXT_PARSE_CAP]
    if not text:
        raise ValueError("Word 文档无文本内容")
    return {"text": text, "meta": {}}
