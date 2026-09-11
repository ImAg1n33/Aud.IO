"""Agent orchestration: Perceive -> Decide -> Execute -> Record pipeline.

v0.4 (RFC-007): System-level prompts sent as role="system" via llm_client.
ContextAssembler now only assembles the user-prompt portion.
All prompt text lives in backend.agent.prompts.
"""

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any, ClassVar

from backend.agent.context_assembler import (
    ContextAssembler,
    ConversationHistoryProvider,
    CurrentlyPlayingProvider,
    EnvironmentProvider,
    EpisodicMemoryProvider,
    SessionSummaryProvider,
    ToolSchemaProvider,
    UserPreferenceProvider,
)
from backend.agent.intent_classifier import Intent, IntentClassifier
from backend.agent.llm_client import call_llm, stream_llm
from backend.agent.memory_manager import MemoryManager
from backend.agent.prompts import (
    GRACEFUL_FALLBACK_TEXT,
    NON_STREAMING_SYSTEM,
    PHASE1_DECISION_SYSTEM,
    PHASE2_STREAM_SYSTEM,
    SINGLE_PASS_STREAM_SYSTEM,
    TOOL_CONSTRAINTS,
    build_phase1_user_prompt,
    build_retry_feedback,
)
from backend.agent.tool_executor import ToolExecutor
from backend.agent.tts_provider import TTSProvider
from backend.memory.embedding import EmbeddingProvider
from backend.memory.episodic_memory import EpisodicMemory
from backend.memory.play_history import PlayHistoryRepository
from backend.memory.reflection import SessionReflector
from backend.services.session_manager import SessionManager
from backend.tools.netease_api import (
    find_similar_songs,
    get_song_mp3_url,
    search_first_song,
)

logger = logging.getLogger(__name__)


class AssistantService:
    MAX_RETRIES = 2
    SESSION_TTL = 86400
    MAX_SESSIONS = 100
    REFLECT_EVERY_TURNS = 10  # Reflection: 每 N 轮对话压一次会话摘要

    def __init__(
        self,
        episodic_db_path: Path | None = None,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        self.session_manager = SessionManager(ttl=self.SESSION_TTL, maxsize=self.MAX_SESSIONS)
        self.episodic_memory = EpisodicMemory(
            db_path=episodic_db_path,  # None triggers data_config defaults
            embedding_provider=embedding_provider,
        )
        # 播放历史（RFC-020 契约 B）—— 与 episodic 共用同一 SQLite 文件
        self.play_history = PlayHistoryRepository(self.episodic_memory.db_path)
        self.play_history.ensure_table()
        self.intent_classifier = IntentClassifier()
        self.tool_executor = ToolExecutor(max_retries=self.MAX_RETRIES)
        self.episodic_provider = EpisodicMemoryProvider(self.episodic_memory)
        self.summary_provider = SessionSummaryProvider(self.episodic_memory)
        self.reflector = SessionReflector(self.episodic_memory)
        self.tts = TTSProvider()

    # ================================================================
    # Session helpers
    # ================================================================

    # 换歌指令（NEXT 快捷指令等）—— 跳过 Phase 1，直接走带工具的单遍路径
    SKIP_SIGNALS: ClassVar[list[str]] = [
        "换一首", "换首", "换一个", "换换", "换点",
        "下一首", "切歌", "再放一首", "再放", "再来一首",
        "next", "skip", "change",
    ]

    # 上一首指令（PREV 快捷指令）—— 从播放历史回放，不经 LLM 选曲
    PREV_SIGNALS: ClassVar[list[str]] = [
        "上一首", "前一首", "上一曲", "previous", "go back",
    ]

    @classmethod
    def _is_skip_request(cls, user_input: str) -> bool:
        """判断是否为换歌类指令（跳过 Phase 1 预取，直接让 LLM 带工具换一曲）。"""
        lowered = user_input.strip().lower()
        return any(sig in lowered for sig in cls.SKIP_SIGNALS)

    @classmethod
    def _is_previous_request(cls, user_input: str) -> bool:
        """判断是否为"上一首"回放指令（v0.7 P1-2）。"""
        lowered = user_input.strip().lower()
        return any(sig in lowered for sig in cls.PREV_SIGNALS)

    def _resolve_session(self, session_id: str | None) -> str:
        return session_id.strip() if session_id else str(uuid.uuid4())

    def _build_tool_schemas(self, intent: Intent) -> list[dict[str, Any]]:
        """按意图门控返回 OpenAI function schemas（RFC: function calling 重构）。

        MUSIC_PLAY / MUSIC_RECOMMEND → 音乐类工具
        WEATHER / CHITCHAT           → 不暴露工具（避免误导调用）
        UNKNOWN                      → 全部可用工具
        通用类工具（如 MCP 挂载）在音乐意图下同样可见。
        """
        categories = set(self.intent_classifier.should_activate_tool_categories(intent))
        if not categories:
            return []
        from backend.tools.base import tool_registry

        tools = [
            t for t in tool_registry.get_available()
            if t.category in categories or t.category == "general"
        ]
        return [t.to_openai_function_schema() for t in tools]

    def _ensure_memory_manager(self, session_id: str) -> None:
        ctx = self.session_manager.get_or_create(session_id)
        if ctx.memory_manager is None:
            ctx.memory_manager = MemoryManager(session_id=session_id)

    def _build_context_assembler(self, session_id: str) -> ContextAssembler:
        """Full assembler for Single-Pass (CHITCHAT, WEATHER, UNKNOWN)."""
        self._ensure_memory_manager(session_id)
        ctx = self.session_manager.get_or_create(session_id)

        return ContextAssembler(
            providers=[
                EnvironmentProvider(),
                ConversationHistoryProvider(ctx.short_term_memory),
                SessionSummaryProvider(self.episodic_memory),
                UserPreferenceProvider(ctx.memory_manager, self.episodic_memory),
                CurrentlyPlayingProvider(),
                ToolSchemaProvider(),
                self.episodic_provider,
            ],
        )

    def _build_phase2_assembler(self, session_id: str) -> ContextAssembler:
        """Ultra-light assembler for Two-Pass Phase 2.

        Song is already resolved — only environment + short conversation
        history needed for continuity.
        """
        self._ensure_memory_manager(session_id)
        ctx = self.session_manager.get_or_create(session_id)

        return ContextAssembler(
            providers=[
                EnvironmentProvider(),
                ConversationHistoryProvider(ctx.short_term_memory),
            ],
        )

    async def _build_phase2_prompt(
        self, session_id: str, user_input: str, intent: Intent,
        metadata: dict[str, Any], song: dict[str, Any],
    ) -> str:
        """构建 Phase 2 用户提示词（歌曲已解析，DJ 台词在前奏上生成）。

        Two-Pass 成功路径、单遍空文案兜底、上一首回放共用。
        """
        assembler = self._build_phase2_assembler(session_id)
        return await assembler.assemble(
            user_input, intent, metadata, resolved_song=song,
            session_id=session_id,
        )

    async def _stream_resolved_song(
        self, sid: str, ctx: Any, user_input: str, intent: Intent,
        metadata: dict[str, Any], song_data: dict[str, Any],
        started: float, path: str = "two_pass",
    ) -> AsyncGenerator[str, None]:
        """歌曲已解析：music 先发 → Phase 2 台词 → TTS → RECORD → done。

        Two-Pass 成功路径与"上一首"回放共用（v0.7 抽取）。
        """
        yield self._sse(
            "status",
            json.dumps({
                "phase": "found",
                "name": song_data["name"],
                "artist": song_data["artist"],
            }, ensure_ascii=False),
        )

        # Music first — like a DJ dropping the track before speaking
        yield self._sse("music", json.dumps(song_data, ensure_ascii=False))
        self._record_play_history(sid, song_data, metadata)

        phase2_prompt = await self._build_phase2_prompt(
            sid, user_input, intent, metadata, song_data,
        )

        reply: dict[str, Any] = {}
        async for chunk in stream_llm(PHASE2_STREAM_SYSTEM, phase2_prompt):
            if isinstance(chunk, str):
                yield self._sse("token", chunk)
            elif isinstance(chunk, dict):
                reply = chunk
                if reply.get("analysis") == "Model call failed.":
                    self._trace(
                        sid, user_input, intent, path,
                        started, error=reply.get("answer", "Request failed"),
                    )
                    yield self._sse("error", reply.get("answer", "Request failed"))
                    return

        yield self._sse("text", reply.get("answer", ""))

        # TTS pre-roll tag — music is already playing, speech is non-blocking
        speech_sse = await self._maybe_yield_speech(
            reply.get("answer", ""), intent.value, is_music_play=True,
        )
        if speech_sse:
            yield speech_sse

        final_reply = {
            **reply,
            "music": song_data,
            "provider": reply.get("provider", ""),
            "model": reply.get("model", ""),
        }
        ctx.short_term_memory.add_turn(
            user_input, reply.get("answer", ""),
            intent=str(intent), played_song=song_data,
        )
        if not self._is_no_record(metadata):
            asyncio.ensure_future(
                self.episodic_memory.store_snapshot(
                    user_input, reply.get("answer", ""),
                    played_song=song_data, session_id=sid,
                )
            )
            self._maybe_reflect(sid, ctx)
        self._trace(sid, user_input, intent, path, started, final_reply)
        yield self._sse("done", json.dumps(final_reply, ensure_ascii=False))

    async def _find_previous_song(
        self, sid: str, metadata: dict[str, Any],
    ) -> dict[str, Any] | None:
        """查该会话播放历史，返回最近一条 ≠ 当前曲 的歌曲（含 mp3_url）。

        v0.8：主数据源为 play_history（RFC-020 契约 B —— 去重、上限、
        元数据齐全）；过渡期回退 episodes（升级前的历史只存在于 episodes，
        尚未产生 play_history 记录时仍可回退）。
        """
        current_id = str((metadata or {}).get("Currently Playing ID") or "").strip()

        # ── 主数据源：play_history（新旧→旧） ──
        try:
            history = await self.play_history.list_async(sid, limit=10)
        except Exception:
            history = []
        if history:
            if current_id:
                prev = next(
                    (h for h in history if str(h.get("song_id")) != current_id),
                    None,
                )
            else:
                prev = history[1] if len(history) >= 2 else None
            if prev is not None:
                return await self._resolve_history_song(prev, "previous")

        # ── 过渡回退：episodes（v0.7 数据源） ──
        try:
            snapshots = await self.episodic_memory.query_recent_played(sid, limit=10)
        except Exception:
            return None
        if not snapshots:
            return None

        if current_id:
            snap = next(
                (
                    h for h in snapshots
                    if h.song_id and str(h.song_id) != current_id
                ),
                None,
            )
        else:
            # 前端未上报当前曲 ID 时，取倒数第二条（避免重播当前曲）
            snap = snapshots[1] if len(snapshots) >= 2 else None

        if snap is None or not snap.song_id or not snap.played_song_name:
            return None

        return await self._resolve_history_song(
            {
                "song_id": str(snap.song_id),
                "name": snap.played_song_name,
                "artist": snap.played_song_artist or "",
            },
            "previous",
        )

    async def _resolve_history_song(
        self, row: dict[str, Any], keyword: str,
    ) -> dict[str, Any] | None:
        """历史记录 → 可播放歌曲载荷（重新解析 mp3_url，URL 会过期）。"""
        song_id = str(row.get("song_id") or "").strip()
        if not song_id:
            return None
        try:
            mp3_url = await get_song_mp3_url(song_id)
        except Exception:
            return None
        return {
            "song_id": song_id,
            "name": row.get("name", ""),
            "artist": row.get("artist", ""),
            "cover_url": row.get("cover_url", ""),
            "album": row.get("album", ""),
            "duration_ms": row.get("duration_ms"),
            "mp3_url": mp3_url,
            "requested_keyword": keyword,
        }

    # ================================================================
    # 自动续播（RFC-020 契约 D）—— DJ 模式电台式连续播放
    # ================================================================

    AUTO_CONTINUE_EXCLUDE = 10  # 排除本会话最近 N 首（避免电台循环重复）

    @staticmethod
    def _is_auto_continue(metadata: dict[str, Any] | None) -> bool:
        value = (metadata or {}).get("Auto Continue")
        return str(value).strip().lower() == "true"

    @staticmethod
    def _is_no_record(metadata: dict[str, Any] | None) -> bool:
        """测试/调试请求标志（RFC-020 数据卫生）——不写历史/记忆/画像。

        自动化冒烟脚本必须携带 context: {"No Record": "true"}，
        否则会污染真实用户的播放历史与画像更新。
        """
        value = (metadata or {}).get("No Record")
        return str(value).strip().lower() == "true"

    async def _auto_continue_stream(
        self, sid: str, ctx: Any, metadata: dict[str, Any], started: float,
    ) -> AsyncGenerator[str, None]:
        """DJ 模式自动续播：无用户输入 → 按品味选下一首 → 复用 Two-Pass 台词流。

        选曲策略（快 → 慢）：
        1. 上一首的相似歌曲（/simi/song 真实音乐关系，无 LLM 延迟）
        2. LLM 带工具按画像挑歌（附"排除最近播放"提示）
        最近 AUTO_CONTINUE_EXCLUDE 首全部排除，避免电台循环重复。
        """
        intent = Intent.MUSIC_PLAY
        user_input = "（自动续播）"
        try:
            recent = await self.play_history.list_async(
                sid, limit=self.AUTO_CONTINUE_EXCLUDE + 2,
            )
        except Exception:
            recent = []
        excluded = {str(h.get("song_id")) for h in recent[: self.AUTO_CONTINUE_EXCLUDE]}

        song_data = await self._auto_pick_song(sid, ctx, recent, excluded)
        if song_data is None:
            logger.info("Auto continue: no candidate (excluded=%d)", len(excluded))
            self._trace(sid, user_input, intent, "auto_dj", started, error="no candidate")
            yield self._sse("error", "电台没找到合适的下一首——说句话我继续。")
            return

        logger.info(
            "Auto continue: %s - %s", song_data["artist"], song_data["name"],
        )
        async for sse in self._stream_resolved_song(
            sid, ctx, user_input, intent, metadata, song_data,
            started, path="auto_dj",
        ):
            yield sse

    async def _auto_pick_song(
        self, sid: str, ctx: Any, recent: list[dict[str, Any]],
        excluded: set[str],
    ) -> dict[str, Any] | None:
        """自动续播选曲：相似优先（快），LLM 兜底（品味）。"""
        disliked = self._disliked_artists(ctx)

        # 策略 1：上一首的相似歌曲（真实音乐关系，无 LLM 延迟）
        if recent:
            last_id = str(recent[0].get("song_id") or "")
            try:
                candidates = await find_similar_songs(last_id, limit=10)
            except Exception:
                candidates = []
            for cand in candidates:
                if str(cand.get("id")) in excluded:
                    continue
                if self._matched_disliked({"artist": cand.get("artist")}, disliked):
                    continue
                resolved = await self._resolve_candidate(cand)
                if resolved is not None:
                    return resolved

        # 策略 2：LLM 按画像挑歌（带排除提示）
        return await self._auto_pick_via_llm(sid, recent, disliked, excluded)

    async def _resolve_candidate(self, cand: dict[str, Any]) -> dict[str, Any] | None:
        """相似候选 → 可播放载荷（解析 mp3_url，失败跳过）。"""
        try:
            mp3_url = await get_song_mp3_url(str(cand["id"]))
        except Exception:
            return None
        return {
            "song_id": str(cand["id"]),
            "name": cand.get("name", ""),
            "artist": cand.get("artist", ""),
            "cover_url": cand.get("cover_url", ""),
            "album": cand.get("album", ""),
            "duration_ms": cand.get("duration_ms"),
            "mp3_url": mp3_url,
            "requested_keyword": "auto",
        }

    async def _auto_pick_via_llm(
        self, sid: str, recent: list[dict[str, Any]],
        disliked: list[str], excluded: set[str],
    ) -> dict[str, Any] | None:
        """LLM 带工具按画像挑歌 —— 排除最近播放与 disliked 后返回。"""
        assembler = self._build_context_assembler(sid)
        hint = "换一首。自动续播：根据我的音乐口味挑一首新的歌。"
        if recent:
            names = "；".join(
                f"{h.get('artist', '')} - {h.get('name', '')}"
                for h in recent[: self.AUTO_CONTINUE_EXCLUDE]
            )
            hint += f" 不要重复最近播放过的：{names}。"
        try:
            prompt = await assembler.assemble(
                hint, Intent.MUSIC_PLAY, {},
                tool_constraints=TOOL_CONSTRAINTS, session_id=sid,
            )
            tools = self._build_tool_schemas(Intent.MUSIC_PLAY)
            reply = await call_llm(
                NON_STREAMING_SYSTEM, prompt, tools=tools, force_tools=True,
            )
            actions = self._parse_actions_from_reply(reply)
            if not actions:
                return None
            results = await self.tool_executor.execute_actions(actions)
            merged = await self._merge_tool_results(reply, results)
        except Exception:
            logger.warning("Auto continue LLM pick failed", exc_info=True)
            return None

        music = merged.get("music")
        if not isinstance(music, dict) or not music.get("song_id"):
            return None
        if str(music.get("song_id")) in excluded:
            return None
        if self._matched_disliked(music, disliked):
            return None
        if not music.get("mp3_url"):
            return None
        music["requested_keyword"] = "auto"
        return music

    # ================================================================
    # 口味硬约束（v0.7 P0-2）—— disliked 播放前校验
    # ================================================================

    @staticmethod
    def _disliked_artists(ctx: Any) -> list[str]:
        """该会话画像的 disliked 艺人列表（缺失时为空——不阻断播放）。"""
        try:
            return ctx.memory_manager.get_artist_constraints().get("disliked", [])
        except Exception:
            return []

    @staticmethod
    def _matched_disliked(music: Any, disliked: list[str]) -> str | None:
        """music.artist 命中 disliked（子串，兼容多人拼接）→ 返回命中项。"""
        if not isinstance(music, dict) or not disliked:
            return None
        artist = str(music.get("artist") or "")
        if not artist:
            return None
        for name in disliked:
            if name and name in artist:
                return name
        return None

    async def _retry_for_disliked(
        self, sid: str, user_input: str, intent: Intent,
        metadata: dict[str, Any], assembler: ContextAssembler,
        tools: list[dict[str, Any]], blocked_artist: str,
    ) -> dict[str, Any] | None:
        """disliked 命中后的带反馈重试：换一位不同艺人的歌。"""
        retry_input = (
            f"{user_input}\n\n[System: 用户明确不喜欢 {blocked_artist}，"
            "必须换一位不同艺人的歌，不要推荐该艺人。]"
        )
        retry_prompt = await assembler.assemble(
            retry_input, intent, metadata, tool_constraints=TOOL_CONSTRAINTS,
            session_id=sid,
        )
        retry_reply = await call_llm(
            NON_STREAMING_SYSTEM, retry_prompt, tools=tools, force_tools=True,
        )
        retry_actions = self._parse_actions_from_reply(retry_reply)
        if not retry_actions:
            return None
        results = await self.tool_executor.execute_actions(retry_actions)
        merged = await self._merge_tool_results(retry_reply, results)
        return merged if merged.get("music") else None

    # ================================================================
    # Non-streaming pipeline (legacy)
    # ================================================================

    async def generate_reply(
        self, user_input: str, context: dict[str, Any] | None,
        session_id: str | None = None,
    ) -> tuple[dict[str, Any], str]:
        """Perceive -> Decide -> Execute -> Record pipeline."""
        sid = self._resolve_session(session_id)
        ctx = self.session_manager.get_or_create(sid)
        self._ensure_memory_manager(sid)

        # === PERCEIVE ===
        intent = await self.intent_classifier.classify_async(user_input)
        metadata: dict[str, Any] = dict(context or {})
        started = time.monotonic()

        profile = ctx.memory_manager.get_profile()
        mood_bias = profile.get("mood_bias", {}) if isinstance(profile, dict) else {}
        self.episodic_provider._mood_keys = [k.lower() for k in mood_bias.keys() if k.strip()]

        assembler = self._build_context_assembler(sid)
        user_prompt = await assembler.assemble(
            user_input, intent, metadata, tool_constraints=TOOL_CONSTRAINTS,
            session_id=sid,
        )

        # === DECIDE + EXECUTE (with retry loop) ===
        working_input = user_input
        retry_count = 0
        tools = self._build_tool_schemas(intent)
        force_tools = intent in {Intent.MUSIC_PLAY, Intent.MUSIC_RECOMMEND}

        while True:
            reply = await call_llm(
                NON_STREAMING_SYSTEM, user_prompt,
                tools=tools, force_tools=force_tools,
            )

            actions = self._parse_actions_from_reply(reply)

            # tool_choice=required 偶发不被遵守——音乐类意图无工具调用时强制重试一次
            if force_tools and not actions and tools:
                strict_input = (
                    f"{working_input}\n\n[System: 你必须调用 search_music 或 get_music_url "
                    "工具实际搜索。不要假设版权问题——先搜索，搜到就能放。]"
                )
                strict_prompt = await assembler.assemble(
                    strict_input, intent, metadata,
                    tool_constraints=TOOL_CONSTRAINTS, session_id=sid,
                )
                strict_reply = await call_llm(
                    NON_STREAMING_SYSTEM, strict_prompt,
                    tools=tools, force_tools=True,
                )
                strict_actions = self._parse_actions_from_reply(strict_reply)
                if strict_actions:
                    reply = strict_reply
                    actions = strict_actions

            results = await self.tool_executor.execute_actions(actions)
            final_reply = await self._merge_tool_results(reply, results)

            retry_contexts = self._collect_retry_contexts(results)
            if not retry_contexts:
                break

            if retry_count >= self.MAX_RETRIES:
                final_reply = self._build_graceful_fallback(final_reply)
                break

            retry_count += 1
            feedback = build_retry_feedback(final_reply, retry_contexts)
            working_input = f"{user_input}\n\n[System: {feedback}]"
            user_prompt = await assembler.assemble(
                working_input, intent, metadata, tool_constraints=TOOL_CONSTRAINTS,
                session_id=sid,
            )

        # === RECORD ===
        if final_reply.get("analysis") != "Model call failed.":
            final_reply = self._ensure_dj_line(final_reply)
            played_song = final_reply.get("music")
            ctx.short_term_memory.add_turn(
                user_input,
                final_reply.get("answer", ""),
                intent=str(intent),
                played_song=played_song,
            )

            if not self._is_no_record(metadata):
                asyncio.ensure_future(
                    self.episodic_memory.store_snapshot(
                        user_input,
                        final_reply.get("answer", ""),
                        played_song=played_song,
                        session_id=sid,
                    )
                )

        if not self._is_no_record(metadata):
            self._maybe_reflect(sid, ctx)
        self._trace(sid, user_input, intent, "generate_reply", started, final_reply)
        return final_reply, user_prompt

    # ================================================================
    # Streaming pipeline
    # ================================================================

    async def generate_reply_stream(
        self, user_input: str, context: dict[str, Any] | None,
        session_id: str | None = None,
    ) -> AsyncGenerator[str, None]:
        """Streaming variant — Two-Pass for music, Single-Pass for everything else.

        RFC-003: Two-Pass pipeline for MUSIC_PLAY.
        RFC-007: System prompts sent as role="system".
        """
        sid = self._resolve_session(session_id)
        ctx = self.session_manager.get_or_create(sid)
        self._ensure_memory_manager(sid)

        metadata: dict[str, Any] = dict(context or {})
        started = time.monotonic()

        # ═══════════════════════════════════════════════════════════
        # 自动续播（RFC-020 契约 D）：DJ 模式歌曲播完，按品味选下一首 ——
        # 无用户输入，走独立的快选路径（相似歌曲优先，LLM 兜底）
        # ═══════════════════════════════════════════════════════════
        if self._is_auto_continue(metadata):
            async for sse in self._auto_continue_stream(sid, ctx, metadata, started):
                yield sse
            return

        # === PERCEIVE ===
        intent = await self.intent_classifier.classify_async(user_input)
        logger.debug("Intent: %s, input: %.60s...", intent.value, user_input)
        phase1_missed = False

        # ═══════════════════════════════════════════════════════════
        # 上一首（v0.7 P1-2）：从播放历史回放该会话的上一条，不经 LLM 选曲
        # ═══════════════════════════════════════════════════════════
        if self._is_previous_request(user_input):
            prev = await self._find_previous_song(sid, metadata)
            if prev is not None:
                logger.info(
                    "Replay previous: %s - %s", prev["artist"], prev["name"],
                )
                async for sse in self._stream_resolved_song(
                    sid, ctx, user_input, intent, metadata, prev,
                    started, path="prev_history",
                ):
                    yield sse
                return
            logger.info("Previous-song request but no history: '%s'", user_input)

        # ═══════════════════════════════════════════════════════════
        # RFC-003 Two-Pass path — 常规 MUSIC_PLAY 播放请求
        # 换歌指令（换一首/下一首/切歌…）不走 Phase 1 —— 直接进带工具的单遍路径，
        # 避免"Phase 1 再搜同一首/搜失败后无工具道歉"的死胡同
        # ═══════════════════════════════════════════════════════════
        if intent == Intent.MUSIC_PLAY and not self._is_skip_request(user_input):
            # ── Phase 1: Silent pre-fetch ──
            yield self._sse("status", '{"phase":"searching"}')
            song_data = await self._phase1_prefetch(user_input, sid, metadata)

            # 口味硬约束（v0.7 P0-2）：Phase 1 直搜命中 disliked 艺人 → 视为未命中
            if song_data is not None and self._matched_disliked(
                song_data, self._disliked_artists(ctx),
            ):
                logger.info(
                    "Phase 1 picked disliked artist: %s → fallback",
                    song_data.get("artist"),
                )
                song_data = None

            if song_data is not None:
                logger.info(
                    "Phase 1 OK: '%s' → %s - %s",
                    user_input, song_data["artist"], song_data["name"],
                )
                async for sse in self._stream_resolved_song(
                    sid, ctx, user_input, intent, metadata, song_data,
                    started, path="two_pass",
                ):
                    yield sse
                return

            # Phase 1 failed — 不直接道歉：降级到带工具的单遍容错链
            # （严格工具重试 → Phase 1 确定性兜底 → Phase 2 台词），
            # 全部失败时才展示收集到的文案。消除"无工具道歉死胡同"。
            logger.info(
                "Phase 1 miss: '%s', falling through to single-pass with tools",
                user_input,
            )
            yield self._sse("status", '{"phase":"not_found"}')
            phase1_missed = True
            # 不 return —— 落入下方 Single-Pass 路径（MUSIC_PLAY 时 yield_tokens=False 静默）

        # ═══════════════════════════════════════════════════════════
        # Single-Pass path — CHITCHAT, WEATHER, UNKNOWN, MUSIC_RECOMMEND,
        # 换歌指令（MUSIC_PLAY + skip signal），以及 Phase 1 失败的 MUSIC_PLAY
        # ═══════════════════════════════════════════════════════════
        if intent == Intent.MUSIC_PLAY:
            logger.info("MUSIC_PLAY via single-pass (skip or phase-1 miss): '%s'", user_input)

        assembler = self._build_context_assembler(sid)
        user_prompt = await assembler.assemble(
            user_input, intent, metadata, tool_constraints=TOOL_CONSTRAINTS,
            session_id=sid,
        )
        tools = self._build_tool_schemas(intent)
        force_tools = intent in {Intent.MUSIC_PLAY, Intent.MUSIC_RECOMMEND}

        # 音乐类意图：预取期间不流式展示模型文案——模型文本可能虚构"版权锁了"
        # 或模板化，音乐解析后一律由 Phase 2 生成最终台词（与 Two-Pass 一致的时序）
        yield_tokens = not force_tools

        reply: dict[str, Any] = {}

        async for chunk in stream_llm(
            SINGLE_PASS_STREAM_SYSTEM, user_prompt,
            tools=tools, force_tools=force_tools,
        ):
            if isinstance(chunk, str):
                if yield_tokens:
                    yield self._sse("token", chunk)
                # 静默模式：文本暂存于 reply.answer，音乐解析失败时才作为最后手段
            elif isinstance(chunk, dict):
                reply = chunk
                if reply.get("analysis") == "Model call failed.":
                    error_msg = reply.get("answer", "Request failed")
                    self._trace(
                        sid, user_input, intent,
                        "single_pass_phase1_miss" if phase1_missed else "single_pass",
                        started, error=error_msg,
                    )
                    yield self._sse("error", error_msg)
                    return

        if yield_tokens:
            yield self._sse("text", reply.get("answer", ""))

        # === EXECUTE (with retry loop — RFC-007: streaming path now retries) ===
        actions = self._parse_actions_from_reply(reply)
        song_data_fallback: dict[str, Any] | None = None

        # tool_choice=required 偶发不被遵守——模型可能只输出文本，甚至凭空臆想
        # "版权锁了"。音乐类意图无工具调用 → 强制重试一次，纠正"先搜索再判断"。
        if force_tools and not actions and tools:
            strict_input = (
                f"{user_input}\n\n[System: 你必须调用 search_music 或 get_music_url "
                "工具实际搜索。不要假设版权问题——先搜索，搜到就能放。]"
            )
            strict_prompt = await assembler.assemble(
                strict_input, intent, metadata,
                tool_constraints=TOOL_CONSTRAINTS, session_id=sid,
            )
            strict_reply = await call_llm(
                NON_STREAMING_SYSTEM, strict_prompt,
                tools=tools, force_tools=True,
            )
            strict_actions = self._parse_actions_from_reply(strict_reply)
            if strict_actions:
                reply = strict_reply
                actions = strict_actions
            else:
                # 模型两次拒绝调工具 → Phase 1 确定性路径兜底：
                # JSON 提取 play_keyword → 直接搜索（不依赖工具调用协议）
                logger.info(
                    "Strict retry refused tools, Phase-1 deterministic fallback: '%s'",
                    user_input,
                )
                song_data_fallback = await self._phase1_prefetch(
                    user_input, sid, metadata,
                )

        results = await self.tool_executor.execute_actions(actions)
        final_reply = await self._merge_tool_results(reply, results)

        if song_data_fallback:
            final_reply["music"] = song_data_fallback

        retry_count = 0
        while self._collect_retry_contexts(results):
            if retry_count >= self.MAX_RETRIES:
                final_reply = self._build_graceful_fallback(final_reply)
                break

            retry_count += 1
            feedback = build_retry_feedback(final_reply, self._collect_retry_contexts(results))
            retry_input = f"{user_input}\n\n[System: {feedback}]"
            retry_prompt = await assembler.assemble(
                retry_input, intent, metadata, tool_constraints=TOOL_CONSTRAINTS,
                session_id=sid,
            )

            # Retry silently via non-streaming LLM — user already saw the original text
            retry_reply = await call_llm(
                NON_STREAMING_SYSTEM, retry_prompt,
                tools=tools, force_tools=force_tools,
            )
            retry_actions = self._parse_actions_from_reply(retry_reply)
            results = await self.tool_executor.execute_actions(retry_actions)
            final_reply = await self._merge_tool_results(retry_reply, results)

        # 口味硬约束（v0.7 P0-2）：disliked 命中 → 带反馈重试换艺人；
        # 重试仍命中/失败 → 丢弃 music（不播放已拉黑艺人，保留文案）
        disliked = self._disliked_artists(ctx)
        blocked = self._matched_disliked(final_reply.get("music"), disliked)
        if blocked:
            logger.info("Disliked artist blocked: %s", blocked)
            retried = await self._retry_for_disliked(
                sid, user_input, intent, metadata, assembler, tools, blocked,
            )
            if retried is not None and not self._matched_disliked(
                retried.get("music"), disliked,
            ):
                final_reply = retried
            else:
                final_reply.pop("music", None)

        music = final_reply.get("music")
        if isinstance(music, dict) and music.get("song_id"):
            yield self._sse("music", json.dumps(music, ensure_ascii=False))
            self._record_play_history(sid, music, metadata)

        # 音乐已开播：文案为空（required 只出 tool_calls）或音乐意图静默预取
        # （模型文本不可靠）→ 用 Phase 2 流程（Hitting the Post）在前奏上生成
        # 自然的 DJ 台词（与 Two-Pass 一致的时序），文案因此多变不模板化
        music_resolved = isinstance(music, dict) and bool(music.get("name"))
        if music_resolved and (not final_reply.get("answer", "").strip() or not yield_tokens):
            phase2_prompt = await self._build_phase2_prompt(
                sid, user_input, intent, metadata, music,
            )
            dj_line = ""
            async for chunk in stream_llm(PHASE2_STREAM_SYSTEM, phase2_prompt):
                if isinstance(chunk, str):
                    dj_line += chunk
                    yield self._sse("token", chunk)
                elif isinstance(chunk, dict):
                    dj_line = chunk.get("answer", "") or dj_line
            dj_line = dj_line.strip()
            if dj_line:
                final_reply["answer"] = dj_line
                yield self._sse("text", dj_line)
            else:
                # LLM 兜底也失败 → 模板兜底（最后手段）
                final_reply = self._ensure_dj_line(final_reply)
                if final_reply.get("answer", "").strip():
                    yield self._sse("text", final_reply["answer"])
        elif not yield_tokens and not music_resolved:
            # 音乐意图且最终无音乐（所有兜底失败）→ 补发收集到的文案（最后手段）
            if final_reply.get("answer", "").strip():
                yield self._sse("text", final_reply["answer"])
            else:
                final_reply = self._ensure_dj_line(final_reply)
                if final_reply.get("answer", "").strip():
                    yield self._sse("text", final_reply["answer"])

        # TTS — for CHITCHAT/WEATHER this is the main audio output (no music)
        speech_sse = await self._maybe_yield_speech(
            final_reply.get("answer", ""), intent.value,
        )
        if speech_sse:
            yield speech_sse

        # === RECORD ===
        played_song = final_reply.get("music")
        ctx.short_term_memory.add_turn(
            user_input,
            final_reply.get("answer", ""),
            intent=str(intent),
            played_song=played_song,
        )
        if not self._is_no_record(metadata):
            asyncio.ensure_future(
                self.episodic_memory.store_snapshot(
                    user_input,
                    final_reply.get("answer", ""),
                    played_song=played_song,
                    session_id=sid,
                )
            )
            self._maybe_reflect(sid, ctx)
        self._trace(
            sid, user_input, intent,
            "single_pass_phase1_miss" if phase1_missed else "single_pass",
            started, final_reply,
        )

        yield self._sse("done", json.dumps(final_reply, ensure_ascii=False))

    # ================================================================
    # 会话级 trace 日志（Agent Observability）—— data/conversations.jsonl
    # 记录每次交互的 意图/路径/工具调用/音乐/回答/延迟/错误，用于行为复盘与调试
    # ================================================================

    @staticmethod
    def _trace(
        session_id: str,
        user_input: str,
        intent: Intent | None,
        path: str,
        started: float,
        reply: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        """追加一条会话 trace（失败静默，绝不影响主流程）。"""
        try:
            from backend.data_config import get_data_dir

            record: dict[str, Any] = {
                "ts": time.time(),
                "session_id": session_id,
                "user_input": user_input[:200],
                "intent": intent.value if intent is not None else None,
                "path": path,
                "latency_ms": round((time.time() - started) * 1000, 1),
                "tool_calls": [],
                "music": None,
                "answer": "",
                "error": error,
            }
            if reply:
                actions = reply.get("actions") or []
                record["tool_calls"] = [
                    {k: v for k, v in a.items() if k in ("tool", "keyword", "song_id")}
                    for a in actions[:5]
                    if isinstance(a, dict)
                ]
                music = reply.get("music")
                if isinstance(music, dict):
                    record["music"] = {
                        "name": music.get("name"),
                        "artist": music.get("artist"),
                        "song_id": music.get("song_id"),
                    }
                record["answer"] = str(reply.get("answer", ""))[:300]

            log_dir = get_data_dir()
            log_dir.mkdir(parents=True, exist_ok=True)
            with (log_dir / "conversations.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.warning("会话 trace 写入失败（不影响主流程）: %s", exc)

    # ═══════════════════════════════════════════════════════════════
    # RFC-003 helpers
    # ═══════════════════════════════════════════════════════════════

    async def _phase1_prefetch(
        self, user_input: str, sid: str, metadata: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Phase 1: silently extract play_keyword -> search -> resolve mp3_url.

        Uses PHASE1_DECISION_SYSTEM (role="system") with minimal context
        (role="user") to disambiguate song-title vs emotion.
        """
        ctx = self.session_manager.get_or_create(sid)
        currently_playing = (metadata or {}).get("Currently Playing", "")
        last_turn = ctx.short_term_memory.get_last_user_message() or ""
        last_reply = ctx.short_term_memory.get_last_assistant_reply() or ""

        decision_user_prompt = build_phase1_user_prompt(
            user_input,
            currently_playing=currently_playing if currently_playing != "None" else "",
            last_turn=last_turn,
            last_reply=last_reply,
        )

        try:
            decision = await call_llm(PHASE1_DECISION_SYSTEM, decision_user_prompt)
        except Exception:
            return None

        play_kw = (decision.get("play_keyword") or "").strip()
        if not play_kw:
            return None

        try:
            search_result = await search_first_song(play_kw)
            mp3_url = await get_song_mp3_url(search_result["id"])
            return {
                "song_id": search_result["id"],
                "name": search_result["name"],
                "artist": search_result["artist"],
                "cover_url": search_result.get("cover_url", ""),
                "album": search_result.get("album", ""),
                "duration_ms": search_result.get("duration_ms"),
                "mp3_url": mp3_url,
                "requested_keyword": play_kw,
            }
        except Exception:
            return None

    def _record_play_history(
        self, sid: str, song: Any, metadata: dict[str, Any] | None = None,
    ) -> None:
        """music 事件发出即写入播放历史（RFC-020 契约 B，失败静默）。

        No Record（测试）请求不写入 —— 防止自动化测试污染真实历史。
        """
        if self._is_no_record(metadata):
            return
        if not isinstance(song, dict) or not song.get("song_id"):
            return
        try:
            asyncio.ensure_future(
                self.play_history.add_async(
                    sid, song, str(song.get("requested_keyword") or ""),
                )
            )
        except Exception:
            logger.warning("播放历史写入调度失败（不影响播放）", exc_info=True)

    @staticmethod
    def _sse(event: str, data: str) -> str:
        return f"event: {event}\ndata: {data}\n\n"

    async def _maybe_yield_speech(
        self, answer_text: str, intent_str: str, is_music_play: bool = False,
    ) -> str | None:
        """Synthesize TTS speech and return the SSE string, or None on skip/fail.

        MUSIC_PLAY: short pre-roll (≤80 chars).  CHITCHAT/WEATHER: full answer.
        """
        if not self.tts.is_enabled or not self.tts.intent_enabled(intent_str):
            return None

        text = (
            self.tts.pre_roll_text(answer_text, max_len=80)
            if is_music_play
            else (answer_text or "").strip()
        )
        if not text:
            return None

        urls = await self.tts.synthesize(text)
        return self._sse("speech", json.dumps({
            "urls": urls,
            "text": text,
            "intent": intent_str,
        }, ensure_ascii=False))

    def schedule_profile_update(
        self,
        background_tasks: Any,
        user_input: str,
        final_reply: dict[str, Any],
        session_id: str | None = None,
    ) -> None:
        sid = self._resolve_session(session_id)
        ctx = self.session_manager.get_or_create(sid)

        # 跨会话整合：Observer 输入带上最近 Reflection 摘要（防重复/防稀释）
        summaries: list[str] = []
        try:
            for item in self.episodic_memory.query_recent_summaries(sid, limit=2):
                text = str(item.get("summary_text") or "").strip()
                if text:
                    summaries.append(text)
        except Exception:
            pass  # 摘要缺失不阻断画像更新

        background_tasks.add_task(
            ctx.memory_manager.async_update_profile,
            user_input,
            json.dumps(final_reply, ensure_ascii=False),
            summaries,
        )

    # ================================================================
    # Reflection（v5）—— 每 N 轮把对话压成跨会话摘要
    # ================================================================

    def _maybe_reflect(self, session_id: str, ctx: Any) -> None:
        """每 REFLECT_EVERY_TURNS 轮触发一次会话摘要（fire-and-forget）。

        用 ctx.last_summary_turn 节流：即使多次请求并发也只会触发一次。
        失败静默（Reflector 内部已降级），不阻断对话。
        """
        turn_count = len(ctx.short_term_memory)
        if turn_count < self.REFLECT_EVERY_TURNS:
            return
        if (turn_count - ctx.last_summary_turn) < self.REFLECT_EVERY_TURNS:
            return
        ctx.last_summary_turn = turn_count

        transcript = ctx.short_term_memory.format_history()
        if not transcript.strip():
            return
        try:
            asyncio.ensure_future(
                self.reflector.summarize_and_store(
                    session_id, transcript, turn_count,
                )
            )
        except Exception:
            logger.warning("Reflection 调度失败（不影响对话）", exc_info=True)

    # ================================================================
    # Helpers
    # ================================================================

    @staticmethod
    def _ensure_dj_line(reply: dict[str, Any]) -> dict[str, Any]:
        """工具调用为 required 时模型可能只输出 tool_calls 不写文案。

        若最终回答为空但音乐已解析，用确定性模板补一句 DJ 台词，
        保证打字机效果与文案展示不空场。
        """
        if reply.get("answer", "").strip():
            return reply
        music = reply.get("music")
        if isinstance(music, dict) and music.get("name"):
            name = music.get("name", "")
            artist = music.get("artist", "")
            reply["answer"] = (
                f"来一首 {artist} 的《{name}》。" if artist else f"来一首《{name}》。"
            )
            reply["analysis"] = ""
        return reply

    @staticmethod
    def _parse_actions_from_reply(reply: dict[str, Any]) -> list[dict[str, Any]]:
        """Normalize reply → tool action dicts.

        RFC: function calling 重构后，LLM 返回的 tool_calls 已在 llm_client
        归一化为 {"tool": name, ...args} 形式，这里只做类型过滤（保留
        向后兼容：旧的 "actions" 文本格式不再解析，ast 兜底已删除）。
        """
        actions = reply.get("actions", [])
        if not isinstance(actions, list):
            return []
        return [item for item in actions if isinstance(item, dict) and "tool" in item]

    @staticmethod
    async def _merge_tool_results(
        reply: dict[str, Any], results: list[Any]
    ) -> dict[str, Any]:
        from backend.tools.netease_api import get_song_mp3_url

        merged = dict(reply)
        for result in results:
            if not hasattr(result, "success"):
                continue
            if not result.success:
                continue
            if not result.data:
                continue

            if "song_id" in result.data and "mp3_url" not in result.data:
                merged["music"] = {
                    "requested_keyword": result.data.get("requested_keyword", ""),
                    "song_id": result.data.get("song_id", ""),
                    "name": result.data.get("name", ""),
                    "artist": result.data.get("artist", ""),
                    "cover_url": result.data.get("cover_url", ""),
                    "album": result.data.get("album", ""),
                    "duration_ms": result.data.get("duration_ms"),
                }
                sid = result.data.get("song_id", "")
                if sid:
                    try:
                        mp3_url = await get_song_mp3_url(str(sid))
                        merged["music"]["mp3_url"] = mp3_url
                    except Exception:
                        merged["music"]["mp3_url"] = ""

            elif "mp3_url" in result.data:
                if "music" in merged and isinstance(merged["music"], dict):
                    merged["music"]["mp3_url"] = result.data.get("mp3_url", "")
                else:
                    merged["music"] = result.data

        return merged

    @staticmethod
    def _collect_retry_contexts(results: list[Any]) -> list[str]:
        contexts: list[str] = []
        for result in results:
            if hasattr(result, "metadata") and result.metadata.get("retry_context"):
                contexts.append(str(result.metadata["retry_context"]))
        return contexts

    @staticmethod
    def _build_graceful_fallback(reply: dict[str, Any]) -> dict[str, Any]:
        degraded = dict(reply)
        degraded.pop("music", None)
        degraded["play_keyword"] = ""

        degraded["answer"] = GRACEFUL_FALLBACK_TEXT
        degraded["say"] = GRACEFUL_FALLBACK_TEXT

        actions = degraded.get("actions")
        if isinstance(actions, list):
            degraded["actions"] = [
                item for item in actions
                if not (isinstance(item, dict) and item.get("tool") in {"search_music", "get_music_url"})
            ]

        return degraded
