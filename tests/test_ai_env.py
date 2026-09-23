"""lib/ai_env.py：AI shell 环境检测。"""
import unittest

from lib.ai_env import ai_tool_name, is_ai_shell_env


class TestAiEnv(unittest.TestCase):
    def test_claude_code_marker(self):
        self.assertEqual(ai_tool_name({"CLAUDECODE": "1"}), "claude-code")
        self.assertTrue(is_ai_shell_env({"CLAUDECODE": "1"}))

    def test_codex_markers(self):
        self.assertEqual(ai_tool_name({"CODEX_SANDBOX": "seatbelt"}), "codex")
        self.assertTrue(is_ai_shell_env({"CODEX_SANDBOX_NETWORK_DISABLED": "1"}))

    def test_claude_code_requires_value_one(self):
        # CLAUDECODE=0 / 空 = 不算（文档语义是布尔标记）
        self.assertIsNone(ai_tool_name({"CLAUDECODE": "0"}))
        self.assertIsNone(ai_tool_name({"CLAUDECODE": ""}))

    def test_plain_env_is_human(self):
        self.assertIsNone(ai_tool_name({"TERM": "xterm-256color", "SHELL": "/bin/zsh"}))
        self.assertFalse(is_ai_shell_env({}))

    def test_inherited_noise_does_not_trigger(self):
        # WARP_* / TERM_PROGRAM 等被继承的终端变量不代表 AI 派生
        env = {"WARP_IS_LOCAL_SHELL_SESSION": "1", "TERM_PROGRAM": "WarpTerminal"}
        self.assertFalse(is_ai_shell_env(env))


if __name__ == "__main__":
    unittest.main()
