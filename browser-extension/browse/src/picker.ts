/**
 * desktopCapture 的选源小窗：chooseDesktopMedia 必须在扩展页的用户手势里调，
 * CLI 发来的指令没有手势，所以由这里的一个按钮点击来补上。选完把 streamId
 * 发回 service worker（`capture.ts` 的 desktopSourcePicked 在那边接）。
 */
import { localize, msg } from "./i18n.ts";

localize();

const query = new URLSearchParams(location.search);
const sources = (query.get("sources") ?? "screen,window").split(",");

const box = document.getElementById("sources");
const labels: Record<string, string> = {
  screen: msg("pickerScreen"),
  window: msg("pickerWindow"),
  tab: msg("pickerTab"),
};

for (const source of sources) {
  const button = document.createElement("button");
  button.className = "btn";
  button.textContent = labels[source] ?? source;
  button.addEventListener("click", () => {
    chrome.desktopCapture.chooseDesktopMedia([source as "screen" | "window" | "tab" | "audio"], (streamId) => {
      void chrome.runtime.sendMessage({ type: "browse-pick", streamId: streamId ?? null });
      window.close();
    });
  });
  box?.append(button);
}
