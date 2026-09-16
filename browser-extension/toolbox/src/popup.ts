import {
  collectPageSnapshot,
  markdownFromSnapshot,
  DEFAULT_MARKDOWN_OPTIONS,
  type MarkdownOptions,
  type PageSnapshot,
} from "./export-markdown.ts";

const element = <T extends HTMLElement>(id: string): T => {
  const found = document.getElementById(id);
  if (!found) throw new Error(`缺少界面元素：${id}`);
  return found as T;
};

const notice = element<HTMLParagraphElement>("notice");

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function showNotice(message: string, error = false): void {
  notice.textContent = message;
  notice.classList.toggle("error", error);
  notice.hidden = false;
}

function clearNotice(): void {
  notice.hidden = true;
}

document.querySelectorAll<HTMLButtonElement>(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab, .panel").forEach((item) => item.classList.remove("active"));
    tab.classList.add("active");
    const panelId = tab.dataset.panel;
    if (panelId) element(panelId).classList.add("active");
    clearNotice();
  });
});

async function loadMarkdownOptions(): Promise<MarkdownOptions> {
  try {
    const stored = await chrome.storage.local.get("markdownOptions");
    return { ...DEFAULT_MARKDOWN_OPTIONS, ...(stored.markdownOptions as Partial<MarkdownOptions> | undefined) };
  } catch {
    // ponytail: 读不到设置就按默认导出，不让坏存储挡住复制
    return DEFAULT_MARKDOWN_OPTIONS;
  }
}

element<HTMLButtonElement>("openDns").addEventListener("click", async () => {
  clearNotice();
  try {
    await chrome.tabs.create({ url: "chrome://net-internals/#dns" });
    showNotice("已打开 DNS 缓存页面，请手动点击 Clear host cache");
  } catch (error) {
    showNotice(`无法打开 DNS 缓存页面：${errorMessage(error)}`, true);
  }
});

element<HTMLButtonElement>("copyMarkdown").addEventListener("click", async (event) => {
  const button = event.currentTarget as HTMLButtonElement;
  clearNotice();
  button.disabled = true;
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tab?.id === undefined) throw new Error("找不到当前网页");
    const results = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: collectPageSnapshot });
    const snapshot = results[0]?.result as PageSnapshot | undefined;
    if (!snapshot) throw new Error("无法读取当前网页；Chrome 内置页面不允许扩展读取");
    await navigator.clipboard.writeText(
      markdownFromSnapshot(snapshot, undefined, await loadMarkdownOptions()),
    );
    showNotice(snapshot.selectionHtml ? "已复制选中内容的 Markdown" : "已复制网页正文的 Markdown");
  } catch (error) {
    showNotice(`复制失败：${errorMessage(error)}`, true);
  } finally {
    button.disabled = false;
  }
});
