import os


def _get_llm_config() -> dict[str, str]:
    provider = os.getenv("LLM_PROVIDER", "deepseek").strip().lower()
    model = os.getenv("LLM_MODEL", "deepseek-chat").strip()
    base_url = os.getenv("LLM_BASE_URL", "https://api.deepseek.com").strip()

    api_key = os.getenv("LLM_API_KEY", "").strip()
    if not api_key:
        if provider == "openai":
            api_key = os.getenv("OPENAI_API_KEY", "").strip()
        elif provider == "anthropic":
            api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        elif provider == "deepseek":
            api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()

    return {
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "api_key": api_key,
    }


def call_llm(prompt: str) -> str:
    """Temporary stub for model calls.

    Replace this with a real provider SDK call once API credentials and
    model routing are configured.
    """
    config = _get_llm_config()
    max_preview = 120
    preview = prompt[:max_preview].replace("\n", " ")
    key_hint = "set" if config["api_key"] else "missing"
    return (
        f"[stub-reply provider={config['provider']} model={config['model']} key={key_hint}] "
        f"{preview}"
    )
