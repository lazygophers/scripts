/**
 * 高亮库的薄包装，单独打成一个包，由 `prettify.ts` 在真的遇到代码文件时才动态加载。
 *
 * 内容脚本匹配 `<all_urls>`，所以每张网页都会拉一遍它的 bundle；高亮库有几百 KB，
 * 塞进去等于让所有页面替代码文件付钱。拆出来之后只有代码页会去取。
 */

import hljs from "highlight.js/lib/common";

/**
 * 着色，返回高亮后的 HTML 片段。语言不在合集里就返回 null，调用方保留纯文本。
 *
 * 先问 `getLanguage`：`highlight` 遇到没注册的语言是直接抛 `Unknown language`，
 * 而「冷门语言退回纯文本」正是要覆盖的场景。
 */
export function colorize(code: string, language: string): string | null {
  if (!hljs.getLanguage(language)) return null;
  return hljs.highlight(code, { language, ignoreIllegals: true }).value;
}
