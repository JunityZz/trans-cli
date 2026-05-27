<div align="center">

# `t` — macOS 终端翻译工具

**在终端里即时翻译任何内容，完全本地运行。**

由腾讯 [Hunyuan-MT（Hy-MT2-1.8B）](https://huggingface.co/mlx-community/Hy-MT2-1.8B-4bit) 模型驱动，借助 [Apple MLX](https://github.com/ml-explore/mlx) 在本地运行。无需 API Key，无需联网，数据不出本机。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform: macOS](https://img.shields.io/badge/平台-macOS%20(Apple%20Silicon)-black?logo=apple)](https://www.apple.com/mac/)
[![Model: Hy-MT2](https://img.shields.io/badge/模型-Hy--MT2--1.8B--4bit-ff6f00)](https://huggingface.co/mlx-community/Hy-MT2-1.8B-4bit)
[![Stars](https://img.shields.io/github/stars/JunityZz/trans-cli?style=social)](https://github.com/JunityZz/trans-cli/stargazers)

[English](README.md) · 简体中文

</div>

---

## ✨ 特性

- **`t <内容>`** —— 直接在终端把内容翻译成你设定的默认语言。
- **`t <语言> <内容>`** —— 随时指定目标语言（`t ja 你好`、`t 英文 你好`、`t en 你好`）。
- **支持 33 种语言**，可用 ISO 代码，也可用中文/英文名称——而且模型还能理解很多你没配置的叫法。
- **流式输出，不滚屏。** 文字就地刷新，眼睛不用追着不断下滚的行跑。
- **常驻守护进程。** 模型只加载一次并常驻内存，日常翻译几乎是瞬时的。
- **100% 本地、隐私安全。** 完全在本机通过 MLX 运行，可离线使用。
- **管道友好。** `pbpaste | t` 或 `t 你好 | pbcopy`。

## 📦 一行安装

> 需要 **Apple 芯片的 Mac**（M 系列）。

```bash
curl -fsSL https://raw.githubusercontent.com/JunityZz/trans-cli/main/install.sh | bash
```

脚本会在需要时安装 [`uv`](https://github.com/astral-sh/uv)，创建独立环境，安装 `t`，把它链接到 `PATH`，并预先下载模型。重启终端（或 `source` 你的配置文件）即可使用。

<details>
<summary>手动安装</summary>

```bash
git clone https://github.com/JunityZz/trans-cli.git
cd trans-cli
uv venv --python 3.12
uv pip install -e .
uv run t 你好
```
</details>

## 🚀 使用

```bash
# 翻译成默认语言（开箱默认英文）
t 你好，世界

# 翻译成指定语言
t ja 大家早上好
t fr 我想要一杯咖啡
t 中文 The weather is nice today
t 普通话 The weather is nice today      # 模型能理解的叫法也可以

# 用管道传入内容（适合翻译剪贴板）
pbpaste | t
pbpaste | t zh

# 把结果输出到别处
t es 你好世界 | pbcopy
```

### 设置默认语言

```bash
t --set-lang zh      # 之后 `t <内容>` 都会翻译成中文
t --lang             # 查看当前默认语言
t --langs            # 列出所有支持的语言和别名
```

### 管理模型守护进程

```bash
t --status           # 模型是否已加载？
t --stop             # 卸载模型（释放内存）
t --restart          # 重新加载
t --config           # 查看配置文件和参数
t --model <id>       # 使用其他 MLX 模型
```

## 🌍 支持的语言

中文 · 繁体中文 · 粤语 · 英语 · 法语 · 葡萄牙语 · 西班牙语 · 日语 · 土耳其语 · 俄语 · 阿拉伯语 · 韩语 · 泰语 · 意大利语 · 德语 · 越南语 · 马来语 · 印尼语 · 菲律宾语 · 印地语 · 波兰语 · 捷克语 · 荷兰语 · 高棉语 · 缅甸语 · 波斯语 · 古吉拉特语 · 乌尔都语 · 泰卢固语 · 马拉地语 · 希伯来语 · 孟加拉语 · 泰米尔语 · 乌克兰语 · 藏语 · 哈萨克语 · 蒙古语 · 维吾尔语

运行 `t --langs` 查看每种语言可接受的全部别名。

## ⚙️ 工作原理

```
t（客户端） ──unix socket──▶  t-daemon（守护进程） ──▶  MLX + Hy-MT2-1.8B-4bit
  解析目标语言、                  让模型常驻内存、
  构造提示词、                    流式返回 token
  渲染流式输出
```

第一次翻译会自动启动一个轻量守护进程，加载一次模型（约 1 GB）。之后的翻译都复用它，所以会立即返回。守护进程在闲置 30 分钟后（可配置）自动退出以释放内存。

配置文件位于 `~/.t-cli/config.json`。

## 🧠 模型

[`mlx-community/Hy-MT2-1.8B-4bit`](https://huggingface.co/mlx-community/Hy-MT2-1.8B-4bit) —— 腾讯 Hunyuan-MT 翻译模型的 4-bit 量化 MLX 版本。磁盘占用约 1 GB，在 Apple 芯片上运行流畅。

## 📈 Star 趋势

<a href="https://star-history.com/#JunityZz/trans-cli&Date">
  <img src="https://api.star-history.com/svg?repos=JunityZz/trans-cli&type=Date" width="600" alt="Star History Chart">
</a>

## 🤝 参与贡献

欢迎提 Issue 和 PR！可以从 [`install.sh`](install.sh) 和 `src/tcli/` 包开始了解代码。

## 📄 许可证

[MIT](LICENSE)
