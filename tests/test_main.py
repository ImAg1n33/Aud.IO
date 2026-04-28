from backend.main import _parse_cors_origins


def test_parse_cors_origins_defaults() -> None:
    assert _parse_cors_origins(None) == [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5500",
        "http://127.0.0.1:5500",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "null",
    ]


def test_parse_cors_origins_custom_list() -> None:
    assert _parse_cors_origins("http://a.com, http://b.com") == ["http://a.com", "http://b.com"]
