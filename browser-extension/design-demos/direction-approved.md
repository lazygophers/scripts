# direction-approved.md

- 展示了三版初稿（A zinc-dark / B stone-light / C neutral-blue 双栏），截图与对比报告在
  `browser-extension/design-demos/`（a.png / b.png / c.png / report.html）。
- 用户选择原话（2026-09-15）：「a+c混合」——A 的 Zinc Dark 配色 + C 的双栏工具台布局
  （左侧锚点导航、方法名 badge、粘底保存条）。
- token 依据：ui.shadcn.com/docs/theming（类1）；zinc 色值按 Tailwind 阶梯换算（推测:），
  实现以本文件选定值为准。
- 落地架构：纯 CSS token + 公共组件类（`src/ui.css`），settings / panel / confirm 三页共用，
  不引入 React/Tailwind。
