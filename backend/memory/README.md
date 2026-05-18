# Local memory files

此目录存储 Aud.IO 的本地记忆系统数据，包含用户个人交互历史 —— **严禁提交到 Git**。

## 记忆架构 (v2.0)

| 组件 | 文件 | 说明 |
|------|------|------|
| 情节记忆 (主) | `chroma_episodes/` | ChromaDB 向量存储，支持语义检索，自动 mood 打标 |
| 情节记忆 (兼容) | `episodes.db` | SQLite 双写，向后兼容与 SQL 聚合统计 |
| 短期记忆 | 内存环形缓冲 | `ConversationMemory`，最大 20 轮对话 |
| 用户画像 | `user_profile.json` | Pydantic 模型，LLM 驱动的 JSON Patch 异步更新 |
| 画像审计 | `memory_update.log` | MemoryManager 更新操作日志 |

## 运行时自动更新的文件

- `user_profile.json` — MemoryManager 后台任务异步更新
- `memory_update.log` — 画像更新的审计追踪
- `episodes.db` — 每次对话后 `EpisodicMemory.store_snapshot()` 写入
- `chroma_episodes/` — 与 episodes.db 同步双写

## .gitignore 覆盖

所有含用户个人数据的文件均已配置 `.gitignore`：
- `user_profile.json`（音乐偏好、艺人喜恶）
- `episodes.db`（完整对话历史）
- `memory_update.log`（画像变更日志）
- `chroma_episodes/`（用户交互的向量嵌入）
