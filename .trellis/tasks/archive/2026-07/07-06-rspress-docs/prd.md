# PRD：建 Rspress 文档站 docs/ 中英多语言

## 背景

仓库现仅有 README 使用手册。用户要建 `docs/` 文档站，用 Rspress v2，支持中英多语言。

调研真值见 `research/rspress-i18n.md`（官方文档全引）。关键修正：Rspress 用顶层 `lang` 字段定默认语言（非 Docusaurus 的 `source.defaultLocale`）；默认语言路由前缀被移除（占 `/`），非默认带前缀（占 `/en/`）。

## 目标

1. 在仓库 `docs/` 下建 Rspress v2 文档站（独立 package.json，不影响主仓 Python 脚本）。
2. 中英双语，默认中文（根 `/`），英文 `/en/`。
3. 内容：首页 hero + 使用手册（README 现有内容迁移：简介 + 安装 + 功能表）。

## 需求规格

### 1. 目录结构

```
docs/
├── package.json              # @rspress/core ^2.0.16, scripts: dev/build/preview
├── rspress.config.ts         # root: 'docs', lang: 'zh', locales [zh, en]
├── tsconfig.json
├── i18n.json                 # nav/meta 文案 key 翻译
└── docs/                     # Rspress root（config root 指此）
    ├── zh/                   # 默认语言，路由占 /
    │   ├── index.md          # pageType: home hero
    │   ├── _nav.json
    │   └── guide/
    │       ├── _meta.json
    │       ├── introduction.md
    │       └── scripts.md    # 功能表（README 迁移）
    └── en/                   # 路由占 /en/
        ├── index.md
        ├── _nav.json
        └── guide/
            ├── _meta.json
            ├── introduction.md
            └── scripts.md
```

注：Rspress config `root` 指向内容目录。为避免 `docs/docs/` 嵌套，结构改为：仓库根 `docs/` 放 Rspress 工程（config/package），内容在 `docs/docs/zh` 与 `docs/docs/en`。或更直观：仓库根建 Rspress 工程，`root: 'docs'`，内容 `docs/{zh,en}/` —— 即 docs 既是目录名又是 root。

**采用方案**（避免嵌套）：Rspress 工程文件（config/package/tsconfig/i18n.json）放仓库根 `docs/`，`root: '.'` 指向自身，内容子目录 `zh/` `en/`：

```
docs/
├── package.json
├── rspress.config.ts         # root: '.'（默认即 cwd，可省略）
├── tsconfig.json
├── i18n.json
├── zh/
│   ├── index.md
│   ├── _nav.json
│   └── guide/{_meta.json, introduction.md, scripts.md}
└── en/
    ├── index.md
    ├── _nav.json
    └── guide/{_meta.json, introduction.md, scripts.md}
```

### 2. rspress.config.ts

```ts
import { defineConfig } from '@rspress/core';

export default defineConfig({
  root: '.',
  lang: 'zh',
  title: 'Scripts',
  locales: [
    { lang: 'zh', label: '简体中文', title: 'Scripts 文档', description: '脚本工具集文档' },
    { lang: 'en', label: 'English', title: 'Scripts Docs', description: 'Script utilities docs' },
  ],
});
```

### 3. 内容（中英镜像）

**使用手册**（guide/）：
- `index.md`：pageType: home，hero（name/text/tagline/actions）+ features（Git/构建/进程/通知）。
- `guide/introduction.md`：项目简介（README 首段）。
- `guide/scripts.md`：功能表（README 脚本表 + 迁移说明）。

**开发指南**（dev-guide/ 或并入 guide/）：
- `guide/structure.md`：目录结构（bin/lib/tests 调用链）。
- `guide/add-script.md`：加新脚本两步（薄壳 + lib/commands 业务逻辑）+ 别名脚本。
- `guide/testing.md`：`python3 -m unittest discover -s tests -q`。
- `guide/contributing.md`：PR 规范。

`_nav.json`：`[{ "text": "guide", "link": "/guide/introduction" }, { "text": "dev", "link": "/dev-guide/structure" }]`（label 用 i18n key）。
`_meta.json` 各目录独立一份。

### 4. .gitignore

仓库根 .gitignore 加：`docs/node_modules/`、`docs/doc_build/`、`docs/.rspress/`。

### 5. 验证

- `cd docs && pnpm install && pnpm run build` 成功。
- `pnpm run dev` 本地可访问，中英切换正常。
- 默认 `/` 显中文，`/en/` 显英文。

### 6. CI 部署 GitHub Pages

`.github/workflows/deploy-docs.yml`：
- 触发：push 到 master（docs/ 或仓库根 README 改动）。
- 步骤：setup node 22 + pnpm → `cd docs && pnpm install --frozen-lockfile` → `pnpm run build` → upload `docs/doc_build/` → deploy to GitHub Pages。
- Rspress config 加 `base: '/scripts/'`（GitHub Pages 子路径，仓库名）。
- 仓库 Settings → Pages source = GitHub Actions（用户手动配，README 标注）。

### 7. README 文档站链接

README 末尾加：
```markdown
## 文档

完整文档站：https://lazygophers.github.io/scripts/
```

## 改动范围

- 新增 `docs/` 全目录（package.json, rspress.config.ts, tsconfig.json, i18n.json, zh/, en/）。
- 仓库根 .gitignore 加 docs 构建产物。
- README 可加文档站链接（可选）。

## 验收标准

- `docs/` 下 Rspress 工程完整，`pnpm install` + `pnpm run build` 无错。
- 中英双内容镜像齐全（index + guide/introduction + guide/scripts）。
- 默认语言中文（根 `/`），英文 `/en/`，语言切换器可用。
- 功能表内容完整（含 push_* 批量 + 迁移说明，从 README 迁移）。
- docs/node_modules / doc_build / .rspress 被 gitignore，不入库。

## 待确认

- [x] 内容范围：使用手册 + 开发指南（已确认）。
- [x] README 加文档站链接（已确认）。
- [x] CI 自动部署 GitHub Pages（已确认）。
