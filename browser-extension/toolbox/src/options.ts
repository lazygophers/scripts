import type { MarkdownOptions } from "./export-markdown.ts";

const element = <T extends HTMLElement>(id: string): T => {
  const found = document.getElementById(id);
  if (!found) throw new Error(`缺少界面元素：${id}`);
  return found as T;
};

const notice = element<HTMLParagraphElement>("notice");
const includeTitle = element<HTMLInputElement>("includeTitle");
const includeSource = element<HTMLInputElement>("includeSource");

function showNotice(message: string, error = false): void {
  notice.textContent = message;
  notice.classList.toggle("error", error);
  notice.hidden = false;
}

async function load(): Promise<void> {
  const stored = await chrome.storage.local.get("markdownOptions");
  const options = stored.markdownOptions as Partial<MarkdownOptions> | undefined;
  includeTitle.checked = options?.includeTitle !== false;
  includeSource.checked = options?.includeSource !== false;
}

element<HTMLButtonElement>("save").addEventListener("click", async () => {
  try {
    await chrome.storage.local.set({
      markdownOptions: { includeTitle: includeTitle.checked, includeSource: includeSource.checked },
    });
    showNotice("已保存");
  } catch (error) {
    showNotice(`保存失败：${error instanceof Error ? error.message : String(error)}`, true);
  }
});

void load().catch((error: unknown) =>
  showNotice(`无法读取设置：${error instanceof Error ? error.message : String(error)}`, true),
);
