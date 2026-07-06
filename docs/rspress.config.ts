import * as path from 'node:path';
import { defineConfig } from '@rspress/core';

export default defineConfig({
  root: path.join(__dirname, 'docs'),
  lang: 'zh',
  base: '/scripts/',
  title: 'Scripts',
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
