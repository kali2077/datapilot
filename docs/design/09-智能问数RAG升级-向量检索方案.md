# 09 · 智能问数 RAG 升级路线 —— 向量检索（①，已规划待实施）

> 状态：**方案定稿，暂缓实施**。触发条件：表数量上百、或黑话提问召回率实测下降时启动。
> 前置：②同义词族展开已交付（2026-11-04，见 ACCEPTANCE 二·十二）——零依赖解决"术语↔黑话"一类召回，
> 本方案是其语义升级版。

## 一、要解决的问题（语义鸿沟）

当前检索链 = 词元重叠打分（②已加术语族展开）。仍在以下场景失灵：

| 用户问 | 元数据里写的 | 词面匹配 |
|---|---|---|
| "营收趋势" | GMV、amount | ❌ 零重叠 |
| "客户画像分析用什么表" | customers、orders | ⚠️ 间接命中 |
| "复购率怎么算" | （无此概念注释） | ❌ |

**向量检索**把文本映射为语义坐标，字面零重叠也能按语义距离召回。它决定问数准确率上限：
表找错 → SQL 必错。

## 二、核心设计原则（红线）

> **部署永远不被 embedding 模型绑架：配了 API key 即启用语义检索；不配，自动降级回词元匹配，行为与现状完全一致。**

向量检索做成**可插拔 Embedder**，平台其余代码不感知实现差异。

## 三、Embedder 接口与三种实现

```python
# app/services/embedder.py
class Embedder(Protocol):
    name: str
    def embed(self, texts: list[str]) -> list[list[float]]: ...

def get_embedder() -> Embedder | None:
    """选择逻辑（启动时确定，失败降级）：
    1. 配置了 AI_EMBED_BASE_URL + AI_EMBED_MODEL(+AI_API_KEY 复用) → OpenAICompatEmbedder
    2. 本地装有 sentence_transformers → LocalEmbedder（可选依赖，默认不装）
    3. 都没有 → None（向量检索关闭，行为=现状②）
    """
```

| 实现 | 触发配置 | 部署成本 | 离线 | 说明 |
|---|---|---|---|---|
| **OpenAICompatEmbedder（默认推荐）** | `AI_EMBED_BASE_URL`、`AI_EMBED_MODEL`（key 复用 AI_API_KEY） | **零**（HTTP 调用，几 ms/条） | ❌ 需网络 | DeepSeek/Qwen(SiliconFlow)/OpenAI 均兼容 |
| LocalEmbedder（可选） | 检测到本地依赖 | 中：onnxruntime + 量化 BGE-small-zh 约 100MB（带 torch 则上 GB） | ✅ | 内网/离线环境选项 |
| NoneEmbedder（默认） | 不配任何 | 零 | ✅ | =现状②，词元匹配 |

## 四、存储与刷新

```sql
CREATE TABLE meta_vec (
    id        INTEGER PRIMARY KEY,
    ref_type  VARCHAR(16),   -- table / column / fewshot / glossary
    ref_id    INTEGER,
    text      TEXT,          -- 被向量化的文本（表: 名称+注释+描述+字段清单；样例: 问题；术语: 术语+同义词+定义）
    vec       TEXT,          -- JSON 数组（sqlite/mysql 通吃；百表规模无需专用向量库）
    model     VARCHAR(64),   -- 产生向量时的 embedder 名（换模型需全量重算）
    updated_at DATETIME,
    UNIQUE(ref_type, ref_id, model)
);
```

**增量刷新**：复用现有钩子——元数据采集后 / few-shot·术语 CRUD 后 / 任务成功刷新元数据后，
对变更对象重算向量（批量 embed，一次几条，成本可忽略）。

## 五、检索链融合

`build_context` 中，embedder 启用时：

```
问题向量 → 与 meta_vec 全量余弦相似度（百表×百列=万级向量，纯 Python 毫秒级）
→ 表 / few-shot / 术语 各取 Top-K
→ 与词元匹配分数融合（加权：词元分 × 0.4 + 余弦分 × 0.6，或 RRF）
→ 后续流程不变（列清单注入、术语命中注入、few-shot 注入）
```

- 未启用 → 走现状②路径（代码分支隔离，测试覆盖两态）
- 融合同时保留词元匹配：精确表名提问（如"查 orders"）不被语义波动稀释

## 六、配置与降级矩阵（部署视角）

| 部署形态 | 配置 | 语义检索 | 额外安装 |
|---|---|---|---|
| 最小部署（现状） | 不配 | ❌（=②） | 无 |
| 推荐 | `AI_EMBED_BASE_URL/MODEL` | ✅ | 无（复用已配供应商） |
| 离线内网 | 本地依赖 + `AI_EMBED_PROVIDER=local` | ✅ | onnxruntime+量化模型 ~100MB |

## 七、实施步骤与工作量（总计约 1 天）

1. `embedder.py`：接口 + OpenAICompatEmbedder + NoneEmbedder + 选择逻辑（2h）
2. `meta_vec` 表 + 迁移 + 增量刷新钩子接入三处（2h）
3. `build_context` 融合检索 + 降级分支（2h）
4. 测试：两态行为对比（启用/关闭）、换模型重算、API 失败降级（2h）
5. （可选）LocalEmbedder ONNX 实现 +0.5 天

**验收标准**：语义样例集（"营业额"→orders、"流水"→orders、"客户贡献"→customers 等离线可断言的
中文黑话集）启用后命中率 ≥ 90%，关闭后行为与②完全一致。

## 八、明确不做

- 不引入专用向量数据库（Milvus/Qdrant/FAISS 服务）——百表规模 JSON 余弦足够，避免新组件
- 不做 embedding 微调/训练
- 不做跨租户共享向量
