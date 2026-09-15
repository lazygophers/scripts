# webgrab

抓网页转 Markdown（反爬直抓 + Playwright 渲染 + 34 站点适配 + 登录态持久化）

## 用法

用法：`webgrab [选项] <url>`

**选项**

| 选项 | 说明 |
| :--- | :--- |
| `-h, --help` | 显示帮助并退出 |
| `--dry-run` | 预览模式，不执行实际操作 |
| `--no-say` | 禁用语音通知 |
| `--debug` | 打印成功命令的输出 |
| `--skills` | 显示给 AI 看的命令能力说明并退出 |

## 示例

```bash
webgrab https://example.com
```
