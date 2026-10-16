import os


def load_keys() -> None:
    grok_key = os.environ.get("GROQ_API_KEY")
    openrouter_key = os.environ.get("OPENROUTER_API_KEY")
    mistral_key = os.environ.get("MISTRAL_API_KEY")

    if not grok_key:
        raise RuntimeError("GROQ_API_KEY is not set (check your .env)")
