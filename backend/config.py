"""集中式配置 —— pydantic-settings 单模块（P2-2 配置集中）。

背景: v0.4 自认 25+ 环境变量散落 12 个文件 os.getenv 的阈值已被突破，
且各模块 import-time 读取依赖 main.py 先 load_dotenv 的时序约定。

本模块:
- 所有环境变量唯一读取点（类型校验 + 默认值集中）
- 模块自带 load_dotenv（幂等）——任何入口（uvicorn / scripts / eval）直接
  import 都拿到 .env 配置，不再依赖调用方时序
- 字段名与环境变量名一一对应（pydantic-settings 大小写不敏感匹配）
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# 幂等加载：main.py 的显式 load_dotenv 保持兼容；scripts/eval 直接 import 也安全
load_dotenv(Path(__file__).resolve().parent / ".env")

_DEFAULT_CORS = (
    "http://localhost:5173,http://127.0.0.1:5173,http://localhost:5500,"
    "http://127.0.0.1:5500,http://localhost:3000,http://127.0.0.1:3000,null"
)


class Settings(BaseSettings):
    """Aud.IO 全局配置 —— 与 backend/.env.example 保持一致。"""

    model_config = SettingsConfigDict(extra="ignore", env_file=None)

    # ── LLM ──────────────────────────────────────────────────────────
    llm_provider: str = "deepseek"
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-v4-flash"
    llm_api_key: str = ""
    # deepseek 推理模型（v4-flash）默认禁用 thinking，防推理吃光输出预算
    llm_disable_thinking: bool = True

    # ── Provider fallback ────────────────────────────────────────────
    deepseek_api_key: str = ""
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    # ── Memory（画像/摘要慢模型） ─────────────────────────────────────
    memory_model: str = "deepseek-v4-pro"
    memory_base_url: str = ""
    memory_api_key: str = ""

    # ── Embedding ────────────────────────────────────────────────────
    embedding_provider: str = "local"  # local | fastembed | api
    embedding_local_model: str = "BAAI/bge-small-zh-v1.5"
    embedding_model: str = "text-embedding-3-small"

    # ── 服务 ─────────────────────────────────────────────────────────
    cors_allow_origins: str = _DEFAULT_CORS
    weather_city: str = ""

    # ── 音乐源 ───────────────────────────────────────────────────────
    netease_api_base_url: str = ""
    netease_cookie: str = ""

    # ── MCP ──────────────────────────────────────────────────────────
    mcp_servers: str = "[]"

    # ── TTS ──────────────────────────────────────────────────────────
    tts_enabled: bool = False
    tts_tool_name: str = "tts_synthesize"
    tts_intents: str = "chitchat,weather"

    # ── 运行时数据 ────────────────────────────────────────────────────
    # 空字符串 = 使用 data_config 默认（backend/data/）
    aud_io_data_dir: str = ""


settings = Settings()