"""用户可见文案的多语言查表。默认 zh_CN，首批只有 zh_CN / en。

语言文件是 `lib/locales/<lang>.json`，一层平铺的 key -> 文案。不用 gettext：那套要
把 .po 编译成 .mo，为两种语言上一整条工具链不划算；仓库里 `docs/i18n.json` 已经是
JSON 字典的先例。

三条不能省的性质：

- **查不到不炸**：当前语言没有这个 key 就回落 zh_CN，zh_CN 也没有就把 key 原样返回。
  线上少一条翻译只该丢一句人话，不该让命令挂掉。
- **format 失败也不炸**：文案里的占位符和调用方给的 kwargs 对不上时返回未格式化的
  原文，同理。
- **语言解析吃真实的环境变量值**：`zh_CN.UTF-8`、`zh-CN`、`en_US.UTF-8`、
  `zh_CN:en`（LANGUAGE 的多值形态）都要归一到 LANGS 里的某一个。
"""

from __future__ import annotations

import json
import os
import pathlib
import re

DEFAULT_LANG = "zh_CN"
# 顺序即回落时的匹配顺序，也是 `--lang` 的可选值。
LANGS = ("zh_CN", "en")

LOCALES_DIR = pathlib.Path(__file__).resolve().parent / "locales"

# 环境变量的优先级，POSIX 惯例：LC_ALL 压一切，LANGUAGE 只在前两个都没说话时才看。
ENV_VARS = ("LC_ALL", "LANG", "LANGUAGE")

# `zh_CN.UTF-8` / `zh_CN@modifier` / `zh_CN:en` 里真正的语言标签只到第一个分隔符为止。
_SEP_RE = re.compile(r"[.@:]")

_LANG = DEFAULT_LANG
_CATALOGS: dict[str, dict] = {}


def normalize(raw) -> str | None:
    """把一个语言串归一到 LANGS 里的某一个，认不出来返回 None。

    `zh_CN.UTF-8` / `zh-CN` / `zh` -> `zh_CN`，`en_US.UTF-8` -> `en`，
    `C` / `POSIX` / 空 / 乱值 -> None。
    """
    text = str(raw or "").strip()
    if not text:
        return None
    tag = _SEP_RE.split(text.replace("-", "_"))[0]
    if not tag:
        return None
    for lang in LANGS:
        if tag.lower() == lang.lower():
            return lang
    primary = tag.split("_")[0].lower()
    for lang in LANGS:
        if lang.split("_")[0].lower() == primary:
            return lang
    return None


def lang_from_env(env=None) -> str | None:
    """按 ENV_VARS 的顺序取第一个能认出来的语言。"""
    env = os.environ if env is None else env
    for name in ENV_VARS:
        lang = normalize(env.get(name))
        if lang is not None:
            return lang
    return None


def resolve_lang(explicit=None, configured=None, env=None) -> str:
    """`--lang` > 配置文件 > 环境变量 > zh_CN。认不出来的那一层直接跳过，不报错。"""
    return (normalize(explicit) or normalize(configured)
            or lang_from_env(env) or DEFAULT_LANG)


def set_lang(lang: str) -> None:
    global _LANG
    _LANG = normalize(lang) or DEFAULT_LANG


def get_lang() -> str:
    return _LANG


def catalog(lang: str) -> dict:
    """读一个语言文件，读过就缓存。文件不在（或坏了）当成空表，交给回落。"""
    if lang not in _CATALOGS:
        path = LOCALES_DIR / f"{lang}.json"
        try:
            _CATALOGS[lang] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _CATALOGS[lang] = {}
    return _CATALOGS[lang]


def t(key: str, **kwargs) -> str:
    """查一条文案并套上参数。查不到就回落 zh_CN，再查不到就返回 key 本身。"""
    text = catalog(_LANG).get(key)
    if text is None:
        text = catalog(DEFAULT_LANG).get(key, key)
    if not kwargs:
        return text
    try:
        return text.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        return text


def consume_lang(argv: list[str]) -> tuple[list[str], str]:
    """剥离 argv 里的 `--lang <值>` / `--lang=<值>`，返回 (剩余 argv, 值)。

    和 `consume_no_say` / `consume_debug` 同一形状：在 argparse / 自己的解析之前调用，
    这样每条子命令都不用单独注册这个参数。没写就返回空串。
    """
    rest = [argv[0]] if argv else []
    value = ""
    i = 1
    while i < len(argv):
        token = argv[i]
        if token == "--lang" and i + 1 < len(argv):
            value = argv[i + 1]
            i += 2
            continue
        if token.startswith("--lang="):
            value = token[len("--lang="):]
            i += 1
            continue
        rest.append(token)
        i += 1
    return rest, value
