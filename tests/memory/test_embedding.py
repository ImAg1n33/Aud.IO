"""Embedding provider 行为测试补全 —— 无模型加载、无网络的纯逻辑路径。"""

import pytest

from backend.memory.embedding import APIEmbedding, ChromaLocalEmbedding, FastEmbedProvider


class TestAPIEmbedding:
    def test_known_model_dimensions(self) -> None:
        provider = APIEmbedding(api_key="test-key", base_url="http://x", model="text-embedding-3-small")
        assert provider.dimension == 1536
        provider_large = APIEmbedding(api_key="test-key", base_url="http://x", model="text-embedding-3-large")
        assert provider_large.dimension == 3072

    def test_unknown_model_defaults_to_1536(self) -> None:
        provider = APIEmbedding(api_key="test-key", base_url="http://x", model="custom-model")
        assert provider.dimension == 1536

    @pytest.mark.asyncio
    async def test_embed_raises_without_key(self, monkeypatch) -> None:
        from backend.config import settings

        monkeypatch.setattr(settings, "llm_api_key", "")
        monkeypatch.setattr(settings, "deepseek_api_key", "")
        provider = APIEmbedding(api_key="", base_url="http://x")
        with pytest.raises(RuntimeError, match="API Key"):
            await provider.embed(["text"])


class TestFastEmbedProvider:
    def test_default_model_and_dimension(self) -> None:
        provider = FastEmbedProvider()
        assert provider.model == "BAAI/bge-small-zh-v1.5"
        assert provider.dimension == 512

    def test_known_model_dimension(self) -> None:
        provider = FastEmbedProvider(model="BAAI/bge-m3")
        assert provider.dimension == 1024


class TestChromaLocalEmbedding:
    def test_dimension_is_384(self) -> None:
        provider = ChromaLocalEmbedding()
        assert provider.dimension == 384