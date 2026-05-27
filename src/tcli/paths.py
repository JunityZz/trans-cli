"""Filesystem locations and constants shared across the package."""

import os
from pathlib import Path

# Everything (config + runtime files) lives under one predictable directory.
# Override with TCLI_HOME for tests or alternate installs.
BASE_DIR = Path(os.environ.get("TCLI_HOME", str(Path.home() / ".t-cli")))

CONFIG_PATH = BASE_DIR / "config.json"
SOCK_PATH = BASE_DIR / "daemon.sock"
PID_PATH = BASE_DIR / "daemon.pid"
LOG_PATH = BASE_DIR / "daemon.log"

DEFAULT_MODEL = "mlx-community/Hy-MT2-1.8B-4bit"


def ensure_base() -> None:
    BASE_DIR.mkdir(parents=True, exist_ok=True)
