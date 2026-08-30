"""lib/build.py 的各语言检查点测试。

tests/test_build.py 覆盖的是 Go main 包探测那一小块；这里把 run/run_no_capture
换成假的，用临时目录搭出 Go / Rust / Python / Java / C / Node 项目骨架，验证
每条检查点选了什么命令、结果状态怎么归类，以及 check_build / run_checkwork
的串行、并行、汇总路径。全程不真的编译任何东西。
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib import build  # noqa: E402
from lib.build import BuildError, CheckResult  # noqa: E402


class TempProject(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.dir = Path(self._td.name).resolve()
        self.addCleanup(self._td.cleanup)

    def write(self, rel: str, text: str = "") -> Path:
        p = self.dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p


class TestCounters(unittest.TestCase):
    def test_issue_lines_skips_blank_and_summary(self) -> None:
        out = "a.py:1: E501\n\nb.py:2: F401\nFound 2 errors\n"
        self.assertEqual(build._count_issue_lines(out), 2)

    def test_issue_lines_on_empty_input(self) -> None:
        self.assertEqual(build._count_issue_lines(""), 0)

    def test_type_errors_matches_case_insensitively(self) -> None:
        out = "src/a.ts(1,1): error TS2304\nnote: fine\nERROR: boom\n"
        self.assertEqual(build._count_type_errors(out), 2)

    def test_check_result_failed_property(self) -> None:
        self.assertTrue(CheckResult("x", "fail").failed)
        self.assertFalse(CheckResult("x", "warn").failed)


class TestHaveAndRunVerbose(unittest.TestCase):
    def test_have_delegates_to_which(self) -> None:
        with mock.patch("shutil.which", return_value="/usr/bin/go"):
            self.assertTrue(build._have("go"))
        with mock.patch("shutil.which", return_value=None):
            self.assertFalse(build._have("go"))

    def test_run_verbose_logs_the_command(self) -> None:
        log = mock.Mock()
        with mock.patch.object(build, "run_no_capture", return_value=0) as rnc:
            rc = build._run_verbose(["cargo", "check"], cwd="/tmp", log=log)
        self.assertEqual(rc, 0)
        rnc.assert_called_once_with(["cargo", "check"], cwd="/tmp")
        self.assertIn("cargo check", log.call_args[0][0])

    def test_run_verbose_without_log(self) -> None:
        with mock.patch.object(build, "run_no_capture", return_value=3):
            self.assertEqual(build._run_verbose(["x"]), 3)


class TestGoHelpers(TempProject):
    def test_main_package_detection_skips_comments(self) -> None:
        self.write("main.go", "// 注释\n/* 块 */\n\npackage main\n\nfunc main() {}\n")
        self.assertTrue(build._is_main_package(self.dir))

    def test_non_main_package(self) -> None:
        self.write("lib.go", "package lib\n")
        self.assertFalse(build._is_main_package(self.dir))

    def test_dir_without_go_files(self) -> None:
        self.assertFalse(build._is_main_package(self.dir))

    def test_go_file_without_package_line(self) -> None:
        self.write("x.go", "// 只有注释\n")
        self.assertFalse(build._is_main_package(self.dir))

    def test_excluded_projects(self) -> None:
        self.assertTrue(build._is_excluded_project("pay-core"))
        self.assertTrue(build._is_excluded_project("user-dao-svc"))
        self.assertFalse(build._is_excluded_project("order"))

    def test_collect_targets_from_cmd_and_app(self) -> None:
        self.write("cmd/api/main.go", "package main\n")
        self.write("app/worker/main.go", "package main\n")
        self.write("app/notmain/x.go", "package worker\n")
        targets: list[Path] = []
        build._collect_go_main_targets(self.dir, targets, include_self=False)
        rels = {str(t.relative_to(self.dir)) for t in targets}
        self.assertEqual(rels, {"cmd/api", "app/worker"})

    def test_collect_targets_includes_cmd_itself(self) -> None:
        self.write("cmd/main.go", "package main\n")
        targets: list[Path] = []
        build._collect_go_main_targets(self.dir, targets, include_self=False)
        self.assertEqual([str(t.relative_to(self.dir)) for t in targets], ["cmd"])

    def test_collect_targets_includes_self(self) -> None:
        self.write("main.go", "package main\n")
        targets: list[Path] = []
        build._collect_go_main_targets(self.dir, targets, include_self=True)
        self.assertEqual(targets, [self.dir])

    def test_go_module_dir_walks_up_to_project_root(self) -> None:
        self.write("go.mod", "module x\n")
        deep = self.dir / "cmd" / "api"
        deep.mkdir(parents=True)
        self.assertEqual(build._go_module_dir(deep, self.dir), self.dir)

    def test_go_module_dir_returns_none_without_go_mod(self) -> None:
        deep = self.dir / "cmd" / "api"
        deep.mkdir(parents=True)
        self.assertIsNone(build._go_module_dir(deep, self.dir))

    def test_go_mod_tidy_failure_raises(self) -> None:
        with mock.patch.object(build, "run_no_capture", return_value=1):
            with self.assertRaises(BuildError):
                build._go_mod_tidy(self.dir, log=mock.Mock())

    def test_go_build_failure_raises(self) -> None:
        with mock.patch.object(build, "run_no_capture", return_value=2):
            with self.assertRaises(BuildError):
                build._go_build(self.dir, log=mock.Mock())

    def test_go_build_uses_devnull_output(self) -> None:
        with mock.patch.object(build, "run_no_capture", return_value=0) as rnc:
            build._go_build(self.dir)
        self.assertIn(os.devnull, rnc.call_args[0][0])


class TestCheckGoProject(TempProject):
    def test_tidy_then_build_all_green(self) -> None:
        self.write("go.mod", "module x\n")
        self.write("cmd/api/main.go", "package main\n")
        with mock.patch.object(build, "run_no_capture", return_value=0):
            results = build._check_go_project(self.dir)
        names = [r.name for r in results]
        self.assertIn("go mod tidy", names)
        self.assertIn("go build: cmd/api", names)
        self.assertTrue(all(r.status == "ok" for r in results))

    def test_tidy_failure_short_circuits_before_build(self) -> None:
        self.write("go.mod", "module x\n")
        self.write("cmd/api/main.go", "package main\n")
        with mock.patch.object(build, "run_no_capture", return_value=1):
            results = build._check_go_project(self.dir)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "fail")

    def test_build_failure_is_recorded_but_others_continue(self) -> None:
        self.write("go.mod", "module x\n")
        self.write("cmd/a/main.go", "package main\n")
        self.write("cmd/b/main.go", "package main\n")

        def rnc(cmd, cwd=None):
            return 1 if cmd[:2] == ["go", "build"] and cwd.endswith("/a") else 0

        with mock.patch.object(build, "run_no_capture", side_effect=rnc):
            results = build._check_go_project(self.dir)
        by_name = {r.name: r.status for r in results}
        self.assertEqual(by_name["go build: cmd/a"], "fail")
        self.assertEqual(by_name["go build: cmd/b"], "ok")

    def test_go_work_skips_tidy_and_root_main(self) -> None:
        self.write("go.work", "go 1.22\n")
        self.write("main.go", "package main\n")
        self.write("service/api/cmd/main.go", "package main\n")
        with mock.patch.object(build, "run_no_capture", return_value=0) as rnc:
            results = build._check_go_project(self.dir)
        self.assertEqual(results[0].name, "go mod tidy")
        self.assertEqual(results[0].status, "warn")
        # 根 main.go 不编译，只编译 service 下的
        built = [c[0][0] for c in rnc.call_args_list]
        self.assertTrue(all(c[:2] == ["go", "build"] for c in built))
        self.assertEqual(len(built), 1)

    def test_excluded_project_name_skips_root_self(self) -> None:
        excluded = self.dir / "pay-core"
        (excluded / "cmd").mkdir(parents=True)
        (excluded / "main.go").write_text("package main\n")
        with mock.patch.object(build, "run_no_capture", return_value=0) as rnc:
            build._check_go_project(excluded)
        self.assertFalse(any(c[0][0][:2] == ["go", "build"] for c in rnc.call_args_list))

    def test_service_subprojects_get_their_own_tidy(self) -> None:
        self.write("service/api/go.mod", "module api\n")
        self.write("service/api/cmd/main.go", "package main\n")
        with mock.patch.object(build, "run_no_capture", return_value=0):
            results = build._check_go_project(self.dir)
        self.assertIn("go mod tidy: service/api", [r.name for r in results])


class TestCheckRustProject(TempProject):
    def test_cargo_check_ok(self) -> None:
        with mock.patch.object(build, "_run_verbose", return_value=0) as rv:
            results = build._check_rust_project(self.dir, log=mock.Mock())
        self.assertEqual(results[0].status, "ok")
        self.assertEqual(rv.call_args[0][0], ["cargo", "check", "--verbose"])

    def test_cargo_check_failure(self) -> None:
        with mock.patch.object(build, "_run_verbose", return_value=101):
            results = build._check_rust_project(self.dir)
        self.assertEqual(results[0].status, "fail")
        self.assertIn("101", results[0].message)


class TestCheckPythonProject(TempProject):
    def test_py_compile_ok(self) -> None:
        self.write("a.py", "x = 1\n")
        with mock.patch.object(build, "_have", return_value=False):
            results = build._check_python_project(self.dir, log=mock.Mock())
        self.assertEqual([r.name for r in results], ["py_compile"])
        self.assertEqual(results[0].status, "ok")

    def test_py_compile_syntax_error_fails(self) -> None:
        self.write("bad.py", "def (:\n")
        with mock.patch.object(build, "_have", return_value=False):
            results = build._check_python_project(self.dir)
        self.assertEqual(results[0].status, "fail")
        self.assertTrue(results[0].message)

    def test_src_dir_is_also_scanned(self) -> None:
        self.write("src/a.py", "x = 1\n")
        with mock.patch.object(build, "_have", return_value=False):
            results = build._check_python_project(self.dir)
        self.assertEqual(results[0].status, "ok")

    def test_no_py_files_means_no_py_compile_entry(self) -> None:
        with mock.patch.object(build, "_have", return_value=False):
            self.assertEqual(build._check_python_project(self.dir), [])

    def test_mypy_and_ruff_are_warn_only(self) -> None:
        self.write("a.py", "x = 1\n")
        p = SimpleNamespace(returncode=1, stdout="a.py:1: err\n", stderr="")
        with mock.patch.object(build, "_have", return_value=True), \
             mock.patch.object(build, "run", return_value=p):
            results = build._check_python_project(self.dir, log=mock.Mock())
        by_name = {r.name: r.status for r in results}
        self.assertEqual(by_name["mypy"], "warn")
        self.assertEqual(by_name["ruff"], "warn")

    def test_mypy_and_ruff_clean(self) -> None:
        self.write("a.py", "x = 1\n")
        p = SimpleNamespace(returncode=0, stdout="", stderr="")
        with mock.patch.object(build, "_have", return_value=True), \
             mock.patch.object(build, "run", return_value=p):
            results = build._check_python_project(self.dir)
        by_name = {r.name: r.status for r in results}
        self.assertEqual(by_name["mypy"], "ok")
        self.assertEqual(by_name["ruff"], "ok")

    def test_ruff_runs_even_without_py_files_at_top_level(self) -> None:
        p = SimpleNamespace(returncode=0, stdout="", stderr="")
        with mock.patch.object(build, "_have", side_effect=lambda c: c == "ruff"), \
             mock.patch.object(build, "run", return_value=p):
            results = build._check_python_project(self.dir)
        self.assertEqual([r.name for r in results], ["ruff"])


class TestCheckJavaProject(TempProject):
    def test_gradle_wrapper_is_preferred(self) -> None:
        self.write("build.gradle", "")
        w = self.write("gradlew", "#!/bin/sh\n")
        w.chmod(0o755)
        with mock.patch.object(build, "_run_verbose", return_value=0) as rv:
            results = build._check_java_project(self.dir)
        self.assertEqual(rv.call_args[0][0][0], "./gradlew")
        self.assertEqual(results[0].status, "ok")

    def test_gradle_kts_without_wrapper_uses_system_gradle(self) -> None:
        self.write("build.gradle.kts", "")
        with mock.patch.object(build, "_run_verbose", return_value=1) as rv:
            results = build._check_java_project(self.dir)
        self.assertEqual(rv.call_args[0][0][0], "gradle")
        self.assertEqual(results[0].status, "fail")

    def test_maven_pom(self) -> None:
        self.write("pom.xml", "<project/>")
        with mock.patch.object(build, "_run_verbose", return_value=0) as rv:
            results = build._check_java_project(self.dir)
        self.assertEqual(rv.call_args[0][0], ["mvn", "compile", "-q"])
        self.assertEqual(results[0].name, "mvn compile")

    def test_no_java_build_file_yields_nothing(self) -> None:
        self.assertEqual(build._check_java_project(self.dir), [])


class TestCheckCcProject(TempProject):
    def test_cmake_configure_only(self) -> None:
        self.write("CMakeLists.txt", "")
        with mock.patch.object(build, "_run_verbose", return_value=0) as rv:
            results = build._check_cc_project(self.dir, log=mock.Mock())
        cmd = rv.call_args[0][0]
        self.assertEqual(cmd[:2], ["cmake", "-S"])
        self.assertIn("-B", cmd)
        self.assertEqual(results[0].status, "ok")

    def test_cmake_failure(self) -> None:
        self.write("CMakeLists.txt", "")
        with mock.patch.object(build, "_run_verbose", return_value=1):
            results = build._check_cc_project(self.dir)
        self.assertEqual(results[0].status, "fail")

    def test_makefile_only_is_skipped(self) -> None:
        self.write("Makefile", "all:\n\techo hi\n")
        self.assertEqual(build._check_cc_project(self.dir), [])

    def test_tmpdir_is_removed_even_on_failure(self) -> None:
        self.write("CMakeLists.txt", "")
        seen: list[str] = []

        def rv(cmd, cwd=None, log=None):
            seen.append(cmd[cmd.index("-B") + 1])
            raise RuntimeError("boom")

        with mock.patch.object(build, "_run_verbose", side_effect=rv):
            with self.assertRaises(RuntimeError):
                build._check_cc_project(self.dir)
        self.assertFalse(Path(seen[0]).exists())


class TestNodePackageManager(TempProject):
    def test_lockfile_priority_bun_first(self) -> None:
        self.write("bun.lockb", "")
        self.write("yarn.lock", "")
        self.assertEqual(build._detect_node_pkg_manager(self.dir), "bun")

    def test_bun_text_lock_also_counts(self) -> None:
        self.write("bun.lock", "")
        self.assertEqual(build._detect_node_pkg_manager(self.dir), "bun")

    def test_yarn_before_pnpm(self) -> None:
        self.write("yarn.lock", "")
        self.write("pnpm-lock.yaml", "")
        self.assertEqual(build._detect_node_pkg_manager(self.dir), "yarn")

    def test_pnpm_before_npm(self) -> None:
        self.write("pnpm-lock.yaml", "")
        self.write("package-lock.json", "")
        self.assertEqual(build._detect_node_pkg_manager(self.dir), "pnpm")

    def test_npm_lockfile(self) -> None:
        self.write("package-lock.json", "")
        self.assertEqual(build._detect_node_pkg_manager(self.dir), "npm")

    def test_no_lockfile_probes_path(self) -> None:
        with mock.patch.object(build, "_have", side_effect=lambda c: c == "pnpm"):
            self.assertEqual(build._detect_node_pkg_manager(self.dir), "pnpm")

    def test_nothing_available_returns_none(self) -> None:
        with mock.patch.object(build, "_have", return_value=False):
            self.assertIsNone(build._detect_node_pkg_manager(self.dir))


class TestReadPackageScripts(TempProject):
    def test_missing_package_json(self) -> None:
        self.assertEqual(build._read_package_scripts(self.dir), {})

    def test_broken_json_returns_empty(self) -> None:
        self.write("package.json", "{ not json")
        self.assertEqual(build._read_package_scripts(self.dir), {})

    def test_scripts_are_returned(self) -> None:
        self.write("package.json", '{"scripts": {"build": "tsc"}}')
        self.assertEqual(build._read_package_scripts(self.dir), {"build": "tsc"})

    def test_null_scripts_becomes_empty(self) -> None:
        self.write("package.json", '{"scripts": null}')
        self.assertEqual(build._read_package_scripts(self.dir), {})


class TestClassifyNodeBuildScript(unittest.TestCase):
    def setUp(self) -> None:
        p = mock.patch.dict(os.environ, {"CHECKWORK_NODE_BUILD": ""})
        p.start()
        self.addCleanup(p.stop)

    def test_plain_tsc_runs(self) -> None:
        self.assertEqual(build._classify_node_build_script("tsc -p ."), "run")

    def test_watch_is_blocked_even_with_tsc(self) -> None:
        self.assertEqual(build._classify_node_build_script("tsc --watch"), "blocked")

    def test_dev_server_is_blocked(self) -> None:
        self.assertEqual(build._classify_node_build_script("vite serve"), "blocked")

    def test_empty_is_unknown(self) -> None:
        self.assertEqual(build._classify_node_build_script("  "), "unknown")

    def test_unknown_tool_is_unknown(self) -> None:
        self.assertEqual(build._classify_node_build_script("mybuilder --go"), "unknown")

    def test_needs_build_subcommand(self) -> None:
        self.assertEqual(build._classify_node_build_script("nuxt build"), "run")
        self.assertEqual(build._classify_node_build_script("nuxt generate"), "unknown")

    def test_harmless_prefix_commands_pass(self) -> None:
        self.assertEqual(build._classify_node_build_script("rm -rf dist && tsc"), "run")

    def test_chained_unknown_command_fails_the_whole_script(self) -> None:
        self.assertEqual(build._classify_node_build_script("tsc && weirdtool"), "unknown")

    def test_env_override_forces_run(self) -> None:
        with mock.patch.dict(os.environ, {"CHECKWORK_NODE_BUILD": "1"}):
            self.assertEqual(build._classify_node_build_script("vite dev"), "run")


class TestCheckNodeProject(TempProject):
    def test_no_package_manager_warns(self) -> None:
        with mock.patch.object(build, "_detect_node_pkg_manager", return_value=None):
            results = build._check_node_project(self.dir, log=mock.Mock())
        self.assertEqual(results[0].status, "warn")

    def test_whitelisted_build_runs(self) -> None:
        self.write("package.json", '{"scripts": {"build": "tsc"}}')
        with mock.patch.object(build, "_detect_node_pkg_manager", return_value="pnpm"), \
             mock.patch.object(build, "_run_verbose", return_value=0) as rv, \
             mock.patch.object(build, "_have", return_value=False):
            results = build._check_node_project(self.dir, log=mock.Mock())
        self.assertEqual(rv.call_args[0][0], ["pnpm", "run", "build"])
        self.assertEqual(results[0].status, "ok")

    def test_failing_build_is_fatal(self) -> None:
        self.write("package.json", '{"scripts": {"build": "tsc"}}')
        with mock.patch.object(build, "_detect_node_pkg_manager", return_value="npm"), \
             mock.patch.object(build, "_run_verbose", return_value=1), \
             mock.patch.object(build, "_have", return_value=False):
            results = build._check_node_project(self.dir)
        self.assertEqual(results[0].status, "fail")

    def test_blocked_build_is_skipped_with_warn(self) -> None:
        self.write("package.json", '{"scripts": {"build": "vite dev"}}')
        with mock.patch.object(build, "_detect_node_pkg_manager", return_value="npm"), \
             mock.patch.object(build, "_run_verbose") as rv, \
             mock.patch.object(build, "_have", return_value=False):
            results = build._check_node_project(self.dir, log=mock.Mock())
        rv.assert_not_called()
        self.assertEqual(results[0].status, "warn")
        self.assertIn("常驻", results[0].message)

    def test_unknown_build_is_skipped_with_hint(self) -> None:
        self.write("package.json", '{"scripts": {"build": "mytool"}}')
        with mock.patch.object(build, "_detect_node_pkg_manager", return_value="npm"), \
             mock.patch.object(build, "_run_verbose") as rv, \
             mock.patch.object(build, "_have", return_value=False):
            results = build._check_node_project(self.dir, log=mock.Mock())
        rv.assert_not_called()
        self.assertIn("CHECKWORK_NODE_BUILD=1", results[0].message)

    def test_typecheck_script_is_warn_only(self) -> None:
        self.write("package.json", '{"scripts": {"typecheck": "tsc --noEmit"}}')
        p = SimpleNamespace(returncode=1, stdout="a.ts(1,1): error TS1\n", stderr="")
        with mock.patch.object(build, "_detect_node_pkg_manager", return_value="npm"), \
             mock.patch.object(build, "run", return_value=p), \
             mock.patch.object(build, "_have", return_value=False):
            results = build._check_node_project(self.dir, log=mock.Mock())
        self.assertEqual(results[0].status, "warn")
        self.assertIn("1 项", results[0].message)

    def test_typecheck_clean(self) -> None:
        self.write("package.json", '{"scripts": {"typecheck": "tsc --noEmit"}}')
        p = SimpleNamespace(returncode=0, stdout="", stderr="")
        with mock.patch.object(build, "_detect_node_pkg_manager", return_value="npm"), \
             mock.patch.object(build, "run", return_value=p), \
             mock.patch.object(build, "_have", return_value=False):
            results = build._check_node_project(self.dir)
        self.assertEqual(results[0].status, "ok")

    def test_tsc_fallback_when_tsconfig_present(self) -> None:
        self.write("package.json", "{}")
        self.write("tsconfig.json", "{}")
        p = SimpleNamespace(returncode=2, stdout="error TS1\nerror TS2\n", stderr="")
        with mock.patch.object(build, "_detect_node_pkg_manager", return_value="npm"), \
             mock.patch.object(build, "_have", return_value=True), \
             mock.patch.object(build, "run", return_value=p) as run_mock:
            results = build._check_node_project(self.dir, log=mock.Mock())
        self.assertEqual(run_mock.call_args[0][0], ["tsc", "--noEmit"])
        self.assertEqual(results[0].status, "warn")
        self.assertIn("2 项", results[0].message)

    def test_tsc_fallback_clean(self) -> None:
        self.write("package.json", "{}")
        self.write("tsconfig.json", "{}")
        p = SimpleNamespace(returncode=0, stdout="", stderr="")
        with mock.patch.object(build, "_detect_node_pkg_manager", return_value="npm"), \
             mock.patch.object(build, "_have", return_value=True), \
             mock.patch.object(build, "run", return_value=p):
            results = build._check_node_project(self.dir)
        self.assertEqual(results[0].status, "ok")

    def test_typecheck_script_suppresses_tsc_fallback(self) -> None:
        self.write("package.json", '{"scripts": {"typecheck": "tsc"}}')
        self.write("tsconfig.json", "{}")
        p = SimpleNamespace(returncode=0, stdout="", stderr="")
        with mock.patch.object(build, "_detect_node_pkg_manager", return_value="npm"), \
             mock.patch.object(build, "_have", return_value=True), \
             mock.patch.object(build, "run", return_value=p):
            results = build._check_node_project(self.dir)
        self.assertEqual([r.name for r in results], ["npm typecheck"])


class TestDetectProjectTypes(TempProject):
    def test_empty_dir_detects_nothing(self) -> None:
        self.assertEqual(build._detect_project_types(self.dir), [])

    def test_go_mod_or_go_work(self) -> None:
        self.write("go.mod", "")
        self.assertEqual([t.name for t in build._detect_project_types(self.dir)], ["Go"])

    def test_mixed_project_detects_every_language(self) -> None:
        for f in ("go.mod", "Cargo.toml", "pyproject.toml", "pom.xml",
                  "CMakeLists.txt", "package.json"):
            self.write(f, "")
        names = [t.name for t in build._detect_project_types(self.dir)]
        self.assertEqual(names, ["Go", "Rust", "Python", "Java", "C/C++", "Node.js"])

    def test_python_detected_via_requirements(self) -> None:
        self.write("requirements.txt", "")
        self.assertEqual([t.name for t in build._detect_project_types(self.dir)], ["Python"])


class TestCheckBuildDispatch(TempProject):
    def test_unknown_project_returns_empty(self) -> None:
        log = mock.Mock()
        self.assertEqual(build.check_build(project_dir=self.dir, log=log), [])
        self.assertIn("未检测到", log.call_args[0][0])

    def test_serial_is_the_default(self) -> None:
        self.write("Cargo.toml", "")
        self.write("package.json", "{}")
        with mock.patch.dict(os.environ, {"CHECKWORK_PARALLEL": ""}), \
             mock.patch.object(build, "_run_checks_serial", return_value=[]) as s, \
             mock.patch.object(build, "_run_checks_parallel") as p:
            build.check_build(project_dir=self.dir)
        s.assert_called_once()
        p.assert_not_called()

    def test_parallel_needs_two_languages_and_the_env_var(self) -> None:
        self.write("Cargo.toml", "")
        self.write("package.json", "{}")
        with mock.patch.dict(os.environ, {"CHECKWORK_PARALLEL": "1"}), \
             mock.patch.object(build, "_run_checks_parallel", return_value=[]) as p:
            build.check_build(project_dir=self.dir)
        p.assert_called_once()

    def test_parallel_flag_with_single_language_stays_serial(self) -> None:
        self.write("Cargo.toml", "")
        with mock.patch.dict(os.environ, {"CHECKWORK_PARALLEL": "1"}), \
             mock.patch.object(build, "_run_checks_serial", return_value=[]) as s:
            build.check_build(project_dir=self.dir)
        s.assert_called_once()

    def test_serial_runner_logs_each_language(self) -> None:
        types = [build.ProjectType("Rust", lambda d, log=None: [CheckResult("cargo", "ok")])]
        log = mock.Mock()
        results = build._run_checks_serial(types, self.dir, log=log)
        self.assertEqual(len(results), 1)
        self.assertIn("Rust", log.call_args[0][0])

    def test_parallel_runner_collects_every_checkpoint(self) -> None:
        types = [
            build.ProjectType("Rust", lambda d, log=None: [CheckResult("cargo", "ok")]),
            build.ProjectType("Node.js", lambda d, log=None: [CheckResult("npm", "ok")]),
        ]
        results = build._run_checks_parallel(types, self.dir, log=mock.Mock())
        self.assertEqual({r.name for r in results}, {"cargo", "npm"})

    def test_parallel_runner_turns_a_crash_into_a_fail(self) -> None:
        def boom(d, log=None):
            raise RuntimeError("检查点炸了")

        types = [
            build.ProjectType("Rust", boom),
            build.ProjectType("Node.js", lambda d, log=None: [CheckResult("npm", "ok")]),
        ]
        results = build._run_checks_parallel(types, self.dir, log=None)
        by_name = {r.name: r for r in results}
        self.assertEqual(by_name["Rust"].status, "fail")
        self.assertIn("检查点炸了", by_name["Rust"].message)
        self.assertEqual(by_name["npm"].status, "ok")


class TestCheckworkSingle(TempProject):
    def setUp(self) -> None:
        super().setUp()
        self.r = mock.MagicMock()

    def test_build_error_maps_to_rc_2(self) -> None:
        with mock.patch.object(build, "check_build", side_effect=BuildError("炸了")):
            rc, detail = build._checkwork_single(self.dir, self.r)
        self.assertEqual(rc, 2)
        self.assertIn("炸了", detail)

    def test_no_results_is_a_pass(self) -> None:
        with mock.patch.object(build, "check_build", return_value=[]):
            rc, detail = build._checkwork_single(self.dir, self.r)
        self.assertEqual(rc, 0)
        self.assertIn("无已知项目类型", detail)

    def test_fail_result_maps_to_rc_2(self) -> None:
        with mock.patch.object(build, "check_build",
                               return_value=[CheckResult("go build", "fail", "exit=1")]):
            rc, detail = build._checkwork_single(self.dir, self.r)
        self.assertEqual(rc, 2)
        self.assertIn("go build", detail)

    def test_warn_only_still_passes(self) -> None:
        with mock.patch.object(build, "check_build",
                               return_value=[CheckResult("ruff", "warn", "3 项")]):
            rc, detail = build._checkwork_single(self.dir, self.r)
        self.assertEqual(rc, 0)
        self.assertIn("告警", detail)

    def test_all_green(self) -> None:
        with mock.patch.object(build, "check_build",
                               return_value=[CheckResult("go build", "ok")]):
            rc, detail = build._checkwork_single(self.dir, self.r)
        self.assertEqual((rc, detail), (0, "通过"))

    def test_parallel_hint_is_shown_only_when_serial(self) -> None:
        with mock.patch.dict(os.environ, {"CHECKWORK_PARALLEL": "1"}), \
             mock.patch.object(build, "check_build", return_value=[]):
            build._checkwork_single(self.dir, self.r)
        self.r.info.assert_not_called()


class TestPrintResults(unittest.TestCase):
    def test_each_status_reaches_its_own_reporter_method(self) -> None:
        r = mock.MagicMock()
        build._print_results(r, [
            CheckResult("a", "ok"),
            CheckResult("b", "warn", "细节"),
            CheckResult("c", "fail", "细节"),
            CheckResult("d", "warn"),
        ])
        r.ok.assert_called_once_with("a")
        self.assertEqual(r.warn.call_count, 2)
        r.err.assert_called_once_with("c — 细节")


class TestRunCheckwork(TempProject):
    def test_inside_a_git_repo_checks_only_the_cwd(self) -> None:
        (self.dir / ".git").mkdir()
        with mock.patch.object(build.Path, "resolve", return_value=self.dir), \
             mock.patch.object(build, "reporter", return_value=mock.MagicMock()), \
             mock.patch.object(build, "_checkwork_single", return_value=(0, "通过")) as single, \
             mock.patch("lib.notify.notify_via_n") as n:
            rc = build.run_checkwork()
        self.assertEqual(rc, 0)
        single.assert_called_once()
        self.assertIn("完成", n.call_args[0][0])

    def test_failure_inside_a_git_repo_returns_2(self) -> None:
        (self.dir / ".git").mkdir()
        with mock.patch.object(build.Path, "resolve", return_value=self.dir), \
             mock.patch.object(build, "reporter", return_value=mock.MagicMock()), \
             mock.patch.object(build, "_checkwork_single", return_value=(2, "失败")), \
             mock.patch("lib.notify.notify_via_n") as n:
            rc = build.run_checkwork()
        self.assertEqual(rc, 2)
        self.assertIn("失败", n.call_args[0][0])

    def test_outside_a_git_repo_falls_back_to_the_batch_runner(self) -> None:
        batch_result = SimpleNamespace(failed=0)
        with mock.patch.object(build.Path, "resolve", return_value=self.dir), \
             mock.patch.object(build, "reporter", return_value=mock.MagicMock()), \
             mock.patch("lib.batch_git.BatchRunner") as runner, \
             mock.patch("lib.notify.notify_via_n"):
            runner.return_value.run.return_value = batch_result
            rc = build.run_checkwork()
        self.assertEqual(rc, 0)
        runner.return_value.run.assert_called_once()

    def test_batch_failure_returns_2(self) -> None:
        batch_result = SimpleNamespace(failed=1)
        with mock.patch.object(build.Path, "resolve", return_value=self.dir), \
             mock.patch.object(build, "reporter", return_value=mock.MagicMock()), \
             mock.patch("lib.batch_git.BatchRunner") as runner, \
             mock.patch("lib.notify.notify_via_n"):
            runner.return_value.run.return_value = batch_result
            rc = build.run_checkwork()
        self.assertEqual(rc, 2)

    def test_batch_callbacks_delegate_to_checkwork_single(self) -> None:
        """抓住传给 BatchRunner 的 detect/execute 回调，单独跑一遍。"""
        captured = {}

        class FakeRunner:
            def run(self, op):
                captured["op"] = op
                return SimpleNamespace(failed=0)

        with mock.patch.object(build.Path, "resolve", return_value=self.dir), \
             mock.patch.object(build, "reporter", return_value=mock.MagicMock()), \
             mock.patch("lib.batch_git.BatchRunner", FakeRunner), \
             mock.patch("lib.notify.notify_via_n"):
            build.run_checkwork()

        op = captured["op"]
        r = mock.MagicMock()
        plan = op.detect_fn(self.dir, r, self.dir)
        self.assertEqual(plan.status, "ok")
        with mock.patch.object(build, "_checkwork_single", return_value=(2, "失败")):
            status, detail = plan.execute(self.dir, plan, r, self.dir)
        self.assertEqual((status, detail), ("fail", "失败"))


if __name__ == "__main__":
    unittest.main()
