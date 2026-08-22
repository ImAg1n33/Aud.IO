# Aud.IO Eval 评估基线

> 目标：为意图分类与记忆检索建立可量化的基准，让每次 Prompt / 嵌入 / 检索策略改动都有回归指标。

## 文件结构

| 文件 | 说明 |
|------|------|
| `golden_intents.json` | 意图 Golden Set（60 条，5 意图，含歧义/英文/噪声用例） |
| `golden_retrieval.json` | 记忆检索评估集（12 条合成快照 + 11 个查询 + 期望命中标注） |
| `run_intent_eval.py` | 意图评估 runner（hybrid / keyword / llm 三模式） |
| `run_retrieval_eval.py` | 检索评估 runner（临时库重建 → recall@k / MRR） |
| `reports/` | 生成的 Markdown 报告（gitignore） |

## 意图评估

```bash
# 生产路径（Hard Signal 短路 → LLM → 关键词兜底）——需要 LLM_API_KEY
python -m eval.run_intent_eval --mode hybrid

# 纯关键词层（确定性、无 LLM、CI 安全）
python -m eval.run_intent_eval --mode keyword

# 纯 LLM 层（度量模型本身，60 次调用较慢）
python -m eval.run_intent_eval --mode llm

# CI 门禁：准确率低于阈值退出码非 0
python -m eval.run_intent_eval --mode keyword --fail-below 0.9
```

报告输出到 `eval/reports/intent-report.md`：总分、混淆矩阵、按类别准确率、错分清单。

**Ground truth 口径**：期望标签 = 产品期望行为（不是 keyword 分类器当前行为）。
标注了 `expected_divergence: ["keyword"]` 的条目（如"谢谢你的推荐"→CHITCHAT）是 keyword
降级层的已知弱点——keyword 模式准确率会因此偏低，这正常且正是评估的价值：
**hybrid 是生产真相，keyword 是降级底线**。

## 检索评估

```bash
# 本地 ONNX 嵌入（与生产一致；首次运行下载 ~80MB 模型）
python -m eval.run_retrieval_eval

# 对比 API 嵌入
python -m eval.run_retrieval_eval --embedding api

# 换 BGE-m3 等新模型时：改 EmbeddingProvider 后重跑即可量化对比
```

报告输出到 `eval/reports/retrieval-report.md`：逐查询命中表、recall@k、MRR。

**注意事项**：
- runner 使用 `tempfile` 临时库，不污染 `backend/data/`；
- 合成数据是 baseline，等真实使用积累后可用 `backend/data/episodes.db` 导出真实查询扩集；
- MiniLM-L6-v2 是英文向模型，中文音乐语境 recall 预期偏低——这正是嵌入升级（BGE-m3 / API）的量化依据。

## 基线记录（2026-08-14 首次建立，2026-08-22 混合检索+嵌入升级后更新）

| 评估项 | 基线 | 备注 |
|--------|------|------|
| 意图分类（keyword 层） | **85.0%** (51/60) | 9 个错分全部为已标注的 `expected_divergence`；core 路径（hard_signal/title_vs_mood/英文）100% |
| 意图分类（hybrid 生产路径） | 待跑 | `python -m eval.run_intent_eval --mode hybrid`（需 LLM_API_KEY，60 次调用） |
| 记忆检索 recall@5（MiniLM 纯语义） | 81.8% (9/11) | MRR 0.345；seed 6（周杰伦晴天）霸榜——MiniLM 中文区分度低的实锤 |
| **记忆检索 recall@5（BGE-small-zh + 混合）** | **81.8% (9/11)** | **MRR 0.644（↑2x）**；霸榜消失，相关结果排名显著提前；两处未命中为合成数据时段语义（"工作/午后"），真实数据下关键词腿可兜底子串查询 |
| 重建向量索引 | `python scripts/rebuild_embeddings.py` | 切换 `EMBEDDING_PROVIDER` 后必须执行（维度自动检测 + 删库重建） |

**结论**：嵌入升级的最大收益不在 recall@5（小样本集已饱和），而在 **MRR 翻倍**——"上次那种感觉的"类查询命中的结果排到了前面，这正是 DJ 记忆体验的关键。

## CI

`.github/workflows/ci.yml` 已含 keyword 模式评估步骤（无 LLM 依赖，CI 安全），
报告以 artifact 形式留存。

## 扩展新用例

1. 意图：向 `golden_intents.json` 的 `cases` 追加 `{"input": "...", "intent": "...", "category": "..."}`；
   确有歧义且 keyword 层会判错的，加 `"expected_divergence": ["keyword"]` 并写 note。
2. 检索：向 `golden_retrieval.json` 的 `seeds` 追加快照、向 `queries` 追加查询（`expect` 指向 seed 下标，从 1 计）。
3. 完整性：`tests/eval/test_golden_sets.py` 会在 pytest 时校验新增用例合法性。