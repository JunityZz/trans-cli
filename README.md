<div align="center">

# `t` — terminal translator for macOS

**Translate anything in your terminal, on-device, instantly.**

Powered by Tencent's [Hunyuan-MT (Hy-MT2-1.8B)](https://huggingface.co/mlx-community/Hy-MT2-1.8B-4bit) running locally with [Apple MLX](https://github.com/ml-explore/mlx). No API keys, no network, no data leaving your Mac.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform: macOS](https://img.shields.io/badge/platform-macOS%20(Apple%20Silicon)-black?logo=apple)](https://www.apple.com/mac/)
[![Model: Hy-MT2](https://img.shields.io/badge/model-Hy--MT2--1.8B--4bit-ff6f00)](https://huggingface.co/mlx-community/Hy-MT2-1.8B-4bit)
[![Stars](https://img.shields.io/github/stars/JunityZz/trans-cli?style=social)](https://github.com/JunityZz/trans-cli/stargazers)

English · [简体中文](README.zh-CN.md)

</div>

---

## ✨ Features

- **`t <text>`** — translate straight from your terminal into your default language.
- **`t <lang> <text>`** — pick a target language on the fly (`t ja hello`, `t 中文 hello`, `t 普通话 hello`).
- **33 languages**, addressed by ISO code *or* their English/Chinese names — and the model understands plenty of names you don't even configure.
- **Streaming output that doesn't scroll.** Tokens render in place so your eyes never chase a moving line.
- **Warm daemon.** The model loads once and stays resident, so everyday translations feel instant.
- **100% local & private.** Runs entirely on-device via MLX. Works offline.
- **Pipe-friendly.** `pbpaste | t` or `t hello | pbcopy`.

## 📦 Install (one line)

> Requires an **Apple Silicon Mac** (M-series).

```bash
curl -fsSL https://raw.githubusercontent.com/JunityZz/trans-cli/main/install.sh | bash
```

This installs [`uv`](https://github.com/astral-sh/uv) if needed, creates an isolated environment, installs `t`, links it onto your `PATH`, and pre-downloads the model. Restart your shell (or `source` your profile) and you're ready.

<details>
<summary>Manual install</summary>

```bash
git clone https://github.com/JunityZz/trans-cli.git
cd trans-cli
uv venv --python 3.12
uv pip install -e .
# add the venv's bin to PATH, or:
uv run t hello
```
</details>

## 🚀 Usage

```bash
# Translate into your default language (English out of the box)
t 你好，世界

# Translate into a specific language
t ja Good morning, everyone
t fr I would like a coffee
t 中文 The weather is nice today
t 普通话 The weather is nice today      # names the model understands work too

# Pipe text in (great for the clipboard)
pbpaste | t
pbpaste | t zh

# Send the result somewhere
t es hello world | pbcopy
```

### Set your default language

```bash
t --set-lang zh      # now `t <text>` outputs Chinese
t --lang             # show current default
t --langs            # list all supported languages and aliases
```

### Manage the model daemon

```bash
t --status           # is the model loaded?
t --stop             # unload it (frees memory)
t --restart          # reload it
t --config           # show config file + values
t --model <id>       # use a different MLX model
```

## 🌍 Supported languages

Chinese · Traditional Chinese · Cantonese · English · French · Portuguese · Spanish · Japanese · Turkish · Russian · Arabic · Korean · Thai · Italian · German · Vietnamese · Malay · Indonesian · Filipino · Hindi · Polish · Czech · Dutch · Khmer · Burmese · Persian · Gujarati · Urdu · Telugu · Marathi · Hebrew · Bengali · Tamil · Ukrainian · Tibetan · Kazakh · Mongolian · Uyghur

Run `t --langs` to see every accepted alias.

## ⚙️ How it works

```
t  (client)  ──unix socket──▶  t-daemon  ──▶  MLX + Hy-MT2-1.8B-4bit
   parses the target lang,        keeps the model resident,
   builds the prompt,             streams tokens back
   renders streaming output
```

The first translation auto-starts a small background daemon that loads the model
once (~1 GB). Subsequent translations reuse it, so they return immediately. The
daemon exits on its own after 30 minutes idle (configurable) to free memory.

Config lives at `~/.t-cli/config.json`.

## 🧠 Model

[`mlx-community/Hy-MT2-1.8B-4bit`](https://huggingface.co/mlx-community/Hy-MT2-1.8B-4bit) — a 4-bit quantized MLX build of Tencent's Hunyuan-MT translation model. ~1 GB on disk, runs comfortably on Apple Silicon.

## 📈 Star history

<a href="https://star-history.com/#JunityZz/trans-cli&Date">
  <img src="https://api.star-history.com/svg?repos=JunityZz/trans-cli&type=Date" width="600" alt="Star History Chart">
</a>

## 🤝 Contributing

Issues and PRs welcome! See [`install.sh`](install.sh) and the `src/tcli/` package to get oriented.

## 📄 License

[MIT](LICENSE)
