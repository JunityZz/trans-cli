"""Background daemon that keeps the MLX translation model warm.

It binds a Unix-domain socket and answers newline-delimited JSON requests:

    {"cmd": "ping"}                                  -> status / readiness
    {"cmd": "translate", "prompt": "...", "max_tokens": N}
                                                     -> stream of {"t": chunk}, then {"done": true}
    {"cmd": "shutdown"}                              -> exits the process

MLX GPU streams are thread-local, so the model is both *loaded* and *used* on a
single dedicated worker thread. The main thread only accepts connections: it
answers ping/shutdown inline and hands translate jobs to the worker via a queue,
which keeps the socket responsive (e.g. for status pings) while the model loads
or while a long translation is streaming.
"""

import json
import os
import queue
import socket
import threading
import time
import traceback
from datetime import datetime

from . import config as cfg
from .paths import LOG_PATH, PID_PATH, SOCK_PATH, ensure_base


def _log(msg: str) -> None:
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().isoformat(timespec='seconds')}] {msg}\n")
    except OSError:
        pass


def _recv_line(conn: socket.socket) -> bytes:
    buf = b""
    while b"\n" not in buf:
        try:
            chunk = conn.recv(4096)
        except OSError:
            break
        if not chunk:
            break
        buf += chunk
    return buf.split(b"\n", 1)[0]


def _send_json(conn: socket.socket, obj) -> None:
    conn.sendall((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))


class Daemon:
    def __init__(self) -> None:
        self.conf = cfg.load()
        self.remote = bool(self.conf.get("base_url"))
        self.model = None
        self.tokenizer = None
        self.sampler = None
        self.logits_processors = None
        self.status = "loading"   # loading -> ready | error
        self.error: str | None = None
        self.jobs: "queue.Queue" = queue.Queue()
        self.last_active = time.time()

    # -- worker thread: owns all MLX work ---------------------------------
    def _worker(self) -> None:
        self._load_model()
        while True:
            conn, req = self.jobs.get()
            self._serve_translate(conn, req)

    def _load_model(self) -> None:
        if self.remote:
            # Remote backend: nothing to load locally — the model lives behind
            # the configured base URL. Mark ready immediately.
            self.status = "ready"
            _log(f"remote backend ready: {self.conf['base_url']} (model {self.conf['model']})")
            return
        try:
            from mlx_lm import load
            from mlx_lm.sample_utils import make_logits_processors, make_sampler

            t0 = time.time()
            _log(f"loading model {self.conf['model']}")
            self.model, self.tokenizer = load(self.conf["model"])
            self.sampler = make_sampler(
                temp=float(self.conf["temp"]),
                top_p=float(self.conf["top_p"]),
                top_k=int(self.conf["top_k"]),
            )
            self.logits_processors = make_logits_processors(
                repetition_penalty=float(self.conf["repetition_penalty"]),
            )
            self.status = "ready"
            _log(f"model ready in {time.time() - t0:.1f}s")
        except Exception as e:  # noqa: BLE001 - surface any load failure to the client
            self.status = "error"
            self.error = f"{type(e).__name__}: {e}"
            _log("model load failed:\n" + traceback.format_exc())

    def _serve_translate(self, conn: socket.socket, req: dict) -> None:
        try:
            if self.status != "ready":
                _send_json(conn, {"error": self.error or f"model is {self.status}"})
                return

            self.last_active = time.time()
            max_tokens = int(req.get("max_tokens", self.conf["max_tokens"]))

            if self.remote:
                from .remote import RemoteError, stream_chat

                try:
                    stream_chat(
                        self.conf, req["prompt"], max_tokens,
                        lambda t: _send_json(conn, {"t": t}),
                    )
                except RemoteError as e:
                    _send_json(conn, {"error": str(e)})
                    return
            else:
                from mlx_lm import stream_generate

                messages = [{"role": "user", "content": req["prompt"]}]
                prompt = self.tokenizer.apply_chat_template(messages, add_generation_prompt=True)
                for resp in stream_generate(
                    self.model,
                    self.tokenizer,
                    prompt,
                    max_tokens=max_tokens,
                    sampler=self.sampler,
                    logits_processors=self.logits_processors,
                ):
                    if resp.text:
                        _send_json(conn, {"t": resp.text})

            _send_json(conn, {"done": True})
            self.last_active = time.time()
        except (BrokenPipeError, ConnectionResetError, OSError) as e:
            _log(f"client disconnected during generation ({type(e).__name__})")
        except Exception as e:  # noqa: BLE001
            _log("generate failed:\n" + traceback.format_exc())
            try:
                _send_json(conn, {"error": f"{type(e).__name__}: {e}"})
            except OSError:
                pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    # -- main thread: accept + dispatch -----------------------------------
    def handle(self, conn: socket.socket) -> None:
        line = _recv_line(conn)
        if not line:
            conn.close()
            return
        try:
            req = json.loads(line.decode("utf-8"))
        except ValueError:
            conn.close()
            return
        cmd = req.get("cmd", "translate")

        if cmd == "ping":
            _send_json(conn, {
                "status": self.status,
                "ready": self.status == "ready",
                "error": self.error,
                "model": self.conf["model"],
                "backend": "remote" if self.remote else "local",
                "base_url": self.conf.get("base_url") or "",
                "pid": os.getpid(),
            })
            conn.close()
            return
        if cmd == "shutdown":
            _send_json(conn, {"ok": True})
            conn.close()
            _log("shutdown requested")
            self._cleanup()
            os._exit(0)
        if cmd == "translate":
            self.jobs.put((conn, req))   # worker closes the connection
            return
        conn.close()

    # -- lifecycle ---------------------------------------------------------
    def _cleanup(self) -> None:
        for p in (SOCK_PATH, PID_PATH):
            try:
                p.unlink()
            except OSError:
                pass

    def run(self) -> None:
        ensure_base()
        try:
            SOCK_PATH.unlink()  # remove any stale socket
        except OSError:
            pass

        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            srv.bind(str(SOCK_PATH))
        except OSError as e:
            _log(f"bind failed ({e}); another daemon is probably running")
            return
        srv.listen(8)
        srv.settimeout(5.0)
        PID_PATH.write_text(str(os.getpid()))
        _log(f"daemon listening pid={os.getpid()}")

        threading.Thread(target=self._worker, daemon=True).start()
        idle = int(self.conf.get("idle_timeout", 0))

        try:
            while True:
                try:
                    conn, _ = srv.accept()
                except socket.timeout:
                    if (
                        idle > 0
                        and self.status == "ready"
                        and self.jobs.empty()
                        and time.time() - self.last_active > idle
                    ):
                        _log("idle timeout, exiting")
                        break
                    continue
                self.handle(conn)
        finally:
            try:
                srv.close()
            except OSError:
                pass
            self._cleanup()


def main() -> None:
    Daemon().run()


if __name__ == "__main__":
    main()
