import pytest

from backend.agent.intent_classifier import Intent, IntentClassifier


@pytest.fixture
def classifier() -> IntentClassifier:
    return IntentClassifier()


class TestMusicPlay:
    def test_english_play(self, classifier) -> None:
        assert classifier.classify("play some jazz") == Intent.MUSIC_PLAY

    def test_chinese_play(self, classifier) -> None:
        assert classifier.classify("播放一首周杰伦的歌") == Intent.MUSIC_PLAY

    def test_chinese_laiyishou(self, classifier) -> None:
        assert classifier.classify("来一首爵士乐") == Intent.MUSIC_PLAY

    def test_skip_track(self, classifier) -> None:
        assert classifier.classify("下一首") == Intent.MUSIC_PLAY

    def test_switch_song(self, classifier) -> None:
        assert classifier.classify("换一首") == Intent.MUSIC_PLAY


class TestMusicRecommend:
    def test_recommend_keyword(self, classifier) -> None:
        assert classifier.classify("推荐一些适合下雨天的音乐") == Intent.MUSIC_RECOMMEND

    def test_mood_based(self, classifier) -> None:
        assert classifier.classify("今天心情低落，想听点治愈的歌") == Intent.MUSIC_RECOMMEND

    def test_what_should_i_listen(self, classifier) -> None:
        assert classifier.classify("what should I listen to today") == Intent.MUSIC_RECOMMEND

    def test_genre_only(self, classifier) -> None:
        assert classifier.classify("jazz") == Intent.MUSIC_RECOMMEND


class TestWeather:
    def test_weather_chinese(self, classifier) -> None:
        assert classifier.classify("今天天气怎么样") == Intent.WEATHER

    def test_weather_english(self, classifier) -> None:
        assert classifier.classify("what's the weather like") == Intent.WEATHER

    def test_rain_check(self, classifier) -> None:
        assert classifier.classify("外面在下雨吗") == Intent.WEATHER


class TestChitchat:
    def test_hello(self, classifier) -> None:
        assert classifier.classify("你好") == Intent.CHITCHAT

    def test_thanks_with_keyword(self, classifier) -> None:
        # Contains "推荐" → classified as MUSIC_RECOMMEND by keyword match.
        # Without conversation history, post-recommendation chitchat is hard to distinguish.
        assert classifier.classify("谢谢你的推荐") == Intent.MUSIC_RECOMMEND

    def test_how_are_you(self, classifier) -> None:
        assert classifier.classify("how are you") == Intent.CHITCHAT


class TestUnknown:
    def test_empty_input(self, classifier) -> None:
        assert classifier.classify("") == Intent.UNKNOWN

    def test_gibberish(self, classifier) -> None:
        assert classifier.classify("xyzzy asdf") == Intent.UNKNOWN


class TestPreferenceGating:
    def test_music_play_includes_preferences(self, classifier) -> None:
        assert classifier.should_include_preferences(Intent.MUSIC_PLAY) is True

    def test_music_recommend_includes_preferences(self, classifier) -> None:
        assert classifier.should_include_preferences(Intent.MUSIC_RECOMMEND) is True

    def test_chitchat_excludes_preferences(self, classifier) -> None:
        assert classifier.should_include_preferences(Intent.CHITCHAT) is False

    def test_weather_excludes_preferences(self, classifier) -> None:
        assert classifier.should_include_preferences(Intent.WEATHER) is False


class TestToolCategories:
    def test_music_play_activates_music(self, classifier) -> None:
        assert "music" in classifier.should_activate_tool_categories(Intent.MUSIC_PLAY)

    def test_weather_activates_weather(self, classifier) -> None:
        assert "weather" in classifier.should_activate_tool_categories(Intent.WEATHER)

    def test_chitchat_activates_nothing(self, classifier) -> None:
        assert classifier.should_activate_tool_categories(Intent.CHITCHAT) == []

    def test_unknown_activates_all(self, classifier) -> None:
        cats = classifier.should_activate_tool_categories(Intent.UNKNOWN)
        assert "music" in cats
        assert "weather" in cats
