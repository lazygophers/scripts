# 会被下载的文件类型默认不接管，强制拦截做成开关

浏览器对少数文件类型判定不可内联、直接存盘，此时页面根本不存在，content script 无从注入。实测（Chrome + macOS）只有 `.yaml`/`.yml`（`application/x-yaml`，不在 Blink 可渲染名单）和 `.csv`（在 `kUnsupportedTextTypes` 拒绝名单）落在这一类；`.md` 会内联渲染，`.go`/`.log`/`.toml` 等系统 MIME 表里没有条目的走嗅探变 `text/plain`，同样内联。出处：`chromium/net/base/mime_util.cc`、`third_party/blink/common/mime_util/mime_util.cc`。

唯一的解法是 `declarativeNetRequest`，在导航发生前把这类 URL 改跳到扩展自己的页面（MV3 普通扩展没有 blocking `webRequest`）。探针实测确认可行：一条 `regexFilter` 规则成功把 `file:///….yaml` 的主框架导航重定向到扩展页。但这条能力改变的是浏览器的下载行为本身——用户点了一个 yaml 链接，本来会拿到文件，开了之后只会看到一个渲染页面。这是一次**行为替换**而非增强，不该默认发生。因此默认不管已被下载的类型，强制拦截做成用户自己开的开关。

## Consequences

`declarativeNetRequest` 权限要常驻声明，即使开关关着。规则最终用动态规则（`updateDynamicRules`）而不是静态规则集：重定向地址要带上原文件地址（`regexSubstitution` 的 `\0`），前缀含扩展 id，静态文件里写死它就得连带把 id 钉死。动态规则存在浏览器里，重启仍在，因此关掉开关时按同一批 id 删干净才算真的恢复下载行为。Firefox 从 113 起支持 dNR，能力对齐；但 Firefox 对 `text/markdown` 反而弹下载框，扩展侧绕不过去，只能在文档里教用户改系统 mime 表（`simov/markdown-viewer:firefox.md` 是同样的处理）。Linux / Windows 的系统 MIME 表未实测，可能有更多类型落进这一类。
