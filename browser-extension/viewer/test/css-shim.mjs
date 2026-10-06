// 测试专用解析钩子：把 KaTeX 的样式表 import 重定向到空模块（node 加载不了 .css），
// 把 mermaid 包重定向到桩（node 里没有 window，排不了版）。
export async function resolve(specifier, context, next) {
  if (specifier.endsWith(".css")) {
    return { url: "data:text/javascript,export default {}", shortCircuit: true };
  }
  if (specifier === "mermaid") {
    return {
      url:
        "data:text/javascript,export default { initialize() {}, render: async () => ({ svg: '<svg/>' }) }",
      shortCircuit: true,
    };
  }
  return next(specifier, context);
}
