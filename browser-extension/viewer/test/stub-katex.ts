/**
 * 测试里替掉真的 KaTeX 包。
 *
 * 真包 `import` 了自己的 CSS，那行只有打包器认得，`node --test` 直接加载源码会失败；
 * 而「公式排版成什么样」同样是第三方库的行为，不归这里测。这里只记下收到的公式和档位。
 */
export function renderMath(node: HTMLElement, tex: string, display: boolean): void {
  node.textContent = `${display ? "块" : "行内"}公式:${tex}`;
}
