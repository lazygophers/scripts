"""拿 root 权限的两条路：重跑自己，或者把 sudo 授权一直续着。

`execvp` 和真 sudo 都不能在测试里跑，所以两者都打桩；断言的是「拼出来的命令行对不对」
和「该续期的时候有没有续」。
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import threading
import unittest
import unittest.mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from lib import privilege


class BecomeRootCase(unittest.TestCase):
    def test_already_root_does_nothing(self) -> None:
        with unittest.mock.patch.object(privilege.os, "geteuid", return_value=0), \
             unittest.mock.patch.object(privilege.os, "execvp") as execvp:
            privilege.become_root(pathlib.Path("/bin/x"), ["connect"])
        execvp.assert_not_called()

    def test_not_root_reruns_itself_through_sudo(self) -> None:
        with unittest.mock.patch.object(privilege.os, "geteuid", return_value=501), \
             unittest.mock.patch.object(privilege.os, "execvp") as execvp:
            privilege.become_root(pathlib.Path("/bin/ovpn"), ["connect", "--verbose"])
        name, cmd = execvp.call_args.args
        self.assertEqual(name, "sudo")
        # 解释器写绝对路径：sudo 的 PATH 里未必有 mise / venv 的那个 python
        self.assertEqual(cmd, ["sudo", sys.executable, "/bin/ovpn", "connect", "--verbose"])

    def test_extra_arguments_ride_along(self) -> None:
        with unittest.mock.patch.object(privilege.os, "geteuid", return_value=501), \
             unittest.mock.patch.object(privilege.os, "execvp") as execvp:
            privilege.become_root(pathlib.Path("/bin/archery"), ["show"], ["--config", "/tmp/a.yaml"])
        self.assertEqual(execvp.call_args.args[1][-2:], ["--config", "/tmp/a.yaml"])

    def test_a_missing_sudo_is_reported_not_swallowed(self) -> None:
        with unittest.mock.patch.object(privilege.os, "geteuid", return_value=501), \
             unittest.mock.patch.object(privilege.os, "execvp", side_effect=OSError("no sudo")):
            with self.assertRaises(privilege.NeedRoot):
                privilege.become_root(pathlib.Path("/bin/x"), [])


class KeepAliveCase(unittest.TestCase):
    def test_already_root_needs_no_session(self) -> None:
        with unittest.mock.patch.object(privilege.os, "geteuid", return_value=0), \
             unittest.mock.patch.object(privilege.subprocess, "run") as run:
            self.assertIsNone(privilege.keep_sudo_alive())
        run.assert_not_called()

    def test_it_validates_first_then_refreshes_until_stopped(self) -> None:
        calls: list[list[str]] = []
        refreshed = threading.Event()

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            if cmd[1] == "-n":
                refreshed.set()
            return subprocess.CompletedProcess(cmd, 0)

        with unittest.mock.patch.object(privilege.os, "geteuid", return_value=501), \
             unittest.mock.patch.object(privilege.subprocess, "run", fake_run):
            # 间隔压到几乎为 0，免得用例真等一分钟
            stop = privilege.keep_sudo_alive(interval=0.01)
            self.assertTrue(refreshed.wait(2), "后台没有续期")
            assert stop is not None
            stop.set()

        self.assertEqual(calls[0], ["sudo", "-v"])
        # 续期用 -n：它只刷时间戳，刷不动也不会在后台弹密码框
        self.assertIn(["sudo", "-n", "-v"], calls)

    def test_a_refused_password_fails_now_instead_of_halfway(self) -> None:
        with unittest.mock.patch.object(privilege.os, "geteuid", return_value=501), \
             unittest.mock.patch.object(
                 privilege.subprocess, "run",
                 return_value=subprocess.CompletedProcess(["sudo", "-v"], 1)):
            with self.assertRaises(privilege.NeedRoot):
                privilege.keep_sudo_alive()

    def test_a_missing_sudo_is_reported_not_swallowed(self) -> None:
        with unittest.mock.patch.object(privilege.os, "geteuid", return_value=501), \
             unittest.mock.patch.object(privilege.subprocess, "run", side_effect=OSError("no sudo")):
            with self.assertRaises(privilege.NeedRoot):
                privilege.keep_sudo_alive()


if __name__ == "__main__":
    unittest.main()
