/**
 * 脱敏。从 Python 侧（`tests/test_browse_redact.py`）照搬过来的一套，**包括那条最要紧
 * 的顺序测试**：长 token 被截断之后仍然已经脱敏。
 *
 * 顺序写反是这次搬家最容易丢的东西：先截断的话 `sk-ant-api03-xxxx…` 只剩个头，正则
 * 匹配不到，半截 token 就原样落进审计了。
 */
import assert from "node:assert/strict";
import test from "node:test";
import {
  DEFAULT_LIMIT,
  PATTERNS,
  REDACTED,
  clean,
  isSensitiveKey,
  redact,
  truncate,
} from "../src/redact.ts";

// 一条正则一个样本。少一条就是少挡一类凭据。
const SAMPLES: Record<string, string> = {
  pem_private_key:
    "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA\n-----END RSA PRIVATE KEY-----",
  aws_access_key: "AKIAIOSFODNN7EXAMPLE",
  github_pat: `github_pat_${"X".repeat(22)}`,
  github_token: `ghp_${"X".repeat(36)}`,
  anthropic_key: `sk-ant-${"X".repeat(20)}`,
  stripe_key: `sk_live_${"X".repeat(24)}`,
  slack_token: "xoxb-1234567890-abcdefghij",
  google_api_key: `AIza${"C".repeat(35)}`,
  jwt: "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk",
};

test("每一条 secret 正则都真的挡住了它那类凭据", () => {
  for (const [name, sample] of Object.entries(SAMPLES)) {
    assert.ok(PATTERNS[name], `${name} 这条正则不见了`);
    const got = redact(`前面 ${sample} 后面`);
    assert.ok(!got.includes(sample), `${name} 没被挡住: ${got}`);
    assert.ok(got.includes(REDACTED), `${name} 没换成 ${REDACTED}`);
  }
});

test("认证头保留方案名，只抹凭据", () => {
  // 日志里还看得出「用了 Bearer」，这是排障要的；token 本身一个字都不留
  assert.equal(
    redact("Authorization: Bearer abcdefghijklmnop"),
    `Authorization: Bearer ${REDACTED}`,
  );
  for (const scheme of ["Basic", "Token", "Digest"]) {
    assert.match(redact(`${scheme} abcdefghijklmnop`), new RegExp(`^${scheme} \\[REDACTED\\]$`));
  }
});

test("普通文本一个字都不动", () => {
  const plain = "GET https://example.com/api/users?page=2";
  assert.equal(redact(plain), plain);
});

test("字段名启发式：像凭据的名字整值抹掉，不看内容", () => {
  for (const key of ["password", "api_key", "accessKey", "token", "cookie", "jwt", "signature"]) {
    assert.equal(isSensitiveKey(key), true, key);
  }
  // 宁可多抹：`auth` 会连 `author` 一起命中，审计里多抹一个字段比漏一个凭据划算
  assert.equal(isSensitiveKey("author"), true);
  assert.equal(isSensitiveKey("url"), false);
  assert.equal(isSensitiveKey("method"), false);
});

test("clean 递归进对象和数组", () => {
  const got = clean({
    url: "https://a.test",
    password: "hunter2",
    nested: [{ token: "x" }, { note: `key=${SAMPLES.aws_access_key}` }],
  }) as Record<string, unknown>;
  assert.equal(got.url, "https://a.test");
  assert.equal(got.password, REDACTED);
  const nested = got.nested as Record<string, unknown>[];
  assert.equal(nested[0]?.token, REDACTED);
  assert.ok(!String(nested[1]?.note).includes(SAMPLES.aws_access_key));
});

test("截断会标出砍掉多少", () => {
  assert.equal(truncate("abcdef", 3), "abc…(+3)");
  assert.equal(truncate("abc", 3), "abc");
  assert.equal(truncate("abc", -1), "abc", "负数 = 不截断");
});

// ------------------------------------------------------------------ 顺序

// 下面四条是从 Python 侧 `tests/test_browse_redact.py::TestRedactBeforeTruncate`
// 一条一条搬过来的，样本和上限都没改 —— 搬家时最容易丢的就是这组。

test("先脱敏再截断：长 token 截断之后仍然已经脱敏", () => {
  const secret = `sk-ant-api03-${"Z".repeat(200)}`;
  const got = clean(`请求失败: ${secret}`, 20) as string;
  assert.ok(!got.includes("sk-ant-"), `半截 token 漏出来了: ${got}`);
  assert.ok(got.includes(REDACTED));
});

test("顺序反过来就会漏 —— 这条把「为什么必须有顺序」钉死", () => {
  const secret = `sk-ant-api03-${"Z".repeat(200)}`;
  const leaked = redact(truncate(secret, 20)); // 先截断：剩下的不再像 token
  assert.ok(leaked.includes("sk-ant-"), "先截断就是会漏，这正是不能写反的原因");
  assert.ok(!leaked.includes(REDACTED));
});

test("整个落在截断点之后的密钥，是被抹掉而不只是被切掉", () => {
  const text = `${"x".repeat(100)} ghp_${"a".repeat(36)}`;
  const got = clean(text, 50) as string;
  assert.ok(!got.includes("ghp_"));
});

test("该截断的还是照样截断", () => {
  const got = clean("y".repeat(100), 10) as string;
  assert.ok(got.startsWith("y".repeat(10)));
  assert.ok(got.includes("(+90)"));
});

test("默认长度上限够放下方法名和 URL，放不下页面正文", () => {
  assert.equal(DEFAULT_LIMIT, 2048);
  const url = `https://example.com/${"a".repeat(100)}`;
  assert.equal(clean(url), url, "正常长度的 URL 不该被截");
});
