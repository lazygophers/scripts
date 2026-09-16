/**
 * 审计日志：配额淘汰、不记内容、记账失败不能把指令搞挂。
 *
 * **配额那条是重点。** 审计是只增不减的流水，10 MB 早晚要撞。测试不去造 10 MB 真数据
 * （慢，而且 node 里没有真的配额），改成让假 storage 的 `set` 抛出 `chrome.storage`
 * 撞配额时抛的那个错 —— 触发条件和线上一模一样，而且能精确控制抛几次。
 */
import assert from "node:assert/strict";
import test, { afterEach } from "node:test";
import { AUDIT_KEY, MAX_ENTRIES, clear, read, record, writeWithEviction } from "../src/audit.ts";
import { REDACTED } from "../src/redact.ts";
import { clearChrome, storageMock } from "./mock.ts";

afterEach(clearChrome);

function entry(n: number) {
  return {
    ts: new Date(Date.now() - n * 1000).toISOString(),
    method: "browsingContext.navigate",
    domain: `d${n}.test`,
    action: null,
    result: "success" as const,
    ms: 1,
  };
}

// ------------------------------------------------------------------ 基本

test("记一条再读回来", async () => {
  storageMock();
  await record(entry(1));
  const got = await read();
  assert.equal(got.length, 1);
  assert.equal(got[0]?.domain, "d1.test");
});

test("audit: false 时一条都不记", async () => {
  const store = storageMock({ "browse:config": { audit: false } });
  await record(entry(1));
  assert.equal(store.data[AUDIT_KEY], undefined);
});

test("超过保留天数的记录被丢掉", async () => {
  const old = { ...entry(1), ts: new Date(Date.now() - 30 * 86400_000).toISOString() };
  storageMock({
    "browse:config": { audit_retention_days: 7 },
    [AUDIT_KEY]: [old],
  });
  await record(entry(2));
  const got = await read();
  assert.equal(got.length, 1, "30 天前那条该没了");
  assert.equal(got[0]?.domain, "d2.test");
});

test("保留天数 <= 0 表示永不删除", async () => {
  const old = { ...entry(1), ts: new Date(Date.now() - 3650 * 86400_000).toISOString() };
  storageMock({
    "browse:config": { audit_retention_days: 0 },
    [AUDIT_KEY]: [old],
  });
  await record(entry(2));
  assert.equal((await read()).length, 2);
});

test("read(limit) 只给最后 N 条，clear 清空", async () => {
  storageMock({ [AUDIT_KEY]: [entry(3), entry(2), entry(1)] });
  assert.equal((await read(2)).length, 2);
  assert.equal((await read(2))[0]?.domain, "d2.test");
  assert.equal(await clear(), 3);
  assert.deepEqual(await read(), []);
});

// ------------------------------------------------------------------ 不记内容

test("审计只记「对哪个域名做了什么」，凭据进不去", async () => {
  storageMock();
  await record({
    ...entry(1),
    method: "storage.setCookie",
    error: "失败了，Authorization: Bearer abcdefghijklmnop",
  });
  const got = await read();
  assert.ok(got[0]?.error?.includes(REDACTED), "错误消息里的凭据必须被抹掉");
  assert.ok(!got[0]?.error?.includes("abcdefghijklmnop"));
  // 记录的字段就这些，没有 params、没有正文
  assert.deepEqual(
    Object.keys(got[0] ?? {}).sort(),
    ["action", "domain", "error", "method", "ms", "result", "ts"],
  );
});

// ------------------------------------------------------------------ 配额

test("撞配额时丢掉最老的一批再重试，而不是整条日志丢掉", async () => {
  const entries = Array.from({ length: 100 }, (_, i) => entry(100 - i));
  const store = storageMock();
  store.failNext = 1; // 第一次 set 抛配额错，第二次成功

  assert.equal(await writeWithEviction(entries), true);
  const saved = store.data[AUDIT_KEY] as ReturnType<typeof entry>[];
  assert.equal(saved.length, 75, "丢掉最老的 25%");
  assert.equal(saved[0]?.domain, "d75.test", "丢的是最老的那头，不是最新的");
  assert.equal(saved.at(-1)?.domain, "d1.test", "最新的一条必须留着");
  assert.equal(store.sets, 2, "抛一次就该重试一次");
});

test("一直撞配额就一直丢，丢到写得进去为止", async () => {
  const entries = Array.from({ length: 100 }, (_, i) => entry(100 - i));
  const store = storageMock();
  store.failNext = 3;

  assert.equal(await writeWithEviction(entries), true);
  const saved = store.data[AUDIT_KEY] as unknown[];
  // 100 → 75 → 57 → 43
  assert.equal(saved.length, 43);
  assert.equal(store.sets, 4);
});

test("丢到只剩一条还写不进去，就放弃这一条，但不抛", async () => {
  const store = storageMock();
  store.failNext = 999;
  assert.equal(await writeWithEviction([entry(1)]), false);
  assert.equal(store.data[AUDIT_KEY], undefined);
});

test("条数硬上限先于配额生效", async () => {
  const store = storageMock();
  const many = Array.from({ length: MAX_ENTRIES + 500 }, (_, i) => entry(i));
  await writeWithEviction(many);
  assert.equal((store.data[AUDIT_KEY] as unknown[]).length, MAX_ENTRIES);
  assert.equal(store.sets, 1, "没超配额就不该重试");
});

test("撞配额不会让这条指令失败 —— record 永不抛", async () => {
  const store = storageMock();
  store.failNext = 999;
  await record(entry(1)); // 不抛就算过
  assert.equal(store.data[AUDIT_KEY], undefined);
});

test("storage 整个没了，record 照样不抛", async () => {
  clearChrome();
  await record(entry(1));
});

test("真的把最老的挤出去：连写 MAX_ENTRIES + 5 条，最早那几条不在了", async () => {
  storageMock();
  for (let i = 0; i < 5; i += 1) {
    await record({ ...entry(i), domain: `seq${i}.test` });
  }
  const store = storageMock({
    [AUDIT_KEY]: Array.from({ length: MAX_ENTRIES }, (_, i) => entry(i)),
  });
  await record({ ...entry(0), domain: "newest.test" });
  const saved = store.data[AUDIT_KEY] as ReturnType<typeof entry>[];
  assert.equal(saved.length, MAX_ENTRIES, "总数封顶");
  assert.equal(saved.at(-1)?.domain, "newest.test", "最新的一条在");
  assert.equal(saved[0]?.domain, "d1.test", "最老的那条被挤出去了");
});
