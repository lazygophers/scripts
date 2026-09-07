"""bin/inject 的 _setup_touch_id_sudo 单元测试：分支、幂等、拒绝路径。"""

from __future__ import annotations

import pathlib
import sys
import types
import unittest
import unittest.mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from tests.test_inject_completion import load_inject


class RecordingReporter:
    """记录调用的 Reporter stub。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def ok(self, msg: str) -> None:
        self.calls.append(msg)

    def step(self, msg: str) -> None:
        self.calls.append(msg)

    def warn(self, msg: str) -> None:
        self.calls.append(msg)

    def panel(self, *_args, **_kwargs) -> None:
        pass


def _fake_pathlib(files: dict[str, str]) -> types.ModuleType:
    """伪造 pathlib 模块：Path(p).exists()/read_text() 由 files 决定。"""
    fake = types.ModuleType("pathlib")

    class _P:
        def __init__(self, p: str) -> None:
            self.p = str(p)

        def __str__(self) -> str:
            return self.p

        def exists(self) -> bool:
            return self.p in files

        def read_text(self, encoding="utf-8") -> str:
            if self.p not in files:
                raise FileNotFoundError(self.p)
            return files[self.p]

    fake.Path = _P
    return fake


class TestSetupTouchIdSudo(unittest.TestCase):
    def _run(self, monkey_files: dict[str, str], *, darwin: bool = True,
             confirm: bool = False):
        inject = load_inject()
        r = RecordingReporter()
        sudo_local = "/etc/pam.d/sudo_local"
        with unittest.mock.patch.object(inject, "pathlib", _fake_pathlib(monkey_files)), \
             unittest.mock.patch("platform.system", return_value="Darwin" if darwin else "Linux"), \
             unittest.mock.patch("subprocess.run") as sub_run, \
             unittest.mock.patch("lib.ui.ask_confirm", return_value=confirm):
            inject._setup_touch_id_sudo(r)
        return r, sub_run

    def test_non_darwin_noop(self) -> None:
        r, sub_run = self._run({}, darwin=False)
        sub_run.assert_not_called()
        self.assertEqual(r.calls, [])

    def test_already_enabled_skips(self) -> None:
        r, sub_run = self._run({
            "/usr/lib/pam/pam_tid.so.2": "",
            "/etc/pam.d/sudo": "auth include sudo_local\n",
            "/etc/pam.d/sudo_local": "auth       sufficient     pam_tid.so\n",
        })
        sub_run.assert_not_called()
        self.assertIn("跳过", " ".join(r.calls))

    def test_declined_does_not_write(self) -> None:
        r, sub_run = self._run({
            "/usr/lib/pam/pam_tid.so.2": "",
            "/etc/pam.d/sudo": "auth include sudo_local\n",
        }, confirm=False)
        sub_run.assert_not_called()

    def test_confirmed_writes_expected_content(self) -> None:
        r, sub_run = self._run({
            "/usr/lib/pam/pam_tid.so.2": "",
            "/etc/pam.d/sudo": "auth include sudo_local\n",
        }, confirm=True)
        sub_run.assert_called_once()
        args, kwargs = sub_run.call_args
        self.assertEqual(args[0][0:3], ["sudo", "tee", "/etc/pam.d/sudo_local"])
        self.assertEqual(kwargs.get("input"), "auth       sufficient     pam_tid.so\n")

    def test_no_sudo_local_hook_skips(self) -> None:
        # 系统未预留 sudo_local include → 不动
        r, sub_run = self._run({
            "/usr/lib/pam/pam_tid.so.2": "",
            "/etc/pam.d/sudo": "auth       required       pam_opendirectory.so\n",
        }, confirm=True)
        sub_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
