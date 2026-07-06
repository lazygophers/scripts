# Implement：Rspress 文档站

单 task，顺序执行（无并行，文件集独立）。

## 步骤

### S1. docs/ 工程骨架
- 建 `docs/package.json`（@rspress/core ^2.0.16, scripts dev/build/preview, private）。
- 建 `docs/tsconfig.json`（research 模板）。
- 建 `docs/rspress.config.ts`（root/lang/locales/base/title，design D1-D3）。
- 建 `docs/i18n.json`（nav/meta 文案 key：guide/dev/scripts/intro/structure/addScript/testing/contributing）。
- `cd docs && pnpm install`（产 pnpm-lock.yaml）。
- 验证：`pnpm run build` 不报错（空内容先跑通）。

### S2. 中文内容 docs/zh/
- `zh/index.md`：pageType home hero + features。
- `zh/_nav.json`：guide + dev-guide 两组。
- `zh/guide/_meta.json` + `zh/guide/introduction.md` + `zh/guide/scripts.md`（功能表，README 迁移）。
- `zh/dev-guide/_meta.json` + `zh/dev-guide/structure.md` + `zh/dev-guide/add-script.md` + `zh/dev-guide/testing.md` + `zh/dev-guide/contributing.md`。
- 内容从原 README（git history 可查 195a791 之前版本）+ CLAUDE.md 取。

### S3. 英文镜像 docs/en/
- 同 S2 结构，英文翻译。
- hero/introduction/scripts/features 全翻译。

### S4. .gitignore + README
- 仓库根 .gitignore 加 docs 构建产物（design D6）。
- README 末尾加「## 文档」链接 https://lazygophers.github.io/scripts/。

### S5. CI
- `.github/workflows/deploy-docs.yml`：push master → node 22 + pnpm → cd docs install --frozen-lockfile → build → upload doc_build → deploy Pages。
- 验证：yml 语法（`actionlint` 或 GitHub UI 检查）。

### S6. 本地构建验证
- `cd docs && pnpm run build`。
- `pnpm run preview` 抽查中英路由。
- 输出 build 结果。

## 失败处理

| 触发 | 一线修复 | 兜底 |
| --- | --- | --- |
| pnpm install 失败 | 查 @rspress/core 版本，node 22 兼容 | 标 TODO，本地手动 |
| build 报错 | 看 frontmatter / _meta.json 格式 | 逐文件注释定位 |
| base 路径链接错 | 确认 Rspress base 行为，本地用 /scripts/ 访问 | dev 时暂去 base |
| CI yml 无效 | actionlint | 标 TODO，用户手动调 |
