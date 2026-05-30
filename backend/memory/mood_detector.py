"""心情自动检测 —— 基于中英文关键词的轻量分类器（无需 LLM，零延迟）。

RFC-008 Step 1: 从 episodic_memory.py 提取。
"""


class MoodDetector:
    """基于中英文关键词的心情检测器。

    设计理由:
    - 零 LLM 成本、零网络延迟 —— 关键词匹配在 < 1ms 内完成
    - 覆盖常见音乐场景心情词（轻松、专注、悲伤、兴奋、平静、浪漫、困倦等）
    - 返回的 mood 标签与 user_profile.json 的 mood_bias 键名对齐，
      确保后续偏好推荐能命中

    注意:
    - 如果用户输入中没有匹配到任何关键词，返回 None（不做猜测）
    - 这不是最终方案 —— Phase 2 可升级为 embedding 相似度分类
    """

    # 中文关键词 → 英文 mood 标签（与 user_profile.json mood_bias 键对齐）
    _CN_TO_MOOD: dict[str, str] = {
        # 轻松 / chill
        "轻松": "calm", "放松": "calm", "舒缓": "calm", "轻快": "calm",
        "休闲": "calm", "chill": "calm", "relax": "calm",
        # 专注 / 工作
        "专注": "focused", "工作": "focused", "学习": "focused",
        "看书": "focused", "阅读": "focused", "编程": "focused",
        "coding": "focused", "study": "focused", "focus": "focused",
        # 开心 / 兴奋
        "开心": "happy", "高兴": "happy", "快乐": "happy", "嗨": "happy",
        "兴奋": "happy", "激动": "happy", "蹦迪": "happy", "派对": "happy",
        "party": "happy", "happy": "happy", "energetic": "happy",
        # 悲伤 / 低落
        "难过": "sad", "悲伤": "sad", "伤心": "sad", "低落": "sad",
        "emo": "sad", "sad": "sad", "忧郁": "sad", "抑郁": "sad",
        # 安静 / 平和
        "安静": "calm", "宁静": "calm", "平和": "calm",
        "peaceful": "calm", "quiet": "calm",
        # 浪漫
        "浪漫": "romantic", "浪漫主义": "romantic", "约会": "romantic",
        "romantic": "romantic", "date": "romantic",
        # 雨天
        "下雨": "rainy", "雨天": "rainy", "雨声": "rainy",
        "rain": "rainy", "rainy": "rainy",
        # 困倦 / 深夜
        "困": "sleepy", "困了": "sleepy", "睡觉": "sleepy", "入睡": "sleepy",
        "催眠": "sleepy", "sleep": "sleepy", "sleepy": "sleepy",
        # 运动
        "运动": "energetic", "跑步": "energetic", "健身": "energetic",
        "锻炼": "energetic", "workout": "energetic", "gym": "energetic",
        # 开车 / 旅行
        "开车": "driving", "驾驶": "driving", "旅途": "driving",
        "旅行": "driving", "公路": "driving", "drive": "driving",
        # 怀旧
        "怀旧": "nostalgic", "回忆": "nostalgic", "老歌": "nostalgic",
        "nostalgia": "nostalgic", "nostalgic": "nostalgic",
    }

    # 优先级更高的关键词（长度更长、更具体的关键词优先匹配）
    _PRIORITY_KEYWORDS: list[str] = [
        # 长关键词排在前面，确保优先匹配
        "浪漫主义", "coding", "study", "focus", "happy", "energetic",
        "workout", "sleep", "sleepy", "rainy", "rain", "sad",
        "party", "chill", "relax", "quiet", "peaceful",
        "romantic", "nostalgic", "nostalgia", "drive",
    ]

    @classmethod
    def detect(cls, user_input: str) -> str | None:
        """从用户输入中检测心情标签。

        Args:
            user_input: 用户原始输入文本

        Returns:
            检测到的英文 mood 标签，无匹配时返回 None
        """
        if not user_input or not user_input.strip():
            return None

        text = user_input.strip()
        text_lower = text.lower()

        matched_mood: str | None = None
        matched_len: int = 0

        # 遍历中英文关键词表，取最长匹配（避免 "困" 误匹配 "困难"）
        for keyword, mood in cls._CN_TO_MOOD.items():
            kw_lower = keyword.lower()
            if kw_lower in text_lower or keyword in text:
                kw_len = len(keyword)
                # 更长关键词优先；同长度时优先级列表中靠前的优先
                if kw_len > matched_len or (
                    kw_len == matched_len
                    and cls._priority_score(keyword) > cls._priority_score(matched_mood or "")
                ):
                    matched_mood = mood
                    matched_len = kw_len

        return matched_mood

    @classmethod
    def _priority_score(cls, keyword: str) -> int:
        """计算关键词优先级分数（越大越优先）。"""
        try:
            # 低索引 = 高优先级
            idx = cls._PRIORITY_KEYWORDS.index(keyword.lower())
            return len(cls._PRIORITY_KEYWORDS) - idx
        except ValueError:
            return 0
