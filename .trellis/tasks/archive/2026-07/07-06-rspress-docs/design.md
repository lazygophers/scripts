# Design：Rspress 文档站

## 关键决策

### D1. 工程位置
Rspress 工程放仓库 `docs/`，`root: '.'`。内容 `docs/zh/` `docs/en/`。避免 `docs/docs/` 嵌套。

### D2. 默认语言
顶层 `lang: 'zh'`（research 修正：非 source.defaultLocale）。路由：`/` = 中文，`/en/` = 英文。

### D3. base 路径
GitHub Pages 子路径：`base: '/scripts/'`（仓库名）。本地 dev 也会用此 base，需注意链接。

### D4. 内容结构
两 nav 组：guide（使用手册）+ dev-guide（开发指南）。每语言镜像。

### D5. CI
GitHub Actions：push master → build → deploy Pages。pnpm frozen-lockfile 需先 `pnpm install` 生成 lockfile 入库。

### D6. .gitignore
仓库根 .gitignore 加：
- `docs/node_modules/`
- `docs/doc_build/`
- `docs/.rspress/`
- `docs/pnpm-lock.yaml` 不忽略（CI 需 frozen-lockfile）

## 风险

- **R1**：`base: '/scripts/'` 本地 dev 链接带前缀，预览需用 `/scripts/` 路径。可接受。
- **R2**：GitHub Pages 需用户在仓库 Settings 手动开（source = GitHub Actions）。README 标注。
- **R3**：pnpm lockfile 首次需生成。implement 时先 install 产 lockfile，再 commit。
