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
        if not args:
            _print_help(); sys.exit(0)
        # `t <lang> <text...>`: first token is a target only if it resolves AND
        # there is something after it. Otherwise the whole thing is content.
        if len(args) >= 2 and resolve(args[0]) is not None:
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
