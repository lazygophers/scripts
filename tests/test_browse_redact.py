"""browse 脱敏测试：9 条正则各命中一次 + 先脱敏再截断的顺序。"""

from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from lib.browse_redact import (  # noqa: E402
    DEFAULT_LIMIT,
    PATTERNS,
    REDACTED,
    clean,
    is_sensitive_key,
    redact,
    truncate,
)

# 每条正则一个样本。key 就是 PATTERNS 的 key，下面的测试会断言两边名字完全对齐，
# 加了正则忘了加样本会当场挂。
SAMPLES = {
    "pem_private_key": "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\nIBAAKC\n-----END RSA PRIVATE KEY-----",
    "aws_access_key": "AKIAIOSFODNN7EXAMPLE",
    "github_pat": "github_pat_11ABCDEFG0aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789",
    "github_token": "ghp_" + "a" * 36,
    "anthropic_key": "sk-ant-api03-" + "B" * 40,
    "stripe_key": "sk_live_" + "4" * 24,
    "slack_token": "xoxb-123456789012-abcdefghijkl",
    "google_api_key": "AIza" + "C" * 35,
    "jwt": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk",
}


class TestNineRegexes(unittest.TestCase):
    def test_sample_per_pattern(self):
        self.assertEqual(set(SAMPLES), set(PATTERNS), "每条正则必须有一个样本")

    def test_each_pattern_hits_once(self):
        for name, secret in SAMPLES.items():
            with self.subTest(name):
                self.assertRegex(secret, PATTERNS[name])
                out = redact(f"前面 {secret} 后面")
                self.assertNotIn(secret, out)
                self.assertIn(REDACTED, out)
                self.assertIn("前面", out)
                self.assertIn("后面", out)

    def test_plain_text_untouched(self):
        text = "browsingContext.navigate https://example.com/login 用时 12ms"
        self.assertEqual(redact(text), text)

    def test_short_lookalikes_not_redacted(self):
        # 长度不够的不是密钥，不该被误伤
        self.assertEqual(redact("AKIA123"), "AKIA123")
        self.assertEqual(redact("ghp_short"), "ghp_short")


class TestAuthScheme(unittest.TestCase):
    def test_four_schemes(self):
        for scheme in ("Bearer", "Basic", "Token", "Digest"):
            with self.subTest(scheme):
                out = redact(f"Authorization: {scheme} abcdef0123456789ABCDEF")
                self.assertEqual(out, f"Authorization: {scheme} {REDACTED}")

    def test_scheme_name_kept(self):
        # 保留方案名，日志里还看得出用了哪种认证
        self.assertIn("Bearer", redact("Bearer " + "z" * 40))


class TestKeyHeuristic(unittest.TestCase):
    def test_sensitive_names(self):
        for key in ("password", "passwd", "apiKey", "api_key", "access_key",
                    "clientSecret", "authToken", "Cookie", "sessionId",
                    "credential", "privateKey", "signature", "jwt"):
            with self.subTest(key):
                self.assertTrue(is_sensitive_key(key))

    def test_ordinary_names_kept(self):
        for key in ("method", "url", "domain", "pid", "result", "ms"):
            with self.subTest(key):
                self.assertFalse(is_sensitive_key(key))

    def test_clean_wipes_value_by_key_name(self):
        out = clean({"method": "storage.setCookie", "password": "hunter2"})
        self.assertEqual(out, {"method": "storage.setCookie", "password": REDACTED})

    def test_clean_recurses(self):
        out = clean({"outer": {"list": ["ghp_" + "a" * 36, "ok"]}})
        self.assertEqual(out["outer"]["list"], [REDACTED, "ok"])

    def test_clean_keeps_scalars(self):
        out = clean({"pid": 42, "ms": 1.5, "ok": True, "none": None})
        self.assertEqual(out, {"pid": 42, "ms": 1.5, "ok": True, "none": None})


class TestTruncate(unittest.TestCase):
    def test_short_text_unchanged(self):
        self.assertEqual(truncate("abc", 10), "abc")

    def test_marks_how_much_was_cut(self):
        self.assertEqual(truncate("abcdef", 3), "abc…(+3)")


class TestRedactBeforeTruncate(unittest.TestCase):
    """顺序反了就失效：截断后 token 不再像 token，正则匹配不到。"""

    def test_long_token_still_redacted_after_truncation(self):
        secret = "sk-ant-api03-" + "Z" * 200
        out = clean("请求失败: " + secret, limit=20)
        self.assertNotIn("sk-ant-", out)
        self.assertIn(REDACTED, out)

    def test_wrong_order_would_leak(self):
        # 反着来（先截断再脱敏）会漏，这条把「为什么必须有顺序」钉死
        secret = "sk-ant-api03-" + "Z" * 200
        leaked = redact(truncate(secret, 20))
        self.assertIn("sk-ant-", leaked)
        self.assertNotIn(REDACTED, leaked)

    def test_secret_past_the_limit_is_gone_not_just_cut(self):
        # 密钥整体落在截断点之后：先脱敏才轮得到它
        text = "x" * 100 + " ghp_" + "a" * 36
        out = clean(text, limit=50)
        self.assertNotIn("ghp_", out)

    def test_truncation_still_happens(self):
        out = clean("y" * 100, limit=10)
        self.assertTrue(out.startswith("y" * 10))
        self.assertIn("(+90)", out)

    def test_default_limit_is_line_sized(self):
        self.assertEqual(DEFAULT_LIMIT, 2048)


if __name__ == "__main__":
    unittest.main()
