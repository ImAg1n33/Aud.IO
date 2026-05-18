"""Dynamic context assembly with pluggable providers, replacing static prompt_builder."""

import json
from abc import ABC, abstractmethod
from typing import Any

from backend.agent.intent_classifier import Intent
from backend.agent.memory_manager import MemoryManager
from backend.memory.conversation_memory import ConversationMemory
from backend.memory.episodic_memory import EpisodicMemory, EpisodicSnapshot
from backend.tools.base import tool_registry


# ============================================================
# Provider interface
# ============================================================

class ContextProvider(ABC):
    name: str = ""

    @abstractmethod
    async def get_context(
        self,
        intent: Intent,
        user_input: str,
        metadata: dict[str, Any],
    ) -> str | None:
        """Return a context block string, or None to skip this provider."""


# ============================================================
# Built-in providers
# ============================================================

class ConversationHistoryProvider(ContextProvider):
    name = "conversation_history"

    def __init__(self, memory: ConversationMemory) -> None:
        self._memory = memory

    async def get_context(self, intent: Intent, user_input: str, metadata: dict[str, Any]) -> str | None:
        formatted = self._memory.format_history(last_n=10)
        if not formatted:
            return None
        return f"[Previous conversation]\n{formatted}"


class UserPreferenceProvider(ContextProvider):
    name = "user_preference"

    def __init__(self, memory_manager: MemoryManager, episodic: EpisodicMemory | None = None) -> None:
        self._manager = memory_manager
        self._episodic = episodic

    async def get_context(self, intent: Intent, user_input: str, metadata: dict[str, Any]) -> str | None:
        # Only inject for music intents
        if intent not in {Intent.MUSIC_PLAY, Intent.MUSIC_RECOMMEND}:
            return None

        summary = self._manager.get_preference_summary()

        # Data-driven stats from episodic memory
        stats_block: str | None = None
        if self._episodic:
            stats = await self._episodic.get_preference_stats()
            stats_block = self._episodic.format_stats_for_prompt(stats)

        if not summary and not stats_block:
            return None

        lines = ["[User Music Profile]"]
        if summary:
            lines.append(summary)

        if stats_block:
            lines.append("")
            lines.append(stats_block)

        return "\n".join(lines)


class CurrentlyPlayingProvider(ContextProvider):
    name = "currently_playing"

    async def get_context(self, intent: Intent, user_input: str, metadata: dict[str, Any]) -> str | None:
        currently_playing = metadata.get("Currently Playing")
        if not currently_playing or currently_playing == "None":
            return None
        return f"[Currently Playing]\n{currently_playing}"


class ToolSchemaProvider(ContextProvider):
    name = "tool_schemas"

    async def get_context(self, intent: Intent, user_input: str, metadata: dict[str, Any]) -> str | None:
        schemas = tool_registry.get_schemas()
        if not schemas:
            return None

        tool_desc = ["[Available tools]"]
        for schema in schemas:
            tool_desc.append(f"- {schema['name']}: {schema['description']}")
        return "\n".join(tool_desc)


class EpisodicMemoryProvider(ContextProvider):
    """情节记忆上下文提供者 —— 注入与当前用户意图语义相似的历史交互。

    v2.0 升级:
    - 主路径使用 query_by_semantic() 做向量语义检索，替代旧版中英文关键词映射
    - "放点轻松的爵士" 自动匹配到 calm/jazz 相关的历史交互，无需显式 mood 标签
    - 保留时间引用检测（"上次那首"、"昨天听的"）作为补充路径
    - 保留 _mood_keys 用于后续偏好分析（不参与检索逻辑）

    设计理由:
    - 旧版 _detect_moods() 依赖手动维护的 中→英 关键词表，覆盖不全且无法处理
      同义词（如 "舒缓" vs "放松" vs "轻快" 都指向 calm）
    - 语义检索天然解决同义词、近义词、跨语言映射问题
    - 向量相似度排序比多次精确标签查询更高效（1 次 embedding + 1 次 query vs N 次 SQL）
    """

    name = "episodic_memory"

    # 中英文心情关键词映射 —— 保留用于 mood_key 兼容性（profile 的 mood_bias 键查询）
    # 不再用于检索，仅用于 get_context 中的辅助判断
    _CN_MOOD_MAP: dict[str, str] = {
        "开心": "happy", "高兴": "happy", "快乐": "happy",
        "难过": "sad", "悲伤": "sad", "低落": "sad", "伤心": "sad", "emo": "sad",
        "专注": "focused", "工作": "focused", "学习": "focused", "coding": "focused",
        "平静": "calm", "安静": "calm", "放松": "calm", "relax": "calm", "chill": "calm",
        "下雨": "rainy", "雨天": "rainy", "雨": "rainy", "rain": "rainy",
        "兴奋": "happy", "激动": "happy", "energetic": "happy",
    }

    def __init__(self, episodic: EpisodicMemory, mood_keys: list[str] | None = None) -> None:
        self._episodic = episodic
        self._mood_keys = [k.lower() for k in (mood_keys or []) if k.strip()]

    # ---- 旧版 mood 检测方法保留（供外部兼容调用，不再用于检索主路径） ----

    def _detect_moods(self, user_input: str) -> list[str]:
        """从用户输入检测心情标签（中英文关键词映射）。

        注意：v2.0 后此方法仅保留用于兼容，检索主路径已升级为语义检索。
        """
        lowered = user_input.lower()
        matched: set[str] = set()

        # 直接英文匹配
        for mk in self._mood_keys:
            if mk in lowered:
                matched.add(mk)

        # 中文 → 英文映射
        for cn_word, en_mood in self._CN_MOOD_MAP.items():
            if cn_word in user_input and en_mood in self._mood_keys:
                matched.add(en_mood)

        return list(matched)

    # ---- 新版语义检索主路径 ----

    async def get_context(
        self, intent: Intent, user_input: str, metadata: dict[str, Any]
    ) -> str | None:
        """注入与用户当前请求语义相似的历史交互上下文。

        检索策略（两级）:
        1. 主路径: 语义向量检索 —— 对 user_input 做 embedding，在 ChromaDB 中
           按余弦相似度召回最相关的过往交互（限 5 条）
        2. 补充路径: 时间引用检测 —— 如果用户提到 "上次"、"昨天" 等信号词，
           额外注入最近 3 条交互（确保时间引用不被语义相似度淹没）
        """
        if intent not in {Intent.MUSIC_PLAY, Intent.MUSIC_RECOMMEND}:
            return None

        snapshots_by_id: dict[int, EpisodicSnapshot] = {}

        # ---- 1) 主路径：语义向量检索 ----
        try:
            semantic_results = await self._episodic.query_by_semantic(
                query_text=user_input,
                limit=5,
            )
            for snap in semantic_results:
                snapshots_by_id[snap.id] = snap
        except Exception:
            # 语义检索失败时静默降级 —— 不影响主流程
            pass

        # ---- 2) 补充路径：时间引用检测 ----
        temporal_signals = [
            "上次", "昨天", "之前", "上次那个", "上次那首",
            "last time", "yesterday", "before", "last song", "previous",
            "again", "再", "又",
        ]
        if any(sig in user_input for sig in temporal_signals):
            recent = await self._episodic.query_recent(limit=3)
            for snap in recent:
                snapshots_by_id[snap.id] = snap

        if not snapshots_by_id:
            return None

        # 按时间戳降序排列，保持上下文的时间连贯性
        sorted_snaps = sorted(
            snapshots_by_id.values(), key=lambda s: s.timestamp, reverse=True
        )

        lines = ["[Past interactions — you may reference these naturally]"]
        for snap in sorted_snaps[:5]:
            song_info = ""
            if snap.played_song_name:
                song_info = (
                    f" [played: {snap.played_song_artist or ''} - {snap.played_song_name}]"
                )
            mood_info = f" (mood: {snap.mood_tag})" if snap.mood_tag else ""
            # 如果有语义相似度分数，展示在末尾供 LLM 参考
            sim_info = ""
            if snap.similarity_score is not None:
                sim_info = f" [similarity: {snap.similarity_score:.2f}]"
            lines.append(
                f"- User: {snap.user_input} | Aud.IO: {snap.assistant_reply[:120]}"
                f"{song_info}{mood_info}{sim_info}"
            )
        return "\n".join(lines)


# ============================================================
# Assembler
# ============================================================

class ContextAssembler:
    def __init__(
        self,
        providers: list[ContextProvider],
        system_persona: str,
        tool_constraints: str,
    ) -> None:
        self.providers = providers
        self.system_persona = system_persona
        self.tool_constraints = tool_constraints

    async def assemble(
        self,
        user_input: str,
        intent: Intent,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Build the full prompt by calling each provider and concatenating non-None results."""
        meta = dict(metadata or {})
        context_blocks: list[str] = []

        for provider in self.providers:
            try:
                block = await provider.get_context(intent, user_input, meta)
                if block:
                    context_blocks.append(block)
            except Exception:
                # A provider should not crash the entire assembly
                continue

        context_text = "\n\n".join(context_blocks) if context_blocks else "- none"

        raw_context = meta.get("raw_context", {})
        raw_lines = [f"- {key}: {_context_to_text(value)}" for key, value in raw_context.items()]
        raw_block = "\n".join(raw_lines) if raw_lines else ""

        sections = [
            self.system_persona,
            self.tool_constraints,
        ]

        if raw_block:
            sections.append(f"Additional Context:\n{raw_block}")

        sections.append(f"Context:\n{context_text}")
        sections.append(f"User:\n{user_input}")

        return "\n\n".join(sections)


def _context_to_text(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)
