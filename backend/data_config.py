import os
from pathlib import Path


def get_data_dir() -> Path:
    """Resolve the runtime data directory.

    Controlled by AUD_IO_DATA_DIR env var. Defaults to <project_root>/backend/data/.
    """
    if env := os.getenv("AUD_IO_DATA_DIR", "").strip():
        return Path(env).resolve()
    backend_root = Path(__file__).resolve().parent
    return backend_root / "data"


def get_db_path(data_dir: Path | None = None) -> Path:
    """Resolve the SQLite database path."""
    root = data_dir or get_data_dir()
    return root / "episodes.db"


def get_chroma_path(data_dir: Path | None = None) -> Path:
    """Resolve the ChromaDB persistent storage directory."""
    root = data_dir or get_data_dir()
    return root / "chroma_episodes"


def get_profiles_dir(data_dir: Path | None = None) -> Path:
    """Resolve the user profiles directory."""
    root = data_dir or get_data_dir()
    return root / "profiles"


def ensure_data_dirs(data_dir: Path | None = None) -> Path:
    """Create all runtime data directories if they don't exist.

    Returns the resolved data_dir for use by callers.
    """
    root = data_dir or get_data_dir()
    root.mkdir(parents=True, exist_ok=True)
    (root / "profiles").mkdir(parents=True, exist_ok=True)
    (root / "chroma_episodes").mkdir(parents=True, exist_ok=True)
    return root
