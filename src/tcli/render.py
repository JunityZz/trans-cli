"""Streaming output that updates in place instead of scrolling the terminal.

`rich.Live` redraws a single anchored region on each refresh, so streamed tokens
replace the previous frame rather than appending a fresh line every time — the
viewport stays put and the eye doesn't have to chase a moving target.

When stdout is not a TTY (piped/redirected) we skip the live rendering entirely
and just emit the final, stripped text so `t ... | pbcopy` stays clean.
"""

import sys

from rich.console import Console
from rich.live import Live
from rich.text import Text


class Stream:
    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()
        self.tty = self.console.is_terminal
        self.buf = ""
        self.live: Live | None = None

    def __enter__(self) -> "Stream":
        if self.tty:
            self.live = Live(
                Text(""),
                console=self.console,
                refresh_per_second=30,
                transient=False,            # leave the final translation on screen
                vertical_overflow="visible",
            )
            self.live.__enter__()
        return self

    def feed(self, chunk: str) -> None:
        self.buf += chunk
        if self.live is not None:
            self.live.update(Text(self.buf.strip()))

    def __exit__(self, *exc) -> None:
        if self.live is not None:
            self.live.update(Text(self.buf.strip()))
            self.live.__exit__(*exc)
        else:
            sys.stdout.write(self.buf.strip() + "\n")
            sys.stdout.flush()
