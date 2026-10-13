# 多 agent 并发改同一仓库：文件分区与终态核对

2026-10-13 browse console 任务（commit 5605451）实测：多个 agent 并发改扩展，
各自报完成，合并后才发现重复 import、manifest.json 重复键、接口漂移（一方改
签名另一方按旧签名写测试），全部靠终态核对去重后才全绿。

- **汇聚点文件指定单一 owner**：`manifest.json`、`handlers/index.ts`、
  `test/mock.ts` 这类多方都要动的文件，开工前指给一个 agent 独占，其余 agent
  只提需求不改文件。人人都想「顺手加一行」的文件就是冲突源。
- **完成以终态为准，不以汇报为准**：每个 agent 报「完成」不算完成；合并后必跑
  终态核对——`git diff` 里 grep 重复键 / 重复 import，测试全绿才算。两份各自
  全绿的 diff 合在一起可以不绿。
- **接口漂移靠 typecheck 拦**：一方改函数签名、另一方按旧签名写测试，只有
  `npm --prefix browser-extension/browse run typecheck` 能拦住；并发改 TS 后
  必跑，不许只跑各自的测试文件（跑法见 testing.md「改完 TypeScript 文件先做
  单文件冒烟，再跑全套 typecheck」）。
