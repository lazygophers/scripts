/**
 * Mermaid 绘图包。只有文档里真的出现了标 `mermaid` 的围栏代码块才会被动态加载。
 *
 * 整个 mermaid 随扩展打包，运行时不从网络取任何东西。
 */

import mermaid from "mermaid";

// 模块被 `import()` 缓存，这行一个页面只跑一次。
// `strict` 是 mermaid 自己的净化档位：图里写的 HTML 会被它清掉，才敢把结果 `innerHTML` 进页面。
mermaid.initialize({
  startOnLoad: false,
  securityLevel: "strict",
  theme: "dark",
  fontFamily: "system-ui, sans-serif",
});

/** 画一张图，返回 SVG 源码。语法有错时 mermaid 自己抛错，由调用方就地显示原文和错误说明。 */
export async function renderDiagram(code: string, id: string): Promise<string> {
  const { svg } = await mermaid.render(id, code);
  return svg;
}
