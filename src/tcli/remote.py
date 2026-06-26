"""Remote OpenAI-compatible backend.

When `base_url` is configured, the daemon forwards translation requests to a
remote OpenAI-compatible chat-completions endpoint instead of loading a local
MLX model. This uses only the standard library, so it adds no dependency.

The base URL is the OpenAI-style root (the part before `/chat/completions`),
e.g. `https://api.openai.com/v1` or `http://localhost:11434/v1`.
"""

import json
import urllib.error
import urllib.request


class RemoteError(Exception):
    pass


def endpoint(base_url: str) -> str:
    """Resolve the chat-completions URL from a configured base URL.

    Accepts either the OpenAI-style root (`.../v1`) or a full
    `.../chat/completions` URL, so both styles of config work.
    """
    base = base_url.strip().rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def stream_chat(conf: dict, prompt: str, max_tokens: int, on_chunk) -> None:
    """Stream a translation from the remote endpoint, calling on_chunk per token.

    Raises RemoteError for transport / HTTP problems. Any error raised by
    on_chunk (e.g. the local client disconnecting) propagates unchanged.
    """
    body = {
        "model": conf["model"],
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "stream": True,
        "temperature": float(conf["temp"]),
        "top_p": float(conf["top_p"]),
    }
    headers = {"Content-Type": "application/json"}
    api_key = (conf.get("api_key") or "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(
        endpoint(conf["base_url"]),
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=300)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace").strip()[:500]
        raise RemoteError(f"remote API returned HTTP {e.code}: {detail or e.reason}") from e
    except urllib.error.URLError as e:
        raise RemoteError(f"cannot reach {conf['base_url']}: {e.reason}") from e

    try:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line or not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            if payload == "[DONE]":
                break
            try:
                msg = json.loads(payload)
            except ValueError:
                continue
            choices = msg.get("choices") or []
            if not choices:
                continue
            piece = (choices[0].get("delta") or {}).get("content")
            if piece:
                on_chunk(piece)
    finally:
        resp.close()
