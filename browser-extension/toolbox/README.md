# 浏览器工具箱

只作用于当前 Chromium 浏览器的小工具扩展：

- 把当前网页选区或正文转换为 Markdown，并复制到剪贴板（工具栏第一个工具）。
- 打开 Chrome 的 DNS 缓存页面，由用户手动点击 `Clear host cache`。

设置页（右键工具图标 → 选项）可开关导出内容里的标题行和来源链接。

扩展不修改系统代理或 DNS，不安装本机程序，也不上传网页内容。

## 构建

```bash
npm install
npm run test
npm run build
```

在 Chrome 打开 `chrome://extensions`，开启“开发者模式”，选择“加载已解压的扩展程序”，加载本目录的 `dist/`。

## 权限

- `activeTab`、`scripting`：用户点击导出按钮后读取当前网页。
- `clipboardWrite`：把 Markdown 写入剪贴板。
- `storage`：保存导出选项。

Chrome 内置页面不允许扩展读取，因此在 `chrome://` 页面导出会显示错误。DNS 按钮只负责打开 `chrome://net-internals/#dns`；Chrome 是否允许扩展直接打开该内置页面，需要在目标 Chrome 版本中人工核验。
