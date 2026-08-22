"""向量索引重建 —— 切换嵌入模型（或升级 provider）后重建 ChromaDB collection。

用法（仓库根目录）:
    # 1) 先在 backend/.env 配置新的嵌入方案:
    #    EMBEDDING_PROVIDER=fastembed            # 本地 BGE 中文模型（推荐）
    #    EMBEDDING_LOCAL_MODEL=BAAI/bge-small-zh-v1.5
    #    或 EMBEDDING_PROVIDER=api               # 远端 API 嵌入

    # 2) 重建（自动检测维度：与 collection 记录不一致时删库重建）
    python scripts/rebuild_embeddings.py

    # 强制全量重建（即使维度一致）
    python scripts/rebuild_embeddings.py --force

数据来源: SQLite episodes 表（source of truth），逐批向量化后写入
ChromaDB collection "episodes"。ID 与 SQLite 行 ID 一致，保证双写对账。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sqlite3
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.data_config import get_chroma_path, get_db_path  # noqa: E402
from backend.memory.embedding import create_embedding_provider  # noqa: E402
from backend.memory.models import _Meta  # noqa: E402

logger = logging.getLogger(__name__)
BATCH_SIZE = 32

_COLUMNS = [
    "id", "timestamp", "user_input", "assistant_reply",
    "played_song_name", "played_song_artist", "mood_tag", "weather_tag",
    "time_of_day", "genre_tag", "session_id", "importance_score", "song_id",
]


def _load_rows(db_path: Path) -> list[tuple[Any, ...]]:
    with sqlite3.connect(str(db_path)) as conn:
        return conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM episodes ORDER BY id"
        ).fetchall()


def _to_metadata(row: tuple[Any, ...]) -> dict[str, str]:
    values = dict(zip(_COLUMNS, row))
    return {
        _Meta.TIMESTAMP: str(values["timestamp"]),
        _Meta.USER_INPUT: str(values["user_input"])[:_Meta.MAX_TEXT_LEN],
        _Meta.ASSISTANT_REPLY: str(values["assistant_reply"] or "")[:_Meta.MAX_TEXT_LEN],
        _Meta.SONG_NAME: str(values["played_song_name"] or ""),
        _Meta.SONG_ARTIST: str(values["played_song_artist"] or ""),
        _Meta.SONG_ID: str(values["song_id"] or ""),
        _Meta.MOOD_TAG: str(values["mood_tag"] or ""),
        _Meta.WEATHER_TAG: str(values["weather_tag"] or ""),
        _Meta.TIME_OF_DAY: str(values["time_of_day"] or "unknown"),
        _Meta.GENRE_TAG: str(values["genre_tag"] or ""),
        _Meta.SESSION_ID: str(values["session_id"] or "default"),
        "importance": str(values["importance_score"] or 0.5),
    }


async def rebuild(force: bool) -> int:
    import chromadb

    provider = create_embedding_provider()
    if getattr(provider, "dimension", 0) == 0:
        probe = await provider.embed(["维度探测"])
        provider.dimension = len(probe[0])

    db_path = get_db_path()
    rows = _load_rows(db_path)
    print(f"SQLite 记录: {len(rows)} | embedding: {type(provider).__name__} "
          f"(dim={provider.dimension})")

    client = chromadb.PersistentClient(path=str(get_chroma_path()))
    collection = client.get_or_create_collection("episodes")

    meta_dim = (collection.metadata or {}).get("dim")
    if meta_dim != str(provider.dimension):
        if collection.count() > 0:
            print(f"维度不匹配 (collection dim={meta_dim}, 新 dim={provider.dimension}) → 删库重建")
            client.delete_collection("episodes")
        collection = client.get_or_create_collection(
            "episodes",
            metadata={"hnsw:space": "cosine", "dim": str(provider.dimension)},
        )
    elif force and collection.count() > 0:
        print("--force → 删库重建")
        client.delete_collection("episodes")
        collection = client.get_or_create_collection(
            "episodes",
            metadata={"hnsw:space": "cosine", "dim": str(provider.dimension)},
        )
    else:
        print("维度一致 → 增量覆盖写入")

    total = len(rows)
    for start in range(0, total, BATCH_SIZE):
        batch = rows[start:start + BATCH_SIZE]
        texts = [str(row[2]) for row in batch]
        embeddings = await provider.embed(texts)
        collection.upsert(
            ids=[str(row[0]) for row in batch],
            embeddings=embeddings,
            documents=texts,
            metadatas=[_to_metadata(row) for row in batch],
        )
        print(f"  [{start + len(batch)}/{total}] 已写入")

    print(f"完成: collection count = {collection.count()}")
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser(description="重建 ChromaDB 向量索引")
    parser.add_argument("--force", action="store_true", help="强制全量重建")
    args = parser.parse_args()
    return await rebuild(force=args.force)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(asyncio.run(main()))