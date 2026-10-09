"""viewer-reveal：native host 的三平台分派、协议帧、清单装/卸"""

import io
import json
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from lib.cli import viewer_reveal
from lib.cli.viewer_reveal import BROWSERS, CHROMIUM_EXTENSION_ID, GECKO_ID, HOST_NAME


class RevealDispatchTest(unittest.TestCase):
    """reveal 按平台选命令；目录和文件、存在与否的分支各验一条"""

    def _run(self, platform: str, argv_head: list[str]):
        with (
            unittest.mock.patch.object(sys, "platform", platform),
            unittest.mock.patch("pathlib.Path.exists", return_value=True),
            unittest.mock.patch("pathlib.Path.is_dir", return_value=True),
            unittest.mock.patch("subprocess.run") as run,
        ):
            viewer_reveal.reveal("/tmp/some/dir")
        return run.call_args[0][0]

    def test_darwin_uses_open_r(self):
        self.assertEqual(self._run("darwin", []), ["open", "-R", "/tmp/some/dir"])

    def test_linux_opens_the_dir_itself(self):
        self.assertEqual(self._run("linux", []), ["xdg-open", "/tmp/some/dir"])

    def test_linux_file_opens_parent(self):
        with (
            unittest.mock.patch.object(sys, "platform", "linux"),
            unittest.mock.patch("pathlib.Path.exists", return_value=True),
            unittest.mock.patch("pathlib.Path.is_dir", return_value=False),
            unittest.mock.patch("subprocess.run") as run,
        ):
            viewer_reveal.reveal("/tmp/some/file.txt")
        self.assertEqual(run.call_args[0][0], ["xdg-open", "/tmp/some"])

    def test_windows_dir_opens_explorer_without_select(self):
        with (
            unittest.mock.patch.object(sys, "platform", "win32"),
            unittest.mock.patch("pathlib.Path.exists", return_value=True),
            unittest.mock.patch("pathlib.Path.is_dir", return_value=True),
            unittest.mock.patch("pathlib.Path.resolve", return_value=Path("C:/x/dir")),
            unittest.mock.patch("subprocess.run") as run,
        ):
            viewer_reveal.reveal("C:/x/dir")
        argv = run.call_args[0][0]
        self.assertEqual(argv[0], "explorer")
        self.assertNotIn("/select,", " ".join(argv))
        self.assertIn("\\", argv[1])

    def test_windows_file_uses_select_with_backslashes(self):
        with (
            unittest.mock.patch.object(sys, "platform", "win32"),
            unittest.mock.patch("pathlib.Path.exists", return_value=True),
            unittest.mock.patch("pathlib.Path.is_dir", return_value=False),
            unittest.mock.patch("pathlib.Path.resolve", return_value=Path("C:/x/f.txt")),
            unittest.mock.patch("subprocess.run") as run,
        ):
            viewer_reveal.reveal("C:/x/f.txt")
        self.assertEqual(run.call_args[0][0], ["explorer", "/select,C:\\x\\f.txt"])

    def test_missing_path_raises(self):
        with unittest.mock.patch("pathlib.Path.exists", return_value=False):
            with self.assertRaises(FileNotFoundError):
                viewer_reveal.reveal("/tmp/nope")


class HostFrameTest(unittest.TestCase):
    """native 协议：4 字节小端长度前缀，回话成/败各一条"""

    def _host(self, payload: dict) -> dict:
        raw = json.dumps(payload).encode()
        framed = len(raw).to_bytes(4, "little") + raw
        with unittest.mock.patch.object(sys, "stdin") as stdin, \
                unittest.mock.patch.object(sys, "stdout") as stdout:
            stdin.buffer = io.BytesIO(framed)
            out = io.BytesIO()
            stdout.buffer = out
            self.assertEqual(viewer_reveal._host(), 0)
        size = int.from_bytes(out.getvalue()[:4], "little")
        return json.loads(out.getvalue()[4:4 + size])

    def test_ok_roundtrip(self):
        with unittest.mock.patch.object(viewer_reveal, "reveal", return_value="done"):
            self.assertEqual(self._host({"path": "/tmp/a"}), {"ok": True, "message": "done"})

    def test_failure_comes_back_as_json_not_crash(self):
        with unittest.mock.patch.object(viewer_reveal, "reveal", side_effect=OSError("boom")):
            reply = self._host({"path": "/tmp/a"})
            self.assertFalse(reply["ok"])
            self.assertEqual(reply["error"], "boom")


class InstallTest(unittest.TestCase):
    """清单按浏览器风味写对字段；uninstall 不留残留；探测目录不存在不写"""

    def setUp(self):
        self._home = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        # 造一个装了 Chrome 的家：探测目录存在，清单目录可以建。
        (Path(self._home.name) / "Library/Application Support/Google/Chrome").mkdir(parents=True)
        # host 脚本用临时文件顶：install 只看它在不在，不执行它。
        self._exe = Path(self._home.name) / "viewer-reveal"
        self._exe.write_text("#!/bin/sh\n")
        self._exe.chmod(0o755)

    def _patched(self):
        return (
            unittest.mock.patch("pathlib.Path.home", return_value=Path(self._home.name)),
            unittest.mock.patch.object(viewer_reveal, "host_executable", return_value=self._exe),
        )

    def _cli(self) -> viewer_reveal.ViewerRevealCli:
        return viewer_reveal.ViewerRevealCli()

    def _manifest_path(self) -> Path:
        return Path(self._home.name) / BROWSERS["darwin"]["chrome"][1] / f"{HOST_NAME}.json"

    def test_install_writes_chromium_manifest(self):
        home, exe = self._patched()
        with home, exe:
            self.assertEqual(self._cli()._install(), 1)
            manifest = json.loads(self._manifest_path().read_text())
        self.assertEqual(manifest["allowed_origins"], [f"chrome-extension://{CHROMIUM_EXTENSION_ID}/"])
        self.assertEqual(manifest["path"], str(self._exe))
        self.assertNotIn("allowed_extensions", manifest)

    def test_gecko_flavor_uses_gecko_id(self):
        (Path(self._home.name) / "Library/Application Support/Mozilla").mkdir(parents=True)
        home, exe = self._patched()
        with home, exe:
            self.assertEqual(self._cli()._install(), 2)
            manifest = json.loads(
                (Path(self._home.name) / BROWSERS["darwin"]["firefox"][1] / f"{HOST_NAME}.json").read_text()
            )
        self.assertEqual(manifest["allowed_extensions"], [GECKO_ID])
        self.assertNotIn("allowed_origins", manifest)

    def test_missing_host_script_refuses(self):
        with unittest.mock.patch("pathlib.Path.home", return_value=Path(self._home.name)), \
                unittest.mock.patch.object(viewer_reveal, "host_executable",
                                           return_value=Path("/no/such/host")):
            with self.assertRaises(FileNotFoundError):
                self._cli()._install()

    def test_uninstall_removes_manifest(self):
        home, exe = self._patched()
        with home, exe:
            self._cli()._install()
            self._cli().uninstall()
        self.assertFalse(self._manifest_path().exists())

    def test_browser_not_installed_is_skipped(self):
        with unittest.mock.patch("pathlib.Path.home", return_value=Path(self._home.name)):
            self.assertNotIn("firefox", self._cli()._destinations())


class SubprocessSmokeTest(unittest.TestCase):
    """端到端跑一次 host 子进程：帧进帧出（真实 python 进程，不 mock）"""

    def test_real_host_process_roundtrip(self):
        request = json.dumps({"path": "/tmp/definitely/not/here"}).encode()
        framed = len(request).to_bytes(4, "little") + request
        done = subprocess.run(
            [sys.executable, str(Path("bin/viewer-reveal")), "host"],
            input=framed, capture_output=True, timeout=30, cwd=Path(__file__).resolve().parents[1],
        )
        self.assertEqual(done.returncode, 0, done.stderr.decode())
        size = int.from_bytes(done.stdout[:4], "little")
        reply = json.loads(done.stdout[4:4 + size])
        self.assertFalse(reply["ok"])
        self.assertIn("not/here", reply["error"])


if __name__ == "__main__":
    unittest.main()
