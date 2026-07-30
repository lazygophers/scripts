"""Tests for lib.build — 项目类型检测 / Node 包管理器选择 / CheckResult。"""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from lib.build import (
    CheckResult,
    _check_go_project,
    _detect_node_pkg_manager,
    _detect_project_types,
    check_build,
)


class TestCheckResult(unittest.TestCase):
    def test_failed_property(self) -> None:
        self.assertTrue(CheckResult("a", "fail").failed)
        self.assertFalse(CheckResult("a", "ok").failed)
        self.assertFalse(CheckResult("a", "warn").failed)


class TestDetectProjectTypes(unittest.TestCase):
    def test_go_detected(self) -> None:
        with TemporaryDirectory() as d:
            (Path(d) / "go.mod").write_text("module x\ngo 1.21\n")
            types = [t.name for t in _detect_project_types(Path(d))]
            self.assertEqual(types, ["Go"])

    def test_node_detected(self) -> None:
        with TemporaryDirectory() as d:
            (Path(d) / "package.json").write_text("{}")
            types = [t.name for t in _detect_project_types(Path(d))]
            self.assertEqual(types, ["Node.js"])

    def test_rust_detected(self) -> None:
        with TemporaryDirectory() as d:
            (Path(d) / "Cargo.toml").write_text("[package]\nname=\"x\"\nversion=\"0.1\"\n")
            types = [t.name for t in _detect_project_types(Path(d))]
            self.assertEqual(types, ["Rust"])

    def test_mixed_go_node(self) -> None:
        with TemporaryDirectory() as d:
            (Path(d) / "go.mod").write_text("module x\ngo 1.21\n")
            (Path(d) / "package.json").write_text("{}")
            types = [t.name for t in _detect_project_types(Path(d))]
            self.assertEqual(types, ["Go", "Node.js"])

    def test_unknown_project_empty(self) -> None:
        with TemporaryDirectory() as d:
            self.assertEqual(_detect_project_types(Path(d)), [])


class TestNodePackageManager(unittest.TestCase):
    def test_bun_lockb(self) -> None:
        with TemporaryDirectory() as d:
            (Path(d) / "bun.lockb").write_bytes(b"")
            self.assertEqual(_detect_node_pkg_manager(Path(d)), "bun")

    def test_yarn_lock(self) -> None:
        with TemporaryDirectory() as d:
            (Path(d) / "yarn.lock").write_text("")
            self.assertEqual(_detect_node_pkg_manager(Path(d)), "yarn")

    def test_pnpm_lock(self) -> None:
        with TemporaryDirectory() as d:
            (Path(d) / "pnpm-lock.yaml").write_text("")
            self.assertEqual(_detect_node_pkg_manager(Path(d)), "pnpm")

    def test_npm_lock(self) -> None:
        with TemporaryDirectory() as d:
            (Path(d) / "package-lock.json").write_text("{}")
            self.assertEqual(_detect_node_pkg_manager(Path(d)), "npm")

    def test_bun_priority_over_yarn(self) -> None:
        with TemporaryDirectory() as d:
            (Path(d) / "bun.lockb").write_bytes(b"")
            (Path(d) / "yarn.lock").write_text("")
            self.assertEqual(_detect_node_pkg_manager(Path(d)), "bun")


class TestCheckGoProject(unittest.TestCase):
    @patch("lib.build.run_no_capture")
    def test_go_mod_tidy_runs_before_build(self, mock_run_no_capture) -> None:
        mock_run_no_capture.return_value = 0
        with TemporaryDirectory() as d:
            root = Path(d)
            (root / "go.mod").write_text("module x\ngo 1.21\n")
            (root / "main.go").write_text("package main\nfunc main() {}\n")

            results = _check_go_project(root)

        calls = [call.args[0] for call in mock_run_no_capture.call_args_list]
        self.assertEqual(calls[0], ["go", "mod", "tidy"])
        self.assertEqual(calls[1][:3], ["go", "build", "-v"])
        self.assertEqual([r.name for r in results], ["go mod tidy", "go build: ."])

    @patch("lib.build.run_no_capture")
    def test_go_mod_tidy_failure_skips_build(self, mock_run_no_capture) -> None:
        mock_run_no_capture.return_value = 1
        with TemporaryDirectory() as d:
            root = Path(d)
            (root / "go.mod").write_text("module x\ngo 1.21\n")
            (root / "main.go").write_text("package main\nfunc main() {}\n")

            results = _check_go_project(root)

        self.assertEqual(mock_run_no_capture.call_count, 1)
        self.assertEqual(results[0].name, "go mod tidy")
        self.assertEqual(results[0].status, "fail")

    @patch("lib.build.run_no_capture")
    def test_go_work_skips_go_mod_tidy(self, mock_run_no_capture) -> None:
        mock_run_no_capture.return_value = 0
        with TemporaryDirectory() as d:
            root = Path(d)
            (root / "go.work").write_text("go 1.21\n\nuse (\n\t./svc/a\n\t./svc/b\n)\n")
            (root / "main.go").write_text("package main\nfunc main() {}\n")
            for name in ("a", "b"):
                mod = root / "svc" / name
                mod.mkdir(parents=True)
                (mod / "go.mod").write_text(f"module example.com/{name}\ngo 1.21\n")

            results = _check_go_project(root)

        calls = [call.args[0] for call in mock_run_no_capture.call_args_list]
        self.assertNotIn(["go", "mod", "tidy"], calls)
        self.assertEqual(results[0].name, "go mod tidy")
        self.assertEqual(results[0].status, "warn")


class TestCheckBuildUnknownProject(unittest.TestCase):
    def test_no_known_type_returns_empty(self) -> None:
        with TemporaryDirectory() as d:
            self.assertEqual(check_build(project_dir=Path(d), log=None), [])


if __name__ == "__main__":
    unittest.main()
