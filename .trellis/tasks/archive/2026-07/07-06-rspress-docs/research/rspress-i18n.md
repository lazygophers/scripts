# Research: Rspress v2 多语言（i18n）配置 + 项目初始化

- **Query**: Rspress v2 i18n 配置规范 + 项目初始化，为建 docs/ 文档站提供真值依据
- **Scope**: external（官方文档 https://rspress.rs/）
- **Date**: 2026-07-06

## 来源（全部官方）

| 页面 | URL | 用途 |
|---|---|---|
| llms.txt 索引 | https://rspress.rs/llms.txt | 全站页面索引 |
| i18n 指南 | https://rspress.rs/guide/basic/i18n.md | 多语言目录/locales/useI18n |
| Quick start | https://rspress.rs/guide/start/getting-started.md | 初始化命令/模板/package.json |
| Homepage | https://rspress.rs/guide/basic/home-page.md | hero/features frontmatter |
| Auto nav/sidebar | https://rspress.rs/guide/basic/auto-nav-sidebar.md | `_meta.json`/`_nav.json` 多语言放置 |
| Basic config API | https://rspress.rs/api/config/config-basic.md | `lang`/`locales`/`root` schema |

---

## 1. 多语言目录结构

**结论**：`docs/<lang>/` 模式 —— 每个语言（包括默认语言）都在 `docs` 下有独立子目录。

官方 i18n 文档明确给出树形结构（i18n.md:111-140）：

```
├── doc
│   ├── en
│   │   ├── _nav.json
│   │   ├── api
│   │   │   └── index.mdx
│   │   ├── guide
│   │   │   ├── _meta.json
│   │   │   └── start
│   │   │       ├── introduction.mdx
│   │   │       └── quick-start.mdx
│   │   └── index.md
│   └── zh
│       ├── _nav.json
│       ├── api
│       │   └── index.mdx
│       ├── guide
│       │   ├── _meta.json
│       │   └── start
│       │       ├── introduction.mdx
│       │       └── quick-start.mdx
│       └── index.md
├── i18n.json
├── package.json
├── rspress.config.ts
└── tsconfig.json
```

> "docs for different languages live in the `en` and `zh` directories under `docs`"（i18n.md:140）

注意：i18n.md 示例顶层目录名是 `doc`（单数），但 getting-started.md:243 手动配置示例用的是 `root: 'docs'`（复数）。后者是约定俗成，**推荐用 `docs`**。

---

## 2. rspress.config.ts —— `locales` 与默认语言

### `locales` 完整 schema（config-basic.md:511-522）

```ts
export interface Locale {
  lang: string;       // 语言代码，作为 URL 前缀，如 'zh' / 'en' / 'zh-CN'
  label: string;      // 导航栏语言切换器显示文本
  title?: string;     // 该语言下的站点标题（覆盖全局 title）
  description?: string;
}
```

`dir` 字段（RTL/LTR）**不在 `locales` 内**。Rspress 的 `Locale` 接口只暴露 `lang/label/title/description`，方向由 `lang` 自动推断。

### 默认语言：顶层 `lang` 字段（非 `source.defaultLocale`）

**重要更正**：Rspress 不使用 `source.defaultLocale` 语法（那是 Docusaurus）。Rspress 用顶层 `lang`（config-basic.md:89-112）：

```ts title="rspress.config.ts"
import { defineConfig } from '@rspress/core';

export default defineConfig({
  lang: 'en',          // 默认语言
  locales: [
    { lang: 'en',  label: 'English',   title: 'Rspress', description: 'Static Site Generator' },
    { lang: 'zh',  label: '简体中文',   title: 'Rspress', description: '静态网站生成器' },
  ],
});
```

> `themeConfig.locales` 也存在但将被废弃，应使用顶层 `locales`（i18n.md:85-89）。

### 文案数据源：`i18n.json`

仓库根（cwd）放 `i18n.json`，类型为 `{ [textId: string]: { [lang: string]: string } }`（i18n.md:27-54）。可用 `i18nSourcePath`（config-basic.md:114）或 `i18nSource`（异步加载）覆盖。

---

## 3. 导航 / 侧边栏 `_nav.json` / `_meta.json`

### 多语言下放置位置（auto-nav-sidebar.md:24-46）

**每语言一份独立的 `_nav.json`**，放在 `docs/<lang>/_nav.json`：

```
docs
├── en
│   ├── _nav.json     ← 导航栏级
│   └── guide
│       ├── _meta.json  ← 侧边栏级
│       └── ...
└── zh
    ├── _nav.json
    └── guide
        ├── _meta.json
        └── ...
```

### `_nav.json` 用 i18n key（i18n.md:150-163）

`text` 字段可填 i18n key，Rspress 按 `i18n.json` + 当前语言翻译：

```json title="docs/zh/_nav.json"
[
  { "text": "guide", "link": "/guide/start/introduction" },
  { "text": "api",   "link": "/api/" }
]
```

注意 `link` **不带语言前缀**，Rspress 会按当前语言自动加前缀。

### `_meta.json` 用 i18n key（i18n.md:169-179）

`label` 字段可填 i18n key：

```json title="docs/zh/guide/_meta.json"
[
  { "type": "dir", "name": "start", "label": "gettingStarted" }
]
```

### 全局共享侧边栏（可选）

若在 `docs/<lang>/` 根同时放 `_meta.json`，则全站共享单一侧边栏（auto-nav-sidebar.md:63-95），否则按 nav 切换。

### `nav` 配置多语言（替代方案）

也可在 `rspress.config.ts` 的 `themeConfig.nav` 里直接声明，但官方**推荐用 `_nav.json`**（HMR 友好、配置精简，auto-nav-sidebar.md:3-7）。

---

## 4. 首页 hero（`pageType: home`）

### frontmatter 字段（home-page.md:7-35）

每个语言目录下放一个 `index.md`（即 `docs/zh/index.md` 和 `docs/en/index.md`），frontmatter：

```yaml title="docs/en/index.mdx"
---
pageType: home
title: Rspress
titleSuffix: 'Rsbuild-based Static Site Generator'

hero:
  name: Rspress
  text: A documentation solution
  tagline: A modern documentation development technology stack
  actions:
    - theme: brand
      text: Introduction
      link: /en/guide/introduction
    - theme: alt
      text: Quick Start
      link: /en/guide/getting-started

features:
  - title: 'MDX Support'
    details: MDX is a powerful way to write content. You can use React components in Markdown.
    icon: 📦
  - title: 'Feature Rich'
    details: Out-of-the-box support for i18n, full-text search, and more.
    icon: 🎨
---
```

### 多语言首页

**每语言一个 `index.md`**，路径 `docs/<lang>/index.md`。hero 链接 `link: /en/guide/introduction` 这里**官方示例带了语言前缀**（home-page.md:21-23），但默认语言会被去掉前缀（见第 7 节），所以默认语言 `link` 应写 `/guide/introduction`。

完整字段类型见 `/api/config/config-frontmatter.md#hero`。

---

## 5. 初始化命令

### 推荐方式（getting-started.md:41-62）

```sh
npm create rspress@latest
# 或
pnpm create rspress@latest
yarn create rspress
bun create rspress@latest
```

Node.js 要求 **20.19+ 或 22.12+**（getting-started.md:31）。

### 模板选项（getting-started.md:78-87）

| Template | Description |
|---|---|
| `basic` | 最小单语言站点 |
| `basic-theme` | 单语言 + `theme/` 目录（自定义主题） |
| **`i18n`** | **多语言（英文 + 中文）** |
| `i18n-theme` | 多语言 + `theme/` 目录 |

脚手架交互会问：项目路径、是否启用 i18n、是否建 `theme/` 目录、可选工具（rslint/eslint/prettier/biome）、可选 Agent Skills。

### 非交互模式（getting-started.md:131-148）

```bash
# 直接生成多语言模板
npx -y create-rspress@latest my-docs --template i18n

# 多语言 + 主题
npx -y create-rspress@latest my-docs --template i18n-theme

# 加工具与 Agent Skills
npx -y create-rspress@latest my-docs --template basic-theme \
  --tools rslint,prettier \
  --skill rspress-docs-generator,rspress-best-practices,rspress-description-generator
```

CLI flags（getting-started.md:158-180）：`-d/--dir`、`-t/--template`、`--tools`、`--skill`、`--override`、`--packageName`、`--template-version`。

**注意**：没有 `rspress init` 子命令；官方初始化通过 `create-rspress` 脚手架（独立 npm 包）。

### `i18n` 模板生成的目录结构（推测）

脚手架生成结构 = 第 1 节官方树（`docs/en/` + `docs/zh/` + `i18n.json` + `rspress.config.ts` + `package.json` + `tsconfig.json`）。

---

## 6. package.json 最小依赖（Rspress v2）

### 最新稳定版（npm registry 实测 2026-07-06）

| 包 | 版本 |
|---|---|
| `@rspress/core` | **2.0.16** |
| `@rspress/plugin-algolia` | 2.0.16 |
| `@rspress/plugin-llms` | 2.0.16 |

所有 `@rspress/*` 包同步发版，取 `^2.0.16`。

### 最小 `package.json`（getting-started.md:196-235）

```json
{
  "name": "my-docs",
  "private": true,
  "scripts": {
    "dev": "rspress dev",
    "build": "rspress build",
    "preview": "rspress preview"
  },
  "devDependencies": {
    "@rspress/core": "^2.0.16"
  }
}
```

- `rspress dev`：启动开发服务器（支持 `--port` `--host`，getting-started.md:289-291）
- `rspress build`：构建产物到 `doc_build/`（默认，getting-started.md:301-303）
- `rspress preview`：本地预览 `doc_build/`（getting-started.md:309-313）

### 可选扩展包

- `@rspress/plugin-algolia` — Algolia 搜索
- `@rspress/plugin-llms` — 生成 `llms.txt` / SSG-MD（config-basic.md:599-621 的 `llms` 配置）
- `@rspress/shiki-twoslash` — TypeScript twoslash 代码块

### `tsconfig.json`（getting-started.md:247-279）

```json
{
  "compilerOptions": {
    "lib": ["DOM", "ES2023"],
    "jsx": "react-jsx",
    "target": "ES2023",
    "module": "ESNext",
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "strict": true
  },
  "include": ["docs", "theme", "rspress.config.ts"]
}
```

---

## 7. 默认语言路由

**结论**：默认语言占根 `/`（语言前缀被移除），非默认语言带前缀。

官方原文（i18n.md:104-105 / config-basic.md:94）：

> "Rspress removes the language prefix from routes in the default language. For example, `/en/guide/getting-started` becomes `/guide/getting-started`."

控制方式：通过顶层 `lang` 字段决定哪个语言是默认（前缀被剥离）。例如：

| 配置 `lang` | 文件 `docs/zh/guide/intro.md` 实际路由 | 文件 `docs/en/guide/intro.md` 实际路由 |
|---|---|---|
| `'zh'`（默认中文） | `/guide/intro` | `/en/guide/intro` |
| `'en'`（默认英文） | `/zh/guide/intro` | `/guide/intro` |

**不能**让默认语言也保留前缀——这是 Rspress 的硬性行为。

---

## 推荐落地配置（中英，默认 zh）

### 推荐目录结构

```
scripts-docs/                  # 文档站独立子目录
├── docs
│   ├── zh                     # 默认语言（路由占 / ）
│   │   ├── index.md           # 首页 pageType: home
│   │   ├── _nav.json
│   │   └── guide
│   │       ├── _meta.json
│   │       └── intro.mdx
│   └── en                     # 非默认（路由占 /en/ ）
│       ├── index.md
│       ├── _nav.json
│       └── guide
│           ├── _meta.json
│           └── intro.mdx
├── i18n.json
├── rspress.config.ts
├── tsconfig.json
└── package.json
```

### 最小 `rspress.config.ts`（默认 zh）

```ts title="rspress.config.ts"
import { defineConfig } from '@rspress/core';

export default defineConfig({
  root: 'docs',
  lang: 'zh',                  // 默认语言 → 路由前缀被移除
  title: 'Scripts 文档',
  locales: [
    {
      lang: 'zh',
      label: '简体中文',
      title: 'Scripts 文档',
      description: '脚本工具集文档站',
    },
    {
      lang: 'en',
      label: 'English',
      title: 'Scripts Docs',
      description: 'Script utilities documentation',
    },
  ],
});
```

### `i18n.json`

```json
{
  "guide":     { "zh": "指南", "en": "Guide" },
  "gettingStarted": { "zh": "开始", "en": "Getting Started" }
}
```

### `docs/zh/_nav.json`

```json
[
  { "text": "guide", "link": "/guide/intro" }
]
```

### `docs/zh/index.md`

```yaml
---
pageType: home
hero:
  name: Scripts
  text: 脚本工具集
  tagline: 提升开发效率
  actions:
    - theme: brand
      text: 开始
      link: /guide/intro      # 默认语言，无 /zh 前缀
features:
  - title: 工具集
    details: Git / 构建 / 进程管理 / 通知
    icon: 🛠️
---
```

### `package.json` scripts

```json
{
  "scripts": {
    "dev": "rspress dev",
    "build": "rspress build",
    "preview": "rspress preview"
  },
  "devDependencies": {
    "@rspress/core": "^2.0.16"
  }
}
```

### 一键生成（推荐先跑脚手架验证）

```bash
npx -y create-rspress@latest scripts-docs --template i18n --tools rslint,prettier
cd scripts-docs && npm install && npm run dev
```

---

## Caveats / Not Found

- **`dir` (RTL) 字段**：`Locale` 接口不含 `dir`，方向由 `lang` 推断。如需强制 RTL，推测需走 `themeConfig` 自定义（未在 i18n 文档展开）。
- **`source.defaultLocale` 语法**：任务原文假设的 Docusaurus 风格 API 在 Rspress **不存在**；Rspress 用顶层 `lang`。
- **i18n 模板目录细节**：官方文档未贴出 `i18n` 模板生成的精确文件清单，但据 i18n.md 树形结构 + getting-started 描述可推断（见第 1/5 节）。
- **`@rspress/*` 包清单**：除 core/plugin-algolia/plugin-llms 外，还有 `@rspress/plugin-client-redirects`、`@rspress/plugin-sitemap` 等；本次只验证了三个常用包的版本号，其余可按需查 npm。
- 所有结论均附官方 URL + 文件:行号，无推测项。
