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

## 哪些文件会被浏览器直接下载

有几类文件浏览器根本不显示，直接下载，扩展的内容脚本连页面都没有，所以帮不上忙。
这时去扩展的选项页打开**强制拦截**：这类本地文件的下载会被取消，改在扩展自己的展示页里
打开，看到的东西和直接打开一个文件完全一样。关掉开关立刻恢复原来的下载行为。

判断哪些类型会被下载不靠猜：浏览器自己发出下载这个动作，就是它在说「这个我不显示」，
扩展据此接管。所以清单只影响你要不要打开这个开关，不影响功能是否正确。

Chrome 决定一个本地文件的类型时，先查它自己写死的一张表，查不到再问操作系统
（`chromium/chromium:net/base/mime_util.cc`）。写死的那张表里，下面这些一定会内联显示：

`.txt` `.text` `.html` `.htm` `.css` `.js` `.mjs` `.xml` `.csv` `.md` `.sh` `.json`

其余扩展名（`.yaml` `.yml` `.toml` `.ini` `.conf` `.log` `.go` `.py` `.ts` `.rs`
`.java` `.rb` `.php` `.sql` `.scss` 等）交给操作系统回答，答案因机器上装了什么软件而异
——比如装过某些 markdown 编辑器之后，`.md` 也会开始被下载。

**要确认你这台机器上的实际情况**，开一个目录放几个空文件，逐个在浏览器里打开，
能看到内容的就是内联显示，弹出下载的就需要强制拦截：

```bash
mkdir -p /tmp/lfv-probe
for e in yaml yml toml ini conf log go py ts rs md csv json; do
  echo "probe $e" > "/tmp/lfv-probe/probe.$e"
done
open -a "Google Chrome" /tmp/lfv-probe/probe.yaml
```
