"""OpenAI-compatible HTTP shim in front of the translation daemon.

This serves a tiny subset of the OpenAI API (`/v1/chat/completions`,
`/v1/models`) over plain HTTP and forwards every request to the already-running
MLX daemon over its Unix socket. Because it reuses the daemon, the model stays
loaded exactly once — this shim holds no weights of its own.

There is no authentication: bind it to localhost only.

Endpoints:
    GET  /                       tiny human-readable hint
    GET  /health                 {"status": "...", "model": "..."}
    GET  /v1/models              OpenAI-style model list
    POST /v1/chat/completions    OpenAI chat completion (stream or not)
"""

import json
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import client, config as cfg


def _messages_to_prompt(messages: list) -> str:
    """Flatten OpenAI `messages` into the single prompt the daemon expects.

    The daemon wraps whatever it receives in one user turn and applies the
    model's chat template, so we concatenate the message contents in order.
    Handles both plain-string content and OpenAI "content parts" arrays.
    """
    parts: list[str] = []
    for m in messages or []:
        content = m.get("content", "")
        if isinstance(content, list):  # [{"type":"text","text":"..."}, ...]
            content = "".join(
                p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"
            )
        if content:
            parts.append(str(content))
    return "\n\n".join(parts)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "trans-cli-openai-shim"

    # -- low-level response helpers --------------------------------------
    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _send_json(self, obj, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, message: str, status: int = 400, etype: str = "invalid_request_error") -> None:
        self._send_json({"error": {"message": message, "type": etype, "code": None}}, status=status)

    def _begin_sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self._cors()
        self.end_headers()

    def _sse(self, obj) -> None:
        self._write_chunk(("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8"))

    def _write_chunk(self, data: bytes) -> None:
        self.wfile.write(f"{len(data):X}\r\n".encode("ascii") + data + b"\r\n")
        self.wfile.flush()

    def _end_chunks(self) -> None:
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    # quieter logging: one line on stderr per request
    def log_message(self, fmt, *a):  # noqa: A002 - signature fixed by stdlib
        pass

    # -- routes -----------------------------------------------------------
    def do_OPTIONS(self):  # noqa: N802 - stdlib naming
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):  # noqa: N802
        if self.path.rstrip("/") in ("", "/v1"):
            self._send_json({"service": "trans-cli OpenAI-compatible shim", "try": "/v1/chat/completions"})
            return
        if self.path == "/health":
            info = client.status() or {}
            self._send_json({"status": info.get("status", "stopped"), "model": cfg.load()["model"]})
            return
        if self.path == "/v1/models":
            model = cfg.load()["model"]
            self._send_json({
                "object": "list",
                "data": [{"id": model, "object": "model", "created": 0, "owned_by": "mlx-community"}],
            })
            return
        self._send_error("not found", status=404, etype="not_found")

    def do_POST(self):  # noqa: N802
        if self.path != "/v1/chat/completions":
            self._send_error("not found", status=404, etype="not_found")
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except (ValueError, UnicodeDecodeError):
            self._send_error("request body is not valid JSON")
            return

        prompt = _messages_to_prompt(payload.get("messages", []))
        if not prompt.strip():
            self._send_error("`messages` must contain at least one non-empty message")
            return

        conf = cfg.load()
        model = payload.get("model") or conf["model"]
        max_tokens = int(payload.get("max_tokens") or conf["max_tokens"])
        stream = bool(payload.get("stream", False))

        try:
            client.ensure_ready()
        except client.DaemonError as e:
            self._send_error(str(e), status=503, etype="model_not_ready")
            return

        cid = "chatcmpl-" + uuid.uuid4().hex
        created = int(time.time())

        if stream:
            self._stream_completion(cid, created, model, prompt, max_tokens)
        else:
            self._whole_completion(cid, created, model, prompt, max_tokens)

    # -- completion strategies -------------------------------------------
    def _whole_completion(self, cid, created, model, prompt, max_tokens) -> None:
        chunks: list[str] = []
        try:
            client.translate_stream(prompt, max_tokens, chunks.append)
        except client.DaemonError as e:
            self._send_error(str(e), status=500, etype="generation_error")
            return
        text = "".join(chunks)
        self._send_json({
            "id": cid,
            "object": "chat.completion",
            "created": created,
            "model": model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        })

    def _stream_completion(self, cid, created, model, prompt, max_tokens) -> None:
        def frame(delta, finish=None):
            return {
                "id": cid,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }

        self._begin_sse()
        try:
            self._sse(frame({"role": "assistant"}))
            client.translate_stream(prompt, max_tokens, lambda t: self._sse(frame({"content": t})))
            self._sse(frame({}, finish="stop"))
            self._write_chunk(b"data: [DONE]\n\n")
            self._end_chunks()
        except client.DaemonError as e:
            # Best effort: surface the error inside the stream, then close.
            self._sse({"error": {"message": str(e), "type": "generation_error"}})
            self._end_chunks()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # client went away mid-stream


def serve(host: str, port: int, on_ready=None) -> None:
    """Block serving the OpenAI shim. Loads the model up front via the daemon."""
    httpd = ThreadingHTTPServer((host, port), _Handler)
    if on_ready:
        on_ready(host, port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
