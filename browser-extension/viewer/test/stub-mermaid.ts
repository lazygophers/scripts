/**
 * 测试里替掉真的 mermaid 包。
 *
 * 真包在 jsdom 里画不出图（要量文字宽高，jsdom 没有排版），而「图长什么样」是第三方库的行为，
 * 不归这里测。这里只保留调用方在乎的两件事：画得出来返回 SVG，画不出来抛错。
 */
export async function renderDiagram(code: string, id: string): Promise<string> {
  if (code.includes("这行是错的")) throw new Error("解析失败");
  return `<svg id="${id}"><title>${code.split("\n")[0]}</title></svg>`;
}
