"""Embedding provider 工厂测试 —— 配置切换（单例属性 monkeypatch，不加载模型，CI 安全）。"""

from backend.config import settings
from backend.memory.embedding import (
    APIEmbedding,
    ChromaLocalEmbedding,
    FastEmbedProvider,
    create_embedding_provider,
)


class TestEmbeddingFactory:
    def test_default_is_local_minilm(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "embedding_provider", "local")
        provider = create_embedding_provider()
        assert isinstance(provider, ChromaLocalEmbedding)

    def test_fastembed_switch(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "embedding_provider", "fastembed")
        provider = create_embedding_provider()
        assert isinstance(provider, FastEmbedProvider)
        assert provider.dimension == 512  # BAAI/bge-small-zh-v1.5

    def test_fastembed_custom_model(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "embedding_provider", "fastembed")
        monkeypatch.setattr(settings, "embedding_local_model", "BAAI/bge-m3")
        provider = create_embedding_provider()
        assert isinstance(provider, FastEmbedProvider)
        assert provider.dimension == 1024

    def test_api_switch(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "embedding_provider", "api")
        provider = create_embedding_provider()
        assert isinstance(provider, APIEmbedding)