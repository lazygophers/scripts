/**
 * Text for the two extension pages, out of `_locales/<lang>/messages.json`.
 *
 * Chrome picks the language itself — the browser UI language, falling back to
 * `default_locale` (`zh_CN`) when there is no `_locales` folder for it. So there
 * is no language switch anywhere: nothing to configure, nothing to pass in. The
 * CLI is a separate thing and keeps printing Chinese.
 *
 * What Chrome does *not* do is substitute `__MSG_x__` inside HTML — that only
 * works in `manifest.json` and CSS — so the static markup carries
 * `data-i18n="<key>"` and the page fills it on load.
 */

/** Replace the text of every `[data-i18n]` node with its message. */
export function localize(root: ParentNode = document): void {
  document.documentElement.lang = chrome.i18n.getUILanguage();
  for (const node of Array.from(root.querySelectorAll<HTMLElement>("[data-i18n]"))) {
    const key = node.dataset.i18n;
    const message = key ? chrome.i18n.getMessage(key) : "";
    if (message) {
      node.textContent = message;
    }
  }
  // 输入框的提示文字不是节点内容，单独一条属性。
  for (const node of Array.from(
    root.querySelectorAll<HTMLInputElement>("[data-i18n-placeholder]"),
  )) {
    const message = chrome.i18n.getMessage(node.dataset.i18nPlaceholder ?? "");
    if (message) {
      node.placeholder = message;
    }
  }
}

/** One message, with `$1`, `$2`… filled in. */
export function msg(key: string, ...args: string[]): string {
  return chrome.i18n.getMessage(key, args);
}
