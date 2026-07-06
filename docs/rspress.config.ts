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
  ],
});
