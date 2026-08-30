"""lib/ovpn.py 单元测试：TOTP、management 协议解析、密码回复拼装、配置读写。"""

from __future__ import annotations

import base64
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from lib import ovpn as ov


class TestTotp(unittest.TestCase):
    # RFC 6238 附录 B 测试向量：seed = ASCII "12345678901234567890"
    SEED = base64.b32encode(b"12345678901234567890").decode("ascii")

    def test_rfc6238_vectors(self):
        cases = [
            (59, "287082"),
            (1111111109, "081804"),
            (1111111111, "050471"),
            (1234567890, "005924"),
            (2000000000, "279037"),
        ]
        for at, expected in cases:
            with self.subTest(at=at):
                self.assertEqual(ov.totp(self.SEED, at=at), expected)

    def test_digits_are_padded(self):
        self.assertEqual(len(ov.totp(self.SEED, at=1234567890)), 6)

    def test_lowercase_and_spaces_accepted(self):
        spaced = " ".join(self.SEED[i:i + 4] for i in range(0, len(self.SEED), 4)).lower()
        self.assertEqual(ov.totp(spaced, at=59), "287082")

    def test_invalid_secret_raises(self):
        with self.assertRaises(ValueError):
            ov.totp("!!!not-base32!!!")


class TestNormalizeSecret(unittest.TestCase):
    def test_otpauth_uri(self):
        uri = "otpauth://totp/Corp:me@x.com?secret=JBSWY3DPEHPK3PXP&issuer=Corp"
        self.assertEqual(ov.normalize_secret(uri), "JBSWY3DPEHPK3PXP")

    def test_strips_spaces_and_dashes(self):
        self.assertEqual(ov.normalize_secret(" jbsw-y3dp ehpk3pxp "), "JBSWY3DPEHPK3PXP")

    def test_empty(self):
        self.assertEqual(ov.normalize_secret(""), "")


class TestParseNeedAuth(unittest.TestCase):
    def test_plain(self):
        hit, flags = ov.parse_need_auth(">PASSWORD:Need 'Auth' username/password")
        self.assertTrue(hit)
        self.assertIsNone(flags)

    def test_static_challenge(self):
        hit, flags = ov.parse_need_auth(
            ">PASSWORD:Need 'Auth' username/password SC:1,Enter your token")
        self.assertTrue(hit)
        self.assertEqual(flags, 1)

    def test_static_challenge_concat_format(self):
        _, flags = ov.parse_need_auth(">PASSWORD:Need 'Auth' username/password SC:3,Token")
        self.assertEqual(flags, 3)

    def test_private_key_prompt_not_matched(self):
        hit, _ = ov.parse_need_auth(">PASSWORD:Need 'Private Key' password")
        self.assertFalse(hit)


class TestParseDynamicChallenge(unittest.TestCase):
    def test_extracts_state_id(self):
        user_b64 = base64.b64encode(b"alice").decode()
        line = (f">PASSWORD:Verification Failed: 'Auth' "
                f"['CRV1:R,E:Om1cQ1==:{user_b64}:Enter token']")
        self.assertEqual(ov.parse_dynamic_challenge(line), "Om1cQ1==")

    def test_plain_failure_returns_none(self):
        self.assertIsNone(ov.parse_dynamic_challenge(">PASSWORD:Verification Failed: 'Auth'"))

    def test_need_auth_line_returns_none(self):
        self.assertIsNone(ov.parse_dynamic_challenge(">PASSWORD:Need 'Auth' username/password"))


class TestParsePushedDns(unittest.TestCase):
    def test_extracts_ipv4_and_ipv6(self):
        line = ("Fri Aug 29 10:00:00 2026 PUSH: Received control message: "
                "'PUSH_REPLY,dhcp-option DNS 10.8.0.1,dhcp-option DNS6 fd00::1,"
                "route 10.8.0.0 255.255.0.0,ping 10'")
        self.assertEqual(ov.parse_pushed_dns(line), ["10.8.0.1", "fd00::1"])

    def test_dedups(self):
        line = "PUSH_REPLY,dhcp-option DNS 10.8.0.1,dhcp-option DNS 10.8.0.1"
        self.assertEqual(ov.parse_pushed_dns(line), ["10.8.0.1"])

    def test_ignores_non_push_lines(self):
        self.assertEqual(ov.parse_pushed_dns("dhcp-option DNS 10.8.0.1"), [])
        self.assertEqual(ov.parse_pushed_dns("PUSH_REPLY,route 10.8.0.0 255.255.0.0"), [])


class TestBuildPasswordReply(unittest.TestCase):
    def test_plain_password(self):
        got = ov.build_password_reply("pw", otp=None, sc_flags=None, crv_state=None)
        self.assertEqual(got, "pw")

    def test_static_challenge_scrv1(self):
        got = ov.build_password_reply("pw", otp="123456", sc_flags=1, crv_state=None)
        self.assertEqual(got, f"SCRV1:{base64.b64encode(b'pw').decode()}:"
                              f"{base64.b64encode(b'123456').decode()}")

    def test_static_challenge_concat_when_format_bit_set(self):
        got = ov.build_password_reply("pw", otp="123456", sc_flags=3, crv_state=None)
        self.assertEqual(got, "pw123456")

    def test_static_challenge_without_secret_raises(self):
        with self.assertRaises(ValueError):
            ov.build_password_reply("pw", otp=None, sc_flags=1, crv_state=None)

    def test_dynamic_challenge(self):
        got = ov.build_password_reply("pw", otp="123456", sc_flags=None, crv_state="ST1")
        self.assertEqual(got, "CRV1::ST1::123456")

    def test_dynamic_challenge_wins_over_static(self):
        got = ov.build_password_reply("pw", otp="000000", sc_flags=1, crv_state="ST1")
        self.assertEqual(got, "CRV1::ST1::000000")


class TestQuote(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(ov._quote("foo"), '"foo"')

    def test_escapes_quote_and_backslash(self):
        self.assertEqual(ov._quote('foo"bar'), '"foo\\"bar"')
        self.assertEqual(ov._quote("a\\b"), '"a\\\\b"')


class TestConfigIO(unittest.TestCase):
    def test_roundtrip_and_permissions(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "sub" / "ovpn.yaml"
            data = {"config": "/tmp/x.ovpn", "username": "u", "password": "p",
                    "totp_secret": "JBSWY3DPEHPK3PXP"}
            ov.save_config(data, p)
            self.assertEqual(ov.load_config(p), data)
            self.assertEqual(os.stat(p).st_mode & 0o777, 0o600)

    def test_missing_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(ov.load_config(pathlib.Path(d) / "nope.yaml"), {})

    def test_non_mapping_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "x.yaml"
            p.write_text("- a\n- b\n", encoding="utf-8")
            self.assertEqual(ov.load_config(p), {})


class TestRootGate(unittest.TestCase):
    def test_real_home_follows_sudo_user(self):
        from unittest import mock

        with mock.patch.dict(os.environ, {"SUDO_USER": "nobody-xyz"}, clear=False), \
             mock.patch("pwd.getpwnam") as getpw:
            getpw.return_value.pw_dir = "/Users/someone"
            self.assertEqual(ov.real_home(), pathlib.Path("/Users/someone"))

    def test_real_home_falls_back_when_user_unknown(self):
        from unittest import mock

        with mock.patch.dict(os.environ, {"SUDO_USER": "nobody-xyz"}, clear=False), \
             mock.patch("pwd.getpwnam", side_effect=KeyError):
            self.assertEqual(ov.real_home(), pathlib.Path.home())

    def test_require_root_reexecs_with_sudo(self):
        from unittest import mock

        with mock.patch.object(ov, "is_root", return_value=False), \
             mock.patch.object(ov.os, "execvp") as execvp:
            ov.require_root(pathlib.Path("/x/bin/ovpn"), ["show"])
        execvp.assert_called_once()
        name, cmd = execvp.call_args[0]
        self.assertEqual(name, "sudo")
        self.assertEqual(cmd[:2], ["sudo", sys.executable])
        self.assertEqual(cmd[2:], ["/x/bin/ovpn", "show"])

    def test_require_root_noop_when_root(self):
        from unittest import mock

        with mock.patch.object(ov, "is_root", return_value=True), \
             mock.patch.object(ov.os, "execvp") as execvp:
            ov.require_root(pathlib.Path("/x/bin/ovpn"), ["show"])
        execvp.assert_not_called()

    def test_load_config_permission_denied_becomes_need_root(self):
        from unittest import mock

        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "ovpn.yaml"
            p.write_text("username: u\n", encoding="utf-8")
            with mock.patch.object(pathlib.Path, "read_text", side_effect=PermissionError):
                with self.assertRaises(ov.NeedRoot):
                    ov.load_config(p)

    def test_secure_config_noop_for_non_root(self):
        from unittest import mock

        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "ovpn.yaml"
            p.write_text("username: u\n", encoding="utf-8")
            with mock.patch.object(ov, "is_root", return_value=False):
                self.assertFalse(ov.secure_config(p))

    def test_secure_config_chowns_user_owned_file(self):
        from unittest import mock

        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "ovpn.yaml"
            p.write_text("username: u\n", encoding="utf-8")
            p.chmod(0o644)
            with mock.patch.object(ov, "is_root", return_value=True), \
                 mock.patch.object(ov.os, "chown") as chown:
                self.assertTrue(ov.secure_config(p))
            chown.assert_called_once_with(p, 0, 0)
            self.assertEqual(os.stat(p).st_mode & 0o777, 0o600)

    def test_secure_config_noop_when_already_root_owned(self):
        from unittest import mock

        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "ovpn.yaml"
            p.write_text("username: u\n", encoding="utf-8")
            p.chmod(0o600)
            fake_stat = os.stat(p)

            class _St:
                st_uid = 0
                st_mode = fake_stat.st_mode

            with mock.patch.object(ov, "is_root", return_value=True), \
                 mock.patch.object(pathlib.Path, "stat", return_value=_St()), \
                 mock.patch.object(ov.os, "chown") as chown:
                self.assertFalse(ov.secure_config(p))
            chown.assert_not_called()


class TestFindOpenvpn(unittest.TestCase):
    def test_finds_binary_in_path(self):
        old = os.environ.get("PATH", "")
        old_extra = ov._EXTRA_BIN_DIRS
        with tempfile.TemporaryDirectory() as d:
            fake = pathlib.Path(d) / "openvpn"
            fake.write_text("#!/bin/sh\n", encoding="utf-8")
            fake.chmod(0o755)
            try:
                os.environ["PATH"] = d
                ov._EXTRA_BIN_DIRS = ()
                self.assertEqual(ov.find_openvpn(), str(fake))
            finally:
                os.environ["PATH"] = old
                ov._EXTRA_BIN_DIRS = old_extra



class TestRunningProcesses(unittest.TestCase):
    """running_processes 只认带 --config 的 openvpn 进程。"""

    def _with_ps_output(self, text):
        import subprocess as sp
        from unittest import mock

        completed = sp.CompletedProcess(args=["ps"], returncode=0, stdout=text, stderr="")
        return mock.patch.object(ov.subprocess, "run", return_value=completed)

    def test_picks_config_processes(self):
        ps = (
            "  501 /opt/homebrew/sbin/openvpn --config /x/a.ovpn --management 127.0.0.1 1\n"
            "  777 /Applications/OpenVPN Connect.app/Contents/MacOS/ovpnagent\n"
            "  888 /usr/sbin/openvpn --version\n"
        )
        with self._with_ps_output(ps):
            got = ov.running_processes()
        self.assertEqual([p for p, _ in got], [501])

    def test_empty_when_nothing_matches(self):
        with self._with_ps_output("  1 /sbin/launchd\n"):
            self.assertEqual(ov.running_processes(), [])


class TestTunInterfaces(unittest.TestCase):
    def test_parses_utun_inet(self):
        import subprocess as sp
        from unittest import mock

        ifc = (
            "en0: flags=8863<UP>\n"
            "\tinet 192.168.1.10 netmask 0xffffff00\n"
            "utun3: flags=8051<UP>\n"
            "\tinet 10.8.0.6 --> 10.8.0.5 netmask 0xffffffff\n"
            "utun4: flags=8051<UP>\n"
        )
        completed = sp.CompletedProcess(args=["ifconfig"], returncode=0, stdout=ifc, stderr="")
        with mock.patch.object(ov.subprocess, "run", return_value=completed):
            self.assertEqual(ov.tun_interfaces(), [("utun3", "10.8.0.6")])


class _FakeReporter:
    """收集 Reporter 调用，断言用。"""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def rec(*args, **kwargs):
            self.calls.append((name, args[0] if args else ""))
        return rec


class TestEnsureOpenvpn(unittest.TestCase):
    def test_returns_existing_binary_without_installing(self):
        from unittest import mock

        with mock.patch.object(ov, "find_openvpn", return_value="/usr/sbin/openvpn"), \
             mock.patch.object(ov.subprocess, "run") as run:
            self.assertEqual(ov.ensure_openvpn(_FakeReporter()), "/usr/sbin/openvpn")
        run.assert_not_called()

    def test_installs_via_brew_when_missing(self):
        import subprocess as sp
        from unittest import mock

        found = iter([None, "/opt/homebrew/sbin/openvpn"])
        completed = sp.CompletedProcess(args=["brew"], returncode=0)
        with mock.patch.object(ov, "find_openvpn", side_effect=lambda: next(found)), \
             mock.patch.object(ov, "find_brew", return_value="/opt/homebrew/bin/brew"), \
             mock.patch.object(ov.subprocess, "run", return_value=completed) as run:
            got = ov.ensure_openvpn(_FakeReporter())
        self.assertEqual(got, "/opt/homebrew/sbin/openvpn")
        run.assert_called_once_with(["/opt/homebrew/bin/brew", "install", "openvpn"])

    def test_returns_none_without_brew(self):
        from unittest import mock

        with mock.patch.object(ov, "find_openvpn", return_value=None), \
             mock.patch.object(ov, "find_brew", return_value=None), \
             mock.patch.object(ov.subprocess, "run") as run:
            self.assertIsNone(ov.ensure_openvpn(_FakeReporter()))
        run.assert_not_called()

    def test_returns_none_when_brew_install_fails(self):
        import subprocess as sp
        from unittest import mock

        completed = sp.CompletedProcess(args=["brew"], returncode=1)
        with mock.patch.object(ov, "find_openvpn", return_value=None), \
             mock.patch.object(ov, "find_brew", return_value="/opt/homebrew/bin/brew"), \
             mock.patch.object(ov.subprocess, "run", return_value=completed):
            self.assertIsNone(ov.ensure_openvpn(_FakeReporter()))

    def test_returns_none_when_still_missing_after_install(self):
        import subprocess as sp
        from unittest import mock

        completed = sp.CompletedProcess(args=["brew"], returncode=0)
        with mock.patch.object(ov, "find_openvpn", return_value=None), \
             mock.patch.object(ov, "find_brew", return_value="/opt/homebrew/bin/brew"), \
             mock.patch.object(ov.subprocess, "run", return_value=completed):
            self.assertIsNone(ov.ensure_openvpn(_FakeReporter()))


class TestTotpFreshness(unittest.TestCase):
    def test_counter_advances_with_period(self):
        self.assertEqual(ov.totp_counter(at=0), 0)
        self.assertEqual(ov.totp_counter(at=29.9), 0)
        self.assertEqual(ov.totp_counter(at=30), 1)

    def test_no_wait_when_counter_is_new(self):
        from unittest import mock

        with mock.patch.object(ov.time, "sleep") as sleep:
            ov.wait_fresh_totp(None)
            ov.wait_fresh_totp(ov.totp_counter() - 1)
        sleep.assert_not_called()

    def test_waits_out_the_window_when_code_already_used(self):
        from unittest import mock

        with mock.patch.object(ov.time, "sleep") as sleep:
            ov.wait_fresh_totp(ov.totp_counter())
        sleep.assert_called_once()
        self.assertGreater(sleep.call_args[0][0], 0)
        self.assertLessEqual(sleep.call_args[0][0], 30.5)


class TestNeedsKeepalive(unittest.TestCase):
    def _profile(self, text):
        d = tempfile.mkdtemp()
        p = pathlib.Path(d) / "x.ovpn"
        p.write_text(text, encoding="utf-8")
        return p

    def test_true_when_profile_has_none(self):
        self.assertTrue(ov._needs_keepalive(self._profile("client\nremote vpn.x 1194\n")))

    def test_false_when_keepalive_present(self):
        self.assertFalse(ov._needs_keepalive(self._profile("client\nkeepalive 10 60\n")))

    def test_false_when_ping_restart_present(self):
        self.assertFalse(ov._needs_keepalive(self._profile("client\nping-restart 120\n")))

    def test_comment_does_not_count(self):
        self.assertTrue(ov._needs_keepalive(self._profile("client\n# keepalive 10 60\n")))

    def test_missing_file_returns_false(self):
        self.assertFalse(ov._needs_keepalive(pathlib.Path("/nope/nope.ovpn")))


class TestReconnectLoop(unittest.TestCase):
    """connect() 的外层重连循环：哪些退出码重连、哪些直接返回。"""

    def _run(self, results, **kw):
        """results: [(rc, connected), ...] 依次作为 _connect_once 的返回。"""
        from unittest import mock

        seq = iter(results)
        calls = []

        def fake_once(cfg, reporter, *, verbose=False, last_otp_counter=None):
            calls.append(last_otp_counter)
            rc, connected = next(seq)
            return rc, connected, last_otp_counter
        with mock.patch.object(ov, "_connect_once", side_effect=fake_once), \
             mock.patch.object(ov.time, "sleep"):
            rc = ov.connect({}, _FakeReporter(), **kw)
        return rc, len(calls)

    def test_clean_exit_does_not_reconnect(self):
        self.assertEqual(self._run([(0, True)]), (0, 1))

    def test_bad_credentials_do_not_reconnect(self):
        self.assertEqual(self._run([(2, False)]), (2, 1))

    def test_missing_binary_does_not_reconnect(self):
        self.assertEqual(self._run([(127, False)]), (127, 1))

    def test_interrupt_does_not_reconnect(self):
        self.assertEqual(self._run([(130, True)]), (130, 1))

    def test_link_failure_reconnects_until_clean_exit(self):
        rc, n = self._run([(1, True), (1, True), (0, True)])
        self.assertEqual((rc, n), (0, 3))

    def test_reconnect_flag_off_returns_immediately(self):
        self.assertEqual(self._run([(1, True)], reconnect=False), (1, 1))

    def test_reconnect_max_gives_up(self):
        rc, n = self._run([(1, False)] * 5, reconnect_max=2)
        self.assertEqual((rc, n), (1, 3))

    def test_successful_connect_resets_the_attempt_counter(self):
        """连上过就把失败计数清零，长跑时不会因为累计次数被 max 挡住。"""
        rc, n = self._run([(1, True)] * 6 + [(0, True)], reconnect_max=2)
        self.assertEqual((rc, n), (0, 7))

    def test_reads_auto_reconnect_from_config(self):
        from unittest import mock

        seq = iter([(1, True)])
        with mock.patch.object(ov, "_connect_once",
                               side_effect=lambda *a, **k: (*next(seq), None)), \
             mock.patch.object(ov.time, "sleep"):
            self.assertEqual(ov.connect({"auto_reconnect": False}, _FakeReporter()), 1)


class TestBinReexecsThroughSudo(unittest.TestCase):
    """bin/ovpn 黑盒：非 root 跑 `show` 要真的 execvp 到 sudo。

    盯的是 bin/ovpn 里 `require_root(SCRIPT_PATH, ORIG_ARGV[1:])` 用到的两个模块级
    常量——漏定义时只在运行时炸 NameError，import 和 py_compile 都发现不了。
    """

    @unittest.skipIf(os.geteuid() == 0, "已经是 root，不会走 sudo 重跑")
    def test_show_execs_sudo(self):
        import subprocess

        repo_root = pathlib.Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as bindir:
            fake_sudo = pathlib.Path(bindir) / "sudo"
            fake_sudo.write_text('#!/bin/sh\necho "FAKE-SUDO $@"\n', encoding="utf-8")
            fake_sudo.chmod(0o755)
            env = dict(os.environ, SCRIPTS_NO_SAY="1",
                       PATH=f"{bindir}:{os.environ.get('PATH', '')}")
            proc = subprocess.run([sys.executable, str(repo_root / "bin" / "ovpn"), "show"],
                                  capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("FAKE-SUDO", proc.stdout)
        self.assertIn("show", proc.stdout)


if __name__ == "__main__":
    unittest.main()
