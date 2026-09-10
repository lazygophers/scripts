"""email — 服务商表 / profile 存储 / IMAP·SMTP 收发。

一律不打真网络：IMAP 和 SMTP 都用假连接替身，验的是我们发出去的命令和
解析回来的结果，不是服务商还活着没有。
"""

from __future__ import annotations

import email as stdlib_email
import imaplib
import pathlib
import sys
import tempfile
import unittest
from email.message import EmailMessage

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from lib import email as mail  # noqa: E402
from lib.profile_store import ProfileStore, mask  # noqa: E402

# 连接用例会把 imaplib.IMAP4 换成替身，那之后 imaplib.IMAP4.error 就没了——先抓住真的
_IMAP_ERROR = imaplib.IMAP4.error


# ---------------------------------------------------------------- 替身

class FakeIMAP:
    """够用的 IMAP 替身：记下发过的命令，按预置数据回应。"""

    def __init__(self, *, uids=b"1 2 3", messages=None, flags=None, login_error=None):
        self.calls: list[tuple] = []
        self.logged_out = False
        self._uids = uids
        self._messages = messages or {}
        self._flags = flags or {}
        self._login_error = login_error
        self.readonly = None

    def login(self, user, password):
        self.calls.append(("login", user, password))
        if self._login_error:
            raise _IMAP_ERROR(self._login_error)
        return "OK", [b"logged in"]

    def select(self, folder, readonly=True):
        self.calls.append(("select", folder, readonly))
        self.readonly = readonly
        return "OK", [b"3"]

    def uid(self, command, *args):
        self.calls.append(("uid", command, *args))
        if command == "SEARCH":
            return "OK", [self._uids]
        if command == "FETCH":
            uid = args[0]
            part = args[1]
            if part == "(FLAGS)":
                return "OK", [b"1 (FLAGS (" + self._flags.get(uid, b"") + b"))"]
            return "OK", [(b"1 (RFC822 {1})", self._messages.get(uid, b""))]
        if command == "STORE":
            return "OK", [b"1 (FLAGS (\\Seen))"]
        raise AssertionError(f"没预料到的 uid 命令: {command}")

    def logout(self):
        self.logged_out = True
        return "BYE", [b"bye"]

    def _simple_command(self, *args):
        self.calls.append(("_simple_command", *args))
        return "OK", [b""]

    def _untagged_response(self, *args):
        return "OK", [b""]


class FakeSMTP:
    def __init__(self, *, login_error=None, send_error=None):
        self.calls: list[tuple] = []
        self.sent: list[EmailMessage] = []
        self.quit_called = False
        self._login_error = login_error
        self._send_error = send_error

    def login(self, user, password):
        self.calls.append(("login", user, password))
        if self._login_error:
            raise OSError(self._login_error)

    def send_message(self, msg):
        if self._send_error:
            raise OSError(self._send_error)
        self.sent.append(msg)

    def quit(self):
        self.quit_called = True


def _profile(provider="gmail", password="secret"):
    return mail.build_profile(f"me@{provider}.com", provider, password)


def _raw(subject="你好", sender="a@b.com", body="正文", attachments=()):
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "me@gmail.com"
    msg["Subject"] = subject
    msg["Date"] = "Wed, 10 Sep 2026 12:00:00 +0800"
    msg.set_content(body)
    for name, data in attachments:
        msg.add_attachment(data, maintype="application", subtype="octet-stream", filename=name)
    return msg.as_bytes()


# ---------------------------------------------------------------- 服务商表

class TestProviders(unittest.TestCase):
    def test_covers_exactly_the_seven_requested(self):
        self.assertEqual(set(mail.PROVIDERS), {"gmail", "qq", "163", "126", "icloud", "fastmail", "zoho"})

    def test_every_provider_is_fully_specified(self):
        for name, spec in mail.PROVIDERS.items():
            with self.subTest(provider=name):
                self.assertTrue(spec["label"], "要有给人看的名字")
                self.assertTrue(spec["domains"], "要有域名用于自动识别")
                self.assertTrue(spec["guide"], "每家都要有自己的取授权码引导")
                for kind in ("imap", "smtp"):
                    conf = spec[kind]
                    self.assertTrue(conf["host"])
                    self.assertIsInstance(conf["port"], int)
                    self.assertIn(conf["mode"], ("ssl", "starttls"))

    def test_guides_differ_per_provider(self):
        first_lines = {name: spec["guide"][0] for name, spec in mail.PROVIDERS.items()}
        self.assertEqual(len(set(first_lines.values())), len(first_lines),
                         "七家的引导第一步必须各不相同，否则等于没有独立引导")

    def test_provider_detected_from_domain(self):
        for address, want in [
            ("me@gmail.com", "gmail"), ("me@googlemail.com", "gmail"),
            ("me@qq.com", "qq"), ("me@foxmail.com", "qq"),
            ("me@163.com", "163"), ("me@126.com", "126"),
            ("me@icloud.com", "icloud"), ("me@me.com", "icloud"),
            ("me@fastmail.com", "fastmail"), ("me@zoho.com", "zoho"),
        ]:
            self.assertEqual(mail.provider_for(address), want, address)

    def test_unknown_domain_returns_empty(self):
        self.assertEqual(mail.provider_for("me@example.org"), "")

    def test_netease_needs_imap_id(self):
        # 163/126 不发 ID 会被服务器以 "Unsafe Login" 踢掉
        self.assertTrue(mail.PROVIDERS["163"].get("needs_id"))
        self.assertTrue(mail.PROVIDERS["126"].get("needs_id"))
        self.assertFalse(mail.PROVIDERS["gmail"].get("needs_id"))

    def test_icloud_imap_user_is_localpart_only(self):
        self.assertEqual(mail._login_user("john@icloud.com", "icloud", "imap"), "john")
        self.assertEqual(mail._login_user("john@icloud.com", "icloud", "smtp"), "john@icloud.com",
                         "iCloud 的 SMTP 要完整地址")
        self.assertEqual(mail._login_user("john@gmail.com", "gmail", "imap"), "john@gmail.com")


class TestEmailKey(unittest.TestCase):
    def test_normalizes_case_and_space(self):
        self.assertEqual(mail.email_key("  Me@QQ.com "), "me@qq.com")

    def test_rejects_non_addresses(self):
        for bad in ("", "nope", "@qq.com", "me@", "   "):
            with self.subTest(bad=bad), self.assertRaises(mail.MailError):
                mail.email_key(bad)

    def test_build_profile_rejects_unknown_provider(self):
        with self.assertRaises(mail.MailError):
            mail.build_profile("me@x.com", "outlook", "pw")

    def test_build_profile_defaults_to_provider_servers(self):
        p = mail.build_profile("me@qq.com", "qq", "code")
        self.assertEqual(p["imap"]["host"], "imap.qq.com")
        self.assertEqual(p["smtp"]["port"], 465)
        self.assertEqual(p["password"], "code")

    def test_build_profile_accepts_overrides(self):
        p = mail.build_profile("me@zoho.com", "zoho", "pw",
                               imap={"host": "imappro.zoho.com", "port": 993, "mode": "ssl"})
        self.assertEqual(p["imap"]["host"], "imappro.zoho.com")
        self.assertEqual(p["smtp"]["host"], "smtp.zoho.com", "没覆盖的那半仍用默认值")


# ---------------------------------------------------------------- 连接与验证

class ConnectionCase(unittest.TestCase):
    def setUp(self):
        self.imap = FakeIMAP()
        self.smtp = FakeSMTP()
        self._saved = (mail.imaplib.IMAP4_SSL, mail.imaplib.IMAP4, mail.smtplib.SMTP_SSL, mail.smtplib.SMTP)
        self.addCleanup(self._restore)
        mail.imaplib.IMAP4_SSL = lambda *a, **kw: self.imap
        mail.imaplib.IMAP4 = lambda *a, **kw: self.imap
        mail.smtplib.SMTP_SSL = lambda *a, **kw: self.smtp
        mail.smtplib.SMTP = lambda *a, **kw: self.smtp

    def _restore(self):
        (mail.imaplib.IMAP4_SSL, mail.imaplib.IMAP4,
         mail.smtplib.SMTP_SSL, mail.smtplib.SMTP) = self._saved


class TestCheck(ConnectionCase):
    def test_passes_when_both_sides_log_in(self):
        ok, detail = mail.check("me@gmail.com", _profile())
        self.assertTrue(ok)
        self.assertIn("imap.gmail.com:993", detail)
        self.assertIn("smtp.gmail.com:465", detail)

    def test_fails_and_explains_when_imap_rejects(self):
        self.imap = FakeIMAP(login_error="AUTHENTICATIONFAILED")
        mail.imaplib.IMAP4_SSL = lambda *a, **kw: self.imap
        ok, detail = mail.check("me@gmail.com", _profile())
        self.assertFalse(ok)
        self.assertIn("IMAP 登录被拒", detail)
        self.assertIn("AUTHENTICATIONFAILED", detail)

    def test_fails_when_smtp_rejects_even_if_imap_passed(self):
        self.smtp = FakeSMTP(login_error="535 auth failed")
        mail.smtplib.SMTP_SSL = lambda *a, **kw: self.smtp
        ok, detail = mail.check("me@gmail.com", _profile())
        self.assertFalse(ok)
        self.assertIn("SMTP 登录被拒", detail)

    def test_missing_server_is_a_readable_error(self):
        profile = _profile()
        profile["imap"] = {}
        ok, detail = mail.check("me@gmail.com", profile)
        self.assertFalse(ok)
        self.assertIn("没有 IMAP 服务器地址", detail)

    def test_netease_sends_imap_id_after_login(self):
        mail.connect_imap("me@163.com", mail.build_profile("me@163.com", "163", "pw"))
        self.assertTrue(any(c[0] == "_simple_command" and c[1] == "ID" for c in self.imap.calls),
                        "163 登录后必须发 ID，否则服务器回 Unsafe Login")

    def test_gmail_does_not_send_imap_id(self):
        mail.connect_imap("me@gmail.com", _profile())
        self.assertFalse(any(c[0] == "_simple_command" for c in self.imap.calls))

    def test_starttls_path_is_used_when_mode_says_so(self):
        calls = []
        self.smtp.starttls = lambda context=None: calls.append(context)
        mail.connect_smtp("me@icloud.com", mail.build_profile("me@icloud.com", "icloud", "pw"))
        self.assertEqual(len(calls), 1, "iCloud 的 SMTP 是 587 STARTTLS")
        self.assertIsNotNone(calls[0], "必须传一个会校验证书的 ssl context")

    def test_ssl_context_verifies_certificate_and_hostname(self):
        # 标准库默认不校验，这里必须是校验的那种，否则等于加密了但不知道对面是谁
        ctx = mail._ssl_context()
        self.assertTrue(ctx.check_hostname)
        self.assertEqual(ctx.verify_mode, mail.ssl.CERT_REQUIRED)


# ---------------------------------------------------------------- 发信

class TestSend(ConnectionCase):
    def test_sends_plain_mail(self):
        rcpts = mail.send("me@gmail.com", _profile(), to="a@b.com", subject="标题", body="正文")
        self.assertEqual(rcpts, ["a@b.com"])
        msg = self.smtp.sent[0]
        self.assertEqual(msg["From"], "me@gmail.com")
        self.assertEqual(msg["Subject"], "标题")
        self.assertIn("正文", msg.get_content())
        self.assertTrue(self.smtp.quit_called, "发完要断开")

    def test_multiple_recipients_and_cc(self):
        rcpts = mail.send("me@gmail.com", _profile(), to="a@b.com, c@d.com",
                          subject="s", body="b", cc="e@f.com")
        self.assertEqual(rcpts, ["a@b.com", "c@d.com", "e@f.com"])
        self.assertEqual(self.smtp.sent[0]["To"], "a@b.com, c@d.com")
        self.assertEqual(self.smtp.sent[0]["Cc"], "e@f.com")

    def test_attachment_is_carried(self):
        with tempfile.TemporaryDirectory() as d:
            f = pathlib.Path(d) / "report.txt"
            f.write_text("hello", encoding="utf-8")
            mail.send("me@gmail.com", _profile(), to="a@b.com", subject="s", body="b",
                      attach=[str(f)])
        names = [p.get_filename() for p in self.smtp.sent[0].iter_attachments()]
        self.assertEqual(names, ["report.txt"])

    def test_missing_attachment_is_a_readable_error(self):
        with self.assertRaises(mail.MailError) as cm:
            mail.send("me@gmail.com", _profile(), to="a@b.com", subject="s", body="b",
                      attach=["/no/such/file.pdf"])
        self.assertIn("附件不存在", str(cm.exception))

    def test_send_failure_still_disconnects(self):
        self.smtp = FakeSMTP(send_error="550 rejected")
        mail.smtplib.SMTP_SSL = lambda *a, **kw: self.smtp
        with self.assertRaises(mail.MailError):
            mail.send("me@gmail.com", _profile(), to="a@b.com", subject="s", body="b")
        self.assertTrue(self.smtp.quit_called, "失败也要断开，不能漏连接")


# ---------------------------------------------------------------- 收信

class TestInbox(ConnectionCase):
    def setUp(self):
        super().setUp()
        self.imap = FakeIMAP(
            uids=b"1 2",
            messages={b"1": _raw(subject="第一封"), b"2": _raw(subject="第二封")},
            flags={b"1": rb"\Seen", b"2": b""},
        )
        mail.imaplib.IMAP4_SSL = lambda *a, **kw: self.imap

    def test_lists_newest_first_with_decoded_chinese_subject(self):
        rows = mail.inbox("me@gmail.com", _profile())
        self.assertEqual([r["Subject"] for r in rows], ["第二封", "第一封"])
        self.assertEqual([r["uid"] for r in rows], ["2", "1"])

    def test_unread_flag_is_reported(self):
        rows = mail.inbox("me@gmail.com", _profile())
        self.assertTrue(rows[0]["unread"], "UID 2 没有 \\Seen，应为未读")
        self.assertFalse(rows[1]["unread"])

    def test_limit_keeps_the_newest(self):
        rows = mail.inbox("me@gmail.com", _profile(), limit=1)
        self.assertEqual([r["Subject"] for r in rows], ["第二封"])

    def test_header_fetch_uses_peek_so_nothing_gets_marked_read(self):
        mail.inbox("me@gmail.com", _profile())
        fetches = [c for c in self.imap.calls if c[:2] == ("uid", "FETCH")]
        self.assertTrue(fetches)
        for c in fetches:
            self.assertNotIn("BODY[", c[3], "只能用 BODY.PEEK，否则一列邮件就全标已读了")

    def test_unread_only_searches_unseen(self):
        mail.inbox("me@gmail.com", _profile(), unread_only=True)
        self.assertIn(("uid", "SEARCH", "UNSEEN"), self.imap.calls)

    def test_select_is_readonly_for_listing(self):
        mail.inbox("me@gmail.com", _profile())
        self.assertIs(self.imap.readonly, True)

    def test_logout_even_when_folder_is_missing(self):
        self.imap.select = lambda folder, readonly=True: ("NO", [b"no such folder"])
        with self.assertRaises(mail.MailError):
            mail.inbox("me@gmail.com", _profile(), folder="Nope")
        self.assertTrue(self.imap.logged_out, "报错也要断开")

    def test_search_passes_utf8_bytes(self):
        mail.search("me@gmail.com", _profile(), "发票")
        call = next(c for c in self.imap.calls if c[:2] == ("uid", "SEARCH"))
        self.assertEqual(call[2:], ("CHARSET", "UTF-8", "TEXT", "发票".encode()),
                         "中文关键词要按 UTF-8 以 literal 传，不能当 ASCII 发")


class TestRead(ConnectionCase):
    def setUp(self):
        super().setUp()
        self.imap = FakeIMAP(messages={
            b"7": _raw(subject="报表", body="见附件", attachments=[("q3.pdf", b"%PDF-1.4")]),
        })
        mail.imaplib.IMAP4_SSL = lambda *a, **kw: self.imap

    def test_reads_body_and_lists_attachments(self):
        m = mail.read("me@gmail.com", _profile(), "7")
        self.assertEqual(m["Subject"], "报表")
        self.assertIn("见附件", m["body"])
        self.assertEqual(m["attachments"], ["q3.pdf"])

    def test_saves_attachments_to_disk(self):
        with tempfile.TemporaryDirectory() as d:
            written = mail.save_attachments("me@gmail.com", _profile(), "7", pathlib.Path(d))
            self.assertEqual([p.name for p in written], ["q3.pdf"])
            self.assertEqual(written[0].read_bytes(), b"%PDF-1.4")

    def test_attachment_filename_cannot_escape_the_target_dir(self):
        msg = EmailMessage()
        msg["Subject"] = "坏文件名"
        msg.set_content("x")
        msg.add_attachment(b"evil", maintype="application", subtype="octet-stream",
                           filename="../../escaped.txt")
        self.imap = FakeIMAP(messages={b"7": msg.as_bytes()})
        mail.imaplib.IMAP4_SSL = lambda *a, **kw: self.imap
        with tempfile.TemporaryDirectory() as d:
            written = mail.save_attachments("me@gmail.com", _profile(), "7", pathlib.Path(d))
            self.assertEqual(written[0].parent, pathlib.Path(d), "附件名里的路径必须被剥掉")
            self.assertEqual(written[0].name, "escaped.txt")

    def test_missing_body_does_not_crash(self):
        raw = b"From: a@b.com\r\nSubject: \r\n\r\n"
        self.imap = FakeIMAP(messages={b"7": raw})
        mail.imaplib.IMAP4_SSL = lambda *a, **kw: self.imap
        m = mail.read("me@gmail.com", _profile(), "7")
        self.assertIsInstance(m["body"], str)


class TestMark(ConnectionCase):
    def test_mark_seen_opens_folder_writable(self):
        mail.mark("me@gmail.com", _profile(), "5")
        self.assertIs(self.imap.readonly, False, "改标记必须以可写方式打开文件夹")
        self.assertIn(("uid", "STORE", b"5", "+FLAGS", r"(\Seen)"), self.imap.calls)

    def test_mark_unread_removes_the_flag(self):
        mail.mark("me@gmail.com", _profile(), "5", seen=False)
        self.assertIn(("uid", "STORE", b"5", "-FLAGS", r"(\Seen)"), self.imap.calls)


# ---------------------------------------------------------------- profile 存储

class TestProfileStore(unittest.TestCase):
    """三个工具共用的这一份，用 email 的 store 验它的性质。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self._tmp.name) / "email.yaml"
        self.addCleanup(self._tmp.cleanup)
        self.store = ProfileStore(
            "email.yaml", error=mail.MailError, key_fn=mail.email_key,
            tool="email", noun="邮箱", key_flag="--email", login_flag="--email",
            key_hint="<邮箱地址>", path_resolver=lambda: self.path,
        )

    def test_missing_file_reads_as_empty(self):
        self.assertEqual(self.store.load(), {})

    def test_saved_file_is_0600(self):
        self.store.save({"current": "me@qq.com"})
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600,
                         "配置里有明文授权码，权限必须是 0600")

    def test_round_trip_keeps_unicode(self):
        self.store.save({"profiles": {"me@qq.com": {"note": "工作邮箱"}}})
        self.assertEqual(self.store.load()["profiles"]["me@qq.com"]["note"], "工作邮箱")

    def test_no_temp_file_left_behind(self):
        self.store.save({"a": 1})
        leftovers = [p.name for p in self.path.parent.iterdir() if ".tmp" in p.name]
        self.assertEqual(leftovers, [])

    def test_single_profile_is_used_without_being_named(self):
        cfg = self.store.put({}, "me@qq.com", {"provider": "qq"})
        cfg.pop("current")
        key, profile = self.store.resolve(cfg)
        self.assertEqual(key, "me@qq.com")
        self.assertEqual(profile["provider"], "qq")

    def test_current_wins_when_several_are_configured(self):
        cfg = {"current": "b@qq.com", "profiles": {"a@qq.com": {}, "b@qq.com": {}}}
        self.assertEqual(self.store.resolve(cfg)[0], "b@qq.com")

    def test_explicit_key_beats_current(self):
        cfg = {"current": "b@qq.com", "profiles": {"a@qq.com": {}, "b@qq.com": {}}}
        self.assertEqual(self.store.resolve(cfg, "A@QQ.com")[0], "a@qq.com")

    def test_several_profiles_without_a_choice_asks_for_one(self):
        cfg = {"profiles": {"a@qq.com": {}, "b@qq.com": {}}}
        with self.assertRaises(mail.MailError) as cm:
            self.store.resolve(cfg)
        msg = str(cm.exception)
        self.assertIn("--email", msg)
        self.assertIn("email use", msg, "报错要告诉用户下一步敲什么")

    def test_empty_config_points_at_login(self):
        with self.assertRaises(mail.MailError) as cm:
            self.store.resolve({})
        self.assertIn("email login", str(cm.exception))

    def test_unknown_key_lists_what_is_configured(self):
        cfg = {"profiles": {"a@qq.com": {}}}
        with self.assertRaises(mail.MailError) as cm:
            self.store.resolve(cfg, "zz@qq.com")
        self.assertIn("a@qq.com", str(cm.exception))

    def test_put_sets_current_only_when_absent(self):
        cfg = self.store.put({}, "a@qq.com", {})
        self.assertEqual(cfg["current"], "a@qq.com")
        cfg = self.store.put(cfg, "b@qq.com", {})
        self.assertEqual(cfg["current"], "a@qq.com", "已有默认值时不该被后来的覆盖")

    def test_lock_is_reentrant_across_calls(self):
        with self.store.lock():
            self.store.save({"a": 1})
        with self.store.lock():
            self.assertEqual(self.store.load(), {"a": 1})


class TestConnectionEdges(ConnectionCase):
    def test_unreachable_host_is_a_readable_error(self):
        def boom(*a, **kw):
            raise OSError("Connection refused")

        mail.imaplib.IMAP4_SSL = boom
        with self.assertRaises(mail.MailError) as cm:
            mail.connect_imap("me@gmail.com", _profile())
        self.assertIn("连不上 IMAP imap.gmail.com:993", str(cm.exception))
        self.assertIn("Connection refused", str(cm.exception))

    def test_unreachable_smtp_is_a_readable_error(self):
        def boom(*a, **kw):
            raise OSError("timed out")

        mail.smtplib.SMTP_SSL = boom
        with self.assertRaises(mail.MailError) as cm:
            mail.connect_smtp("me@gmail.com", _profile())
        self.assertIn("连不上 SMTP", str(cm.exception))

    def test_missing_smtp_server_is_a_readable_error(self):
        profile = _profile()
        profile["smtp"] = {}
        with self.assertRaises(mail.MailError) as cm:
            mail.connect_smtp("me@gmail.com", profile)
        self.assertIn("没有 SMTP 服务器地址", str(cm.exception))

    def test_imap_id_failure_is_not_fatal(self):
        def boom(*a, **kw):
            raise OSError("ID unsupported")

        self.imap._simple_command = boom
        # 不抛就算过：真需要 ID 的服务器会在后续命令上明确报错
        mail.connect_imap("me@163.com", mail.build_profile("me@163.com", "163", "pw"))

    def test_failed_search_is_reported(self):
        self.imap.uid = lambda command, *a: ("NO", [b"invalid criteria"])
        with self.assertRaises(mail.MailError) as cm:
            mail.inbox("me@gmail.com", _profile())
        self.assertIn("搜索失败", str(cm.exception))

    def test_empty_mailbox_lists_nothing(self):
        self.imap = FakeIMAP(uids=b"")
        mail.imaplib.IMAP4_SSL = lambda *a, **kw: self.imap
        self.assertEqual(mail.inbox("me@gmail.com", _profile()), [])

    def test_fetch_without_content_is_reported(self):
        self.imap.uid = lambda command, *a: ("OK", [b"nothing useful"]) if command == "FETCH" else ("OK", [b"1"])
        with self.assertRaises(mail.MailError) as cm:
            mail.read("me@gmail.com", _profile(), "1")
        self.assertIn("没有内容返回", str(cm.exception))

    def test_failed_store_is_reported(self):
        self.imap.uid = lambda command, *a: ("NO", [b"cannot store"])
        with self.assertRaises(mail.MailError) as cm:
            mail.mark("me@gmail.com", _profile(), "5")
        self.assertIn("标记邮件 5 失败", str(cm.exception))


class TestConfigPath(unittest.TestCase):
    def test_default_path_is_under_the_shared_config_dir(self):
        p = mail.default_config_path()
        self.assertEqual(p.name, "email.yaml")
        self.assertEqual(p.parent.name, "scripts")
        self.assertEqual(p.parent.parent.name, "lazygophers")


class TestLoginVerifyThenSave(unittest.TestCase):
    """用户 2026-09-10 的原话：配置完成后要先 check，确认可行再保存，否则询问是否保存。"""

    def setUp(self):
        from lib.cli.email import EmailCli

        self._tmp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self._tmp.name) / "email.yaml"
        self.addCleanup(self._tmp.cleanup)
        self.cli = EmailCli()
        self.saved: list[dict] = []

        import lib.cli.email as cli_mod

        self.cli_mod = cli_mod
        self._orig = (cli_mod.load_config, cli_mod.save_config, cli_mod.config_lock)
        self.addCleanup(self._restore)
        cli_mod.load_config = lambda *a, **kw: dict(self.saved[-1]) if self.saved else {}
        cli_mod.save_config = lambda cfg, *a, **kw: self.saved.append(dict(cfg))
        cli_mod.config_lock = lambda *a, **kw: _null_ctx()

    def _restore(self):
        (self.cli_mod.load_config, self.cli_mod.save_config,
         self.cli_mod.config_lock) = self._orig

    def _patch_check(self, ok, detail):
        orig = mail.check  # 必须先抓原件：addCleanup 的参数是当场求值的，
        mail.check = lambda *a, **kw: (ok, detail)  # patch 之后再读就只会把替身还回去
        self.addCleanup(setattr, mail, "check", orig)

    def _patch_confirm(self, answer):
        import lib.ui as ui

        orig = ui.ask_confirm
        ui.ask_confirm = lambda *a, **kw: answer
        self.addCleanup(setattr, ui, "ask_confirm", orig)

    def test_saves_when_the_check_passes(self):
        self._patch_check(True, "都登录成功")
        rc = self.cli._verify_then_save("me@qq.com", _profile("qq"))
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.saved), 1, "验证通过就直接保存，不该再问")
        self.assertIn("me@qq.com", self.saved[0]["profiles"])

    def test_asks_before_saving_a_failing_config(self):
        self._patch_check(False, "IMAP 登录被拒")
        self._patch_confirm(True)
        rc = self.cli._verify_then_save("me@qq.com", _profile("qq"))
        self.assertEqual(rc, 1, "存下来了但验证没过，退出码要非 0")
        self.assertEqual(len(self.saved), 1, "用户说存就存")

    def test_discards_a_failing_config_when_declined(self):
        self._patch_check(False, "IMAP 登录被拒")
        self._patch_confirm(False)
        rc = self.cli._verify_then_save("me@qq.com", _profile("qq"))
        self.assertEqual(rc, 1)
        self.assertEqual(self.saved, [], "用户说不存就一个字节都不能落盘")


class _null_ctx:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestCli(unittest.TestCase):
    """CLI 层：参数怎么传给下面、报错怎么变成退出码、输出里有没有该有的东西。"""

    def setUp(self):
        import io

        import lib.cli.email as cli_mod
        from lib.ui import Reporter

        self.cli_mod = cli_mod
        self.cli = cli_mod.EmailCli()
        self.buf = io.StringIO()
        self.cli._r = Reporter.from_buffer(self.buf)

        self.cfg = {"current": "me@qq.com", "profiles": {
            "me@qq.com": mail.build_profile("me@qq.com", "qq", "code1234"),
            "me@gmail.com": mail.build_profile("me@gmail.com", "gmail", "pw"),
        }}
        self.saved: list[dict] = []
        self._orig = (cli_mod.load_config, cli_mod.save_config, cli_mod.config_lock)
        self.addCleanup(self._restore)
        cli_mod.load_config = lambda *a, **kw: {"current": self.cfg.get("current", ""),
                                                "profiles": dict(self.cfg["profiles"])}
        cli_mod.save_config = lambda cfg, *a, **kw: self.saved.append(dict(cfg))
        cli_mod.config_lock = lambda *a, **kw: _null_ctx()

    def _restore(self):
        (self.cli_mod.load_config, self.cli_mod.save_config,
         self.cli_mod.config_lock) = self._orig

    def _patch(self, name, fn):
        orig = getattr(mail, name)
        setattr(mail, name, fn)
        self.addCleanup(setattr, mail, name, orig)

    @property
    def out(self) -> str:
        return self.buf.getvalue()

    # -------------------------------------------------------- 配置类

    def test_list_shows_accounts_with_masked_password(self):
        self.assertEqual(self.cli.list(), 0)
        self.assertIn("me@qq.com", self.out)
        self.assertIn("me@gmail.com", self.out)
        self.assertNotIn("code1234", self.out, "密码绝不能明文出现在输出里")

    def test_list_on_empty_config_points_at_login(self):
        self.cli_mod.load_config = lambda *a, **kw: {}
        self.assertEqual(self.cli.list(), 0)
        self.assertIn("email login", self.out)

    def test_use_sets_current(self):
        self.assertEqual(self.cli.use("ME@Gmail.com"), 0)
        self.assertEqual(self.saved[-1]["current"], "me@gmail.com")

    def test_use_unknown_account_is_exit_2(self):
        self.assertEqual(self.cli.use("nobody@qq.com"), 2)
        self.assertEqual(self.saved, [])

    def test_remove_drops_the_account(self):
        self.assertEqual(self.cli.remove("me@gmail.com"), 0)
        self.assertNotIn("me@gmail.com", self.saved[-1]["profiles"])

    def test_removing_the_current_account_repoints_current(self):
        self.assertEqual(self.cli.remove("me@qq.com"), 0)
        self.assertEqual(self.saved[-1]["current"], "me@gmail.com",
                         "删掉默认邮箱后要自动指到剩下的那个")

    def test_check_reports_failure_as_exit_1(self):
        self._patch("check", lambda *a, **kw: (False, "IMAP 登录被拒"))
        self.assertEqual(self.cli.check(), 1)
        self.assertIn("IMAP 登录被拒", self.out)

    def test_check_targets_the_named_account(self):
        seen = {}

        def fake_check(addr, profile, **kw):
            seen["addr"] = addr
            return True, "ok"

        self._patch("check", fake_check)
        self.assertEqual(self.cli.check(email="me@gmail.com"), 0)
        self.assertEqual(seen["addr"], "me@gmail.com")

    # -------------------------------------------------------- 收发类

    def test_send_passes_arguments_through(self):
        seen = {}

        def fake_send(addr, profile, **kw):
            seen.update(kw, addr=addr)
            return ["a@b.com"]

        self._patch("send", fake_send)
        self.assertEqual(self.cli.send(to="a@b.com", subject="标题", body="正文"), 0)
        self.assertEqual(seen["addr"], "me@qq.com", "没指定 --email 时用 current")
        self.assertEqual((seen["to"], seen["subject"], seen["body"]), ("a@b.com", "标题", "正文"))

    def test_send_splits_attachments_on_comma(self):
        seen = {}
        self._patch("send", lambda a, p, **kw: seen.update(kw) or ["x@y.com"])
        self.cli.send(to="x@y.com", subject="s", body="b", attach="a.pdf,b.png")
        self.assertEqual(seen["attach"], ["a.pdf", "b.png"])

    def test_send_reads_body_from_stdin_on_dash(self):
        import io

        seen = {}
        self._patch("send", lambda a, p, **kw: seen.update(kw) or ["x@y.com"])
        orig = sys.stdin
        sys.stdin = io.StringIO("从管道来的正文")
        self.addCleanup(setattr, sys, "stdin", orig)
        self.cli.send(to="x@y.com", subject="s", body="-")
        self.assertEqual(seen["body"], "从管道来的正文")

    def test_send_failure_is_exit_2(self):
        def boom(*a, **kw):
            raise mail.MailError("550 rejected")

        self._patch("send", boom)
        self.assertEqual(self.cli.send(to="a@b.com", subject="s", body="b"), 2)
        self.assertIn("550 rejected", self.out)

    def test_inbox_prints_a_table(self):
        self._patch("inbox", lambda *a, **kw: [
            {"uid": "9", "unread": True, "From": "a@b.com", "Subject": "发票", "Date": "今天"},
        ])
        self.assertEqual(self.cli.inbox(), 0)
        self.assertIn("发票", self.out)
        self.assertIn("email read <UID>", self.out)

    def test_inbox_forwards_flags(self):
        seen = {}
        self._patch("inbox", lambda a, p, **kw: seen.update(kw) or [])
        self.cli.inbox(limit=5, folder="Sent", unread=True)
        self.assertEqual((seen["limit"], seen["folder"], seen["unread_only"]), (5, "Sent", True))

    def test_empty_inbox_says_so(self):
        self._patch("inbox", lambda *a, **kw: [])
        self.assertEqual(self.cli.inbox(), 0)
        self.assertIn("没有邮件", self.out)

    def test_search_reports_nothing_found(self):
        self._patch("search", lambda *a, **kw: [])
        self.assertEqual(self.cli.search("发票"), 0)
        self.assertIn("发票", self.out)

    def test_read_prints_body_and_hints_at_attachments(self):
        self._patch("read", lambda *a, **kw: {
            "uid": "9", "From": "a@b.com", "To": "me@qq.com", "Subject": "报表",
            "Date": "今天", "body": "见附件", "attachments": ["q3.pdf"],
        })
        self.assertEqual(self.cli.read("9"), 0)
        self.assertIn("见附件", self.out)
        self.assertIn("email attach 9", self.out)

    def test_attach_reports_each_written_file(self):
        self._patch("save_attachments", lambda *a, **kw: [pathlib.Path("/tmp/q3.pdf")])
        self.assertEqual(self.cli.attach("9"), 0)
        self.assertIn("q3.pdf", self.out)

    def test_attach_with_no_attachments_says_so(self):
        self._patch("save_attachments", lambda *a, **kw: [])
        self.assertEqual(self.cli.attach("9"), 0)
        self.assertIn("没有附件", self.out)

    def test_mark_defaults_to_read(self):
        seen = {}
        self._patch("mark", lambda a, p, uid, **kw: seen.update(kw, uid=uid))
        self.assertEqual(self.cli.mark("9"), 0)
        self.assertEqual((seen["uid"], seen["seen"]), ("9", True))
        self.assertIn("已标为已读", self.out)

    def test_mark_unread(self):
        seen = {}
        self._patch("mark", lambda a, p, uid, **kw: seen.update(kw, uid=uid))
        self.cli.mark("9", unread=True)
        self.assertFalse(seen["seen"])
        self.assertIn("已标为未读", self.out)

    def test_ambiguous_account_is_exit_2_with_guidance(self):
        self.cfg["current"] = ""
        self._patch("inbox", lambda *a, **kw: [])
        self.assertEqual(self.cli.inbox(), 2)
        self.assertIn("--email", self.out)


class TestLoginWizard(unittest.TestCase):
    """每家一套独立引导 + 保存前先验证（用户 2026-09-10 的两条要求）。"""

    def setUp(self):
        import io

        import lib.cli.email as cli_mod
        import lib.ui as ui
        from lib.ui import Reporter

        self.cli_mod, self.ui = cli_mod, ui
        self.cli = cli_mod.EmailCli()
        self.buf = io.StringIO()
        self.cli._r = Reporter.from_buffer(self.buf)
        self.saved: list[dict] = []

        self._orig_cfg = (cli_mod.load_config, cli_mod.save_config, cli_mod.config_lock)
        self._orig_ui = (ui.ask_text, ui.ask_secret, ui.ask_select, ui.ask_confirm)
        self.addCleanup(self._restore)
        cli_mod.load_config = lambda *a, **kw: {}
        cli_mod.save_config = lambda cfg, *a, **kw: self.saved.append(dict(cfg))
        cli_mod.config_lock = lambda *a, **kw: _null_ctx()

        self.texts: list[str] = []
        self.secret = "abcd efgh ijkl mnop"
        self.selected: str | None = None
        self.confirm = False
        ui.ask_text = lambda prompt, default="": self.texts.pop(0) if self.texts else default
        ui.ask_secret = lambda prompt: self.secret
        ui.ask_select = lambda prompt, options, **kw: self.selected
        ui.ask_confirm = lambda prompt, default=False: self.confirm
        self._patch_check(True, "都登录成功")

    def _restore(self):
        (self.cli_mod.load_config, self.cli_mod.save_config,
         self.cli_mod.config_lock) = self._orig_cfg
        (self.ui.ask_text, self.ui.ask_secret,
         self.ui.ask_select, self.ui.ask_confirm) = self._orig_ui

    def _patch_check(self, ok, detail):
        orig = mail.check
        mail.check = lambda *a, **kw: (ok, detail)
        self.addCleanup(setattr, mail, "check", orig)

    @property
    def out(self) -> str:
        return self.buf.getvalue()

    def _profile_saved(self) -> dict:
        return self.saved[-1]["profiles"]["me@qq.com"]

    def test_provider_detected_from_the_address_so_no_menu(self):
        picked = []
        self.ui.ask_select = lambda p, o, **kw: picked.append(o) or None
        self.assertEqual(self.cli.login(email="me@qq.com"), 0)
        self.assertEqual(picked, [], "域名认得出来就不该再弹菜单")
        self.assertEqual(self._profile_saved()["provider"], "qq")

    def test_the_providers_own_guide_is_printed(self):
        self.cli.login(email="me@qq.com")
        for line in mail.PROVIDERS["qq"]["guide"]:
            self.assertIn(line[:12], self.out)
        self.assertIn(mail.PROVIDERS["qq"]["warn"][:12], self.out, "该提醒的坑也要说")

    def test_a_different_provider_gets_a_different_guide(self):
        self.cli.login(email="me@gmail.com")
        self.assertIn("两步验证", self.out)
        self.assertNotIn("生成授权码", self.out, "Gmail 不该出现 QQ 的说法")

    def test_spaces_in_the_app_password_are_stripped(self):
        self.cli.login(email="me@qq.com")
        self.assertEqual(self._profile_saved()["password"], "abcdefghijklmnop",
                         "Gmail 的 16 位是带空格展示的，存之前要去掉")

    def test_unknown_domain_asks_which_provider(self):
        self.selected = "zoho — Zoho Mail"
        self.assertEqual(self.cli.login(email="me@example.org"), 0)
        self.assertEqual(self.saved[-1]["profiles"]["me@example.org"]["provider"], "zoho")

    def test_cancels_when_no_provider_is_picked(self):
        self.selected = None
        self.assertEqual(self.cli.login(email="me@example.org"), 1)
        self.assertEqual(self.saved, [])

    def test_cancels_when_no_password_is_entered(self):
        self.secret = ""
        self.assertEqual(self.cli.login(email="me@qq.com"), 1)
        self.assertEqual(self.saved, [])

    def test_rejects_an_unknown_provider_name(self):
        self.assertEqual(self.cli.login(email="me@qq.com", provider="outlook"), 2)
        self.assertIn("不认识的服务商", self.out)

    def test_default_servers_are_shown_before_asking(self):
        self.cli.login(email="me@qq.com")
        self.assertIn("imap.qq.com:993", self.out)
        self.assertIn("smtp.qq.com:465", self.out)

    def test_servers_can_be_overridden(self):
        self.confirm = True
        self.texts = ["imappro.zoho.com", "993", "smtppro.zoho.com", "587", "starttls"]
        self.assertEqual(self.cli.login(email="me@zoho.com"), 0)
        p = self.saved[-1]["profiles"]["me@zoho.com"]
        self.assertEqual(p["imap"]["host"], "imappro.zoho.com")
        self.assertEqual(p["smtp"], {"host": "smtppro.zoho.com", "port": 587, "mode": "starttls"})

    def test_address_typed_at_the_prompt_when_not_given(self):
        self.texts = ["  Me@QQ.com  "]
        self.assertEqual(self.cli.login(), 0)
        self.assertIn("me@qq.com", self.saved[-1]["profiles"])

    def test_a_failing_check_still_asks_before_saving(self):
        self._patch_check(False, "IMAP 登录被拒")
        self.confirm = False
        self.assertEqual(self.cli.login(email="me@qq.com"), 1)
        self.assertEqual(self.saved, [], "验证没过又不同意保存，就不能落盘")


class TestMask(unittest.TestCase):
    def test_hides_the_middle(self):
        self.assertEqual(mask("abcdefgh"), "ab****gh")

    def test_short_secret_is_fully_hidden(self):
        self.assertEqual(mask("abcd"), "****")

    def test_empty_is_labelled(self):
        self.assertEqual(mask(""), "(未设置)")


class TestStdlibNotShadowed(unittest.TestCase):
    """lib/email.py 不能把标准库的 email 挡掉——挡掉了整个工具就废了。"""

    def test_stdlib_email_still_resolves(self):
        self.assertIn("/email/__init__.py", stdlib_email.__file__)
        self.assertNotEqual(pathlib.Path(stdlib_email.__file__).parent.name, "lib")

    def test_our_module_is_importable_under_lib(self):
        self.assertEqual(mail.__name__, "lib.email")


if __name__ == "__main__":
    unittest.main()
