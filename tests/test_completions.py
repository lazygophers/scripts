"""shell completion 单元测试。"""

from __future__ import annotations

import pathlib
import tempfile
import unittest

from lib.completions import bash_zsh_completion, completion_map, fish_completion, fish_completion_for, subcommands, tool_names


class TestCompletions(unittest.TestCase):
    def test_tool_names_include_bin_entries(self) -> None:
        names = tool_names(pathlib.Path(__file__).resolve().parent.parent / "bin")
        self.assertIn("inject", names)
        self.assertIn("unsleep", names)
        self.assertIn("merge_master", names)

    def test_subcommands_extract_fire_methods(self) -> None:
        commands = subcommands(pathlib.Path(__file__).resolve().parent.parent / "bin" / "inject")
        self.assertIn("run", commands)
        self.assertIn("show", commands)
        self.assertIn("uninstall", commands)

    def test_completion_map_follows_symlink_target(self) -> None:
        data = completion_map(pathlib.Path(__file__).resolve().parent.parent / "bin")
        self.assertIn("auto", data["merge_master"])

    def test_completion_scripts_include_shell_commands(self) -> None:
        data = {"inject": ["run", "show"], "unsleep": ["timed", "with-command", "with_command"]}
        sh = bash_zsh_completion(data)
        fish = fish_completion(data)
        self.assertIn("complete -F _lazygophers_scripts_complete", sh)
        self.assertLess(sh.index("bashcompinit"), sh.index("complete -F"))
        self.assertIn("inject) opts='run show'", sh)
        self.assertIn("complete -c inject -f -a run", fish)
        self.assertIn("complete -c unsleep -f -a with-command", fish)
        self.assertIn("complete -c inject -l dry-run", fish_completion_for("inject", []))

    def test_bad_script_has_no_subcommands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = pathlib.Path(tmp) / "bad"
            p.write_text("not python )", encoding="utf-8")
            self.assertEqual(subcommands(p), [])


if __name__ == "__main__":
    unittest.main()
