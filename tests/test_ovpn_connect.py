"""lib/ovpn.py 的连接层测试：management 协议、重连策略、断开、配置读写。

tests/test_ovpn.py 覆盖的是 TOTP、挑战解析和 root 门槛；这里补 ManagementClient
（对着真实 loopback socket 跑）、_drive 主循环、connect 的重连退避、_connect_once
的启动路径，以及 disconnect / running_processes / ensure_openvpn 这些外部命令包装。

除 ManagementClient 用真 socket 外，其余外部调用（openvpn、sudo、ps、ifconfig、brew）
全部是假的，不会启动任何进程，也不碰真实网络。
"""

from __future__ import annotations

import os
import pathlib
import socket
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from lib import ovpn  # noqa: E402


def _r() -> mock.MagicMock:
    return mock.MagicMock()


class FakeMgmt:
    """假的 management 客户端：readline 按脚本吐行，send 记进 sent。"""

    def __init__(self, lines: list[str | None]) -> None:
        self._lines = list(lines)
        self.sent: list[str] = []
        self.closed = False
        self.authenticated = False

    def authenticate(self) -> None:
        self.authenticated = True

    def send(self, line: str) -> None:
        self.sent.append(line)

    def readline(self, timeout: float | None = None):
        if not self._lines:
            return None
        item = self._lines.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def close(self) -> None:
        self.closed = True


class FakeProc:
    """假的 openvpn 进程：poll 先返回 None，exit_after 次之后返回退出码。"""

    def __init__(self, returncode: int = 0, exit_after: int | None = None) -> None:
        self.returncode = returncode
        self._exit_after = exit_after
        self._polls = 0
        self.terminated = False
        self.killed = False
        self.stdout = iter(())

    def poll(self):
        self._polls += 1
        if self._exit_after is not None and self._polls > self._exit_after:
            return self.returncode
        return None

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True


# ── 配置读写 ──────────────────────────────────────────────────────────

class TestConfigIO(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self._td.name) / "sub" / "ovpn.yaml"
        self.addCleanup(self._td.cleanup)

    def test_missing_file_reads_as_empty(self) -> None:
        self.assertEqual(ovpn.load_config(self.path), {})

    def test_round_trip_and_mode_is_0600(self) -> None:
        with mock.patch.object(ovpn, "is_root", return_value=False):
            ovpn.save_config({"username": "u", "password": "p"}, self.path)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(ovpn.load_config(self.path)["username"], "u")

    def test_non_dict_yaml_reads_as_empty(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text("- a\n- b\n", encoding="utf-8")
        self.assertEqual(ovpn.load_config(self.path), {})

    def test_unreadable_file_raises_need_root(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text("x: 1\n", encoding="utf-8")
        with mock.patch.object(pathlib.Path, "read_text", side_effect=PermissionError):
            with self.assertRaises(ovpn.NeedRoot):
                ovpn.load_config(self.path)

    def test_unwritable_path_raises_need_root(self) -> None:
        with mock.patch.object(ovpn.os, "open", side_effect=PermissionError):
            with self.assertRaises(ovpn.NeedRoot):
                ovpn.save_config({"a": 1}, self.path)

    def test_root_takes_ownership_when_saving(self) -> None:
        with mock.patch.object(ovpn, "is_root", return_value=True), \
             mock.patch.object(ovpn.os, "chown") as chown:
            ovpn.save_config({"a": 1}, self.path)
        chown.assert_called_once_with(self.path, 0, 0)


class TestSecureConfig(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self._td.name) / "ovpn.yaml"
        self.path.write_text("a: 1\n", encoding="utf-8")
        self.addCleanup(self._td.cleanup)

    def test_non_root_does_nothing(self) -> None:
        with mock.patch.object(ovpn, "is_root", return_value=False):
            self.assertFalse(ovpn.secure_config(self.path))

    def test_missing_file_does_nothing(self) -> None:
        with mock.patch.object(ovpn, "is_root", return_value=True):
            self.assertFalse(ovpn.secure_config(self.path.with_name("gone.yaml")))

    def test_already_root_owned_0600_is_a_noop(self) -> None:
        st = SimpleNamespace(st_uid=0, st_mode=0o100600)
        with mock.patch.object(ovpn, "is_root", return_value=True), \
             mock.patch.object(pathlib.Path, "stat", return_value=st), \
             mock.patch.object(ovpn.os, "chown") as chown:
            self.assertFalse(ovpn.secure_config(self.path))
        chown.assert_not_called()

    def test_user_owned_file_is_taken_over(self) -> None:
        st = SimpleNamespace(st_uid=501, st_mode=0o100644)
        with mock.patch.object(ovpn, "is_root", return_value=True), \
             mock.patch.object(pathlib.Path, "stat", return_value=st), \
             mock.patch.object(ovpn.os, "chown") as chown, \
             mock.patch.object(ovpn.os, "chmod") as chmod:
            self.assertTrue(ovpn.secure_config(self.path))
        chown.assert_called_once_with(self.path, 0, 0)
        chmod.assert_called_once_with(self.path, 0o600)


class TestRealHome(unittest.TestCase):
    def test_falls_back_to_home_without_sudo_user(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(ovpn.real_home(), pathlib.Path.home())

    def test_unknown_sudo_user_falls_back_to_home(self) -> None:
        with mock.patch.dict(os.environ, {"SUDO_USER": "查无此人"}):
            self.assertEqual(ovpn.real_home(), pathlib.Path.home())

    def test_sudo_user_home_is_used(self) -> None:
        entry = SimpleNamespace(pw_dir="/Users/someone")
        with mock.patch.dict(os.environ, {"SUDO_USER": "someone"}), \
             mock.patch("pwd.getpwnam", return_value=entry):
            self.assertEqual(ovpn.real_home(), pathlib.Path("/Users/someone"))


class TestRequireRoot(unittest.TestCase):
    def test_root_returns_immediately(self) -> None:
        with mock.patch.object(ovpn, "is_root", return_value=True), \
             mock.patch.object(ovpn.os, "execvp") as ex:
            ovpn.require_root(pathlib.Path("/bin/ovpn"), ["show"])
        ex.assert_not_called()

    def test_non_root_re_execs_through_sudo(self) -> None:
        with mock.patch.object(ovpn, "is_root", return_value=False), \
             mock.patch.object(ovpn.os, "execvp") as ex:
            ovpn.require_root(pathlib.Path("/bin/ovpn"), ["show"])
        cmd = ex.call_args[0][1]
        self.assertEqual(cmd[0], "sudo")
        self.assertEqual(cmd[-2:], ["/bin/ovpn", "show"])

    def test_missing_sudo_raises_need_root(self) -> None:
        with mock.patch.object(ovpn, "is_root", return_value=False), \
             mock.patch.object(ovpn.os, "execvp", side_effect=OSError("没有 sudo")):
            with self.assertRaises(ovpn.NeedRoot):
                ovpn.require_root(pathlib.Path("/bin/ovpn"), ["show"])


# ── 二进制查找 / 安装 ─────────────────────────────────────────────────

class TestBinaryLookup(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.bindir = pathlib.Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def _make_exe(self, name: str) -> pathlib.Path:
        p = self.bindir / name
        p.write_text("#!/bin/sh\n")
        p.chmod(0o755)
        return p

    def test_found_on_path(self) -> None:
        exe = self._make_exe("openvpn")
        with mock.patch.dict(os.environ, {"PATH": str(self.bindir)}):
            self.assertEqual(ovpn.find_openvpn(), str(exe))

    def test_not_found_anywhere(self) -> None:
        with mock.patch.dict(os.environ, {"PATH": str(self.bindir)}), \
             mock.patch.object(ovpn, "_EXTRA_BIN_DIRS", ()):
            self.assertIsNone(ovpn.find_openvpn())

    def test_extra_dirs_are_searched(self) -> None:
        exe = self._make_exe("openvpn")
        with mock.patch.dict(os.environ, {"PATH": ""}), \
             mock.patch.object(ovpn, "_EXTRA_BIN_DIRS", (str(self.bindir),)):
            self.assertEqual(ovpn.find_openvpn(), str(exe))

    def test_find_brew_prefers_known_paths(self) -> None:
        with mock.patch.object(ovpn.os.path, "isfile", return_value=True), \
             mock.patch.object(ovpn.os, "access", return_value=True):
            self.assertEqual(ovpn.find_brew(), "/opt/homebrew/bin/brew")

    def test_find_brew_falls_back_to_which(self) -> None:
        with mock.patch.object(ovpn.os.path, "isfile", return_value=False), \
             mock.patch.object(ovpn.shutil, "which", return_value="/x/brew"):
            self.assertEqual(ovpn.find_brew(), "/x/brew")


class TestEnsureOpenvpn(unittest.TestCase):
    def test_already_installed(self) -> None:
        with mock.patch.object(ovpn, "find_openvpn", return_value="/usr/sbin/openvpn"):
            self.assertEqual(ovpn.ensure_openvpn(_r()), "/usr/sbin/openvpn")

    def test_no_brew_gives_up_with_instructions(self) -> None:
        r = _r()
        with mock.patch.object(ovpn, "find_openvpn", return_value=None), \
             mock.patch.object(ovpn, "find_brew", return_value=None):
            self.assertIsNone(ovpn.ensure_openvpn(r))
        r.err.assert_called_once()
        self.assertIn("brew.sh", r.info.call_args[0][0])

    def test_brew_install_failure(self) -> None:
        r = _r()
        with mock.patch.object(ovpn, "find_openvpn", return_value=None), \
             mock.patch.object(ovpn, "find_brew", return_value="/b/brew"), \
             mock.patch.object(ovpn.subprocess, "run",
                               return_value=SimpleNamespace(returncode=1)):
            self.assertIsNone(ovpn.ensure_openvpn(r))
        self.assertIn("失败", r.err.call_args[0][0])

    def test_brew_succeeds_but_binary_still_missing(self) -> None:
        r = _r()
        with mock.patch.object(ovpn, "find_openvpn", side_effect=[None, None]), \
             mock.patch.object(ovpn, "find_brew", return_value="/b/brew"), \
             mock.patch.object(ovpn.subprocess, "run",
                               return_value=SimpleNamespace(returncode=0)):
            self.assertIsNone(ovpn.ensure_openvpn(r))
        self.assertIn("仍然找不到", r.err.call_args[0][0])

    def test_brew_installs_it(self) -> None:
        r = _r()
        with mock.patch.object(ovpn, "find_openvpn", side_effect=[None, "/b/openvpn"]), \
             mock.patch.object(ovpn, "find_brew", return_value="/b/brew"), \
             mock.patch.object(ovpn.subprocess, "run",
                               return_value=SimpleNamespace(returncode=0)):
            self.assertEqual(ovpn.ensure_openvpn(r), "/b/openvpn")
        r.ok.assert_called_once()


class TestFreePort(unittest.TestCase):
    def test_returns_a_usable_port(self) -> None:
        port = ovpn._free_port()
        self.assertGreater(port, 0)
        self.assertLess(port, 65536)


# ── 运行态查询 ────────────────────────────────────────────────────────

class TestRunningProcesses(unittest.TestCase):
    def _ps(self, out: str):
        return mock.patch.object(ovpn.subprocess, "run",
                                 return_value=SimpleNamespace(stdout=out))

    def test_picks_openvpn_with_config_only(self) -> None:
        out = (
            " 101 /usr/sbin/openvpn --config /a.ovpn --verb 3\n"
            " 102 /Applications/OpenVPN/ovpnagent\n"
            " 103 /usr/sbin/openvpn --version\n"
            "\n"
            "bad line\n"
        )
        with self._ps(out):
            self.assertEqual(ovpn.running_processes(),
                             [(101, "/usr/sbin/openvpn --config /a.ovpn --verb 3")])

    def test_no_matches(self) -> None:
        with self._ps(" 1 /sbin/launchd\n"):
            self.assertEqual(ovpn.running_processes(), [])


class TestTunInterfaces(unittest.TestCase):
    def test_parses_ifconfig_output(self) -> None:
        out = (
            "en0: flags=8863\n"
            "\tinet 192.168.1.2 netmask 0xffffff00\n"
            "utun4: flags=8051\n"
            "\tinet 10.8.0.6 --> 10.8.0.5 netmask 0xffffffff\n"
            "utun5: flags=8051\n"
            "\tinet6 fe80::1 prefixlen 64\n"
        )
        with mock.patch.object(ovpn.subprocess, "run",
                               return_value=SimpleNamespace(stdout=out)):
            self.assertEqual(ovpn.tun_interfaces(), [("utun4", "10.8.0.6")])


class TestDisconnect(unittest.TestCase):
    def setUp(self) -> None:
        self.clean = mock.patch("lib.ovpn_split.clean_resolver_files")
        self.clean_mock = self.clean.start()
        self.addCleanup(self.clean.stop)
        sleep = mock.patch.object(ovpn.time, "sleep")
        sleep.start()
        self.addCleanup(sleep.stop)

    def test_nothing_running_still_cleans_resolver_files(self) -> None:
        r = _r()
        with mock.patch.object(ovpn, "running_processes", return_value=[]):
            self.assertEqual(ovpn.disconnect(r), 0)
        self.clean_mock.assert_called_once()

    def test_sigterm_is_enough(self) -> None:
        procs = [[(101, "openvpn --config a")], []]
        with mock.patch.object(ovpn, "running_processes", side_effect=lambda: procs.pop(0) if procs else []), \
             mock.patch.object(ovpn.subprocess, "run",
                               return_value=SimpleNamespace(returncode=0)) as run:
            self.assertEqual(ovpn.disconnect(_r()), 0)
        self.assertEqual(run.call_args[0][0][:3], ["sudo", "kill", "-TERM"])

    def test_kill_failure_is_propagated(self) -> None:
        with mock.patch.object(ovpn, "running_processes",
                               return_value=[(101, "openvpn --config a")]), \
             mock.patch.object(ovpn.subprocess, "run",
                               return_value=SimpleNamespace(returncode=1)):
            self.assertEqual(ovpn.disconnect(_r()), 1)

    def test_stubborn_process_gets_sigkill(self) -> None:
        r = _r()
        with mock.patch.object(ovpn, "running_processes",
                               return_value=[(101, "openvpn --config a")]), \
             mock.patch.object(ovpn.subprocess, "run",
                               return_value=SimpleNamespace(returncode=0)) as run:
            self.assertEqual(ovpn.disconnect(r), 0)
        self.assertIn("-KILL", run.call_args[0][0])
        r.warn.assert_called_once()


# ── management 协议 ───────────────────────────────────────────────────

class TestManagementClient(unittest.TestCase):
    """对着真实 loopback server 跑，验证行协议的收发与超时语义。"""

    def setUp(self) -> None:
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(1)
        self.port = self.server.getsockname()[1]
        self.addCleanup(self.server.close)
        self.conn: socket.socket | None = None

    def _accept(self, greeting: bytes = b"") -> None:
        def run():
            conn, _ = self.server.accept()
            self.conn = conn
            if greeting:
                conn.sendall(greeting)

        t = threading.Thread(target=run, daemon=True)
        t.start()
        self._thread = t

    def _client(self, greeting: bytes = b"") -> ovpn.ManagementClient:
        self._accept(greeting)
        c = ovpn.ManagementClient("127.0.0.1", self.port, "pw")
        self._thread.join(timeout=2)
        self.addCleanup(c.close)
        self.addCleanup(lambda: self.conn and self.conn.close())
        return c

    def test_readline_strips_crlf(self) -> None:
        c = self._client()
        self.conn.sendall(b">STATE:1,CONNECTED\r\n>STATE:2,EXITING\r\n")
        self.assertEqual(c.readline(timeout=2), ">STATE:1,CONNECTED")
        self.assertEqual(c.readline(timeout=2), ">STATE:2,EXITING")

    def test_readline_returns_none_when_peer_closes(self) -> None:
        c = self._client()
        self.conn.close()
        self.assertIsNone(c.readline(timeout=2))

    def test_readline_timeout_raises(self) -> None:
        c = self._client()
        with self.assertRaises(socket.timeout):
            c.readline(timeout=0.05)

    def test_send_appends_newline(self) -> None:
        c = self._client()
        c.send("hold release")
        self.conn.settimeout(2)
        self.assertEqual(self.conn.recv(64), b"hold release\n")

    def test_authenticate_replies_to_the_password_prompt(self) -> None:
        c = self._client(greeting=b"ENTER PASSWORD:")
        c.authenticate()
        self.conn.settimeout(2)
        self.assertEqual(self.conn.recv(64), b"pw\n")

    def test_authenticate_without_prompt_sends_nothing(self) -> None:
        c = self._client(greeting=b">INFO:hello\r\n")
        c.authenticate()
        self.conn.settimeout(0.2)
        with self.assertRaises(socket.timeout):
            self.conn.recv(64)

    def test_authenticate_tolerates_a_silent_server(self) -> None:
        c = self._client()
        c.sock = SimpleNamespace(settimeout=lambda _t: None,
                                 recv=mock.Mock(side_effect=socket.timeout),
                                 close=lambda: None)
        c.authenticate()  # 不应抛出
        self.assertEqual(c._buf, b"")

    def test_close_is_idempotent(self) -> None:
        c = self._client()
        c.close()
        c.close()


class TestNeedsKeepalive(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self._td.name) / "p.ovpn"
        self.addCleanup(self._td.cleanup)

    def test_profile_without_keepalive_needs_one(self) -> None:
        self.path.write_text("client\nremote vpn.example.com 1194\n")
        self.assertTrue(ovpn._needs_keepalive(self.path))

    def test_explicit_keepalive_is_respected(self) -> None:
        self.path.write_text("client\nkeepalive 10 60\n")
        self.assertFalse(ovpn._needs_keepalive(self.path))

    def test_ping_restart_counts_too(self) -> None:
        self.path.write_text("ping-restart 60\n")
        self.assertFalse(ovpn._needs_keepalive(self.path))

    def test_commented_out_keepalive_does_not_count(self) -> None:
        self.path.write_text("# keepalive 10 60\n; ping 10\n")
        self.assertTrue(ovpn._needs_keepalive(self.path))

    def test_unreadable_profile_returns_false(self) -> None:
        self.assertFalse(ovpn._needs_keepalive(self.path.with_name("gone.ovpn")))


# ── _drive 主循环 ─────────────────────────────────────────────────────

class TestDrive(unittest.TestCase):
    def _drive(self, lines, proc=None, *, secret="", verbose=False, split=None,
               last_counter=None):
        mgmt = FakeMgmt(lines)
        proc = proc or FakeProc(returncode=0)
        rc, connected, counter = ovpn._drive(
            mgmt, proc, _r(), username="u", password="p", secret=secret,
            verbose=verbose, last_otp_counter=last_counter, split=split,
        )
        return mgmt, rc, connected, counter

    def test_handshake_sends_state_and_hold_release(self) -> None:
        mgmt, rc, connected, _ = self._drive([None])
        self.assertTrue(mgmt.authenticated)
        self.assertEqual(mgmt.sent[:2], ["state on", "hold release"])
        self.assertEqual(rc, 0)
        self.assertFalse(connected)

    def test_plain_auth_fills_username_and_password(self) -> None:
        mgmt, _, _, _ = self._drive([">PASSWORD:Need 'Auth' username/password", None])
        self.assertIn('username "Auth" "u"', mgmt.sent)
        self.assertIn('password "Auth" "p"', mgmt.sent)

    def test_static_challenge_appends_the_otp(self) -> None:
        with mock.patch.object(ovpn, "totp", return_value="123456"), \
             mock.patch.object(ovpn, "wait_fresh_totp"):
            mgmt, _, _, counter = self._drive(
                [">PASSWORD:Need 'Auth' username/password SC:2,验证码", None],
                secret="ABCDEFGH",
            )
        self.assertIn('password "Auth" "p123456"', mgmt.sent)
        self.assertIsNotNone(counter)

    def test_static_challenge_without_a_secret_is_a_credential_error(self) -> None:
        _, rc, _, _ = self._drive(
            [">PASSWORD:Need 'Auth' username/password SC:0,验证码", None])
        self.assertEqual(rc, 2)

    def test_dynamic_challenge_is_answered_with_crv1(self) -> None:
        lines = [
            ">PASSWORD:Verification Failed: 'Auth' ['CRV1:R,E:state99:u:输入验证码']",
            ">PASSWORD:Need 'Auth' username/password",
            None,
        ]
        with mock.patch.object(ovpn, "totp", return_value="654321"), \
             mock.patch.object(ovpn, "wait_fresh_totp"):
            mgmt, rc, _, _ = self._drive(lines, secret="ABCDEFGH")
        self.assertIn('password "Auth" "CRV1::state99::654321"', mgmt.sent)
        self.assertEqual(rc, 0)

    def test_verification_failure_returns_two(self) -> None:
        _, rc, _, _ = self._drive([">PASSWORD:Verification Failed: 'Auth'", None])
        self.assertEqual(rc, 2)

    def test_connected_state_starts_split_tunnel(self) -> None:
        split = mock.MagicMock()
        line = ">STATE:1700000000,CONNECTED,SUCCESS,10.8.0.6,203.0.113.1"
        _, _, connected, _ = self._drive([line, None], split=split)
        self.assertTrue(connected)
        split.start.assert_called_once_with("10.8.0.6")

    def test_other_states_are_only_reported(self) -> None:
        lines = [
            ">STATE:1,RECONNECTING,ping-restart",
            ">STATE:2,EXITING,exit-with-notification",
            ">STATE:3,WAIT,",
            None,
        ]
        _, rc, connected, _ = self._drive(lines)
        self.assertEqual(rc, 0)
        self.assertFalse(connected)

    def test_verbose_echoes_management_lines(self) -> None:
        mgmt = FakeMgmt([">INFO:hi", None])
        r = _r()
        ovpn._drive(mgmt, FakeProc(), r, username="u", password="p", secret="",
                    verbose=True, last_otp_counter=None, split=None)
        r.output.assert_called_once()

    def test_socket_timeout_just_loops(self) -> None:
        _, rc, _, _ = self._drive([socket.timeout(), ">STATE:1,WAIT,", None])
        self.assertEqual(rc, 0)

    def test_socket_error_breaks_the_loop(self) -> None:
        _, rc, _, _ = self._drive([OSError("管理口断了")])
        self.assertEqual(rc, 0)

    def test_reconnecting_then_management_eof_triggers_outer_retry(self) -> None:
        lines = [
            ">STATE:1,CONNECTED,SUCCESS,10.8.0.6,203.0.113.1",
            ">STATE:2,RECONNECTING,server-pushed-connection-reset",
            None,
        ]
        _, rc, connected, _ = self._drive(lines)
        self.assertEqual(rc, 1)
        self.assertTrue(connected)

    def test_reconnecting_then_management_error_triggers_outer_retry(self) -> None:
        lines = [
            ">STATE:1,CONNECTED,SUCCESS,10.8.0.6,203.0.113.1",
            ">STATE:2,RECONNECTING,server-pushed-connection-reset",
            OSError("management socket closed"),
        ]
        _, rc, connected, _ = self._drive(lines)
        self.assertEqual(rc, 1)
        self.assertTrue(connected)

    def test_process_exit_ends_the_loop(self) -> None:
        proc = FakeProc(returncode=3, exit_after=0)
        _, rc, _, _ = self._drive([], proc=proc)
        self.assertEqual(rc, 3)

    def test_zero_exit_code_from_a_dead_process_becomes_one(self) -> None:
        proc = FakeProc(returncode=0, exit_after=0)
        _, rc, _, _ = self._drive([], proc=proc)
        self.assertEqual(rc, 1)


# ── connect 的重连策略 ────────────────────────────────────────────────

class TestConnectRetry(unittest.TestCase):
    def setUp(self) -> None:
        sleep = mock.patch.object(ovpn.time, "sleep")
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)

    def _connect(self, results, cfg=None, **kw) -> int:
        with mock.patch.object(ovpn, "_connect_once", side_effect=results):
            return ovpn.connect(cfg or {}, _r(), **kw)

    def test_clean_exit_does_not_retry(self) -> None:
        rc = self._connect([(0, True, None)])
        self.assertEqual(rc, 0)
        self.sleep.assert_not_called()

    def test_credential_error_does_not_retry(self) -> None:
        self.assertEqual(self._connect([(2, False, None)]), 2)

    def test_missing_binary_does_not_retry(self) -> None:
        self.assertEqual(self._connect([(127, False, None)]), 127)

    def test_ctrl_c_does_not_retry(self) -> None:
        self.assertEqual(self._connect([(130, False, None)]), 130)

    def test_reconnect_disabled_returns_the_error(self) -> None:
        self.assertEqual(self._connect([(1, False, None)], reconnect=False), 1)

    def test_config_can_disable_reconnect(self) -> None:
        self.assertEqual(self._connect([(1, False, None)], {"auto_reconnect": False}), 1)

    def test_backoff_doubles_and_caps_at_60s(self) -> None:
        results = [(1, False, None)] * 6 + [(0, False, None)]
        self._connect(results, reconnect=True)
        delays = [c[0][0] for c in self.sleep.call_args_list]
        self.assertEqual(delays[:4], [10.0, 20.0, 40.0, 60.0])
        self.assertEqual(delays[-1], 60.0)

    def test_a_successful_connection_resets_the_backoff(self) -> None:
        results = [(1, False, None), (1, True, None), (0, False, None)]
        self._connect(results, reconnect=True)
        delays = [c[0][0] for c in self.sleep.call_args_list]
        self.assertEqual(delays, [10.0, 5.0])

    def test_reconnect_max_gives_up(self) -> None:
        r = _r()
        with mock.patch.object(ovpn, "_connect_once", return_value=(1, False, None)):
            rc = ovpn.connect({}, r, reconnect=True, reconnect_max=2)
        self.assertEqual(rc, 1)
        self.assertIn("已停止", r.err.call_args[0][0])

    def test_reconnect_max_comes_from_the_config(self) -> None:
        with mock.patch.object(ovpn, "_connect_once", return_value=(1, False, None)):
            rc = ovpn.connect({"reconnect_max": 1}, _r(), reconnect=True)
        self.assertEqual(rc, 1)

    def test_ctrl_c_while_waiting_returns_130(self) -> None:
        self.sleep.side_effect = KeyboardInterrupt
        with mock.patch.object(ovpn, "_connect_once", return_value=(1, False, None)):
            self.assertEqual(ovpn.connect({}, _r(), reconnect=True), 130)

    def test_the_otp_counter_is_threaded_between_attempts(self) -> None:
        seen: list[int | None] = []

        def once(cfg, reporter, *, verbose=False, last_otp_counter=None):
            seen.append(last_otp_counter)
            return (1, False, 42) if len(seen) == 1 else (0, False, 42)

        with mock.patch.object(ovpn, "_connect_once", side_effect=once):
            ovpn.connect({}, _r(), reconnect=True)
        self.assertEqual(seen, [None, 42])


# ── _connect_once 启动路径 ────────────────────────────────────────────

class TestConnectOnce(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._td.name)
        self.profile = self.root / "p.ovpn"
        self.profile.write_text("client\nkeepalive 10 60\n")
        self.addCleanup(self._td.cleanup)

        self.split = mock.MagicMock()
        self.split.enabled = False
        patches = [
            mock.patch("lib.ovpn_split.SplitTunnel", return_value=self.split),
            mock.patch("lib.ovpn_split.clean_resolver_files"),
            mock.patch.object(ovpn.time, "sleep"),
            mock.patch.object(ovpn, "ensure_openvpn", return_value="/usr/sbin/openvpn"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _cfg(self, **kw) -> dict:
        base = {"config": str(self.profile), "username": "u", "password": "p"}
        base.update(kw)
        return base

    def test_missing_binary_returns_127(self) -> None:
        with mock.patch.object(ovpn, "ensure_openvpn", return_value=None):
            rc, connected, _ = ovpn._connect_once(self._cfg(), _r())
        self.assertEqual((rc, connected), (127, False))

    def test_missing_profile_returns_2(self) -> None:
        rc, connected, _ = ovpn._connect_once(self._cfg(config=""), _r())
        self.assertEqual((rc, connected), (2, False))

    def test_nonexistent_profile_returns_2(self) -> None:
        rc, _, _ = ovpn._connect_once(self._cfg(config=str(self.root / "gone.ovpn")), _r())
        self.assertEqual(rc, 2)

    def test_openvpn_exiting_early_is_reported(self) -> None:
        proc = FakeProc(returncode=4, exit_after=0)
        r = _r()
        with mock.patch.object(ovpn.subprocess, "Popen", return_value=proc), \
             mock.patch.object(ovpn, "ManagementClient", side_effect=OSError):
            rc, connected, _ = ovpn._connect_once(self._cfg(), r)
        self.assertEqual((rc, connected), (4, False))
        r.err.assert_called_once()

    def test_unreachable_management_port_gives_up(self) -> None:
        proc = FakeProc(returncode=0)
        with mock.patch.object(ovpn.subprocess, "Popen", return_value=proc), \
             mock.patch.object(ovpn, "ManagementClient", side_effect=OSError):
            rc, connected, _ = ovpn._connect_once(self._cfg(), _r())
        self.assertEqual((rc, connected), (1, False))
        self.assertTrue(proc.terminated)

    def test_happy_path_hands_off_to_drive(self) -> None:
        proc = FakeProc(returncode=0)
        mgmt = mock.MagicMock()
        with mock.patch.object(ovpn.subprocess, "Popen", return_value=proc), \
             mock.patch.object(ovpn, "ManagementClient", return_value=mgmt), \
             mock.patch.object(ovpn, "_drive", return_value=(0, True, 7)) as drive:
            rc, connected, counter = ovpn._connect_once(self._cfg(), _r())
        self.assertEqual((rc, connected, counter), (0, True, 7))
        drive.assert_called_once()
        mgmt.close.assert_called_once()

    def test_command_line_carries_the_management_flags(self) -> None:
        proc = FakeProc(returncode=0)
        with mock.patch.object(ovpn.subprocess, "Popen", return_value=proc) as popen, \
             mock.patch.object(ovpn, "ManagementClient", return_value=mock.MagicMock()), \
             mock.patch.object(ovpn, "_drive", return_value=(0, True, None)):
            ovpn._connect_once(self._cfg(extra_args=["--float"]), _r())
        cmd = popen.call_args[0][0]
        self.assertEqual(cmd[:2], ["sudo", "/usr/sbin/openvpn"])
        self.assertIn("--management-hold", cmd)
        self.assertIn("--float", cmd)
        self.assertIn("--verb", cmd)
        self.assertEqual(cmd[cmd.index("--verb") + 1], "1")

    def test_split_mode_forces_verb_3_and_route_nopull(self) -> None:
        self.split.enabled = True
        proc = FakeProc(returncode=0)
        with mock.patch.object(ovpn.subprocess, "Popen", return_value=proc) as popen, \
             mock.patch.object(ovpn, "ManagementClient", return_value=mock.MagicMock()), \
             mock.patch.object(ovpn, "_drive", return_value=(0, True, None)):
            ovpn._connect_once(self._cfg(), _r())
        cmd = popen.call_args[0][0]
        self.assertEqual(cmd[cmd.index("--verb") + 1], "3")
        self.assertIn("--route-nopull", cmd)
        self.split.stop.assert_called_once()

    def test_split_can_be_switched_off_per_run(self) -> None:
        self.split.enabled = True
        proc = FakeProc(returncode=0)
        with mock.patch.object(ovpn.subprocess, "Popen", return_value=proc) as popen, \
             mock.patch.object(ovpn, "ManagementClient", return_value=mock.MagicMock()), \
             mock.patch.object(ovpn, "_drive", return_value=(0, True, None)):
            ovpn._connect_once(self._cfg(split_tunnel=False), _r())
        self.assertNotIn("--route-nopull", popen.call_args[0][0])

    def test_keepalive_is_added_when_the_profile_lacks_one(self) -> None:
        self.profile.write_text("client\n")
        proc = FakeProc(returncode=0)
        with mock.patch.object(ovpn.subprocess, "Popen", return_value=proc) as popen, \
             mock.patch.object(ovpn, "ManagementClient", return_value=mock.MagicMock()), \
             mock.patch.object(ovpn, "_drive", return_value=(0, True, None)):
            ovpn._connect_once(self._cfg(), _r())
        self.assertIn("--ping-restart", popen.call_args[0][0])

    def test_a_live_process_is_terminated_on_the_way_out(self) -> None:
        proc = FakeProc(returncode=0)
        with mock.patch.object(ovpn.subprocess, "Popen", return_value=proc), \
             mock.patch.object(ovpn, "ManagementClient", return_value=mock.MagicMock()), \
             mock.patch.object(ovpn, "_drive", return_value=(0, True, None)):
            ovpn._connect_once(self._cfg(), _r())
        self.assertTrue(proc.terminated)

    def test_a_hung_process_is_killed(self) -> None:
        proc = FakeProc(returncode=0)
        proc.wait = mock.Mock(side_effect=ovpn.subprocess.TimeoutExpired("openvpn", 10))
        with mock.patch.object(ovpn.subprocess, "Popen", return_value=proc), \
             mock.patch.object(ovpn, "ManagementClient", return_value=mock.MagicMock()), \
             mock.patch.object(ovpn, "_drive", return_value=(0, True, None)):
            ovpn._connect_once(self._cfg(), _r())
        self.assertTrue(proc.killed)

    def test_log_pump_forwards_pushed_dns_to_the_split_tunnel(self) -> None:
        """抓住喂给日志线程的 stdout，验证 PUSH_REPLY 被解析并转给 SplitTunnel。"""
        self.split.enabled = True
        self.split.note_pushed_dns.return_value = ["10.8.0.1"]
        proc = FakeProc(returncode=0)
        proc.stdout = iter([
            "PUSH: Received control message: 'PUSH_REPLY,dhcp-option DNS 10.8.0.1'\n",
            "普通日志，非关键行\n",
            "Initialization Sequence Completed\n",
            "ERROR: 关键行\n",
            "\n",
        ])
        r = _r()
        real_thread = threading.Thread
        started: list[threading.Thread] = []

        class SyncThread:
            """把日志线程改成同步执行，测试里不引入时序不确定性。"""

            def __init__(self, target=None, daemon=None):
                self._target = target

            def start(self):
                self._target()
                started.append(self)

        with mock.patch.object(ovpn.subprocess, "Popen", return_value=proc), \
             mock.patch.object(ovpn, "ManagementClient", return_value=mock.MagicMock()), \
             mock.patch.object(ovpn, "_drive", return_value=(0, True, None)), \
             mock.patch.object(threading, "Thread", SyncThread):
            ovpn._connect_once(self._cfg(), r)

        self.assertEqual(threading.Thread, real_thread)  # patch 已还原
        self.split.note_pushed_dns.assert_called_once_with(["10.8.0.1"])
        printed = [c[0][0] for c in r.output.call_args_list]
        self.assertIn("VPN 已经连通；现在可以访问需要 VPN 的内网站点｜原始日志: Initialization Sequence Completed", printed)
        self.assertIn("ERROR: 关键行", printed)
        self.assertNotIn("普通日志，非关键行", printed)


if __name__ == "__main__":
    unittest.main()
