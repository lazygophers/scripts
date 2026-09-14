/**
 * Text for the two extension pages, out of `_locales/<lang>/messages.json`.
 *
 * Chrome picks the language itself (browser UI language, falling back to
 * `default_locale`), so there is no language setting here and none to keep in
 * sync with the CLI's `--lang`. What Chrome does *not* do is substitute
 * `__MSG_x__` inside HTML — that only works in `manifest.json` and CSS — so the
 * static markup carries `data-i18n="<key>"` and the page fills it on load.
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
}

/** One message, with `$1`, `$2`… filled in. */
export function msg(key: string, ...args: string[]): string {
  return chrome.i18n.getMessage(key, args);
}
