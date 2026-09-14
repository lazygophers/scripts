"""lib/i18n.py：语言解析、优先级、回落，以及两个语言文件必须一一对应。

最重要的是 `test_every_locale_has_the_same_keys`：少一条翻译的后果是用户在某个语言下
看到英文 key，这条测试就是为了让那种改动当场变红。
"""

from __future__ import annotations

import json
import unittest

from lib import i18n


class Normalize(unittest.TestCase):
    """真实环境变量里会出现的写法，各自归一到什么。"""

    def test_real_env_values(self):
        cases = {
            "zh_CN.UTF-8": "zh_CN",
            "zh-CN": "zh_CN",
            "zh": "zh_CN",
            "zh_TW": "zh_CN",          # 没有繁体文件，回落到同语族
            "zh_CN@pinyin": "zh_CN",
            "en_US.UTF-8": "en",
            "en": "en",
            "EN_us": "en",
            "en_US:zh_CN": "en",       # LANGUAGE 的多值形态，取第一个
        }
        for raw, want in cases.items():
            self.assertEqual(i18n.normalize(raw), want, raw)

    def test_unrecognised_values_are_none(self):
        for raw in ("", "   ", None, "C", "POSIX", "klingon", "!!!", ".UTF-8"):
            self.assertIsNone(i18n.normalize(raw), repr(raw))


class EnvOrder(unittest.TestCase):
    def test_lc_all_beats_lang_beats_language(self):
        env = {"LC_ALL": "en_US.UTF-8", "LANG": "zh_CN.UTF-8", "LANGUAGE": "zh_CN"}
        self.assertEqual(i18n.lang_from_env(env), "en")
        env.pop("LC_ALL")
        self.assertEqual(i18n.lang_from_env(env), "zh_CN")

    def test_unrecognised_var_falls_through_to_the_next(self):
        # LC_ALL=C 是真实常见值，不能因此就把 LANG 说的话吞掉
        self.assertEqual(i18n.lang_from_env({"LC_ALL": "C", "LANG": "en_US.UTF-8"}), "en")

    def test_nothing_set(self):
        self.assertIsNone(i18n.lang_from_env({}))


class Priority(unittest.TestCase):
    """--lang > 配置文件 > 环境变量 > zh_CN。"""

    ENV = {"LANG": "en_US.UTF-8"}

    def test_flag_beats_config(self):
        self.assertEqual(i18n.resolve_lang("zh_CN", "en", self.ENV), "zh_CN")

    def test_config_beats_env(self):
        self.assertEqual(i18n.resolve_lang("", "zh_CN", self.ENV), "zh_CN")

    def test_env_used_when_nothing_else_says(self):
        self.assertEqual(i18n.resolve_lang("", "", self.ENV), "en")

    def test_default_is_zh_cn(self):
        self.assertEqual(i18n.resolve_lang("", "", {}), "zh_CN")

    def test_unrecognised_layer_is_skipped_not_fatal(self):
        self.assertEqual(i18n.resolve_lang("klingon", "", self.ENV), "en")
        self.assertEqual(i18n.resolve_lang("", "klingon", self.ENV), "en")


class ConsumeLang(unittest.TestCase):
    def test_space_form(self):
        self.assertEqual(i18n.consume_lang(["browse", "--lang", "en", "stop"]),
                         (["browse", "stop"], "en"))

    def test_equals_form(self):
        self.assertEqual(i18n.consume_lang(["browse", "--lang=en", "stop"]),
                         (["browse", "stop"], "en"))

    def test_absent(self):
        self.assertEqual(i18n.consume_lang(["browse", "stop"]), (["browse", "stop"], ""))

    def test_last_one_wins(self):
        self.assertEqual(i18n.consume_lang(["browse", "--lang", "en", "--lang", "zh_CN"]),
                         (["browse"], "zh_CN"))


class Lookup(unittest.TestCase):
    def setUp(self):
        self.addCleanup(i18n.set_lang, i18n.DEFAULT_LANG)

    def test_missing_key_returns_the_key_itself(self):
        for lang in i18n.LANGS:
            i18n.set_lang(lang)
            self.assertEqual(i18n.t("no.such.key.at.all"), "no.such.key.at.all")

    def test_missing_key_does_not_raise_even_with_kwargs(self):
        self.assertEqual(i18n.t("no.such.key", sock="/tmp/x"), "no.such.key")

    def test_bad_placeholder_returns_unformatted_text(self):
        # 文案里要 {sock}，调用方给了别的：宁可少填一个占位符，也不让命令崩掉
        self.assertIn("{sock}", i18n.t("daemon.running", nope=1))

    def test_formatting(self):
        i18n.set_lang("zh_CN")
        self.assertEqual(i18n.t("daemon.running", sock="/tmp/x.sock"), "daemon 在跑：/tmp/x.sock")
        i18n.set_lang("en")
        self.assertEqual(i18n.t("daemon.running", sock="/tmp/x.sock"), "daemon is running: /tmp/x.sock")

    def test_unknown_lang_falls_back_to_default(self):
        i18n.set_lang("klingon")
        self.assertEqual(i18n.get_lang(), "zh_CN")


class Catalogs(unittest.TestCase):
    def test_every_locale_has_the_same_keys(self):
        """少一条翻译 = 用户在那个语言下看到英文 key。改坏任一边这里都要红。"""
        base = sorted(i18n.catalog(i18n.DEFAULT_LANG))
        self.assertTrue(base, "zh_CN 语言文件是空的")
        for lang in i18n.LANGS:
            self.assertEqual(sorted(i18n.catalog(lang)), base, f"{lang} 的 key 集合和 zh_CN 不一致")

    def test_no_empty_message(self):
        for lang in i18n.LANGS:
            for key, text in i18n.catalog(lang).items():
                self.assertTrue(text.strip(), f"{lang}/{key} 是空的")

    def test_locale_files_are_on_disk_and_valid_json(self):
        for lang in i18n.LANGS:
            path = i18n.LOCALES_DIR / f"{lang}.json"
            self.assertTrue(path.is_file(), path)
            self.assertIsInstance(json.loads(path.read_text(encoding="utf-8")), dict)

    def test_missing_locale_file_is_an_empty_catalog_not_a_crash(self):
        self.assertEqual(i18n.catalog("nope_XX"), {})


class UsedKeys(unittest.TestCase):
    """代码里 `t("x")` 引用的每个 key 都得真的存在。"""

    def test_every_key_used_in_code_exists(self):
        import re

        root = i18n.LOCALES_DIR.parent
        known = set(i18n.catalog(i18n.DEFAULT_LANG))
        used = set()
        for path in (root / "cli" / "browse.py", root / "browse_install.py"):
            for match in re.finditer(r'\bt\(\s*"([^"]+)"', path.read_text(encoding="utf-8")):
                used.add(match.group(1))
        # 三元里按条件选 key 的那两处不是字面量参数
        used |= {"daemon.running", "daemon.not_running_at"}
        self.assertGreater(len(used), 30, f"只扫到 {len(used)} 个 key，正则大概失效了")
        self.assertEqual(used - known, set())


class CliPriority(unittest.TestCase):
    """`browse` 真跑一遍：`--lang` / `browse.yaml` / 环境变量三层的实际生效顺序。"""

    def setUp(self):
        import contextlib
        import io
        import os
        import pathlib
        import tempfile

        from lib.cli import browse

        self.browse = browse
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = pathlib.Path(self.tmp.name)
        self.addCleanup(i18n.set_lang, i18n.DEFAULT_LANG)

        self._env = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(self._env)))
        os.environ.update(HOME=str(self.home), LC_ALL="en_US.UTF-8", SCRIPTS_NO_SAY="1")
        os.environ.pop("LANG", None)
        os.environ.pop("LANGUAGE", None)
        self._io = (contextlib, io)

    def write_config(self, lang: str) -> None:
        path = self.home / ".config" / "lazygophers" / "scripts" / "browse.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"lang: {lang}\n", encoding="utf-8")

    def help_out(self, *argv: str) -> str:
        contextlib, io = self._io
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            self.assertEqual(self.browse.main(["browse", *argv, "--help"]), 0)
        return buf.getvalue()

    def test_env_alone(self):
        self.assertIn("drive a browser extension", self.help_out())

    def test_config_beats_env(self):
        self.write_config("zh_CN")
        self.assertIn("用命令行驱动浏览器扩展", self.help_out())

    def test_flag_beats_config(self):
        self.write_config("zh_CN")
        self.assertIn("drive a browser extension", self.help_out("--lang", "en"))

    def test_broken_config_does_not_break_the_command(self):
        path = self.home / ".config" / "lazygophers" / "scripts" / "browse.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("this: is: not: yaml:\n", encoding="utf-8")
        self.assertIn("drive a browser extension", self.help_out())


if __name__ == "__main__":
    unittest.main()
