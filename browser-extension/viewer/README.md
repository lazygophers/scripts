# viewer

把本地的纯文本文件就地渲染成好看的样子：markdown 排版、代码上色、json / yaml 折叠树、
csv 表格、日志分级上色、目录列表，另外接管页内查找。

## 打包

```bash
npm --prefix browser-extension/viewer install
npm --prefix browser-extension/viewer run package
```

产出两个安装包，都在 `browser-extension/viewer/dist-package/`：

- `viewer-<版本>-chrome.zip`，Chrome / Edge / Brave 用
- `viewer-<版本>-firefox.zip`，Firefox 用

两个包由同一份源码和同一份 `src/manifest.json` 生成，浏览器之间的差异在
`browser-extension/shared/manifest.mjs` 里吸收，没有第二份需要手工同步的清单。

打包前会自己验一遍：manifest 里点到名的文件必须真的在包里，manifest 必须是合法的
第 3 版，包里不许有 sourcemap。任何一条不过，这条命令直接失败，不会产出一个装进浏览器
才发现缺文件的包。

开发时用 `npm run build`（Chrome）、`npm run build:firefox`、`npm run watch`，
产物分别在 `dist/`、`dist-firefox/`，带 sourcemap。

## 安装（Chrome）

1. 解压 `viewer-<版本>-chrome.zip`，或者直接用 `dist/` 目录。
2. 地址栏输入 `chrome://extensions` 回车，右上角打开「开发者模式」。
3. 点「加载已解压的扩展程序」，选上一步那个目录。

## 必须打开「允许访问文件网址」

**不打开这一项，扩展读不到你电脑里的文件，装了也没用。** 浏览器不允许扩展自己打开它，
只能你去点：

1. `chrome://extensions` 里找到 viewer，点「详情」。
2. 把「允许访问文件网址」打开。

装好扩展时会自动弹一张页面把这几步再说一遍；已经打开过的人不会被打扰。
之后随时可以在扩展的「扩展程序选项」里看这个开关现在是开是关。

## 安装（Firefox）

Firefox 不接受未签名的扩展常驻，所以是「临时载入」，关掉浏览器就没了：

1. 地址栏输入 `about:debugging#/runtime/this-firefox` 回车。
2. 点「临时载入附加组件」，选 `dist-firefox/manifest.json`。

### Firefox 上还要改一次系统的文件类型表

Firefox 拿到 `text/markdown` 这种类型时不显示，而是弹出下载框；Chrome 会当纯文本显示。
这是 Firefox 自己的行为，扩展改不了（Mozilla 的缺陷单：
<https://bugzilla.mozilla.org/show_bug.cgi?id=1319262>）。要在 Firefox 里内联看 markdown，
得让系统把 `.md` 报成 `text/plain`：

1. 地址栏输入 `about:config`，搜 `helpers.private_mime_types_file`，
   它的值就是 Firefox 读的那张表的路径，默认是 `~/.mime.types`。
2. 编辑那个文件（没有就新建），加一行：

   ```text
   text/plain md markdown mdx
   ```

3. 完全退出 Firefox 再打开。

同一条路子对别的扩展名也管用，把扩展名接在那一行后面即可。做法出处：
Firefox 官方的文件类型说明 <https://support.mozilla.org/en-US/kb/Managing%20file%20types>，
以及同类扩展 markdown-viewer 的说明 `simov/markdown-viewer:firefox.md`。

## 主题

四套主题按「在什么介质上读」分，换的不只是颜色——字体、字号、行距、每行多少字一起换：

| 主题 | 长什么样 | 适合 |
|---|---|---|
| 纸 | 暖白底 + 宋体，行距 1.9，每行 68 字 | 白天读长文 |
| 夜 | 深墨底，无衬线，行距 1.8，每行 74 字 | 暗处；默认就是它 |
| 墨水屏 | 纯灰阶、高对比、字号 17.5px | 像电子书阅读器那样读 |
| 苔 | 低饱和绿灰，对比温和 | 久看不累 |

再加一套「自定义」：挑一套现成的当底子，只改你想改的颜色（8 项）和排版（字号 / 行距 /
每行字数）。只存改过的那几项，底子以后调了色，没动过的部分跟着一起更新。

两个地方能切：

- **文件页右上角的「主题」按钮**，点开就是这几套，选一个立刻生效。
- **扩展的「扩展程序选项」页**，那里还有一块实时预览和自定义的调色面板。

主题是全局的：在任意一页切一次，**其余已经开着的页面立刻跟着换**，不用刷新。

## 哪些文件会被浏览器直接下载

有几类文件浏览器判定为不能内联显示，直接存盘，页面根本不存在，扩展的内容脚本也就无从注入。
**Chrome + macOS 上实测只有三种**：

| 扩展名 | 为什么 |
| --- | --- |
| `.yaml` `.yml` | 系统报成 `application/x-yaml`，不在浏览器的可渲染名单里 |
| `.csv` | 在浏览器的 `kUnsupportedTextTypes` 拒绝名单里 |

其余常见的都会内联显示：`.md` 直接渲染；`.go` `.py` `.log` `.toml` `.conf` 这些系统 MIME 表里
没有条目的走内容嗅探变成 `text/plain`。出处：`chromium/chromium:net/base/mime_util.cc`、
`chromium/chromium:third_party/blink/common/mime_util/mime_util.cc`，决策见
`docs/adr/0003-viewer-download-types-opt-in.md`。

要看这三类，去扩展的选项页打开**强制拦截**：它们的整页导航会在发生之前被改跳到扩展自己的
展示页，看到的东西和直接打开一个文件完全一样，当前标签页和前进后退也都正常。关掉开关立刻
恢复原来的下载行为，不用重装扩展。

Linux 和 Windows 的系统 MIME 表没有实测过，上面这三种之外可能还有别的。想确认自己这台机器，
建一批空文件逐个打开，能看到内容的就是内联显示，弹出下载的就需要强制拦截：

```bash
mkdir -p /tmp/lfv-probe
for e in yaml yml toml ini conf log go py ts rs md csv json; do
  echo "probe $e" > "/tmp/lfv-probe/probe.$e"
done
open -a "Google Chrome" /tmp/lfv-probe/probe.yaml
```
