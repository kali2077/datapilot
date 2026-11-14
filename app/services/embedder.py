"""可插拔 Embedder（RAG 升级③，见 docs/design/09）。

红线：部署永远不被 embedding 模型绑架——
- 配置了 AI_EMBED_MODEL(+BASE_URL，key 复用 AI_API_KEY) → OpenAICompatEmbedder（HTTP 调用，零安装）
- 未配置 → get_embedder() 返回 None，向量检索关闭，行为与词元匹配版完全一致
- 调用失败由上层捕获降级，绝不让业务请求失败
"""
from typing import List, Optional

from .. import config


class Embedder:
    name = "base"

    def embed(self, texts: List[str]) -> List[List[float]]:
        raise NotImplementedError


class OpenAICompatEmbedder(Embedder):
    """OpenAI 兼容 /embeddings 接口（DeepSeek/Qwen(SiliconFlow)/OpenAI 均可）。"""

    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.name = f"openai-compat:{model}"

    def embed(self, texts: List[str]) -> List[List[float]]:
        import httpx
        resp = httpx.post(
            f"{self.base_url}/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "input": list(texts)},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json().get("data") or []
        # 按 index 归位，保证与输入顺序一致
        return [d["embedding"] for d in sorted(data, key=lambda x: x.get("index", 0))]


def get_embedder() -> Optional[Embedder]:
    """选择逻辑：配置即启用；未配置返回 None（=词元匹配现状）。"""
    base = config.AI_EMBED_BASE_URL or config.AI_BASE_URL
    if base and config.AI_EMBED_MODEL and config.AI_API_KEY:
        return OpenAICompatEmbedder(base, config.AI_API_KEY, config.AI_EMBED_MODEL)
    return None


# ---------------- 向量工具（纯 Python 余弦，百表规模毫秒级，无需专用向量库） ----------------

def cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / (na * nb)


def pack_vec(vec: List[float]) -> str:
    import json
    return json.dumps(vec)


def unpack_vec(raw: str) -> List[float]:
    import json
    try:
        v = json.loads(raw or "[]")
        return v if isinstance(v, list) else []
    except Exception:
        return []
