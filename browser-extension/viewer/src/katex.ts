/**
 * 数学公式排版包。只有文档里真的出现了 `$…$` 才会被动态加载。
 *
 * 连同 KaTeX 自己的样式表和字体一起打包（`katex.min.css` 里的 `url()` 由 esbuild 改写成
 * dist 里的字体文件），所以排版过程中不向网络取任何东西——扩展的内容安全策略也不允许。
 */

import katex from "katex";

import "katex/dist/katex.min.css";

/**
 * 把一个占位节点换成排好版的公式。
 *
 * `throwOnError: false` 是 KaTeX 自己的降级：语法有错时它就地渲染成红色的原始文本并带上
 * 错误说明，正好是这里要的行为——一条写错的公式不该让整篇文档白屏。
 */
export function renderMath(node: HTMLElement, tex: string, display: boolean): void {
  katex.render(tex, node, { displayMode: display, throwOnError: false, output: "html" });
}
