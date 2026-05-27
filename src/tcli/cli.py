"""`t` — command-line entrypoint.

Usage:
    t <text>                 translate <text> into your default language
    t <lang> <text>          translate <text> into <lang>
    pbpaste | t              translate piped text into your default language
    pbpaste | t <lang>       translate piped text into <lang>

Management:
    t --set-lang <lang>      set the default target language
    t --lang                 show the current default language
    t --langs                list supported languages and aliases
    t --status               show the translator daemon status
    t --stop | --restart     control the daemon
    t --config               show config file and values
    t --model <id>           set the MLX model id
    t --help | --version
"""

import sys

from rich.console import Console

from . import __version__
from . import client, config as cfg
from .langs import LANGUAGES, resolve
from .paths import CONFIG_PATH, LOG_PATH
from .prompt import build_prompt

out = Console()                 # translation result -> stdout
err = Console(stderr=True)      # headers, spinners, messages -> stderr

HELP = """[bold]t[/] — fast on-device terminal translator (Hunyuan-MT / MLX)

[bold]Translate[/]
  t <text>              translate into your default language
  t <lang> <text>       translate into <lang>  (e.g. t ja hello world)
  t                     paste mode: clears screen, paste/type, [bold]Ctrl+D[/] to translate
  t <lang>              paste mode targeting <lang>  (e.g. t ja)
  pbpaste | t           translate clipboard / piped text
  pbpaste | t zh        translate piped text into Chinese

[bold]Configure[/]
  t --set-lang <lang>   set default language   (e.g. t --set-lang zh)
  t --lang              show current default
  t --langs             list supported languages
  t --model <id>        set the MLX model id
  t --config            show config path and values

[bold]Daemon[/]
  t --status            model / daemon status
  t --stop              stop the background model
  t --restart           restart it

  t --help              this help        t --version
"""


def _read_stdin() -> str | None:
    if sys.stdin is None or sys.stdin.isatty():
        return None
    data = sys.stdin.read()
    return data if data.strip() else None


# clear screen + scrollback + move cursor home
_CLEAR = "\x1b[2J\x1b[3J\x1b[H"


def _interactive_input(target) -> str | None:
    """Clear the screen and let the user paste / type multi-line text.

    Finish with Ctrl+D to translate; Ctrl+C cancels. Returns the text, or None
    if cancelled. Falls back to a plain cooked read if raw mode is unavailable.
    """
    w = sys.stderr  # draw the UI on stderr so stdout stays clean for piping
    w.write(_CLEAR)
    w.flush()
    err.print(
        f"[bold cyan]t[/]  粘贴或输入内容，完成后按 [bold]Ctrl+D[/] 翻译"
        f"  ·  [dim]Ctrl+C 取消[/]  →  目标 [bold]{target.zh_name}[/] [dim]({target.en_name})[/]"
    )
    err.print("[dim]" + "─" * 60 + "[/]")
    try:
        text = _raw_read(sys.stdin.fileno(), w)
    except KeyboardInterrupt:
        err.print("\n[dim]已取消[/]")
        return None
    except Exception:  # noqa: BLE001 - termios.error etc.: fall back to cooked read
        try:
            text = sys.stdin.read()   # not a real tty: cooked fallback (Ctrl+D)
        except KeyboardInterrupt:
            return None
    err.print("[dim]" + "─" * 60 + "[/]")
    return text


def _raw_read(fd: int, w) -> str:
    """Read multi-line input in raw mode; Ctrl+D submits, Ctrl+C raises."""
    import codecs
    import os
    import termios
    import tty

    old = termios.tcgetattr(fd)
    decoder = codecs.getincrementaldecoder("utf-8")()
    buf: list[str] = []
    esc = 0  # 0: none, 1: saw ESC, 2: inside an escape sequence

    w.write("\x1b[?2004l")  # disable bracketed paste so its markers don't leak in
    w.flush()
    try:
        # TCSANOW: switch to raw immediately without flushing input already typed
        # or pasted before we got here (TCSAFLUSH, the default, would drop it).
        tty.setraw(fd, termios.TCSANOW)
        done = False
        while not done:
            data = os.read(fd, 4096)
            if not data:
                break  # EOF
            for ch in decoder.decode(data):
                if esc == 1:
                    esc = 2 if ch in "[O" else 0
                    continue
                if esc == 2:
                    if ch.isalpha() or ch == "~":
                        esc = 0
                    continue
                if ch == "\x04":            # Ctrl+D -> submit
                    done = True
                    break
                if ch == "\x03":            # Ctrl+C -> cancel
                    raise KeyboardInterrupt
                if ch == "\x1b":            # arrow keys etc. — consume & ignore
                    esc = 1
                    continue
                if ch in ("\r", "\n"):
                    buf.append("\n")
                    w.write("\r\n"); w.flush()
                    continue
                if ch in ("\x7f", "\b"):    # backspace (within the current line)
                    if buf and buf[-1] != "\n":
                        buf.pop()
                        w.write("\b \b"); w.flush()
                    continue
                if ord(ch) < 0x20 and ch != "\t":
                    continue                # ignore other control chars
                buf.append(ch)
                w.write(ch); w.flush()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        w.write("\r\n"); w.flush()
    return "".join(buf)


def _print_help() -> None:
    err.print(HELP)


def _cmd_set_lang(arg: str | None) -> int:
    if not arg:
        err.print("[red]Usage:[/] t --set-lang <lang>")
        return 1
    lang = resolve(arg)
    if not lang:
        err.print(f"[red]Unknown language:[/] {arg}.  Run [bold]t --langs[/] to see options.")
        return 1
    cfg.set_value("default_lang", lang.code)
    err.print(f"Default language set to [bold]{lang.en_name}[/] [dim]({lang.code} / {lang.zh_name})[/].")
    return 0


def _cmd_lang() -> int:
    conf = cfg.load()
    lang = resolve(conf["default_lang"]) or LANGUAGES["en"]
    err.print(f"Default language: [bold]{lang.en_name}[/] [dim]({lang.code} / {lang.zh_name})[/]")
    return 0


def _cmd_langs() -> int:
    from rich.table import Table

    table = Table(title="Supported languages", show_lines=False, header_style="bold")
    table.add_column("code", style="cyan")
    table.add_column("English")
    table.add_column("中文")
    table.add_column("aliases", style="dim")
    aliases_by_code: dict[str, list[str]] = {}
    from .langs import ALIASES
    for alias, lang in ALIASES.items():
        aliases_by_code.setdefault(lang.code, [])
        if alias not in aliases_by_code[lang.code]:
            aliases_by_code[lang.code].append(alias)
    for code, lang in LANGUAGES.items():
        sample = ", ".join(aliases_by_code.get(code, [])[:6])
        table.add_row(code, lang.en_name, lang.zh_name, sample)
    err.print(table)
    return 0


def _cmd_status() -> int:
    info = client.status()
    if not info:
        err.print("Translator daemon: [yellow]not running[/] (starts on first translation).")
        return 0
    state = info.get("status", "?")
    color = {"ready": "green", "loading": "yellow", "error": "red"}.get(state, "white")
    err.print(f"Translator daemon: [{color}]{state}[/]  pid={info.get('pid')}")
    err.print(f"Model: [dim]{info.get('model')}[/]")
    if info.get("error"):
        err.print(f"[red]{info['error']}[/]")
    return 0


def _cmd_stop() -> int:
    err.print("Stopped." if client.stop() else "Daemon was not running.")
    return 0


def _cmd_restart() -> int:
    client.stop()
    err.print("Restarting translator…")
    with err.status("[dim]loading model…[/]", spinner="dots") as st:
        try:
            client.ensure_ready(on_status=lambda s: st.update(f"[dim]loading model ({s})…[/]"))
        except client.DaemonError as e:
            err.print(f"[red]{e}[/]")
            return 1
    err.print("[green]Ready.[/]")
    return 0


def _cmd_config() -> int:
    conf = cfg.load()
    err.print(f"Config file: [dim]{CONFIG_PATH}[/]")
    for k, v in conf.items():
        err.print(f"  [cyan]{k}[/] = {v}")
    return 0


def _cmd_download() -> int:
    """Download + load the model in the foreground (shows HF progress)."""
    conf = cfg.load()
    err.print(f"Downloading / loading model [bold]{conf['model']}[/] … [dim](one-time ~1GB)[/]")
    try:
        from mlx_lm import load

        load(conf["model"])
    except Exception as e:  # noqa: BLE001
        err.print(f"[red]Failed:[/] {e}")
        return 1
    err.print("[green]Model ready.[/]  Try:  [bold]t 你好，世界[/]")
    return 0


def _cmd_model(arg: str | None) -> int:
    if not arg:
        err.print(f"Model: [bold]{cfg.load()['model']}[/]")
        return 0
    cfg.set_value("model", arg)
    err.print(f"Model set to [bold]{arg}[/].  Run [bold]t --restart[/] to load it.")
    return 0


def _translate(target, content: str) -> int:
    conf = cfg.load()
    prompt = build_prompt(target, content)
    max_tokens = int(conf["max_tokens"])

    try:
        with err.status("[dim]starting translator…[/]", spinner="dots") as st:
            client.ensure_ready(
                on_status=lambda s: st.update(f"[dim]loading model ({s})…[/]")
            )
    except client.DaemonError as e:
        err.print(f"[red]Translator error:[/] {e}")
        err.print(f"[dim]See log: {LOG_PATH}[/]")
        return 1

    err.print(f"[dim]→ {target.en_name}[/]")
    from .render import Stream
    try:
        with Stream(out) as stream:
            client.translate_stream(prompt, max_tokens, stream.feed)
    except client.DaemonError as e:
        err.print(f"[red]Translation failed:[/] {e}")
        return 1
    return 0


def main() -> None:
    args = sys.argv[1:]

    # ---- management commands -------------------------------------------
    if args:
        a0 = args[0]
        if a0 in ("-h", "--help", "help"):
            _print_help(); sys.exit(0)
        if a0 in ("-V", "--version", "version"):
            err.print(f"t {__version__}"); sys.exit(0)
        if a0 in ("--langs", "langs"):
            sys.exit(_cmd_langs())
        if a0 in ("--lang", "-L"):
            sys.exit(_cmd_lang())
        if a0 in ("--set-lang", "-s", "set-lang"):
            sys.exit(_cmd_set_lang(args[1] if len(args) > 1 else None))
        if a0 in ("--status", "status"):
            sys.exit(_cmd_status())
        if a0 in ("--stop", "stop"):
            sys.exit(_cmd_stop())
        if a0 in ("--restart", "restart"):
            sys.exit(_cmd_restart())
        if a0 in ("--config", "config"):
            sys.exit(_cmd_config())
        if a0 in ("--model", "model"):
            sys.exit(_cmd_model(args[1] if len(args) > 1 else None))
        if a0 in ("--download", "download"):
            sys.exit(_cmd_download())
        if a0 == "--":            # force everything after as text
            args = args[1:]

    # ---- translation ----------------------------------------------------
    conf = cfg.load()
    default = resolve(conf["default_lang"]) or LANGUAGES["en"]
    stdin_data = _read_stdin()

    if stdin_data is not None:
        # Piped input is the content; args (if any) name the target language.
        content = stdin_data
        target = resolve(" ".join(args)) if args else default
        if target is None:
            target = default
    else:
        # `t` (or `t <lang>`) with nothing else on a terminal opens paste mode.
        interactive = sys.stdin.isatty() and (
            not args or (len(args) == 1 and resolve(args[0]) is not None)
        )
        if interactive:
            target = resolve(args[0]) if args else default
            content = _interactive_input(target)
            if content is None:        # cancelled
                sys.exit(0)
        elif not args:
            _print_help(); sys.exit(0)
        # `t <lang> <text...>`: first token is a target only if it resolves AND
        # there is something after it. Otherwise the whole thing is content.
        elif len(args) >= 2 and resolve(args[0]) is not None:
            target = resolve(args[0])
            content = " ".join(args[1:])
        else:
            target = default
            content = " ".join(args)

    content = content.strip()
    if not content:
        err.print("[red]Nothing to translate.[/]")
        sys.exit(1)

    sys.exit(_translate(target, content))


if __name__ == "__main__":
    main()
