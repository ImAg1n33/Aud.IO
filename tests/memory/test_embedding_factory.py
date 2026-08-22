"""Embedding provider 工厂测试 —— 环境变量切换（不加载模型，CI 安全）。"""

from backend.memory.embedding import (
    APIEmbedding,
    ChromaLocalEmbedding,
    FastEmbedProvider,
    create_embedding_provider,
)


class TestEmbeddingFactory:
    def test_default_is_local_minilm(self, monkeypatch) -> None:
        monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
        provider = create_embedding_provider()
        assert isinstance(provider, ChromaLocalEmbedding)

    def test_fastembed_switch(self, monkeypatch) -> None:
        monkeypatch.setenv("EMBEDDING_PROVIDER", "fastembed")
        provider = create_embedding_provider()
        assert isinstance(provider, FastEmbedProvider)
        assert provider.dimension == 512  # BAAI/bge-small-zh-v1.5

    def test_fastembed_custom_model(self, monkeypatch) -> None:
        monkeypatch.setenv("EMBEDDING_PROVIDER", "fastembed")
        monkeypatch.setenv("EMBEDDING_LOCAL_MODEL", "BAAI/bge-m3")
        provider = create_embedding_provider()
        assert isinstance(provider, FastEmbedProvider)
        assert provider.dimension == 1024

    def test_api_switch(self, monkeypatch) -> None:
        monkeypatch.setenv("EMBEDDING_PROVIDER", "api")
        provider = create_embedding_provider()
        assert isinstance(provider, APIEmbedding)