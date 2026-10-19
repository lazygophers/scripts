"""gitnexuswatch：仓库发现 / 新鲜度 / 事件归类 / 重建 / 守护接线。

主缝：GRAPHWATCH_HOME 指向临时目录（配置与锁都在里面），git 仓库在临时目录里现建。
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
import unittest.mock
from pathlib import Path

from lib import gitnexuswatch as gw
from lib import graphwatch_service
from lib.graphwatch_config import GraphwatchError


def _git_init(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "init"], check=True, env=env)
    return path.resolve()


def _write_meta(repo: Path, commit: str) -> Path:
    meta = repo / ".gitnexus" / "meta.json"
    meta.parent.mkdir(exist_ok=True)
    meta.write_text(json.dumps({"lastCommit": commit}))
    return meta


def _head(repo: Path) -> str:
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(dir="/tmp")).resolve()
        self.home = self.tmp / "home"
        p = unittest.mock.patch.dict(os.environ, {"GRAPHWATCH_HOME": str(self.home)})
        p.start()
        self.addCleanup(p.stop)
        import shutil

        self.addCleanup(shutil.rmtree, self.tmp, True)


class RegistryTest(Case):
    def test_git_root_registers_itself(self):
        repo = _git_init(self.tmp / "r")
        self.assertEqual(gw.add_folder(repo), [str(repo)])
        self.assertEqual(gw.load_config()["folders"], [str(repo)])

    def test_non_git_folder_splits_into_repos_skipping_deps_and_hidden(self):
        a = _git_init(self.tmp / "ws" / "go" / "a")
        b = _git_init(self.tmp / "ws" / "node" / "b")
        _git_init(self.tmp / "ws" / "node_modules" / "dep")
        _git_init(self.tmp / "ws" / ".claude" / "worktrees" / "w")
        _git_init(a / "sub")  # 仓库里的嵌套仓库不单独登记
        self.assertEqual(gw.add_folder(self.tmp / "ws"), [str(a), str(b)])

    def test_add_is_idempotent(self):
        repo = _git_init(self.tmp / "r")
        gw.add_folder(repo)
        gw.add_folder(repo)
        self.assertEqual(gw.load_config()["folders"], [str(repo)])

    def test_add_without_repos_errors(self):
        (self.tmp / "empty").mkdir()
        with self.assertRaisesRegex(GraphwatchError, "没有 git 仓库"):
            gw.add_folder(self.tmp / "empty")
        with self.assertRaisesRegex(GraphwatchError, "目录不存在"):
            gw.add_folder(self.tmp / "nope")

    def test_remove_folder_removes_everything_under_it_only(self):
        a = _git_init(self.tmp / "ws" / "a")
        b = _git_init(self.tmp / "ws" / "b")
        other = _git_init(self.tmp / "ws-other")
        gw.add_folder(self.tmp / "ws")
        gw.add_folder(other)
        self.assertEqual(gw.remove_folder(self.tmp / "ws"), [str(a), str(b)])
        self.assertEqual(gw.load_config()["folders"], [str(other)])
        with self.assertRaisesRegex(GraphwatchError, "未登记过"):
            gw.remove_folder(self.tmp / "ws")

    def test_config_written_0600(self):
        gw.add_folder(_git_init(self.tmp / "r"))
        self.assertEqual(gw.config_path().stat().st_mode & 0o777, 0o600)

    def test_config_validation(self):
        self.home.mkdir(parents=True)
        for text, msg in [("[1]", "顶层应是映射"), ("debounce: x", "应是数字"), ("debounce: 0", "应大于 0"),
                          ("folders: 3", "仓库路径列表"), (":\n -", "合法 YAML")]:
            gw.config_path().write_text(text)
            with self.subTest(text=text), self.assertRaisesRegex(GraphwatchError, msg):
                gw.load_config()
        gw.config_path().write_text("debounce: 5\nunknown: 1\n")
        self.assertEqual(gw.load_config()["debounce"], 5.0)


class GitStateTest(Case):
    def test_git_dir_follows_gitdir_file(self):
        repo = _git_init(self.tmp / "r")
        wt = self.tmp / "wt"
        subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", str(wt)], check=True)
        self.assertEqual(gw.git_dir(str(wt)), (repo / ".git" / "worktrees" / "wt").resolve())
        self.assertEqual(gw.git_dir(str(repo)), repo / ".git")

    def test_git_busy(self):
        gdir = _git_init(self.tmp / "r") / ".git"
        self.assertFalse(gw.git_busy(gdir))
        for name in ("index.lock", "MERGE_HEAD"):
            (gdir / name).touch()
            self.assertTrue(gw.git_busy(gdir))
            (gdir / name).unlink()
        (gdir / "rebase-merge").mkdir()
        self.assertTrue(gw.git_busy(gdir))

    def test_stale_never_indexed(self):
        repo = _git_init(self.tmp / "r")
        self.assertEqual(gw.stale(str(repo)), repo / ".gitnexus" / "meta.json")
        self.assertEqual(gw.freshness(str(repo), running=False)[0], "skip")

    def test_fresh_when_commit_matches_and_tree_clean(self):
        repo = _git_init(self.tmp / "r")
        (repo / ".gitignore").write_text(".gitnexus/\n")
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "i"],
                       check=True)
        _write_meta(repo, _head(repo))
        self.assertIsNone(gw.stale(str(repo)))
        self.assertEqual(gw.freshness(str(repo), running=False)[0], "ok")

    def test_stale_after_head_moves(self):
        repo = _git_init(self.tmp / "r")
        _write_meta(repo, "0" * 40)
        self.assertEqual(gw.stale(str(repo)), repo / ".git" / "HEAD")
        self.assertEqual(gw.freshness(str(repo), running=False)[0], "fail")
        self.assertEqual(gw.freshness(str(repo), running=True)[0], "updating")

    def test_stale_on_dirty_file_newer_than_index(self):
        repo = _git_init(self.tmp / "r")
        (repo / ".gitignore").write_text(".gitnexus/\n")
        meta = _write_meta(repo, _head(repo))
        old = time.time() - 100
        os.utime(meta, (old, old))
        os.utime(repo / ".gitignore", (old - 10, old - 10))
        self.assertIsNone(gw.stale(str(repo)))  # 脏但比索引旧：索引已含它
        (repo / "a.py").write_text("x = 2\n")
        self.assertEqual(gw.stale(str(repo)), repo / "a.py")

    def test_stale_counts_deleted_and_renamed(self):
        repo = _git_init(self.tmp / "r")
        (repo / ".gitignore").write_text(".gitnexus/\n")
        subprocess.run(["git", "-C", str(repo), "add", ".gitignore"], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "i"],
                       check=True)
        meta = _write_meta(repo, _head(repo))
        old = time.time() - 100
        os.utime(meta, (old, old))
        subprocess.run(["git", "-C", str(repo), "mv", "a.py", "b.py"], check=True)
        os.utime(repo / "b.py", (old - 10, old - 10))
        self.assertIsNone(gw.stale(str(repo)))  # 重命名后的文件比索引旧，原路径字段不能被当成条目
        (repo / "b.py").unlink()
        self.assertIsNotNone(gw.stale(str(repo)))  # 删除算改动

    def test_corrupt_meta_is_stale(self):
        repo = _git_init(self.tmp / "r")
        meta = _write_meta(repo, "x")
        meta.write_text("{bad")
        self.assertEqual(gw.stale(str(repo)), meta)

    def test_empty_repo_has_nothing_to_build(self):
        repo = self.tmp / "e"
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        _write_meta(repo, "x")
        self.assertIsNone(gw.stale(str(repo)))

    def test_freshness_flags_moved_away_repo(self):
        self.assertEqual(gw.freshness(str(self.tmp / "gone"))[0], "fail")


class ClassifyTest(unittest.TestCase):
    parts = Path("/r").parts

    def test_kinds(self):
        cases = {
            "/r/src/a.py": "file",
            "/r/.git/HEAD": "git",
            "/r/.git/logs/HEAD": "git",
            "/r/.git/index": None,
            "/r/.git/refs/heads/x": None,
            "/r/.gitnexus/lbug/x": None,
            "/r/node_modules/x/a.js": None,
            "/r/web/dist/a.js": None,
            "/r/dist": "file",  # 文件名恰好叫 dist 不算目录
            "/r": None,
            "": None,
        }
        for path, kind in cases.items():
            with self.subTest(path=path):
                self.assertEqual(gw.classify(self.parts, path), kind)


class ListenerTest(Case):
    def _listener(self, repo, debounce=0.3):
        hits = []
        listener = gw._Listener(str(repo), debounce, lambda: hits.append(time.monotonic()))
        listener.start()
        self.addCleanup(listener.stop)
        return listener, hits

    def _wait(self, hits, n, timeout=10.0):
        end = time.monotonic() + timeout
        while len(hits) < n and time.monotonic() < end:
            time.sleep(0.05)

    def test_file_change_debounced_into_one_rebuild(self):
        repo = _git_init(self.tmp / "r")
        _l, hits = self._listener(repo)
        time.sleep(0.3)
        for i in range(5):
            (repo / "a.py").write_text(f"x = {i}\n")
            time.sleep(0.05)
        self._wait(hits, 1)
        time.sleep(0.6)
        self.assertEqual(len(hits), 1)

    def test_branch_switch_fires_fast_even_with_long_debounce(self):
        repo = _git_init(self.tmp / "r")
        with unittest.mock.patch.object(gw, "HEAD_DEBOUNCE", 0.2):
            _l, hits = self._listener(repo, debounce=60)
            time.sleep(0.3)
            started = time.monotonic()
            subprocess.run(["git", "-C", str(repo), "checkout", "-qb", "feat"], check=True)
            self._wait(hits, 1)
        self.assertEqual(len(hits), 1)
        self.assertLess(hits[0] - started, 5)

    def test_stop_unschedules(self):
        repo = _git_init(self.tmp / "r")
        listener, hits = self._listener(repo, debounce=0.1)
        listener.stop()
        listener.stop()  # 重复 stop 无害
        listener.join()
        (repo / "a.py").write_text("y\n")
        time.sleep(0.5)
        self.assertEqual(hits, [])

    def test_fast_timer_not_pushed_back_by_file_events(self):
        fired = threading.Event()
        listener = gw._Listener(str(self.tmp), 60, fired.set)
        handler = listener._handler
        ev = unittest.mock.Mock(is_directory=False, dest_path="")
        with unittest.mock.patch.object(gw, "HEAD_DEBOUNCE", 0.1):
            ev.src_path = str(self.tmp / ".git" / "HEAD")
            handler.on_any_event(ev)
            ev.src_path = str(self.tmp / "a.py")
            handler.on_any_event(ev)
            self.assertTrue(fired.wait(3))

    def test_moved_into_head_counts(self):
        fired = threading.Event()
        listener = gw._Listener(str(self.tmp), 60, fired.set)
        ev = unittest.mock.Mock(is_directory=False, src_path=str(self.tmp / ".git" / "HEAD.lock"),
                                dest_path=str(self.tmp / ".git" / "HEAD"))
        with unittest.mock.patch.object(gw, "HEAD_DEBOUNCE", 0.1):
            listener._handler.on_any_event(ev)
            self.assertTrue(fired.wait(3))

    def test_directory_and_ignored_events_skipped(self):
        fired = threading.Event()
        listener = gw._Listener(str(self.tmp), 0.05, fired.set)
        listener._handler.on_any_event(unittest.mock.Mock(is_directory=True, src_path=str(self.tmp / "a")))
        listener._handler.on_any_event(unittest.mock.Mock(is_directory=False, dest_path="",
                                                          src_path=str(self.tmp / ".git" / "index")))
        self.assertFalse(fired.wait(0.3))


class RebuildTest(Case):
    def _run(self, rc=0, stderr=""):
        calls = []

        def run(cmd, **kw):
            calls.append((cmd, kw))
            return subprocess.CompletedProcess(cmd, rc, None, stderr)
        return calls, run

    def test_runs_analyze_index_only_low_priority(self):
        repo = _git_init(self.tmp / "r")
        calls, run = self._run()
        with unittest.mock.patch.object(gw, "gitnexus_bin", return_value="/x/gitnexus"), \
                unittest.mock.patch.object(gw._log, "record") as rec:
            self.assertEqual(gw.rebuild(str(repo), run=run), 0)
        cmd, kw = calls[0]
        self.assertEqual(cmd[-4:], ["/x/gitnexus", "analyze", "--index-only", str(repo)])
        self.assertEqual(cmd[:-4], gw.low_priority())
        self.assertEqual(kw["timeout"], gw.ANALYZE_TIMEOUT)
        self.assertEqual(rec.call_args.args[0], "gitnexuswatch.rebuild")

    def test_failure_logs_stderr_tail(self):
        repo = _git_init(self.tmp / "r")
        _calls, run = self._run(rc=3, stderr="boom")
        with unittest.mock.patch.object(gw, "gitnexus_bin", return_value="g"), \
                unittest.mock.patch.object(gw._log, "record") as rec:
            self.assertEqual(gw.rebuild(str(repo), run=run), 3)
        self.assertEqual(rec.call_args.kwargs["stderr"], "boom")
        self.assertEqual(rec.call_args.kwargs["level"], "error")

    def test_waits_for_git_operation_to_finish(self):
        repo = _git_init(self.tmp / "r")
        lock = repo / ".git" / "index.lock"
        lock.touch()
        sleeps = []

        def sleep(_s):
            sleeps.append(1)
            if len(sleeps) == 3:
                lock.unlink()
        calls, run = self._run()
        with unittest.mock.patch.object(gw, "gitnexus_bin", return_value="g"):
            self.assertEqual(gw.rebuild(str(repo), sleep=sleep, run=run), 0)
        self.assertEqual(len(sleeps), 3)
        self.assertEqual(len(calls), 1)

    def test_skips_when_git_stays_busy(self):
        repo = _git_init(self.tmp / "r")
        (repo / ".git" / "MERGE_HEAD").touch()
        calls, run = self._run()
        with unittest.mock.patch.object(gw._log, "record") as rec:
            self.assertEqual(gw.rebuild(str(repo), sleep=lambda _s: None, run=run), 0)
        self.assertEqual(calls, [])
        self.assertEqual(rec.call_args.args[0], "gitnexuswatch.skip")

    def test_low_priority_per_platform(self):
        with unittest.mock.patch.object(gw.sys, "platform", "darwin"):
            self.assertEqual(gw.low_priority(), ["taskpolicy", "-b"])
        with unittest.mock.patch.object(gw.sys, "platform", "linux"):
            self.assertEqual(gw.low_priority(), ["nice", "-n", "19"])

    def test_missing_gitnexus(self):
        with unittest.mock.patch.object(gw.shutil, "which", return_value=None), \
                self.assertRaisesRegex(GraphwatchError, "npm i -g gitnexus"):
            gw.ensure()


class ServiceTest(Case):
    def test_plist_has_own_label_script_and_gitnexus_path(self):
        with unittest.mock.patch.object(gw.shutil, "which", return_value="/n/bin/gitnexus"):
            spec = gw.service_spec()
        plist = graphwatch_service.launchd_plist(spec)
        self.assertIn("<string>com.lazygophers.gitnexuswatch</string>", plist)
        self.assertIn(str(graphwatch_service.script_path(spec)), plist)
        self.assertTrue(str(graphwatch_service.script_path(spec)).endswith("bin/gitnexuswatch"))
        self.assertIn("<key>PATH</key><string>/n/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>", plist)
        self.assertNotIn("GRAPHIFY", plist)
        self.assertEqual(graphwatch_service.launchd_plist_path(spec).name, "com.lazygophers.gitnexuswatch.plist")
        self.assertIn("Environment=PATH=/n/bin:", graphwatch_service.systemd_unit(spec))
        self.assertIn("gitnexuswatch", graphwatch_service.schtasks_create_command(spec))

    def test_control_needs_registration_named_after_tool(self):
        with unittest.mock.patch.object(graphwatch_service, "service_registered", return_value=False), \
                self.assertRaisesRegex(GraphwatchError, "先: gitnexuswatch install"):
            graphwatch_service.service_control("start", runner=lambda *a, **k: None, spec=gw.SPEC)

    def test_bin_registered(self):
        text = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text()
        self.assertIn('gitnexuswatch = "lib.cli.gitnexuswatch:main"', text)


class DaemonTest(Case):
    def test_daemon_rebuilds_on_change_and_catches_up_stale(self):
        """真跑 run_daemon：共用 Observer 监听，stale 补课入队，改文件触发重建，regraph 不跑。"""
        fresh = _git_init(self.tmp / "fresh")
        (fresh / ".gitignore").write_text(".gitnexus/\n")
        subprocess.run(["git", "-C", str(fresh), "add", "."], check=True)
        subprocess.run(["git", "-C", str(fresh), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "i"],
                       check=True)
        _write_meta(fresh, _head(fresh))
        never = _git_init(self.tmp / "never")
        gw.add_folder(fresh)
        gw.add_folder(never)
        self.home.joinpath("gitnexuswatch.yaml").write_text(
            f"folders: [{fresh}, {never}]\ndebounce: 0.3\nrebuild_concurrency: 1\n")
        built: list[str] = []
        stop = threading.Event()
        started = threading.Event()
        regraph = unittest.mock.Mock(return_value=0)

        def rebuild(repo):
            built.append(repo)
            return 0
        with unittest.mock.patch.object(gw, "rebuild", rebuild), \
                unittest.mock.patch.object(gw, "ensure", lambda: None):
            t = threading.Thread(target=gw.run_daemon, kwargs=dict(
                stop_event=stop, ensure=gw.ensure, rebuild_runner=gw.rebuild, listener_factory=gw._Listener,
                name=gw.NAME, load=gw.load_config, lock_file=gw.lock_path(), stale=gw.stale,
                poll_interval=0.2, on_started=started.set, regraph_runner=regraph), daemon=True)
            t.start()
            self.assertTrue(started.wait(10))
            self.assertTrue(gw.daemon_alive(gw.lock_path()))
            end = time.monotonic() + 10
            while str(never) not in built and time.monotonic() < end:
                time.sleep(0.05)
            self.assertIn(str(never), built)
            self.assertNotIn(str(fresh), built)
            time.sleep(0.5)
            (fresh / "a.py").write_text("x = 9\n")
            end = time.monotonic() + 10
            while str(fresh) not in built and time.monotonic() < end:
                time.sleep(0.05)
            stop.set()
            t.join(10)
        self.assertIn(str(fresh), built)
        regraph.assert_not_called()
        self.assertFalse(gw.daemon_alive(gw.lock_path()))

    def test_run_wires_gitnexus_parts_into_shared_daemon(self):
        with unittest.mock.patch.object(gw, "run_daemon") as rd:
            gw.run(stop_event="s")
        kw = rd.call_args.kwargs
        self.assertEqual((kw["name"], kw["load"], kw["stale"], kw["rebuild_runner"], kw["listener_factory"]),
                         ("gitnexuswatch", gw.load_config, gw.stale, gw.rebuild, gw._Listener))
        self.assertEqual(kw["lock_file"], gw.lock_path())


class CliTest(Case):
    def _cli(self, *argv):
        from lib.fire_base import run_cli

        with unittest.mock.patch("sys.argv", ["gitnexuswatch", *argv]):
            try:
                return run_cli(gw.GitnexuswatchCli())
            except SystemExit as e:
                return e.code

    def test_add_list_remove_round_trip(self):
        import contextlib
        import io

        repo = _git_init(self.tmp / "r")
        buf = io.StringIO()
        with unittest.mock.patch.dict(os.environ, {"CLAUDECODE": "1"}), contextlib.redirect_stdout(buf):
            self.assertIn(self._cli("add", str(repo)), (0, None))
            self.assertIn(self._cli("list"), (0, None))
            self.assertIn(self._cli("remove", str(repo)), (0, None))
        self.assertIn(f"{repo} | 未构建 | 无索引（尚未构建）", buf.getvalue())
        self.assertEqual(gw.load_config()["folders"], [])

    def test_rebuild_rejects_non_repo(self):
        self.assertEqual(self._cli("rebuild", str(self.tmp)), 2)

    def test_rebuild_passes_rc_through(self):
        repo = _git_init(self.tmp / "r")
        for rc in (0, 4):
            with self.subTest(rc=rc), unittest.mock.patch.object(gw, "rebuild", return_value=rc) as rb:
                self.assertEqual(gw.GitnexuswatchCli().rebuild(str(repo)), rc)
            rb.assert_called_once_with(str(repo))

    def test_service_commands_use_own_spec(self):
        cli = gw.GitnexuswatchCli()
        with unittest.mock.patch.object(gw, "service_control") as ctl:
            for action in ("start", "stop", "restart"):
                self.assertEqual(getattr(cli, action)(), 0)
        self.assertEqual([c.args[0] for c in ctl.call_args_list], ["start", "stop", "restart"])
        self.assertTrue(all(c.kwargs["spec"] is gw.SPEC for c in ctl.call_args_list))
        with unittest.mock.patch.object(gw, "uninstall_service") as un:
            self.assertEqual(cli.uninstall(), 0)
        self.assertIs(un.call_args.kwargs["spec"], gw.SPEC)
        with unittest.mock.patch.object(gw.shutil, "which", return_value="/n/bin/gitnexus"), \
                unittest.mock.patch.object(gw, "ensure"), \
                unittest.mock.patch.object(gw, "install_service") as inst:
            self.assertEqual(cli.install(), 0)
        self.assertIn(("PATH", "/n/bin:/usr/bin:/bin:/usr/sbin:/sbin"), inst.call_args.kwargs["spec"].env)

    def test_run_swallows_ctrl_c(self):
        with unittest.mock.patch.object(gw, "run", side_effect=KeyboardInterrupt):
            self.assertEqual(gw.GitnexuswatchCli().run(), 0)

    def test_status_and_list_render_for_humans(self):
        import contextlib
        import io

        repo = _git_init(self.tmp / "r")
        gw.add_folder(repo)
        cli = gw.GitnexuswatchCli()
        state = {"registered": False, "running": False, "pid": "-"}
        buf = io.StringIO()
        with unittest.mock.patch.object(gw, "service_state", return_value=state), \
                unittest.mock.patch.object(cli._r, "console") as console, contextlib.redirect_stderr(buf):
            self.assertEqual(cli.status(), 0)
            self.assertEqual(cli.list(), 0)
        from rich.table import Table

        tables = [c.args[0] for c in console.print.call_args_list if isinstance(c.args[0], Table)]
        self.assertEqual(len(tables), 2)
        self.assertIn("服务未注册", tables[0].title)
        self.assertEqual(tables[0].row_count, 1)

    def test_empty_registry_hint(self):
        cli = gw.GitnexuswatchCli()
        with unittest.mock.patch.object(cli, "_r") as r:
            self.assertEqual(cli._table([], "t"), 0)
        self.assertIn("gitnexuswatch add", r.info.call_args.args[0])

    def test_ensure_requires_watchdog(self):
        with unittest.mock.patch.dict("sys.modules", {"watchdog": None}), \
                self.assertRaisesRegex(GraphwatchError, "watchdog 未安装"):
            gw.ensure()

    def test_freshness_probes_daemon_when_not_told(self):
        repo = _git_init(self.tmp / "r")
        _write_meta(repo, "0" * 40)
        with unittest.mock.patch.object(gw, "daemon_alive", return_value=True) as alive:
            self.assertEqual(gw.freshness(str(repo))[0], "updating")
        alive.assert_called_once_with(gw.lock_path())


if __name__ == "__main__":
    unittest.main()
