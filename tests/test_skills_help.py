#!/usr/bin/env python3
"""Tests for AI-facing --skills output."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib.skills_help import consume_skills, render_skills

REPO_ROOT = Path(__file__).resolve().parent.parent
BIN_DIR = REPO_ROOT / "bin"


class TestRenderSkills(unittest.TestCase):
    def test_known_command_includes_ai_audience_and_command_guidance(self) -> None:
        out = render_skills("cicd", "轮询当前分支 CI/CD")
        self.assertIn("Audience: AI agents", out)
        self.assertIn("cicd run", out)
        self.assertIn("cicd id", out)
        self.assertIn("cicd fail", out)
        self.assertIn("--help", out)

    def test_archery_guidance_prioritizes_common_sql_and_workflow_examples(self) -> None:
        out = render_skills("archery", "Archery SQL 平台命令行客户端")
        self.assertIn("archery query execute 'select 1'", out)
        self.assertIn("archery workflow check", out)
        self.assertIn("archery workflow submit", out)


class TestConsumeSkills(unittest.TestCase):
    def test_passthrough_without_flag(self) -> None:
        argv = ["bin/x", "arg"]
        self.assertEqual(consume_skills(argv, "desc"), argv)

    def test_prints_and_exits_zero(self) -> None:
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf), self.assertRaises(SystemExit) as ctx:
            consume_skills(["bin/cicd", "--skills"], "desc")
        self.assertEqual(ctx.exception.code, 0)
        self.assertIn("# cicd skills", buf.getvalue())


class TestAllBinsSkillsFlag(unittest.TestCase):
    def _run_shell(self, name: str) -> subprocess.CompletedProcess:
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": tempfile.mkdtemp(prefix="skills_home_"),
            "PYTHONPATH": str(REPO_ROOT),
            "LC_ALL": "en_US.UTF-8",
            "TERM": "dumb",
            "SCRIPTS_NO_SAY": "1",
        }
        cwd = tempfile.mkdtemp(prefix="skills_cwd_")
        if name in {"disable-ipv6", "enable-ipv6"}:
            cmd = [str(BIN_DIR / name), "--skills"]
        else:
            cmd = [sys.executable, str(BIN_DIR / name), "--skills"]
        return subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=cwd, timeout=10)

    def test_all_bins_support_skills(self) -> None:
        failures = []
        for p in sorted(BIN_DIR.iterdir()):
            if p.name.startswith(".") or not p.is_file() or p.name == "__pycache__":
                continue
            with self.subTest(shell=p.name):
                run = self._run_shell(p.name)
                out = run.stdout + run.stderr
                if run.returncode != 0 or "Audience: AI agents" not in out:
                    failures.append((p.name, run.returncode, out[:200]))
        if failures:
            msg = "\n".join(f"{name}: exit={code} output={out}" for name, code, out in failures)
            self.fail(f"--skills failed:\n{msg}")


if __name__ == "__main__":
    unittest.main()
