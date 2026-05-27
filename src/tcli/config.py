"""User configuration stored as JSON at ~/.t-cli/config.json."""

import json

from .paths import CONFIG_PATH, DEFAULT_MODEL, ensure_base

DEFAULTS = {
    "default_lang": "en",          # target language when none is given
    "model": DEFAULT_MODEL,
    "max_tokens": 2048,
    "idle_timeout": 1800,          # seconds; daemon exits after this idle. 0 = never
    # Hy-MT2-1.8B recommended sampling params:
    "temp": 0.7,
    "top_p": 0.6,
    "top_k": 20,
    "repetition_penalty": 1.05,
}


def load() -> dict:
    ensure_base()
    data = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            data.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            pass
    return data


def save(data: dict) -> None:
    ensure_base()
    CONFIG_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def set_value(key: str, value) -> dict:
    data = load()
    data[key] = value
    save(data)
    return data
