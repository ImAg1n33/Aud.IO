from backend.memory.conversation_memory import ConversationMemory, ConversationTurn


class TestConversationTurn:
    def test_defaults(self) -> None:
        turn = ConversationTurn(user_input="hello")
        assert turn.user_input == "hello"
        assert turn.assistant_reply == ""
        assert turn.timestamp  # auto-generated

    def test_played_song_none_by_default(self) -> None:
        turn = ConversationTurn(user_input="play something")
        assert turn.played_song is None


class TestConversationMemory:
    def test_add_and_retrieve(self) -> None:
        mem = ConversationMemory(max_turns=5)
        mem.add_turn("hello", "hi there", intent="chitchat")
        assert len(mem) == 1
        history = mem.get_history()
        assert history[0].user_input == "hello"
        assert history[0].assistant_reply == "hi there"

    def test_max_turns_enforced(self) -> None:
        mem = ConversationMemory(max_turns=3)
        for i in range(5):
            mem.add_turn(f"msg {i}")
        assert len(mem) == 3
        assert mem.get_history()[0].user_input == "msg 2"

    def test_get_last_n(self) -> None:
        mem = ConversationMemory(max_turns=10)
        mem.add_turn("a")
        mem.add_turn("b")
        mem.add_turn("c")
        assert len(mem.get_history(last_n=2)) == 2
        assert mem.get_history(last_n=2)[0].user_input == "b"

    def test_get_last_user_message(self) -> None:
        mem = ConversationMemory()
        mem.add_turn("first")
        mem.add_turn("second")
        assert mem.get_last_user_message() == "second"

    def test_get_last_user_message_empty_inputs(self) -> None:
        mem = ConversationMemory()
        mem.add_turn("", "reply")
        assert mem.get_last_user_message() is None

    def test_get_last_assistant_reply(self) -> None:
        mem = ConversationMemory()
        mem.add_turn("hello", "world")
        assert mem.get_last_assistant_reply() == "world"

    def test_format_history(self) -> None:
        mem = ConversationMemory()
        mem.add_turn("play jazz", "Playing Miles Davis.", played_song={"name": "So What", "artist": "Miles Davis"})
        formatted = mem.format_history()
        assert "play jazz" in formatted
        assert "Miles Davis" in formatted

    def test_format_history_empty(self) -> None:
        mem = ConversationMemory()
        assert mem.format_history() == ""

    def test_clear(self) -> None:
        mem = ConversationMemory()
        mem.add_turn("msg")
        mem.clear()
        assert len(mem) == 0
