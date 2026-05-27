"""Thin client that talks to the translation daemon over a Unix socket.

Auto-starts the daemon when it isn't running and waits for the model to load.
"""

import json
import socket
import subprocess
import sys
import time
from pathlib import Path

from .paths import LOG_PATH, SOCK_PATH, ensure_base


class DaemonError(Exception):
    pass


def _connect(timeout: float | None) -> socket.socket:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(str(SOCK_PATH))
    return s


def _ping() -> dict | None:
    """Return the daemon status dict, or None if it isn't reachable."""
    try:
        s = _connect(2.0)
    except OSError:
        return None
    try:
        f = s.makefile("rwb")
        f.write(b'{"cmd": "ping"}\n')
        f.flush()
        line = f.readline()
        return json.loads(line.decode("utf-8")) if line else None
    except (OSError, ValueError):
        return None
    finally:
        s.close()


def _spawn() -> None:
    ensure_base()
    logf = open(LOG_PATH, "a", encoding="utf-8")
    subprocess.Popen(
        [sys.executable, "-m", "tcli.daemon"],
        stdout=logf,
        stderr=logf,
        start_new_session=True,
        cwd=str(Path.home()),
    )


def ensure_ready(on_status=None, timeout: float = 900.0) -> dict:
    """Make sure a daemon is up and the model is loaded. Returns the status dict."""
    info = _ping()
    if info and info.get("ready"):
        return info
    if info is None:
        _spawn()

    deadline = time.time() + timeout
    while time.time() < deadline:
        info = _ping()
        if info:
            if info.get("ready"):
                return info
            if info.get("status") == "error":
                raise DaemonError(info.get("error") or "model failed to load")
            if on_status:
                on_status(info.get("status", "starting"))
        elif on_status:
            on_status("starting")
        time.sleep(0.4)
    raise DaemonError("timed out waiting for the translator to start")


def translate_stream(prompt: str, max_tokens: int, on_chunk) -> None:
    """Send a translation request and feed each streamed chunk to on_chunk."""
    s = _connect(timeout=None)  # generation can take a while; no read timeout
    try:
        f = s.makefile("rwb")
        req = {"cmd": "translate", "prompt": prompt, "max_tokens": max_tokens}
        f.write((json.dumps(req, ensure_ascii=False) + "\n").encode("utf-8"))
        f.flush()
        while True:
            line = f.readline()
            if not line:
                break
            msg = json.loads(line.decode("utf-8"))
            if "t" in msg:
                on_chunk(msg["t"])
            elif msg.get("done"):
                break
            elif "error" in msg:
                raise DaemonError(msg["error"])
    finally:
        s.close()


def status() -> dict | None:
    return _ping()


def stop() -> bool:
    """Ask a running daemon to shut down. Returns True if one was running."""
    if _ping() is None:
        return False
    try:
        s = _connect(2.0)
        f = s.makefile("rwb")
        f.write(b'{"cmd": "shutdown"}\n')
        f.flush()
        f.readline()
        s.close()
    except OSError:
        pass
    return True
