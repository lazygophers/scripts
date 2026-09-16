/**
 * The confirmation dialog window. Reads the question out of its own URL and
 * posts the answer back to the service worker, which is what the daemon is
 * waiting on. Closing the window without clicking is handled service-worker
 * side (`windowClosed`) and counts as a refusal.
 */
import { localize, msg } from "./i18n.ts";

localize();

const query = new URLSearchParams(location.search);

function fill(id: string, value: string): void {
  const node = document.getElementById(id);
  if (node) {
    node.textContent = value;
  }
}

fill("action", query.get("action") ?? "?");
fill("method", query.get("method") ?? "?");
fill("url", query.get("url") || msg("confirmWholeBrowser"));

function answer(approved: boolean): void {
  void chrome.runtime.sendMessage({
    type: "browse-confirm",
    token: query.get("token"),
    approved,
  });
}

document.getElementById("yes")?.addEventListener("click", () => answer(true));
document.getElementById("no")?.addEventListener("click", () => answer(false));
