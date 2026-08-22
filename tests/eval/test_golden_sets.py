"""Golden Set 完整性测试 —— 保证评估基准数据本身合法、无重复。

任何对 golden_*.json 的增改都必须通过本测试，防止脏数据污染评估结果。
"""

import json
from pathlib import Path

from backend.memory.profile_schema import VALID_MOODS

EVAL_DIR = Path(__file__).resolve().parent.parent.parent / "eval"

VALID_INTENTS = {"music_play", "music_recommend", "weather", "chitchat", "unknown"}
VALID_CATEGORIES = {
    "hard_signal", "title_vs_mood", "emotion", "context", "weather",
    "chitchat", "english", "mixed", "garbage", "unknown_scope",
}


def _load(name: str) -> dict:
    return json.loads((EVAL_DIR / name).read_text(encoding="utf-8"))


class TestIntentGoldenSet:
    def test_file_exists_and_parses(self) -> None:
        data = _load("golden_intents.json")
        assert isinstance(data["cases"], list)
        assert len(data["cases"]) >= 40

    def test_intent_coverage(self) -> None:
        data = _load("golden_intents.json")
        by_intent: dict[str, int] = {}
        for case in data["cases"]:
            by_intent[case["intent"]] = by_intent.get(case["intent"], 0) + 1
        for intent in VALID_INTENTS:
            assert by_intent.get(intent, 0) >= 5, f"意图 {intent} 用例不足 5 条"

    def test_case_shape_and_labels(self) -> None:
        data = _load("golden_intents.json")
        for case in data["cases"]:
            assert isinstance(case["input"], str) and case["input"].strip(), "input 必须为非空字符串"
            assert case["intent"] in VALID_INTENTS, f"非法意图: {case['intent']!r}"
            assert case.get("category") in VALID_CATEGORIES, f"非法类别: {case.get('category')!r}"

    def test_no_duplicate_inputs(self) -> None:
        data = _load("golden_intents.json")
        inputs = [case["input"] for case in data["cases"]]
        assert len(inputs) == len(set(inputs)), "存在重复 input"

    def test_divergence_modes_valid(self) -> None:
        data = _load("golden_intents.json")
        for case in data["cases"]:
            for mode in case.get("expected_divergence", []):
                assert mode in {"keyword", "llm"}, f"非法 divergence 模式: {mode!r}"


class TestRetrievalGoldenSet:
    def test_file_exists_and_parses(self) -> None:
        data = _load("golden_retrieval.json")
        assert len(data["seeds"]) >= 10
        assert len(data["queries"]) >= 10

    def test_seed_shape_and_moods(self) -> None:
        data = _load("golden_retrieval.json")
        for seed in data["seeds"]:
            assert isinstance(seed["user_input"], str) and seed["user_input"].strip()
            assert isinstance(seed["assistant_reply"], str) and seed["assistant_reply"].strip()
            assert isinstance(seed["song_name"], str)
            assert isinstance(seed["song_artist"], str)
            mood = seed.get("mood_tag")
            if mood is not None:
                assert mood in VALID_MOODS, f"非法 mood 标签: {mood!r}"
            time_of_day = seed.get("time_of_day")
            if time_of_day is not None:
                assert time_of_day in {"morning", "afternoon", "evening", "night"}, (
                    f"非法时段: {time_of_day!r}"
                )

    def test_query_expect_within_seed_range(self) -> None:
        data = _load("golden_retrieval.json")
        n_seeds = len(data["seeds"])
        for query in data["queries"]:
            assert isinstance(query["query"], str) and query["query"].strip()
            expect = query["expect"]
            ids = expect if isinstance(expect, list) else [expect]
            for seed_idx in ids:
                assert 1 <= seed_idx <= n_seeds, (
                    f"查询 {query['query']!r} 的 expect {seed_idx} 超出 seed 范围 [1, {n_seeds}]"
                )