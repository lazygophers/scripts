import * as fs from 'node:fs';
import * as path from 'node:path';
import { defineConfig } from '@rspress/core';

/**
 * 侧边栏里那个目录叫什么名字。取自该目录所在层的 `_meta.json`，读不到就用目录名本身。
 *
 * llms.txt 默认按导航栏分组，而导航栏只有两条，于是四十来页全挤进「指南」一段。
 * 文档按目录分好的组本来就写在 `_meta.json` 里，这里拿来当分组标题。
 */
function dirLabel(lang: string, parent: string, dir: string): string {
  const meta = path.join(__dirname, 'docs', lang, parent, '_meta.json');
  if (!fs.existsSync(meta)) return dir;
  const entries = JSON.parse(fs.readFileSync(meta, 'utf8')) as {
    name?: string;
    label?: string;
  }[];
  return entries.find(entry => entry.name === dir)?.label ?? dir;
}

export default defineConfig({
  root: path.join(__dirname, 'docs'),
  lang: 'zh',
  base: '/scripts/',
  title: 'Scripts',
  // 给大模型读的那一份文档：构建时额外产出 llms.txt（目录索引）、llms-full.txt（全文）
  // 以及每个页面的 .md 版本，多语言各出一份。规范见 https://llmstxt.org/ 。
  // 索引按侧边栏的目录分组，而不是按导航栏那两条，见下面的 `dirLabel`。
  llms: {
    llmsTxt: ({ title, description, lang, sections }) => {
      const groups = new Map<string, string[]>();
      for (const section of sections) {
        for (const page of section.pages) {
          // `/zh/guide/git-workflow/merge_branch` -> 分到 `guide` 下的 `git-workflow` 那一组。
          // 直接躺在 `guide/` 下的页面没有这一层目录，仍归导航栏那一段。
          const parts = page.routePath.replace(/^\//, '').split('/');
          const from = parts[0] === lang ? 1 : 0;
          const parent = parts[from];
          const dir = parts[from + 1];
          const label =
            parent !== undefined && dir !== undefined && parts.length > from + 2
              ? dirLabel(lang, parent, dir)
              : section.title;
          const line = `- [${page.title}](${page.link})${
            page.description ? `: ${page.description}` : ''
          }`;
          groups.set(label, [...(groups.get(label) ?? []), line]);
        }
      }

      const body = [...groups]
        .map(([label, lines]) => `## ${label}\n\n${lines.join('\n')}`)
        .join('\n\n');
      // 站点级 description 只写在各语言的 locales 里，顶层没有；没有就不摆那一行。
      const summary = description === undefined ? '' : `> ${description}\n\n`;
      return `# ${title}\n\n${summary}${body}\n`;
    },
  },
  locales: [
    {
      lang: 'zh',
      label: '简体中文',
      title: 'Scripts 文档',
      description: '开发效率工具集文档',
    },
    {
      lang: 'en',
      label: 'English',
      title: 'Scripts Docs',
      description: 'Development efficiency script utilities',
    },
    {
      lang: 'fr',
      label: 'Français',
      title: 'Scripts (FR)',
      description: 'Scripts d\'outils d\'efficacité de développement',
    },
    {
      lang: 'es',
      label: 'Español',
      title: 'Scripts (ES)',
      description: 'Scripts de utilidades de eficiencia de desarrollo',
    },
    {
      lang: 'ru',
      label: 'Русский',
      title: 'Scripts (RU)',
      description: 'Скрипты для повышения эффективности разработки',
    },
    {
      lang: 'ar',
      label: 'العربية',
      title: 'Scripts (AR)',
      description: 'نص برمجي لأدوات كفاءة التطوير',
    },
  ],
});
