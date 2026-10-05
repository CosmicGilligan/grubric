"""
model_catalog.py

Fetches currently-available LLMs directly from provider APIs instead of
relying on the hardcoded [ANTHROPIC_MODELS]/[OPENAI_MODELS]/[GOOGLE_MODELS]
sections in config_rubric.ini, which go stale every time a provider
ships/retires a model.

Usage sketch (see bottom of file for a runnable example):

    catalog = get_model_catalog(config, providers=("anthropic",))
    model_id = prompt_model_choice(catalog)          # CLI
    # or, inside a Streamlit app:
    model_id = streamlit_model_picker(catalog, config["SETTINGS"]["default_model"])
    save_default_model(config_path, config, model_id)

Results are cached locally so you're not hitting the provider API on every
single grading run — see CACHE_TTL_SECONDS.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

import requests

CACHE_DIR = Path("./embeddings_cache")  # reuses your existing cache convention
CACHE_FILE = CACHE_DIR / "model_catalog_cache.json"
CACHE_TTL_SECONDS = 7 * 24 * 3600  # re-fetch at most once a week; override with force_refresh=True


@dataclass
class ModelInfo:
    id: str
    display_name: str
    provider: str
    created_at: Optional[str] = None


def _read_key(key_file: str) -> str:
    return Path(key_file).read_text().strip()


# ---------------------------------------------------------------------------
# Per-provider fetchers
# ---------------------------------------------------------------------------

def fetch_anthropic_models(api_key_file: str) -> list[ModelInfo]:
    api_key = _read_key(api_key_file)
    resp = requests.get(
        "https://api.anthropic.com/v1/models",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()["data"]  # most-recently-released models listed first
    return [
        ModelInfo(
            id=m["id"],
            display_name=m.get("display_name", m["id"]),
            provider="anthropic",
            created_at=m.get("created_at"),
        )
        for m in data
    ]


def fetch_openai_models(api_key_file: str) -> list[ModelInfo]:
    api_key = _read_key(api_key_file)
    resp = requests.get(
        "https://api.openai.com/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()["data"]
    # /v1/models returns everything (embeddings, whisper, tts, etc) —
    # filter down to chat-capable model families.
    chat_models = [m for m in data if m["id"].startswith(("gpt-", "o1", "o3", "o4"))]
    return [
        ModelInfo(id=m["id"], display_name=m["id"], provider="openai", created_at=str(m.get("created")))
        for m in chat_models
    ]


def fetch_google_models(api_key_file: str) -> list[ModelInfo]:
    api_key = _read_key(api_key_file)
    resp = requests.get(
        f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}",
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()["models"]
    gen_models = [m for m in data if "generateContent" in m.get("supportedGenerationMethods", [])]
    return [
        ModelInfo(id=m["name"].split("/")[-1], display_name=m.get("displayName", m["name"]), provider="google")
        for m in gen_models
    ]


_FETCHERS = {
    "anthropic": fetch_anthropic_models,
    "openai": fetch_openai_models,
    "google": fetch_google_models,
}


# ---------------------------------------------------------------------------
# Cache + orchestration
# ---------------------------------------------------------------------------

def get_model_catalog(
    config,
    providers: tuple[str, ...] = ("anthropic",),
    force_refresh: bool = False,
) -> dict[str, list[ModelInfo]]:
    """
    Returns {provider: [ModelInfo, ...]} for each requested provider.

    Defaults to Anthropic only, since that's what you're actually grading
    with day to day. Pass providers=("anthropic", "google") etc. to include
    others when you want them.
    """
    CACHE_DIR.mkdir(exist_ok=True)

    if CACHE_FILE.exists() and not force_refresh:
        cached = json.loads(CACHE_FILE.read_text())
        if time.time() - cached.get("_fetched_at", 0) < CACHE_TTL_SECONDS:
            cached_providers = set(cached) - {"_fetched_at"}
            if set(providers) <= cached_providers:
                return {
                    p: [ModelInfo(**m) for m in cached[p]]
                    for p in providers
                }

    key_file_map = {
        "anthropic": config["API_SETTINGS"].get("anthropic_key_file", ""),
        "openai": config["API_SETTINGS"].get("openai_key_file", ""),
        "google": config["API_SETTINGS"].get("google_key_file", ""),
    }

    catalog: dict[str, list[ModelInfo]] = {}
    for provider in providers:
        key_file = key_file_map.get(provider, "")
        if not key_file:
            print(f"Warning: no key file configured for {provider}; skipping.")
            catalog[provider] = []
            continue
        try:
            catalog[provider] = _FETCHERS[provider](key_file)
        except Exception as e:
            print(f"Warning: couldn't fetch {provider} models ({e}); returning empty list for it.")
            catalog[provider] = []

    to_cache = {p: [asdict(m) for m in models] for p, models in catalog.items()}
    to_cache["_fetched_at"] = time.time()
    CACHE_FILE.write_text(json.dumps(to_cache, indent=2))
    return catalog


# ---------------------------------------------------------------------------
# Pickers
# ---------------------------------------------------------------------------

def prompt_model_choice(catalog: dict[str, list[ModelInfo]]) -> str:
    """CLI picker — returns the chosen model id."""
    all_models = [m for models in catalog.values() for m in models]
    if not all_models:
        raise RuntimeError("No models available — check your API key files / network.")
    print("\nAvailable models:")
    for i, m in enumerate(all_models, 1):
        print(f"  {i}. [{m.provider}] {m.display_name}  ({m.id})")
    choice = int(input("Pick a model number: "))
    return all_models[choice - 1].id


def streamlit_model_picker(catalog: dict[str, list[ModelInfo]], current_default: str):
    """Drop-in for a Streamlit app — call inside your existing UI code."""
    import streamlit as st  # local import so this module doesn't require streamlit for CLI use

    all_models = [m for models in catalog.values() for m in models]
    labels = [f"[{m.provider}] {m.display_name}" for m in all_models]
    ids = [m.id for m in all_models]
    default_idx = ids.index(current_default) if current_default in ids else 0

    idx = st.selectbox("Model", range(len(labels)), format_func=lambda i: labels[i], index=default_idx)
    if st.button("Refresh model list"):
        st.cache_data.clear()  # if you wrap get_model_catalog in @st.cache_data
        st.rerun()
    return ids[idx]


# ---------------------------------------------------------------------------
# Writing the choice back to config_rubric.ini
# ---------------------------------------------------------------------------

def save_default_model(config_path: str, config, model_id: str) -> None:
    config["SETTINGS"]["default_model"] = model_id
    with open(config_path, "w") as f:
        config.write(f)


# ---------------------------------------------------------------------------
# Runnable CLI example
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import configparser

    config_path = "config_rubric.ini"
    config = configparser.ConfigParser()
    config.read(config_path)

    catalog = get_model_catalog(config, providers=("anthropic",))
    chosen = prompt_model_choice(catalog)
    save_default_model(config_path, config, chosen)
    print(f"default_model set to: {chosen}")
