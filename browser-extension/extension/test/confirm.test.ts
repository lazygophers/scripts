import assert from "node:assert/strict";
import test, { afterEach } from "node:test";
import { answerConfirm, askUser, windowClosed } from "../src/confirm-ui.ts";
import { confirm, setConfirmHook, type ConfirmRequest } from "../src/handlers/confirm.ts";
import { clearChrome, installChrome, rejectsWith, storageMock } from "./mock.ts";

const COOKIES = {
  action: "readCookies",
  method: "storage.getCookies",
  url: "bank.test",
};

afterEach(() => {
  setConfirmHook(async () => true);
  clearChrome();
});

// ------------------------------------------------------- 裁决就在插件里

test("silent 模式下高危动作直接放行，一个框都不弹", async () => {
  storageMock({ "browse:config": { confirm_mode: "silent" } });
  setConfirmHook(async () => {
    throw new Error("silent 模式不该问用户");
  });
  await confirm({ ...COOKIES } as ConfirmRequest);
});

test("always 模式每次都问，同意过也照问", async () => {
  storageMock({ "browse:config": { confirm_mode: "always" } });
  let asked = 0;
  setConfirmHook(async () => {
    asked += 1;
    return true;
  });
  await confirm({ ...COOKIES } as ConfirmRequest);
  await confirm({ ...COOKIES } as ConfirmRequest);
  assert.equal(asked, 2);
});

test("per_domain 模式问一次，同意之后这个域名就不再问", async () => {
  const store = storageMock({ "browse:config": { confirm_mode: "per_domain" } });
  let asked = 0;
  setConfirmHook(async () => {
    asked += 1;
    return true;
  });
  await confirm({ ...COOKIES, url: "https://bank.test/x" } as ConfirmRequest);
  await confirm({ ...COOKIES, url: "https://bank.test/y" } as ConfirmRequest);
  assert.equal(asked, 1, "同一个域名只该问一次");
  assert.deepEqual((store.data["browse:config"] as { approved_domains: string[] }).approved_domains,
                   ["bank.test"], "同意必须落盘，不能只活在内存里");

  await confirm({ ...COOKIES, url: "https://other.test/" } as ConfirmRequest);
  assert.equal(asked, 2, "换个域名要重新问");
});

test("per_domain 下点了拒绝，不记进免确认名单", async () => {
  const store = storageMock({ "browse:config": { confirm_mode: "per_domain" } });
  setConfirmHook(async () => false);
  await rejectsWith(
    () => confirm({ ...COOKIES, url: "https://bank.test/" } as ConfirmRequest),
    "lg:user rejected",
  );
  const saved = store.data["browse:config"] as { approved_domains?: string[] } | undefined;
  assert.deepEqual(saved?.approved_domains ?? [], []);
});

test("配置读不出来时按最严的模式办，不是按最松的", async () => {
  // 存储坏了 / 权限没了：这时静默放行所有高危动作是最糟的失败方式
  clearChrome();
  let asked = 0;
  setConfirmHook(async () => {
    asked += 1;
    return true;
  });
  await confirm({ ...COOKIES } as ConfirmRequest);
  assert.equal(asked, 1, "读不到配置必须退到 always，而不是 silent");
});

test("hook 自己抛了异常，当拒绝处理", async () => {
  storageMock({ "browse:config": { confirm_mode: "always" } });
  setConfirmHook(async () => {
    throw new Error("service worker 被回收了");
  });
  await rejectsWith(() => confirm({ ...COOKIES } as ConfirmRequest), "lg:user rejected");
});

// ------------------------------------------------------- the dialog window

interface Created {
  url: string;
  type: string;
}

/** `chrome.windows` that records what was opened and hands back window ids. */
function windows(): { created: Created[]; removed: number[] } {
  const created: Created[] = [];
  const removed: number[] = [];
  installChrome({
    runtime: { getURL: (path: string) => `chrome-extension://x/${path}` },
    windows: {
      create: async (options: Created) => {
        created.push(options);
        return { id: 100 + created.length };
      },
      remove: async (id: number) => {
        removed.push(id);
      },
    },
  });
  return { created, removed };
}

/** Wait for the Nth dialog to exist, then click Allow/Deny in it. */
async function click(
  chrome: { created: Created[] },
  nth: number,
  approved: boolean,
): Promise<void> {
  while (chrome.created.length < nth) {
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
  const token = new URL(chrome.created[nth - 1].url).searchParams.get("token");
  assert.equal(answerConfirm(token, approved), true);
}

test("askUser opens a popup carrying the question and resolves on the click", async () => {
  const chrome = windows();
  const answer = askUser({ ...COOKIES } as ConfirmRequest);
  await click(chrome, 1, true);
  assert.equal(await answer, true);

  assert.equal(chrome.created.length, 1);
  assert.equal(chrome.created[0].type, "popup");
  const url = new URL(chrome.created[0].url);
  assert.equal(url.searchParams.get("action"), "readCookies");
  assert.equal(url.searchParams.get("method"), "storage.getCookies");
  assert.equal(url.searchParams.get("url"), "bank.test");
  assert.deepEqual(chrome.removed, [101], "the dialog closes itself once answered");
});

test("a denial resolves false", async () => {
  const chrome = windows();
  const first = askUser({ ...COOKIES, url: "deny.test" } as ConfirmRequest);
  await click(chrome, 1, false);
  assert.equal(await first, false);
});

test("the same question inside the recall window is not asked twice", async () => {
  const chrome = windows();
  const first = askUser({ ...COOKIES, url: "once.test" } as ConfirmRequest);
  await click(chrome, 1, true);
  assert.equal(await first, true);
  // This is the handler's own inline confirm() for the command the daemon just
  // gated — same question, milliseconds later. One dialog, not two.
  assert.equal(await askUser({ ...COOKIES, url: "once.test" } as ConfirmRequest), true);
  assert.equal(chrome.created.length, 1);
});

test("a different domain is a different question and asks again", async () => {
  const chrome = windows();
  const first = askUser({ ...COOKIES, url: "a.test" } as ConfirmRequest);
  await click(chrome, 1, true);
  await first;
  const second = askUser({ ...COOKIES, url: "b.test" } as ConfirmRequest);
  await click(chrome, 2, true);
  await second;
  assert.equal(chrome.created.length, 2);
});

test("closing the dialog without choosing is a refusal", async () => {
  const chrome = windows();
  const answer = askUser({ ...COOKIES, url: "closed.test" } as ConfirmRequest);
  while (chrome.created.length < 1) {
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
  await new Promise((resolve) => setTimeout(resolve, 0)); // let windowId land
  windowClosed(101);
  assert.equal(await answer, false);
});

test("no windows API at all is a refusal, never an approval", async () => {
  installChrome({ runtime: { getURL: (p: string) => p } });
  assert.equal(await askUser({ ...COOKIES, url: "nowin.test" } as ConfirmRequest), false);
});

test("a click nobody is waiting for is dropped", () => {
  // The service worker was evicted mid-dialog and restarted: the promise the
  // daemon was waiting on is gone, and the daemon already counted a refusal.
  assert.equal(answerConfirm("999", true), false);
  assert.equal(answerConfirm(undefined, true), false);
});
