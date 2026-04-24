from typing import Mapping


def build_prompt(user_input: str, context: Mapping[str, str]) -> str:
    context_lines = [f"- {key}: {value}" for key, value in context.items()]
    context_block = "\n".join(context_lines) if context_lines else "- none"

    return (
        "You are Aud.IO, a helpful voice-first assistant.\n"
        "Keep responses concise, practical, and friendly.\n\n"
        f"Context:\n{context_block}\n\n"
        f"User:\n{user_input}\n"
    )
