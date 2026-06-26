"""Fullscreen streaming renderer for terminal translations.

TTY output uses the terminal's alternate screen buffer, the same primitive used
by full-screen terminal programs. Streaming tokens are redrawn inside that
isolated screen, so shell scrollback and prompts never mix with partial frames.

When stdout is not a TTY we skip the fullscreen UI and emit only the final text
so `t ... | pbcopy` and redirects stay clean.
"""

from __future__ import annotations

import io
import os
import select
import shutil
import subprocess
import termios
import threading
import time
import tty
import unicodedata
from collections.abc import Callable

from rich.console import Console
from rich.text import Text


_ALT_SCREEN_ON = "\x1b[?1049h"
_ALT_SCREEN_OFF = "\x1b[?1049l"
_HIDE_CURSOR = "\x1b[?25l"
_SHOW_CURSOR = "\x1b[?25h"
_CLEAR_HOME = "\x1b[2J\x1b[H"
_CURSOR_HOME = "\x1b[H"

# Escape sequences treated as navigation keys. Terminals send arrows/home/end
# as either CSI (ESC [) or SS3 (ESC O) depending on cursor-key mode, so accept
# both forms.
_KEY_SEQUENCES: dict[bytes, str] = {
    b"\x1b[A": "up",
    b"\x1bOA": "up",
    b"\x1b[B": "down",
    b"\x1bOB": "down",
    b"\x1b[5~": "page_up",
    b"\x1b[6~": "page_down",
    b"\x1b[H": "home",
    b"\x1bOH": "home",
    b"\x1b[1~": "home",
    b"\x1b[7~": "home",
    b"\x1b[F": "end",
    b"\x1bOF": "end",
    b"\x1b[4~": "end",
    b"\x1b[8~": "end",
}


class StreamCancelled(Exception):
    """Raised inside the producer thread when the user exits early."""


class Stream:
    def __init__(self, console: Console | None = None, target_label: str = "") -> None:
        self.console = console or Console()
        self.target_label = target_label
        self.tty = self.console.is_terminal and not _env_truthy("T_NO_TUI")
        self.buf = ""

        self.cancel_event = threading.Event()
        self.cancelled = False
        self.completed = False
        self.error: BaseException | None = None

        self._state = threading.RLock()
        self._pause_cond = threading.Condition(self._state)
        self._finished = False
        self._paused = False
        self._dirty = True
        self._exit_requested = False
        self._message = ""
        self._message_until = 0.0
        self._spinner = 0
        self._scroll_offset = 0
        self._max_scroll_offset = 0
        self._zoom = 0

        self._tty = None
        self._tty_fd: int | None = None
        self._old_term = None
        self._key_buf = b""
        self._esc_pending_since = 0.0

    def __enter__(self) -> "Stream":
        if self.tty:
            self.tty = self._enter_fullscreen()
        return self

    def run(self, producer: Callable[[], None]) -> None:
        """Run the stream producer while the fullscreen UI handles keyboard input."""
        if not self.tty:
            producer()
            self.completed = True
            return

        worker = threading.Thread(target=self._produce, args=(producer,), daemon=True)
        worker.start()
        self._event_loop(worker)

        self.cancel_event.set()
        with self._pause_cond:
            self._pause_cond.notify_all()
        worker.join(timeout=0.2 if self.cancelled else 2.0)

        if self.error is not None:
            raise self.error

    def feed(self, chunk: str) -> None:
        with self._pause_cond:
            while self._paused and not self.cancel_event.is_set():
                self._pause_cond.wait(timeout=0.2)
            if self.cancel_event.is_set():
                raise StreamCancelled()
            self.buf += chunk
            self._dirty = True

    def note(self, message: str, ttl: float = 1.0) -> None:
        with self._state:
            self._message = message
            self._message_until = time.monotonic() + ttl
            self._dirty = True

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.tty:
            self._leave_fullscreen()

        should_print = (
            exc_type is None
            and self.completed
            and not self.cancelled
            and bool(self.buf.strip())
        )
        if should_print and self.tty:
            self.console.print(Text(self.buf.strip()))
        elif should_print:
            self.console.file.write(self.buf.strip() + "\n")
            self.console.file.flush()

    # -- terminal lifecycle ---------------------------------------------
    def _enter_fullscreen(self) -> bool:
        try:
            self._tty = open("/dev/tty", "r+b", buffering=0)
            self._tty_fd = self._tty.fileno()
            self._old_term = termios.tcgetattr(self._tty_fd)
            tty.setraw(self._tty_fd, termios.TCSANOW)
        except OSError:
            self._close_tty()
            return False

        self._write(_ALT_SCREEN_ON + _HIDE_CURSOR + _CLEAR_HOME)
        return True

    def _leave_fullscreen(self) -> None:
        try:
            if self._old_term is not None and self._tty_fd is not None:
                termios.tcsetattr(self._tty_fd, termios.TCSADRAIN, self._old_term)
        finally:
            self._write(_SHOW_CURSOR + _ALT_SCREEN_OFF)
            self._close_tty()

    def _close_tty(self) -> None:
        if self._tty is not None:
            try:
                self._tty.close()
            except OSError:
                pass
        self._tty = None
        self._tty_fd = None

    # -- producer + UI loop ---------------------------------------------
    def _produce(self, producer: Callable[[], None]) -> None:
        try:
            producer()
            with self._state:
                self.completed = not self.cancelled and not self.cancel_event.is_set()
        except StreamCancelled:
            with self._state:
                self.cancelled = True
        except BaseException as e:  # noqa: BLE001 - surface daemon/client errors
            with self._state:
                self.error = e
        finally:
            with self._state:
                self._finished = True
                self._dirty = True

    def _event_loop(self, worker: threading.Thread) -> None:
        next_frame = 0.0
        while True:
            for key in self._poll_keys():
                self._handle_key(key)

            now = time.monotonic()
            with self._state:
                finished = self._finished
                paused = self._paused
                message_expired = bool(self._message) and now >= self._message_until
                force = (
                    self._dirty
                    or message_expired
                    or (not finished and not paused and now >= next_frame)
                )
                error = self.error
                exit_requested = self._exit_requested

            if force:
                self._redraw()
                next_frame = now + 0.05

            if error is not None:
                break
            if exit_requested:
                break
            if finished and not worker.is_alive():
                # Keep the completed translation visible until the user exits.
                time.sleep(0.04)
            else:
                time.sleep(0.02)

    # -- keyboard --------------------------------------------------------
    def _poll_keys(self) -> list[str]:
        if self._tty_fd is None:
            return []
        keys: list[str] = []
        got_data = False
        while True:
            try:
                readable, _, _ = select.select([self._tty_fd], [], [], 0)
            except OSError:
                break
            if not readable:
                break
            try:
                data = os.read(self._tty_fd, 1024)
            except OSError:
                break
            if not data:
                break
            got_data = True
            self._key_buf += data
            keys.extend(self._drain_keys())

        # A leading ESC left in the buffer is either an escape sequence still
        # arriving (an arrow key split across reads — the rest is microseconds
        # away) or a real Escape press (nothing follows). Fresh bytes this poll
        # mean input is still flowing, so refresh the timer; treat the ESC as a
        # real Escape only once it has been idle — no new bytes at all — for the
        # grace period. A scroll burst keeps feeding bytes, so it never trips
        # this, which is what used to make scrolling exit at random.
        now = time.monotonic()
        if self._key_buf[:1] == b"\x1b":
            if got_data or self._esc_pending_since == 0.0:
                self._esc_pending_since = now
            elif now - self._esc_pending_since > 0.1:
                self._key_buf = self._key_buf[1:]
                self._esc_pending_since = 0.0
                keys.append("escape")
                keys.extend(self._drain_keys())
        else:
            self._esc_pending_since = 0.0
        return keys

    def _drain_keys(self) -> list[str]:
        """Turn buffered bytes into key names, leaving an incomplete escape
        sequence in the buffer so a split arrow key is never read as Escape."""
        keys: list[str] = []
        while self._key_buf:
            if self._key_buf[0] == 0x1B:
                result = self._take_escape()
                if result is None:
                    break               # incomplete sequence — wait for more
                if result:              # a recognised navigation key
                    keys.append(result)
                continue                # "" -> a sequence we consume silently

            b0 = self._key_buf[0]
            self._key_buf = self._key_buf[1:]
            if b0 == 0x03:
                keys.append("ctrl_c")
            elif b0 in (0x0D, 0x0A):
                keys.append("enter")
            elif 0x20 <= b0 < 0x7F:
                keys.append(chr(b0))
        return keys

    def _take_escape(self) -> str | None:
        """Consume one escape sequence from the head of the buffer.

        Returns the key name for a recognised sequence, "" for a complete but
        unhandled sequence (consumed, no key emitted), or None when only an
        incomplete sequence is buffered — in which case nothing is consumed and
        the caller waits for the rest. Assumes the buffer starts with ESC.
        """
        buf = self._key_buf
        for seq, name in _KEY_SEQUENCES.items():
            if buf.startswith(seq):
                self._key_buf = buf[len(seq):]
                return name
        if len(buf) == 1:
            return None                 # lone ESC so far
        c1 = buf[1]
        if c1 == 0x5B:                  # '[' -> CSI: parameters until a final byte
            i = 2
            while i < len(buf) and 0x20 <= buf[i] <= 0x3F:
                i += 1
            if i >= len(buf):
                return None             # no final byte yet — incomplete
            self._key_buf = buf[i + 1:]
            return ""
        if c1 == 0x4F:                  # 'O' -> SS3: ESC O <final>
            if len(buf) < 3:
                return None
            self._key_buf = buf[3:]
            return ""
        # ESC + another byte (e.g. Alt-key): drop just the ESC, keep the rest so
        # the following byte is read normally rather than triggering an exit.
        self._key_buf = buf[1:]
        return ""

    def _handle_key(self, key: str) -> None:
        with self._pause_cond:
            finished = self._finished

            if key in ("q", "Q", "escape", "ctrl_c"):
                if not finished:
                    self.cancelled = True
                    self.cancel_event.set()
                    self._message = "cancelled"
                    self._message_until = time.monotonic() + 1.5
                self._exit_requested = True
                self._dirty = True
                self._pause_cond.notify_all()
                return

            if key in ("p", "P", " "):
                if not finished:
                    self._paused = not self._paused
                    self._message = "paused" if self._paused else "resumed"
                    self._message_until = time.monotonic() + 1.5
                    self._dirty = True
                    self._pause_cond.notify_all()
                return

            if key in ("c", "C", "y", "Y"):
                self._copy()
                return

            if key in ("+", "="):
                self._zoom = min(3, self._zoom + 1)
                self._message = f"display zoom {100 + self._zoom * 25}%"
                self._message_until = time.monotonic() + 1.5
                self._dirty = True
                return

            if key in ("-", "_"):
                self._zoom = max(0, self._zoom - 1)
                self._message = f"display zoom {100 + self._zoom * 25}%"
                self._message_until = time.monotonic() + 1.5
                self._dirty = True
                return

            if key == "0":
                self._zoom = 0
                self._message = "display zoom reset"
                self._message_until = time.monotonic() + 1.5
                self._dirty = True
                return

            step = 1
            if key == "page_up":
                step = 8
                key = "up"
            elif key == "page_down":
                step = 8
                key = "down"

            if key in ("up", "k"):
                self._scroll_offset = max(0, self._scroll_offset - step)
                self._dirty = True
            elif key in ("down", "j"):
                self._scroll_offset = min(
                    self._max_scroll_offset,
                    self._scroll_offset + step,
                )
                self._dirty = True
            elif key == "home":
                self._scroll_offset = 0
                self._dirty = True
            elif key == "end":
                self._scroll_offset = self._max_scroll_offset
                self._dirty = True

    def _copy(self) -> None:
        text = self.buf.strip()
        if not text:
            self._message = "nothing to copy"
            self._message_until = time.monotonic() + 1.5
            self._dirty = True
            return
        try:
            subprocess.run(["pbcopy"], input=text, text=True, check=True)
        except (OSError, subprocess.CalledProcessError):
            self._message = "copy failed"
        else:
            self._message = "copied"
        self._message_until = time.monotonic() + 1.5
        self._dirty = True

    # -- drawing ---------------------------------------------------------
    def _redraw(self) -> None:
        width, height = self._terminal_size()
        width = max(24, width)
        height = max(6, height)

        frame = self._build_frame(width, height)
        # Overwrite in place from the home cursor instead of clearing first: a
        # per-frame \x1b[2J blanks the screen before repainting, which flickers
        # at streaming speed. Every line is padded to full width, so the new
        # frame fully covers the old one and the screen never goes blank. The
        # one-time clear on entry (_enter_fullscreen) still wipes stale content.
        self._write(_CURSOR_HOME + frame)
        with self._state:
            self._dirty = False
            self._spinner = (self._spinner + 1) % 4

    def _build_frame(self, width: int, height: int) -> str:
        with self._state:
            text = self.buf.strip()
            paused = self._paused
            finished = self._finished
            completed = self.completed
            cancelled = self.cancelled
            spinner = self._spinner
            zoom = self._zoom
            scroll_offset = self._scroll_offset
            message = self._message if time.monotonic() < self._message_until else ""
            if not message:
                self._message = ""

        body_height = height - 2
        body_width = max(20, width - 2 - zoom * max(4, width // 12))
        left = max(0, (width - body_width) // 2)

        if text:
            lines = _wrap_text(text, body_width)
        elif finished:
            lines = [""]
        else:
            lines = ["Waiting for translation..."]

        # scroll_offset is the index of the first visible line (0 = top). The
        # view holds still as new lines stream in below it and only moves when
        # the user scrolls, so reading is never interrupted by auto-scrolling.
        max_offset = max(0, len(lines) - body_height)
        scroll_offset = min(scroll_offset, max_offset)
        start = scroll_offset
        visible = lines[start:start + body_height]

        with self._state:
            self._max_scroll_offset = max_offset
            self._scroll_offset = scroll_offset

        total = len(lines)
        ruler = ""
        if total > body_height:
            ruler = f"{start + 1}-{min(start + body_height, total)}/{total}"

        header = self._header(
            width, paused, finished, completed, cancelled, spinner, ruler
        )
        footer = self._footer(width, message, zoom)

        body: list[str] = []
        for line in visible:
            clipped = _fit_cells(line, body_width)
            body.append(" " * left + _pad_cells(clipped, body_width))
        while len(body) < body_height:
            body.append("")

        # Raw mode clears OPOST/ONLCR, so a bare "\n" moves down a row without
        # returning to column 0. Join with "\r\n" or every line staircases right
        # and scrolls the real content off-screen, leaving a blank viewport.
        return "\r\n".join([header, *(_pad_cells(line, width) for line in body), footer])

    def _header(
        self,
        width: int,
        paused: bool,
        finished: bool,
        completed: bool,
        cancelled: bool,
        spinner: int,
        ruler: str = "",
    ) -> str:
        if cancelled:
            status = "cancelled"
        elif finished and completed:
            status = "done"
        elif finished:
            status = "stopped"
        elif paused:
            status = "paused"
        else:
            status = "generating " + "|/-\\"[spinner]

        target = f" -> {self.target_label}" if self.target_label else ""
        left = f" t{target}  {status}"
        right = f"{ruler} " if ruler else ""
        gap = width - _display_width(left) - _display_width(right)
        bar = left + " " * gap + right if gap >= 1 else left
        return "\x1b[7m" + _pad_cells(_fit_cells(bar, width), width) + "\x1b[0m"

    def _footer(self, width: int, message: str, zoom: int) -> str:
        if message:
            text = message
        else:
            text = (
                "q/esc exit  p pause  c copy  +/- zoom  "
                "up/down scroll"
            )
        if zoom:
            text += f"  [{100 + zoom * 25}%]"
        return "\x1b[2m" + _pad_cells(_fit_cells(text, width), width) + "\x1b[0m"

    def _write(self, data: str) -> None:
        if self._tty is not None:
            self._tty.write(data.encode("utf-8", errors="replace"))
            self._tty.flush()
        else:
            self.console.file.write(data)
            self.console.file.flush()

    def _terminal_size(self) -> tuple[int, int]:
        if self._tty_fd is not None:
            try:
                size = os.get_terminal_size(self._tty_fd)
                return size.columns, size.lines
            except OSError:
                pass
        size = shutil.get_terminal_size((80, 24))
        return size.columns, size.lines


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").lower() in {"1", "true", "yes", "on"}


def _wrap_text(text: str, width: int) -> list[str]:
    console = Console(
        file=io.StringIO(),
        force_terminal=False,
        color_system=None,
        width=width,
    )
    wrapped = Text(text).wrap(console, width, overflow="fold")
    return [line.plain for line in wrapped] or [""]


def _cell_width(ch: str) -> int:
    if not ch:
        return 0
    if unicodedata.combining(ch):
        return 0
    if unicodedata.category(ch) in {"Cc", "Cf"}:
        return 0
    return 2 if unicodedata.east_asian_width(ch) in {"F", "W"} else 1


def _display_width(text: str) -> int:
    return sum(_cell_width(ch) for ch in text)


def _fit_cells(text: str, width: int) -> str:
    if _display_width(text) <= width:
        return text
    if width <= 3:
        return "." * max(0, width)

    out: list[str] = []
    used = 0
    limit = width - 3
    for ch in text:
        w = _cell_width(ch)
        if used + w > limit:
            break
        out.append(ch)
        used += w
    return "".join(out) + "..."


def _pad_cells(text: str, width: int) -> str:
    used = _display_width(text)
    if used >= width:
        return text
    return text + " " * (width - used)
