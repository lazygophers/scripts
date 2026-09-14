import assert from "node:assert/strict";
import test, { afterEach } from "node:test";
import { answerConfirm, askUser, windowClosed } from "../src/confirm-ui.ts";
import {
  confirmRequest,
  setConfirmHook,
  type ConfirmRequest,
} from "../src/handlers/confirm.ts";
import { dispatch } from "../src/handlers/index.ts";
import { clearChrome, installChrome, rejectsWith } from "./mock.ts";

const COOKIES = {
  action: "readCookies",
  method: "storage.getCookies",
  url: "bank.test",
};

afterEach(() => {
  setConfirmHook(async () => true);
  clearChrome();
});

// ------------------------------------------------------- lg:confirm.request

test("lg:confirm.request answers with the hook's decision, it never throws", async () => {
  const seen: ConfirmRequest[] = [];
  setConfirmHook(async (request) => {
    seen.push(request);
    return false;
  });
  assert.deepEqual(await dispatch("lg:confirm.request", { ...COOKIES }), {
    approved: false,
  });
  assert.deepEqual(seen, [COOKIES]);

  setConfirmHook(async () => true);
  assert.deepEqual(await dispatch("lg:confirm.request", { ...COOKIES }), {
    approved: true,
  });
});

test("a browser-wide action arrives with a null url", async () => {
  let got: ConfirmRequest | null = null;
  setConfirmHook(async (request) => {
    got = request;
    return true;
  });
  await confirmRequest({ action: "readBookmarks", method: "lg:bookmarks.search", url: null });
  assert.deepEqual(got, {
    action: "readBookmarks",
    method: "lg:bookmarks.search",
    url: null,
  });
});

test("an unknown action is an argument error, not a silent approval", async () => {
  setConfirmHook(async () => {
    throw new Error("the hook must not be reached");
  });
  for (const params of [
    { action: "takeOverTheWorld", method: "storage.getCookies", url: null },
    { action: "readCookies", url: null },
    {},
  ]) {
    await rejectsWith(() => confirmRequest(params), "invalid argument");
  }
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
