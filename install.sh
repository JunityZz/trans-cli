#!/usr/bin/env bash
#
# Installer for `t` — terminal translator for macOS (Hunyuan-MT / MLX).
#
#   curl -fsSL https://raw.githubusercontent.com/JunityZz/trans-cli/main/install.sh | bash
#
# Environment overrides:
#   TRANS_CLI_REPO        git URL to install from   (default: github JunityZz/trans-cli)
#   TRANS_CLI_REF         git ref / branch / tag    (default: main)
#   TRANS_CLI_MODEL       MLX model id              (default: mlx-community/Hy-MT2-1.8B-4bit)
#   TRANS_CLI_SKIP_MODEL  set to 1 to skip the model download
#
set -euo pipefail

REPO="${TRANS_CLI_REPO:-https://github.com/JunityZz/trans-cli.git}"
REF="${TRANS_CLI_REF:-main}"
MODEL="${TRANS_CLI_MODEL:-mlx-community/Hy-MT2-1.8B-4bit}"

if [ -t 1 ]; then
  bold=$(tput bold 2>/dev/null || true); dim=$(tput dim 2>/dev/null || true)
  green=$(tput setaf 2 2>/dev/null || true); reset=$(tput sgr0 2>/dev/null || true)
else
  bold=""; dim=""; green=""; reset=""
fi
say()  { printf "%s\n" "$*"; }
info() { printf "  ${dim}•${reset} %s\n" "$*"; }
die()  { printf "${bold}error:${reset} %s\n" "$*" >&2; exit 1; }

say "${bold}Installing t — terminal translator${reset}"

# 1. Platform checks ---------------------------------------------------------
[ "$(uname -s)" = "Darwin" ] || die "t currently supports macOS only."
[ "$(uname -m)" = "arm64" ]  || die "t needs an Apple Silicon (arm64) Mac to run MLX."

# 2. Ensure uv ---------------------------------------------------------------
if ! command -v uv >/dev/null 2>&1; then
  info "Installing uv (Python toolchain manager)…"
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
command -v uv >/dev/null 2>&1 || die "uv is not on PATH after install; see https://github.com/astral-sh/uv"

# 3. Install the t CLI (isolated, managed by uv) -----------------------------
info "Installing the t CLI from ${REPO}@${REF}…"
uv tool install --force --python 3.12 "git+${REPO}@${REF}"
uv tool update-shell >/dev/null 2>&1 || true

T_BIN="$(command -v t || echo "$HOME/.local/bin/t")"

# 4. Download the model ------------------------------------------------------
"$T_BIN" --model "$MODEL" >/dev/null 2>&1 || true
if [ "${TRANS_CLI_SKIP_MODEL:-0}" != "1" ]; then
  info "Downloading the translation model (one-time, ~1GB)…"
  "$T_BIN" --download || \
    info "Model download skipped/failed — it will download on your first translation."
fi

# 5. Done --------------------------------------------------------------------
say ""
say "${green}${bold}✓ Installed!${reset}"
say ""
say "  Translate:        ${bold}t 你好，世界${reset}"
say "  Pick a language:  ${bold}t ja Good morning${reset}"
say "  Set default lang: ${bold}t --set-lang zh${reset}"
say "  Help:             ${bold}t --help${reset}"
say ""
if ! command -v t >/dev/null 2>&1; then
  say "${dim}If 't' isn't found, restart your shell or run:${reset}"
  say "  ${bold}export PATH=\"\$HOME/.local/bin:\$PATH\"${reset}"
fi
