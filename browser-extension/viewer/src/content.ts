import { TEXT_TYPE, prettify } from "./prettify.ts";

/**
 * 防闪：在 `document_start` 先把正文藏起来，等 DOM 齐了再一次性放出来。
 *
 * 判定要读 `<pre>` 的结构，只能等到 DOMContentLoaded；不先藏的话，中间这段时间
 * 用户会看见一眼没排版的原始文本。藏和放在同一个任务里完成，浏览器不会画出中间态。
 */
const style = document.createElement("style");
style.textContent = "body > pre { visibility: hidden }";

if (TEXT_TYPE.test(document.contentType)) {
  document.documentElement.append(style);
  document.addEventListener("DOMContentLoaded", () => {
    style.remove();
    prettify(document);
  });
}
