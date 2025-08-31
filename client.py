# client.py — unified raw SDK client loader
import os
import anthropic
from openai import OpenAI  # works with the modern OpenAI SDK
from google import genai   # pip install google-genai

_HOME = os.path.expanduser("~")
_KEYS = {
    "anthropic": os.path.join(_HOME, "anthropic.key"),
    "openai":    os.path.join(_HOME, "openai.key"),
    "google":    os.path.join(_HOME, "genai.key"),
}

def _read_key(path: str) -> str:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Key file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()

def get_client(provider: str = "anthropic"):
    """
    Return a low-level SDK client for: 'anthropic' | 'openai' | 'google'
    """
    p = provider.lower()
    if p not in _KEYS:
        raise ValueError(f"Unknown provider: {provider}")
    key = _read_key(_KEYS[p])

    if p == "anthropic":
        return anthropic.Anthropic(api_key=key)
    elif p == "openai":
        # Prefer explicit client instance in the v1+ OpenAI SDK
        return OpenAI(api_key=key)
    elif p == "google":
        return genai.Client(api_key=key)
    else:
        # should never hit due to the guard above
        raise ValueError(f"Unknown provider: {provider}")
