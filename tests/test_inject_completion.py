"""inject completion 单元测试。"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import pathlib
import tempfile
import unittest
from unittest.mock import patch


class ReporterStub:
    def ok(self, *_args, **_kwargs) -> None:
        pass

    def step(self, *_args, **_kwargs) -> None:
        pass

    def panel(self, *_args, **_kwargs) -> None:
        pass


def load_inject():
    path = pathlib.Path(__file__).resolve().parent.parent / "bin" / "inject"
    loader = importlib.machinery.SourceFileLoader("inject_mod", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载 bin/inject")
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class TestInjectCompletion(unittest.TestCase):
    def test_scripts_sh_sources_completion_file(self) -> None:
        inject = load_inject()
        content = inject._build_scripts_sh(pathlib.Path("/tmp/bin"))
        self.assertIn('export PATH="/tmp/bin:$PATH"', content)
        self.assertIn("completions.sh", content)

    def test_prompt_keeps_existing_config_on_skip(self) -> None:
        inject = load_inject()
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            inject._SCRIPTS_SH = root / "scripts.sh"
            inject._SCRIPTS_SH.write_text('export LAZYGOPHERS_SCRIPTS_BASE_URL="http://old"\nexport LAZYGOPHERS_SCRIPTS_TOKEN="abc"\n', encoding="utf-8")
            with patch("lib.ui.ask_confirm", return_value=False), patch("lib.ui.ask_text") as ask_text:
                url, token = inject._prompt_ai_config(ReporterStub())
            self.assertEqual((url, token), ("http://old", "abc"))
            ask_text.assert_not_called()

    def test_prompt_keeps_existing_config_on_cancel(self) -> None:
        inject = load_inject()
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            inject._SCRIPTS_SH = root / "scripts.sh"
            inject._SCRIPTS_SH.write_text('export LAZYGOPHERS_SCRIPTS_BASE_URL=http://old\nexport LAZYGOPHERS_SCRIPTS_TOKEN=abc\n', encoding="utf-8")
            with patch("lib.ui.ask_confirm", return_value=None), patch("lib.ui.ask_text") as ask_text:
                url, token = inject._prompt_ai_config(ReporterStub())
            self.assertEqual((url, token), ("http://old", "abc"))
            ask_text.assert_not_called()

    def test_write_completion_files_writes_bash_zsh_and_fish(self) -> None:
        inject = load_inject()
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            inject._SCRIPTS_DIR = root / "scripts"
            inject._COMPLETIONS_SH = inject._SCRIPTS_DIR / "completions.sh"
            inject._FISH_CONFIG_DIR = root / "fish"
            inject._FISH_COMPLETIONS_DIR = inject._FISH_CONFIG_DIR / "completions"

            inject._write_completion_files(ReporterStub())

            self.assertIn("complete -F _lazygophers_scripts_complete", inject._COMPLETIONS_SH.read_text())
            self.assertIn("compinit", inject._COMPLETIONS_SH.read_text())
            self.assertIn("complete -c inject -f -a run", (inject._FISH_COMPLETIONS_DIR / "inject.fish").read_text())
            self.assertIn("complete -c unsleep", (inject._FISH_COMPLETIONS_DIR / "unsleep.fish").read_text())

if __name__ == "__main__":
    unittest.main()
