"""Tests for lib.build — 项目类型检测 / Node 包管理器选择 / CheckResult。"""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from lib.build import (
    CheckResult,
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


class TestCheckBuildUnknownProject(unittest.TestCase):
    def test_no_known_type_returns_empty(self) -> None:
        with TemporaryDirectory() as d:
            self.assertEqual(check_build(project_dir=Path(d), log=None), [])


if __name__ == "__main__":
    unittest.main()
