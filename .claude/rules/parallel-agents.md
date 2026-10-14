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
- **多 agent prompt 里的产物路径复制粘贴，不手写。** 2026-10-14 round2 方向二的
  subagent prompt 把输出目录混写成 `design-demos/` 与 `round2/` 两套，实际落点靠
  事后核对才确认。分发前把每条 prompt 里的路径互相对一遍：同一产物只能有一个名字；
  路径直接抄上一条消息或 `ls` 输出，不凭记忆敲。
- **一个文件混进两个任务的改动，按 hunk 拆干净再 commit**：`git diff -- <file>`
  看全量，python 按 `@@` 拆 hunk，挑着 `git apply --cached`，分次 commit。
  `git add -p` 是交互式的，agent 用不了；hunk 拆分是同文件双任务唯一的干净路
  （2026-10-14 票 07 的 mcp 入口与临时插进来的 `open` 默认后台同落
  `lib/cli/browse.py`，拆成 7a18ac0 / 21a2fe8 两个 commit）。
