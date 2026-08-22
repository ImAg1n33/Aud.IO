"""集中式配置测试（P2-2）—— 默认值、环境变量覆盖、布尔解析、data_config 联动。"""

from backend.config import Settings, settings
from backend.data_config import (
    ensure_data_dirs,
    get_chroma_path,
    get_db_path,
    get_data_dir,
    get_profiles_dir,
)


class TestSettingsDefaults:
    def test_llm_defaults(self) -> None:
        assert settings.llm_provider == "deepseek"
        assert settings.llm_model == "deepseek-v4-flash"
        assert settings.llm_disable_thinking is True

    def test_embedding_defaults(self, monkeypatch) -> None:
        # .env 可能覆盖这些值——清掉后断言代码默认值
        monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
        monkeypatch.delenv("EMBEDDING_LOCAL_MODEL", raising=False)
        monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
        fresh = Settings()
        assert fresh.embedding_provider == "local"
        assert fresh.embedding_local_model == "BAAI/bge-small-zh-v1.5"
        assert fresh.embedding_model == "text-embedding-3-small"

    def test_feature_flags_default_off(self) -> None:
        assert settings.tts_enabled is False
        assert settings.tts_tool_name == "tts_synthesize"
        assert settings.tts_intents == "chitchat,weather"

    def test_runtime_defaults(self) -> None:
        assert settings.aud_io_data_dir == ""
        assert settings.cors_allow_origins != ""
        assert settings.mcp_servers == "[]"


class TestSettingsEnvOverride:
    def test_env_override(self, monkeypatch) -> None:
        monkeypatch.setenv("LLM_MODEL", "custom-model")
        fresh = Settings()
        assert fresh.llm_model == "custom-model"

    def test_bool_parsing_true(self, monkeypatch) -> None:
        monkeypatch.setenv("TTS_ENABLED", "true")
        fresh = Settings()
        assert fresh.tts_enabled is True

    def test_bool_parsing_false(self, monkeypatch) -> None:
        monkeypatch.setenv("LLM_DISABLE_THINKING", "false")
        fresh = Settings()
        assert fresh.llm_disable_thinking is False

    def test_case_insensitive_env_match(self, monkeypatch) -> None:
        monkeypatch.setenv("EMBEDDING_PROVIDER", "fastembed")
        fresh = Settings()
        assert fresh.embedding_provider == "fastembed"


class TestDataConfigIntegration:
    def test_default_data_dir_is_backend_data(self) -> None:
        assert get_data_dir().name == "data"
        assert get_data_dir().parent.name == "backend"

    def test_data_dir_override(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(settings, "aud_io_data_dir", str(tmp_path))
        assert get_data_dir() == tmp_path.resolve()

    def test_sub_paths_compose(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(settings, "aud_io_data_dir", str(tmp_path))
        assert get_db_path() == tmp_path / "episodes.db"
        assert get_chroma_path() == tmp_path / "chroma_episodes"
        assert get_profiles_dir() == tmp_path / "profiles"

    def test_ensure_data_dirs_creates(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(settings, "aud_io_data_dir", str(tmp_path))
        ensure_data_dirs()
        assert (tmp_path / "episodes.db").parent.exists()
        assert (tmp_path / "profiles").is_dir()
        assert (tmp_path / "chroma_episodes").is_dir()