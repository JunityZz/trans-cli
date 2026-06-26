"""`t` — command-line entrypoint.

Usage:
    t <text>                 translate <text> into your default language
    t <lang> <text>          translate <text> into <lang>
    t --pb                   translate the clipboard into your default language
    t <lang> --pb            translate the clipboard into <lang>
    … | t [lang]             translate piped text (e.g. cat file | t)
    t [lang] -- <text>       everything after `--` is literal text, even a dash

Management:
    t --set-lang <lang>      set the default target language
    t --lang                 show the current default language
    t --langs                list supported languages and aliases
    t --status               show the translator daemon status
    t --stop | --restart     control the daemon
    t --config               show config file and values
    t --model <id>           set the model id (local MLX repo, or remote model name)
    t --base-url <url>       use a remote OpenAI-compatible API (none/empty = local MLX)
    t --api-key <key>        API key for the remote endpoint
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
  t --pb                translate the clipboard  ([bold]t <lang> --pb[/] for a target)
  t                     paste mode: clears screen, paste/type, [bold]Ctrl+D[/] to translate
  t <lang>              paste mode targeting <lang>  (e.g. t ja)
  … | t                 translate piped text  (e.g. cat notes.txt | t zh)

  [dim]Quotes or symbols ([/]'[dim], [/]"[dim], [/]$[dim]) in the text confuse the shell, not[/] t[dim].
  Don't fight it: copy the text and run[/] t --pb[dim], or run[/] t [dim]and paste.[/]

[bold]Configure[/]
  t --set-lang <lang>   set default language   (e.g. t --set-lang zh)
  t --lang              show current default
  t --langs             list supported languages
  t --model <id>        set the model id (local MLX repo, or remote model name)
  t --base-url <url>    use a remote OpenAI-compatible API  ([bold]none[/] = local MLX)
  t --api-key <key>     API key for the remote endpoint
  t --config            show config path and values

[bold]Daemon[/]
  t --status            model / daemon status
  t --stop              stop the background model
  t --restart           restart it
  t --serve [port]      OpenAI-compatible HTTP endpoint (reuses the daemon)

  t --help              this help        t --version
"""


def _read_stdin() -> str | None:
    if sys.stdin is None or sys.stdin.isatty():
        return None
    data = sys.stdin.read()
    return data if data.strip() else None


def _read_clipboard() -> str | None:
    """Read the macOS clipboard via `pbpaste`. None on error (already reported)."""
    import subprocess

    try:
        proc = subprocess.run(["pbpaste"], capture_output=True, text=True)
    except FileNotFoundError:
        err.print("[red]--pb needs macOS [bold]pbpaste[/], which wasn't found.[/]")
        return None
    if proc.returncode != 0:
        detail = proc.stderr.strip() or f"pbpaste exited with {proc.returncode}"
        err.print(f"[red]Could not read clipboard:[/] {detail}")
        return None
    return proc.stdout


def _target_from_args(args: list[str], default):
    """Resolve a target language from args that name only a language.

    Used when the content came from elsewhere (clipboard / pipe). Falls back to
    the default if args are empty or don't name a known language.
    """
    return (resolve(" ".join(args)) if args else default) or default


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
    if info.get("backend") == "remote":
        err.print(f"Backend: [bold]remote[/] [dim]{info.get('base_url', '')}[/]")
    else:
        err.print("Backend: [bold]local[/] [dim](on-device MLX)[/]")
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
        if k == "api_key" and v:
            v = "•" * 8 + " [dim](set)[/]"   # don't print the secret in the clear
        err.print(f"  [cyan]{k}[/] = {v}")
    return 0


def _cmd_download() -> int:
    """Download + load the model in the foreground (shows HF progress)."""
    conf = cfg.load()
    if conf.get("base_url"):
        err.print(
            "[yellow]A remote base URL is set, so there's nothing to download.[/]\n"
            "[dim]Clear it with [bold]t --base-url none[/] to use a local MLX model.[/]"
        )
        return 0
    err.print(f"Downloading / loading model [bold]{conf['model']}[/] … [dim](one-time ~1GB)[/]")
    try:
        from mlx_lm import load

        load(conf["model"])
    except Exception as e:  # noqa: BLE001
        err.print(f"[red]Failed:[/] {e}")
        return 1
    err.print("[green]Model ready.[/]  Try:  [bold]t 你好，世界[/]")
    return 0


DEFAULT_SERVE_HOST = "127.0.0.1"
DEFAULT_SERVE_PORT = 52817   # deliberately obscure; nothing common squats here


def _cmd_serve(rest: list[str]) -> int:
    """Start an OpenAI-compatible HTTP endpoint in front of the daemon.

    Usage: t --serve [PORT | HOST:PORT]
    """
    host, port = DEFAULT_SERVE_HOST, DEFAULT_SERVE_PORT
    if rest:
        spec = rest[0]
        if ":" in spec:
            h, _, p = spec.rpartition(":")
            host = h or host
        else:
            p = spec
        try:
            port = int(p)
        except ValueError:
            err.print(f"[red]Invalid port:[/] {p}")
            return 1

    from . import serve as serve_mod

    err.print("Starting OpenAI-compatible endpoint… [dim](reuses the daemon — one copy of the model)[/]")
    try:
        with err.status("[dim]loading model…[/]", spinner="dots") as st:
            client.ensure_ready(on_status=lambda s: st.update(f"[dim]loading model ({s})…[/]"))
    except client.DaemonError as e:
        err.print(f"[red]{e}[/]")
        err.print(f"[dim]See log: {LOG_PATH}[/]")
        return 1

    def announce(h: str, p: int) -> None:
        base = f"http://{h}:{p}/v1"
        err.print("[green]Ready.[/]  [bold]No API key[/] — localhost only.")
        err.print(f"  Base URL:   [bold cyan]{base}[/]")
        err.print(f"  Chat:       [dim]{base}/chat/completions[/]")
        err.print(f"  Model id:   [dim]{cfg.load()['model']}[/]")
        err.print("[dim]Ctrl+C to stop.[/]")

    try:
        serve_mod.serve(host, port, on_ready=announce)
    except OSError as e:
        err.print(f"[red]Could not bind {host}:{port}:[/] {e}")
        return 1
    err.print("\n[dim]Endpoint stopped.[/]")
    return 0


def _cmd_model(arg: str | None) -> int:
    if not arg:
        err.print(f"Model: [bold]{cfg.load()['model']}[/]")
        return 0
    cfg.set_value("model", arg)
    err.print(f"Model set to [bold]{arg}[/].  Run [bold]t --restart[/] to load it.")
    return 0


def _cmd_base_url(arg: str | None) -> int:
    if arg is None:
        url = cfg.load().get("base_url") or ""
        if url:
            err.print(f"Base URL: [bold]{url}[/]  [dim](remote backend)[/]")
        else:
            err.print("Base URL: [dim]not set — using the local MLX model[/]")
        return 0
    if arg.strip().lower() in ("", "none", "off", "local"):
        cfg.set_value("base_url", "")
        err.print("Cleared base URL — using the [bold]local MLX[/] model.  Run [bold]t --restart[/].")
        return 0
    cfg.set_value("base_url", arg.strip())
    err.print(
        f"Base URL set to [bold]{arg.strip()}[/]  [dim](remote OpenAI-compatible API)[/].\n"
        "Set the model with [bold]t --model <id>[/], a key with [bold]t --api-key <key>[/] if needed, "
        "then [bold]t --restart[/]."
    )
    return 0


def _cmd_api_key(arg: str | None) -> int:
    if arg is None:
        err.print("API key: [bold]set[/]" if cfg.load().get("api_key") else "API key: [dim]not set[/]")
        return 0
    if arg.strip().lower() in ("", "none", "off"):
        cfg.set_value("api_key", "")
        err.print("Cleared API key.")
        return 0
    cfg.set_value("api_key", arg)
    err.print("API key saved.  Run [bold]t --restart[/] if the daemon is running.")
    return 0


def _translate(target, content: str) -> int:
    conf = cfg.load()
    prompt = build_prompt(target, content)
    max_tokens = int(conf["max_tokens"])

    from .render import Stream

    def run_translation(stream: Stream) -> None:
        client.ensure_ready(
            on_status=lambda s: stream.note(f"loading model ({s})...")
        )
        stream.note("translating...", ttl=0.8)
        client.translate_stream(
            prompt,
            max_tokens,
            stream.feed,
            cancel_event=stream.cancel_event,
        )

    try:
        with Stream(out, target_label=target.en_name) as stream:
            if not stream.tty:
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
                stream.run(
                    lambda: client.translate_stream(
                        prompt,
                        max_tokens,
                        stream.feed,
                        cancel_event=stream.cancel_event,
                    )
                )
            else:
                stream.run(lambda: run_translation(stream))
            if stream.cancelled:
                return 130
    except client.DaemonError as e:
        err.print(f"[red]Translation failed:[/] {e}")
        err.print(f"[dim]See log: {LOG_PATH}[/]")
        return 1
    return 0


def main() -> None:
    args = sys.argv[1:]

    # ---- management commands (recognized only as the first token) ------
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
        if a0 in ("--base-url", "base-url"):
            sys.exit(_cmd_base_url(args[1] if len(args) > 1 else None))
        if a0 in ("--api-key", "api-key"):
            sys.exit(_cmd_api_key(args[1] if len(args) > 1 else None))
        if a0 in ("--download", "download"):
            sys.exit(_cmd_download())
        if a0 in ("--serve", "serve"):
            sys.exit(_cmd_serve(args[1:]))

    # `--` ends flag parsing: everything after it is literal text to translate,
    # so text that starts with a dash still works (e.g. t -- --pb, t zh -- --x).
    forced_text = None
    if "--" in args:
        cut = args.index("--")
        forced_text = " ".join(args[cut + 1:])
        args = args[:cut]

    # --pb pulls content from the clipboard; remaining args name the target.
    use_clipboard = "--pb" in args
    if use_clipboard:
        args = [a for a in args if a != "--pb"]

    # ---- translation ----------------------------------------------------
    conf = cfg.load()
    default = resolve(conf["default_lang"]) or LANGUAGES["en"]
    # Explicit text sources (after `--`, or --pb) mean we don't read stdin.
    stdin_data = None if (use_clipboard or forced_text is not None) else _read_stdin()

    if forced_text is not None:
        # Text after `--`; args before it (if any) name the target language.
        content = forced_text
        target = _target_from_args(args, default)
    elif use_clipboard:
        content = _read_clipboard()
        if content is None:
            sys.exit(1)            # error already reported
        target = _target_from_args(args, default)
    elif stdin_data is not None:
        # Piped input is the content; args (if any) name the target language.
        content = stdin_data
        target = _target_from_args(args, default)
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
