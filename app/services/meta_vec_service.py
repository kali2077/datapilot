"""meta_vec 向量缓存：增量刷新 + 余弦检索（RAG 升级③，见 docs/design/09）。

- 刷新钩子：元数据采集后 / 表人工补录后 / 术语·样例 CRUD 后（embedder 为 None 时全部 no-op）
- embed API 失败：静默跳过，业务请求不受影响（红线：部署不被 embedding 模型绑架）
- 孤儿向量无害：检索结果会与当前业务表连接，id 不存在自然被过滤
"""
import datetime
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from ..models import MetaTable, MetaColumn, GlossaryTerm, FewShot, MetaVec
from . import embedder as emb_mod
from .embedder import Embedder, cosine, pack_vec, unpack_vec

MODEL_MAX = 64


def _get_emb() -> Optional[Embedder]:
    return emb_mod.get_embedder()


# ---------------- 被向量化文本 ----------------

def table_text(mt: MetaTable, cols: List[MetaColumn]) -> str:
    col_str = ", ".join(f"{c.name}({c.data_type}){(' ' + c.comment) if c.comment else ''}"
                        for c in cols[:40])
    return " ".join(filter(None, [
        mt.name, mt.table_type or "", mt.comment or "", mt.manual_desc or "", col_str]))


def fewshot_text(fs: FewShot) -> str:
    return fs.question or ""


def glossary_text(g: GlossaryTerm) -> str:
    return f"{g.term} {g.synonyms or ''} {g.definition or ''}".strip()


# ---------------- 增量刷新 ----------------

def _upsert(db: Session, emb: Embedder, ref_type: str, ref_id: int, text: str, vec: List[float]):
    row = (db.query(MetaVec)
             .filter(MetaVec.ref_type == ref_type, MetaVec.ref_id == ref_id,
                     MetaVec.model == emb.name[:MODEL_MAX]).first())
    if row is None:
        row = MetaVec(ref_type=ref_type, ref_id=ref_id, model=emb.name[:MODEL_MAX])
        db.add(row)
    row.text = text[:2000]
    row.vec = pack_vec(vec)
    row.updated_at = datetime.datetime.now()


EMBED_CHUNK = 32  # embedding 接口普遍有批量上限，分块提交避免大集合一次打挂


def _embed_batch(emb: Embedder, texts: List[str]) -> Optional[List[List[float]]]:
    """分块批量 embed；任一块失败返回 None（降级，不留半批数据）。"""
    if not texts:
        return []
    try:
        vecs: List[List[float]] = []
        for i in range(0, len(texts), EMBED_CHUNK):
            vecs.extend(emb.embed(texts[i:i + EMBED_CHUNK]))
        return vecs if len(vecs) == len(texts) else None
    except Exception:
        return None


def refresh_tables(db: Session, table_ids: Optional[List[int]] = None) -> int:
    """刷新表向量（默认全量；传 table_ids 增量，空列表 = 不刷新）。返回刷新条数。"""
    emb = _get_emb()
    if emb is None:
        return 0
    q = db.query(MetaTable)
    if table_ids is not None:
        if not table_ids:
            return 0  # 空数据源/空增量：不触发全量重算
        q = q.filter(MetaTable.id.in_(table_ids))
    tables = q.all()
    if not tables:
        return 0
    cols_map: Dict[int, List[MetaColumn]] = {}
    for c in db.query(MetaColumn).filter(
            MetaColumn.table_id.in_([t.id for t in tables])).order_by(MetaColumn.ordinal):
        cols_map.setdefault(c.table_id, []).append(c)
    texts = [table_text(t, cols_map.get(t.id, [])) for t in tables]
    vecs = _embed_batch(emb, texts)
    if vecs is None:
        return 0
    for t, text, vec in zip(tables, texts, vecs):
        _upsert(db, emb, "table", t.id, text, vec)
    db.commit()
    return len(vecs)


def refresh_fewshots(db: Session) -> int:
    """全量重刷样例向量（样例表小，CRUD 后全刷成本可忽略；删除项由先清后建覆盖）。"""
    emb = _get_emb()
    if emb is None:
        return 0
    rows = db.query(FewShot).all()
    return _refresh_simple(db, emb, "fewshot", [(r.id, fewshot_text(r)) for r in rows])


def refresh_glossary(db: Session) -> int:
    emb = _get_emb()
    if emb is None:
        return 0
    rows = db.query(GlossaryTerm).all()
    return _refresh_simple(db, emb, "glossary", [(r.id, glossary_text(r)) for r in rows])


def _refresh_simple(db: Session, emb: Embedder, ref_type: str,
                    items: List[Tuple[int, str]]) -> int:
    """小集合（fewshot/glossary）全刷：先清当前 model 的旧向量，再整批写入。"""
    vecs = _embed_batch(emb, [t for _, t in items])
    if vecs is None:
        return 0
    (db.query(MetaVec).filter(MetaVec.ref_type == ref_type,
                              MetaVec.model == emb.name[:MODEL_MAX]).delete())
    for (rid, text), vec in zip(items, vecs):
        if text:
            _upsert(db, emb, ref_type, rid, text, vec)
    db.commit()
    return len(items)


def invalidate_ref(db: Session, ref_type: str, ref_id: int):
    """对象删除时清理其全部模型的向量。"""
    db.query(MetaVec).filter(MetaVec.ref_type == ref_type,
                             MetaVec.ref_id == ref_id).delete()
    db.commit()


# ---------------- 检索 ----------------

def embed_query(emb: Optional[Embedder], text: str) -> Optional[List[float]]:
    """问题向量化；未配置或失败返回 None（调用方走词元路径）。"""
    if emb is None or not (text or "").strip():
        return None
    try:
        vecs = emb.embed([text])
        return vecs[0] if vecs else None
    except Exception:
        return None


def search(db: Session, q_vec: Optional[List[float]], ref_type: str,
           top_k: int = 10, model: str = "") -> Dict[int, float]:
    """问题向量 vs meta_vec 余弦，返回 {ref_id: cos}（仅正值，降序取 TopK）。

    只与当前 embedder 同名模型的向量比较（model 为空时自动取当前 embedder 名）：
    不同模型的向量空间不可比（维度都可能不同），绝不跨模型回退。
    换模型后旧向量自动失效，等待刷新钩子按新模型重算；当前模型尚未刷过的
    对象类型返回空 dict，调用方自然落回词元路径。
    """
    if not q_vec:
        return {}
    if not model:
        emb = _get_emb()
        if emb is None:
            return {}
        model = emb.name[:MODEL_MAX]
    rows = (db.query(MetaVec)
              .filter(MetaVec.ref_type == ref_type, MetaVec.model == model).all())
    scored = []
    for r in rows:
        c = cosine(q_vec, unpack_vec(r.vec))
        if c > 0:
            scored.append((r.ref_id, c))
    scored.sort(key=lambda x: -x[1])
    return dict(scored[:top_k])
