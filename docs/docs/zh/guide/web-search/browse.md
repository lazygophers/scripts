# browse

驱动浏览器扩展（开标签页 / 点击 / 填表 / 截图 / 看网络请求，支持并发批量）

## 用法

```text
browse — 用命令行驱动浏览器扩展

用法
  browse <module> <action> [位置参数...] [--参数 值...]
  browse run [--concurrency N] [--no-fail-fast] '<指令串>'... | browse run -
  browse status                                      一条命令看完整条链路（装没装、连没连）
  browse bridge start | stop | status | log          管理 bridge 进程
  browse stop                                       中止在途指令，bridge 留着
  browse audit [clear] [--limit N] [--table]        看或清空插件里的审计日志
  browse install | uninstall                        装 / 卸（扩展本体仍需你手动加载一次）

先跑起来
  browse install                                    第一次用：构建 + 注册 + 指引加载扩展
  browse status                                     看链路：daemon / 浏览器连接 / 注册三环
  browse browsingContext getTree --table            看浏览器连上没有、有哪些标签页
  browse browsingContext navigate https://example.com
  browse page snapshot --table                      列出这一页能点/能填的元素
  browse input type 'css=input[name=user]' 'myname'
  browse input click 'text=登录'
  browse script evaluate 'document.title'
  browse storage getCookies --domain example.com
  browse network subscribe --match-url '*/api/*' --duration 30s > api.jsonl
  browse run 'browsingContext.navigate https://a.com' 'browsingContext.navigate https://b.com'

选项（CLI 自己的，其余 --xxx 一律当指令参数发给浏览器）
  --table            结果用表格给人看（默认 stdout 出纯 JSON，可 | jq）
  --socket PATH      指定 daemon 的 socket 文件
  --concurrency N    run 的并发上限，默认 4
  --no-fail-fast     run 的每条各自独立，不因为前面失败就停，整体退出码 0
  --duration 30s     network subscribe 听多久，事件一行一个 JSON
  --limit N          audit 只取最近 N 条
  --browser NAME     发给哪个浏览器：chrome / brave / edge / ...
                     只有一个连着时不用写；多个连着又不写会报错并列出都有谁
  --debug / --no-say 仓库通用开关

参数怎么写
  --参数名按 kebab → camel 转成线上 key：--match-url 就是 matchUrl
  值先按 JSON 解、解不动当字符串：--index 3 是数字，--domain a.com 是字符串
  要强行传字符串形态的数字，把 JSON 引号带上：--text '"123"'
  定位器四种前缀：css= / text= / xpath= / js=，不写前缀默认 css=
  选哪个标签页：--context <id> > --match-url '<glob>' > 当前活动标签页
  选哪个浏览器：--browser <名字>（跟在 action 后面）；只有一个连着时可以不写

同时开着好几个浏览器
  `browse install` 默认给探测到的每个浏览器都注册，所以 Chrome 和 Brave 可以同时连着
  browse bridge status                             看现在连着谁
  browse browsingContext getTree --browser brave    指定发给谁
  装完或升级后**要重启浏览器**，它才会去读新的通信配置

确认与审计（都在插件里，不在这边）
  设置页：浏览器的扩展详情 →「扩展程序选项」，或点插件面板上的「设置」
  确认模式 silent 直接执行（默认） / per_domain 每个域名问一次 / always 每次都问
  要问的时候浏览器会弹一个小窗，不点就按拒绝算（退出码 4）
  拒绝名单里的域名一律拒绝，连窗都不弹
  这些设置和审计日志都存在插件的 chrome.storage.local 里 —— daemon 没起来也能改

退出码
  0 成功   1 指令失败   2 参数写错   3 浏览器未连接   4 用户拒绝确认

指令全集
  browse browsingContext getTree
  browse browsingContext create <url>
  browse browsingContext close
  browse browsingContext activate
  browse browsingContext navigate <url>
  browse browsingContext reload
  browse browsingContext captureScreenshot
  browse script evaluate <expression>
  browse script callFunction <functionDeclaration>
  browse input click <selector>
  browse input type <selector> <text>
  browse input key <key>
  browse input scroll
  browse storage getCookies
  browse storage setCookie <url> <name> <value>
  browse storage deleteCookies
  browse storage getLocalStorage <key>
  browse storage setLocalStorage <key> <value>
  browse network subscribe
  browse network unsubscribe <subscription>
  browse lg:history search <text>
  browse lg:history delete <url>
  browse lg:bookmarks search <query>
  browse lg:bookmarks create <url>
  browse lg:bookmarks remove <id>
  browse lg:downloads start <url>
  browse lg:downloads list
  browse lg:downloads cancel <id>
  browse lg:page snapshot
  browse audit
  browse audit clear
```

## 示例

```bash
browse browsingContext getTree --table
```
