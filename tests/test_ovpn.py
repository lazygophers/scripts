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


if __name__ == "__main__":
    unittest.main()
