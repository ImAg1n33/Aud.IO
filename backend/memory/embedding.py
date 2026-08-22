"""向量嵌入提供者抽象层 —— 为情节记忆的语义检索提供文本向量化能力。

设计理念：
- 默认使用 ChromaDB 内置的 ONNX all-MiniLM-L6-v2 模型（完全离线，零网络依赖）
- 可选通过 API 端点使用 OpenAI 兼容的远端 Embedding 服务
- 统一异步接口，方便在 store_snapshot / query_by_semantic 中无缝切换

使用示例:
    # 离线模式（默认，首次运行会自动下载 ~80MB ONNX 模型）
    provider = ChromaLocalEmbedding()

    # API 模式
    provider = APIEmbedding(
        base_url="https://api.deepseek.com",
        api_key="sk-xxx",
        model="text-embedding-3-small",
    )
"""

import asyncio
import logging
import os
from abc import ABC, abstractmethod
from typing import Any, ClassVar

import httpx

logger = logging.getLogger(__name__)

# 向量维度 —— MiniLM-L6-v2 输出 384 维
_LOCAL_EMBEDDING_DIM = 384


class EmbeddingProvider(ABC):
    """向量嵌入的抽象接口。

    所有实现必须保证：对输入列表 texts 的返回顺序与输入一一对应。
    """

    # 子类应覆盖此值，告知下游 ChromaDB collection 的期望维度
    dimension: int

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """对一批文本进行向量化。

        Args:
            texts: 待向量化的文本列表，长度 >= 1

        Returns:
            等长的向量列表，每个向量为 float 列表（维度和 provider 相关）
        """
        ...


# ================================================================
# 内置实现：ChromaDB ONNX 本地模型（推荐默认）
# ================================================================

class ChromaLocalEmbedding(EmbeddingProvider):
    """使用 ChromaDB 内置的 ONNX all-MiniLM-L6-v2 模型进行本地向量化。

    特点:
    - 完全离线，不消耗 API token
    - 首次使用自动下载 ONNX 模型文件（~80MB），缓存在本地
    - 单线程 CPU 推理，适合本地单用户场景（< 10K 条记录）
    - 输出维度：384

    注意:
    - ChromaDB 的 DefaultEmbeddingFunction 是同步的，通过 asyncio.to_thread 包装
    - 如需更高精度可替换为 APIEmbedding（调用远端大模型）
    """

    dimension: int = _LOCAL_EMBEDDING_DIM

    def __init__(self) -> None:
        self._ef = self._create_embedding_function()

    @staticmethod
    def _create_embedding_function():
        """延迟导入 ChromaDB embedding function，避免启动时加载 ONNX 模型。"""
        try:
            from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
            return DefaultEmbeddingFunction()
        except ImportError:
            logger.error(
                "ChromaDB 未安装，无法使用本地 Embedding。"
                "请执行: pip install chromadb"
            )
            raise

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """对文本批量向量化（同步调用通过线程池异步化）。"""
        # DefaultEmbeddingFunction.__call__ 接受单个 str 或 str 列表
        # 返回 numpy ndarray 或 list[list[float]]
        result = await asyncio.to_thread(self._ef, texts)
        # 统一转为 Python list 格式
        if hasattr(result, "tolist"):
            return result.tolist()
        return list(result)


# ================================================================
# OpenAI 兼容 API Embedding（远端，按需启用）
# ================================================================

class APIEmbedding(EmbeddingProvider):
    """通过 OpenAI 兼容的 /embeddings 端点进行远端向量化。

    适用场景:
    - 用户已配置 LLM provider（DeepSeek / OpenAI / 等），且该 provider 支持 embeddings
    - 需要更高精度的向量表示（如 text-embedding-3-large: 3072 维）
    - 不想在本地下载 ONNX 模型

    注意:
    - 每次调用都会消耗 API token 并依赖网络
    - 对大批量文本会自动逐条请求（多数 OpenAI 兼容端点不支持 batch 输入）
    """

    # OpenAI text-embedding-3-small 默认 1536 维
    dimension: int

    # 已知模型的默认维度
    _KNOWN_DIMS: ClassVar[dict[str, int]] = {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
        "text-embedding-ada-002": 1536,
    }

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        """初始化 API Embedding provider。

        Args:
            base_url: LLM API 基地址（默认从环境变量 LLM_BASE_URL 读取）
            api_key: API 密钥（默认从环境变量 LLM_API_KEY 读取）
            model: Embedding 模型名（默认从 EMBEDDING_MODEL 环境变量读取，
                   最终 fallback 为 text-embedding-3-small）
        """
        self._base_url = (base_url or os.getenv("LLM_BASE_URL", "https://api.deepseek.com")).rstrip("/")
        self._api_key = api_key or os.getenv("LLM_API_KEY", "") or os.getenv("DEEPSEEK_API_KEY", "")
        self._model = (
            model
            or os.getenv("EMBEDDING_MODEL", "").strip()
            or "text-embedding-3-small"
        )
        self.dimension = self._KNOWN_DIMS.get(self._model, 1536)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """逐条请求 /embeddings 端点，返回向量列表。"""
        if not self._api_key:
            raise RuntimeError("APIEmbedding: 未配置 API Key（请设置 LLM_API_KEY 环境变量）")

        endpoint = f"{self._base_url}/embeddings"
        all_embeddings: list[list[float]] = []

        async with httpx.AsyncClient(timeout=30.0) as client:
            for text in texts:
                try:
                    resp = await client.post(
                        endpoint,
                        json={
                            "model": self._model,
                            "input": text,
                        },
                        headers={
                            "Content-Type": "application/json",
                            "Authorization": f"Bearer {self._api_key}",
                        },
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    embedding = data["data"][0]["embedding"]
                    # 确保是 float 列表
                    all_embeddings.append([float(v) for v in embedding])
                except (httpx.HTTPError, KeyError, IndexError, TypeError) as exc:
                    logger.error("APIEmbedding 请求失败 (text=%.60s...): %s", text, exc)
                    raise RuntimeError(f"Embedding API 调用失败: {exc}") from exc

        return all_embeddings


# ================================================================
# FastEmbedProvider —— BGE 中文向量模型（fastembed ONNX，推荐升级路径）
# ================================================================


class FastEmbedProvider(EmbeddingProvider):
    """基于 fastembed（ONNX 推理）的本地向量化 —— 支持中文优化模型。

    对比 ChromaLocalEmbedding（MiniLM-L6-v2，英文向）:
    - BGE 系列对中文语义（音乐/心情表达）区分度显著更好
    - 默认 BAAI/bge-small-zh-v1.5（~95MB，512 维，轻量）
    - 可选 BAAI/bge-m3（1024 维，中英多语，~2GB 内存，更准）

    依赖: pip install fastembed（复用 chromadb 已有的 onnxruntime）
    模型: 首次使用自动下载并缓存（HuggingFace）
    """

    # 已知模型的默认维度（未知模型在 __init__ 时实测一次）
    _KNOWN_DIMS: ClassVar[dict[str, int]] = {
        "BAAI/bge-small-zh-v1.5": 512,
        "BAAI/bge-small-en-v1.5": 384,
        "BAAI/bge-base-en-v1.5": 768,
        "BAAI/bge-m3": 1024,
    }

    def __init__(self, model: str | None = None) -> None:
        self.model = model or os.getenv("EMBEDDING_LOCAL_MODEL", "").strip() or "BAAI/bge-small-zh-v1.5"
        self.dimension: int = self._KNOWN_DIMS.get(self.model, 0)
        self._model_obj: Any | None = None

    def _lazy_model(self) -> Any:
        if self._model_obj is None:
            from fastembed import TextEmbedding

            self._model_obj = TextEmbedding(model_name=self.model)
            # 未知维度模型 → 实测一次确定维度
            if self.dimension == 0:
                probe = self._model_obj.embed(["维度探测"])
                self.dimension = len(list(probe)[0])
        return self._model_obj

    async def embed(self, texts: list[str]) -> list[list[float]]:
        model = await asyncio.to_thread(self._lazy_model)

        def _run() -> list[list[float]]:
            vectors = model.embed(texts)
            # fastembed 返回 numpy ndarray（np.float32）——必须 tolist()
            # 转成纯 Python float，否则 ChromaDB 拒绝写入/查询
            return [v.tolist() for v in vectors]

        return await asyncio.to_thread(_run)


# ================================================================
# 工厂函数：根据环境变量自动选择 provider
# ================================================================

def create_embedding_provider() -> EmbeddingProvider:
    """根据环境变量自动创建 Embedding provider。

    决策逻辑:
    1. EMBEDDING_PROVIDER=api → APIEmbedding（远端，EMBEDDING_MODEL 指定模型）
    2. EMBEDDING_PROVIDER=fastembed → FastEmbedProvider（本地 BGE 中文模型，
       EMBEDDING_LOCAL_MODEL 指定，默认 BAAI/bge-small-zh-v1.5）
    3. 其他/默认 → ChromaLocalEmbedding（本地 ONNX MiniLM，向后兼容）

    环境变量参考:
        EMBEDDING_PROVIDER=api|fastembed|local   (默认: local)
        EMBEDDING_MODEL=xxx                      (仅 api 模式)
        EMBEDDING_LOCAL_MODEL=xxx                (仅 fastembed 模式)
    """
    provider_kind = os.getenv("EMBEDDING_PROVIDER", "local").strip().lower()

    if provider_kind == "api":
        logger.info("使用远端 API Embedding provider")
        return APIEmbedding()

    if provider_kind == "fastembed":
        logger.info("使用 FastEmbed 本地 BGE provider (%s)", os.getenv("EMBEDDING_LOCAL_MODEL", "BAAI/bge-small-zh-v1.5"))
        return FastEmbedProvider()

    logger.info("使用本地 ChromaDB ONNX Embedding provider (all-MiniLM-L6-v2)")
    return ChromaLocalEmbedding()
