"""向量检索 RAG（docs/design/09 ③）：启用/关闭两态、增量刷新钩子、降级红线。

验收标准（文档 09）：语义样例（词面零重叠的中文黑话）启用后可召回；
关闭后行为与②词元版完全一致；embed API 失败不影响任何业务请求。
"""
import pytest
from conftest import H, make_sqlite_ds

from app.db import SessionLocal
from app.models import User, MetaVec
from app.services import agent, embedder as emb_mod, meta_vec_service

GMV_DDL = "CREATE TABLE gmv_fact (id INTEGER, amount REAL, dt TEXT)"
GMV_ROWS = [(1, 100.0, "2026-10-01"), (2, 200.0, "2026-10-02")]


class FakeEmbedder:
    """确定性假 embedder：关键词 → one-hot。营收/GMV 同向，词面零重叠也能语义召回。"""
    name = "fake-embed"
    KEYMAP = [("营收", 0), ("gmv", 0), ("营业", 0), ("订单", 1), ("orders", 1), ("库存", 2), ("stock", 2)]

    def embed(self, texts):
        out = []
        for t in texts:
            low = (t or "").lower()
            v = [0.0, 0.0, 0.0]
            for kw, idx in self.KEYMAP:
                if kw in low:
                    v[idx] = 1.0
            out.append(v)
        return out


class BrokenEmbedder(FakeEmbedder):
    """embed API 打不通的坏 embedder（降级红线用）。"""

    def embed(self, texts):
        raise RuntimeError("connection refused")


def _user(db, username="analyst"):
    return db.query(User).filter(User.username == username).first()


@pytest.fixture
def fake_emb(monkeypatch):
    emb = FakeEmbedder()
    monkeypatch.setattr(emb_mod, "get_embedder", lambda: emb)
    return emb


def _first_table_line(system: str) -> str:
    """system prompt 中「相关表元数据」下的第一条表行。"""
    seg = system.split("相关表元数据:\n")[-1]
    return seg.split("\n")[0]


def test_collect_refreshes_table_vectors(client, tokens, fake_emb):
    """采集钩子：采集后 meta_vec 生成表向量，语义检索可召回（词面零重叠）。"""
    make_sqlite_ds(client, tokens["engineer"], "rag_src", {GMV_DDL: GMV_ROWS})
    db = SessionLocal()
    try:
        rows = db.query(MetaVec).filter(MetaVec.ref_type == "table").all()
        assert rows and all(r.model == "fake-embed" for r in rows)
        emb = fake_emb
        q_vec = meta_vec_service.embed_query(emb, "营收趋势怎么样")
        hits = meta_vec_service.search(db, q_vec, "table", top_k=5)
        assert hits, "向量检索应命中 gmv_fact"
        best_id, best_cos = max(hits.items(), key=lambda x: x[1])
        from app.models import MetaTable
        t = db.query(MetaTable).filter(MetaTable.id == best_id).first()
        assert t is not None and t.name == "gmv_fact"
        assert best_cos > 0.99
    finally:
        db.close()


def test_build_context_semantic_recall(client, tokens, fake_emb):
    """启用向量后：语义鸿沟问题（营收 vs GMV）把 gmv_fact 排到相关表首位。"""
    make_sqlite_ds(client, tokens["engineer"], "rag_src2", {GMV_DDL: GMV_ROWS})
    db = SessionLocal()
    try:
        system, _ctx = agent.build_context(db, _user(db), "营收趋势怎么样", session_id=0)
        assert "gmv_fact" in _first_table_line(system)
    finally:
        db.close()


def test_build_context_disabled_unchanged(client, tokens, monkeypatch):
    """关闭向量：零词面重叠的表不进入注入链（行为与②词元版一致）；fewshot 也不注入。"""
    make_sqlite_ds(client, tokens["engineer"], "rag_src3", {GMV_DDL: GMV_ROWS})
    monkeypatch.setattr(emb_mod, "get_embedder", lambda: None)
    db = SessionLocal()
    try:
        u = _user(db)
        system, _ = agent.build_context(db, u, "营收趋势怎么样", session_id=0)
        assert "参考样例" not in system
        # gmv_fact 与问题词面零重叠，只能经由兜底 top5 进入；断言其不在首位（词元分 0）
        first = _first_table_line(system)
        if "gmv_fact" in first:
            pytest.fail("关闭向量后零重叠表不应出现在首位")
    finally:
        db.close()


def test_fewshot_vector_recall(client, tokens, fake_emb):
    """样例向量召回：问题「营收怎么算」与样例「GMV 口径」词面零重叠，启用后注入。"""
    r = client.post("/api/meta/fewshot", headers=H(tokens["engineer"]),
                    json={"question": "GMV 口径说明", "sql": "SELECT SUM(amount) FROM gmv_fact",
                          "datasource_id": 0, "enabled": True})
    assert r.status_code == 200, r.text
    db = SessionLocal()
    try:
        system, _ = agent.build_context(db, _user(db), "营收怎么算", session_id=0)
        assert "参考样例" in system and "SELECT SUM(amount) FROM gmv_fact" in system
    finally:
        db.close()


def test_fewshot_vector_disabled(client, tokens, fake_emb, monkeypatch):
    """同一样例，关闭向量后零词面重叠不注入（与启用态对比）。"""
    monkeypatch.setattr(emb_mod, "get_embedder", lambda: None)
    db = SessionLocal()
    try:
        system, _ = agent.build_context(db, _user(db), "营收怎么算", session_id=0)
        assert "SELECT SUM(amount) FROM gmv_fact" not in system
    finally:
        db.close()


def test_glossary_semantic_hit(client, tokens, fake_emb):
    """术语向量命中：术语「GMV」与问题「营收趋势」词面零重叠，启用后进命中注入。"""
    r = client.post("/api/meta/glossary", headers=H(tokens["engineer"]),
                    json={"term": "GMV", "synonyms": "", "definition": "成交总额，含退款冲销"})
    assert r.status_code == 200, r.text
    db = SessionLocal()
    try:
        system, _ = agent.build_context(db, _user(db), "营收趋势怎么样", session_id=0)
        assert "命中术语" in system and "GMV=成交总额" in system
    finally:
        db.close()


def test_refresh_tables_empty_ids_noop(client, tokens, fake_emb):
    """边界：空 table_ids = 不刷新（不触发全量重算，也不写任何向量）。"""
    make_sqlite_ds(client, tokens["engineer"], "rag_src5", {GMV_DDL: GMV_ROWS})
    db = SessionLocal()
    try:
        assert meta_vec_service.refresh_tables(db, []) == 0
    finally:
        db.close()


def test_vec_floor_filters_weak_similarity(client, tokens, monkeypatch):
    """相关性下限：真实 embedding 下任意文本余弦>0，弱相关（cos<0.5）不得注入 fewshot。"""
    class WeakEmb:
        name = "weak-embed"

        def embed(self, texts):
            out = []
            for t in texts:
                if "营收" in t:
                    out.append([1.0, 0.0, 0.0])
                elif "库存" in t:
                    out.append([0.4, 0.9, 0.0])  # 与「营收怎么算」的向量 cos≈0.41 < 0.5
                else:
                    out.append([0.0, 0.0, 1.0])
            return out

    monkeypatch.setattr(emb_mod, "get_embedder", lambda: WeakEmb())
    client.post("/api/meta/fewshot", headers=H(tokens["engineer"]),
                json={"question": "库存台账明细", "sql": "SELECT 1", "enabled": True})
    db = SessionLocal()
    try:
        # 词面零重叠 + 弱语义相关 → 不注入；无其他强相关样例 → 无参考样例段
        system, _ = agent.build_context(db, _user(db), "营收怎么算", session_id=0)
        assert "库存台账明细" not in system
        assert "参考样例" not in system
    finally:
        db.close()


def test_search_never_crosses_models(client, tokens, fake_emb, monkeypatch):
    """红线：查询向量只与同名模型的向量比较，绝不跨模型回退（不同向量空间不可比）。"""
    client.post("/api/meta/glossary", headers=H(tokens["engineer"]),
                json={"term": "GMV", "synonyms": "", "definition": "成交总额"})
    db = SessionLocal()
    try:
        assert db.query(MetaVec).filter(MetaVec.ref_type == "glossary").count() > 0
        # 切换到另一个模型（不刷新 glossary）：旧模型向量必须完全不参与检索
        class OtherEmb(FakeEmbedder):
            name = "other-embed"
        monkeypatch.setattr(emb_mod, "get_embedder", lambda: OtherEmb())
        q_vec = meta_vec_service.embed_query(OtherEmb(), "营收趋势")
        assert meta_vec_service.search(db, q_vec, "glossary") == {}
        # 刷新钩子触发后（重算为新模型）恢复检索
        meta_vec_service.refresh_glossary(db)
        hits = meta_vec_service.search(db, q_vec, "glossary")
        assert hits
    finally:
        db.close()


def test_embed_failure_degrades_cleanly(client, tokens, monkeypatch):
    """降级红线：embed API 打不通时刷新为 0、检索为空、build_context 正常工作。"""
    make_sqlite_ds(client, tokens["engineer"], "rag_src4", {GMV_DDL: GMV_ROWS})
    monkeypatch.setattr(emb_mod, "get_embedder", lambda: BrokenEmbedder())
    db = SessionLocal()
    try:
        assert meta_vec_service.refresh_tables(db) == 0
        assert meta_vec_service.refresh_glossary(db) == 0
        q_vec = meta_vec_service.embed_query(BrokenEmbedder(), "营收趋势")
        assert q_vec is None
        assert meta_vec_service.search(db, q_vec, "table") == {}
        system, _ctx = agent.build_context(db, _user(db), "营收趋势怎么样", session_id=0)
        assert "相关表元数据" in system  # 兜底路径正常
    finally:
        db.close()
