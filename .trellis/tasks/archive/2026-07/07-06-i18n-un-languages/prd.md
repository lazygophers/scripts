# PRD：多语言支持（UN 6 官方语言）

## 背景

仓库文档现状仅中英两语（`docs/docs/zh/` + `docs/docs/en/`，README.md 单中文）。用户要求支持**联合国 6 种官方工作语言**：中文 / English / Français / Español / Русский / العربية。

## 目标

README + docs 全套扩展到 6 语，rspress config locales 配齐，语言切换可用。

## 6 语言清单

| lang | label | title | description |
|---|---|---|---|
| zh | 简体中文 | Scripts 文档 | 开发效率工具集文档 |
| en | English | Scripts Docs | Development efficiency script utilities |
| fr | Français | Scripts (FR) | Scripts d'outils d'efficacité de développement |
| es | Español | Scripts (ES) | Scripts de utilidades de eficiencia de desarrollo |
| ru | Русский | Scripts (RU) | Скрипты для повышения эффективности разработки |
| ar | العربية | Scripts (AR) | نص برمجي لأدوات كفاءة التطوير |

## 范围

### 1. rspress config（`docs/rspress.config.ts`）

现有 2 locales → 扩 6 locales（上表）。`lang` 默认仍 `zh`。RTL（ar）rspress 自动处理（lang=ar 触发 dir=rtl）。

### 2. docs 目录结构

每语完整镜像现有 zh/en 结构：

```
docs/docs/<lang>/
  _nav.json              # 顶栏（语言切换 / 主导航）
  index.md               # 首页
  guide/_meta.json
  guide/introduction.md
  guide/scripts.md
  dev-guide/_meta.json
  dev-guide/structure.md
  dev-guide/testing.md
  dev-guide/contributing.md
  dev-guide/add-script.md
```

× 6 语 = 60 文件（zh/en 已存 16，新增 fr/es/ru/ar 各 8 = 32 新文件）。

`_nav.json` 含语言切换链接（rspress 内置 locale switcher，_nav 主要放功能导航，但每语都要有）。

### 3. README 多语言

- `README.md`（中文，主入口，顶部加 6 语切换链接）
- `README.en.md` / `README.fr.md` / `README.es.md` / `README.ru.md` / `README.ar.md`

每文件顶部：
```markdown
[简体中文](README.md) | [English](README.en.md) | [Français](README.fr.md) | [Español](README.es.md) | [Русский](README.ru.md) | [العربية](README.ar.md)
```

### 4. 翻译执行

**用 haiku 模型**（trellis-implement 派单时 `model: "haiku"`）。AI 直译，不需人工占位。术语统一（脚本名 `checkwork`/`gitc`/`cpd` 等保持原文不译）。

## 改动范围

- `docs/rspress.config.ts`：locales 2→6
- `docs/docs/fr/` `es/` `ru/` `ar/`：各 8 文件（新建）
- `docs/docs/zh/_nav.json` `en/_nav.json`：确认含 locale switcher（若无补）
- `README.md`：顶部加语言切换链接
- `README.en.md`（现若有则改，无则新建）+ `README.fr.md` `README.es.md` `README.ru.md` `README.ar.md`

## 验收标准

- [ ] `docs/rspress.config.ts` locales 含 6 语（zh/en/fr/es/ru/ar），各带 label/title/description
- [ ] `docs/docs/{fr,es,ru,ar}/` 各含 8 文件（index + _nav + guide/{_meta,introduction,scripts} + dev-guide/{_meta,structure,testing,contributing,add-script}）
- [ ] 内容是实际翻译（非占位 / 非复制中英）
- [ ] README 6 文件全在，顶部语言切换链接 6 条
- [ ] `npm run build`（docs/ 下）成功，6 语 locale switcher 可用
- [ ] 脚本名（checkwork/gitc/cpd/kk/kkp/n/loop 等）跨语保持原文
- [ ] ar 语 RTL 正常（rspress lang=ar 自动）

## 实现策略

派 1 个 trellis-implement（haiku 模型）做全部 4 语新译（fr/es/ru/ar），zh/en 已存在只需补 locale switcher / README 顶部链接。

若 haiku 译质量不足或 token 超限 → 拆 4 subagent 各一语（仍 haiku），并行。

## 依赖

无上游依赖。GitHub Pages action 已存在（`deploy-docs.yml`），本 task 合并后 push 自动触发部署。

## 待确认

- [x] 翻译方式：AI 直译（haiku 模型）
- [x] 范围：全部 6 语（README + docs index + guide + dev-guide）
- [x] ar RTL：靠 rspress 内置（lang=ar）

## 调研点（已闭合）

- 现有 docs 结构：zh/en 各 8 文件（index + _nav + guide×3 + dev-guide×5 中的 _meta + 4 md），见 `find docs/docs -type f`
- rspress config：`docs/rspress.config.ts` 现 2 locales
- GitHub Pages workflow：已存在 `.github/workflows/deploy-docs.yml`，无需新建
