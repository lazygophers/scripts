# browse

先运行 `browse install`，再用 `browse status` 确认 bridge 和浏览器扩展都已连接。

`browse` 是命令行工具：它通过本机 bridge（中间通信服务）调用浏览器扩展。

## 用法

```text
browse <动词> <参数...>                         常用平铺命令
browse <名词组> <动词> <参数...>                 分组命令
browse api <module> <action> <参数...>           底层透传
browse run '<指令串>'... | browse run -           并发批量
browse status                                   查看连接链路
browse bridge start|stop|status|log              管理 bridge
browse stop                                     中止在途指令，不停止 bridge
browse audit [clear] [--limit N]                 查看或清空扩展审计日志
browse install | uninstall                       注册或移除扩展通信配置
```

## 先跑起来

```bash
browse install
browse status
browse open https://example.com
browse list
browse click '登录'
browse fill 'css=input[name=user]' 'myname'
browse snapshot
browse text
browse screenshot
```

`browse open` 默认把新标签页放入 `browse/default` 分组。用 `--group 调研` 指定分组，`--no-group` 禁止分组。

## 常用命令

```text
browse open <url>                              新建标签页
browse close [网址通配]                         关闭当前页或匹配到的全部标签页
browse goto <url>                              当前标签页跳转；别名 navigate
browse list                                    列出标签页
browse click <目标>                            点击元素；默认按可见文字匹配
browse fill <目标> <文字>                       填写输入框
browse screenshot [文件]                       截图；默认保存到 ~/Downloads
browse snapshot                                查看当前页可操作元素
browse text                                    输出正文纯文字
browse html                                    输出页面源码
browse eval <js>                               执行页面 JavaScript；别名 script
browse wait <条件>                             等待元素、文字、网址或网络空闲
browse back                                    后退
browse forward                                 前进
browse reload                                  刷新
browse activate                                激活标签页
```

## 分组命令

```text
browse tab save [文件]                         保存 MHTML 页面存档
browse tab list | tab close                    list / close 的分组写法
browse page key <键>                           按键
browse page scroll                             滚动；--x / --y / --selector
browse group list                              列出标签分组
browse group add <名字>                        当前页加入分组
browse group rename <旧名> <新名>               重命名分组
browse group color <名字> <颜色>                设置分组颜色
browse group dissolve <名字>                   解散分组，保留标签页
browse data cookies                            查看 cookie
browse data cookie-set <url> <名> <值>           写入 cookie
browse data cookie-del                         删除 cookie
browse data local-get <键> | local-set <键> <值> 读写本地存储
browse data history <文字>                      搜索历史记录
browse data bookmarks <关键词>                  搜索书签
browse data downloads                          查看下载
browse data download <url>                      开始下载
browse data reading-list                       查看阅读清单
browse data top-sites                          查看常用网站
browse net watch                               监听网络事件，输出 JSONL
browse net unwatch <订阅号>                     停止监听
browse net dns <主机名>                         查询 DNS
browse net proxy                               查看代理
browse net proxy-set <模式>                     设置代理
browse net proxy-clear                          清除代理
browse sys info | processes | idle              查看浏览器系统信息
browse sys notify <标题> <正文>                 显示通知
browse sys awake | awake-off                   防止或停止防休眠
browse sys clipboard | clipboard-set <文字>     读写剪贴板
browse sys search <文字>                       搜索
browse sys perms                               查看权限
browse rec tab | desktop                       开始录制
browse rec stop <录制号>                       停止录制
```

## 底层透传

```bash
browse api browsingContext getTree
browse api gcm token <entity>
browse run 'api browsingContext navigate https://a.com'
```

`browse api` 的参数形状直接跟随扩展 handler，不做兼容包装；扩展改变参数时，`browse api` 同步改变。常用层命令名和参数可以为了手感调整。

## 目标和选项

目标解析按以下顺序取第一个命中项：

```text
--context <id> > --url '<glob>' > --group <名字> > 当前活动页（在 browse/ 分组内） > 当前活动页
```

`--url` 或 `--group` 找不到目标时直接报错，不回退到其他目标。读写操作匹配多个标签页时报错；`close` 匹配多个时全部关闭。

```text
--context <id>          指定标签页
--url '<glob>'          按网址匹配标签页
--group <名字>          按分组选择标签页
--browser <名字>        指定浏览器
--table / --json        强制表格或 JSON
--timeout 10s           wait 超时，默认 30s
--socket PATH           指定 bridge socket
--debug / --no-say      仓库通用开关
```

参数名按 kebab-case 转成 camelCase：`--match-url` 变成 `matchUrl`。值先按 JSON 解析；解析失败时保留字符串。

定位符前缀：`text=`、`css=`、`xpath=`、`js=`。`click` 和 `fill` 不写前缀时默认使用 `text=`。

终端输出默认表格，管道或重定向默认 JSON。`net watch` 和 `audit` 始终输出 JSONL。

## 连接与审计

```bash
browse status
browse bridge log
browse audit
browse audit clear
browse stop
browse bridge stop
```

确认策略和审计日志都在浏览器扩展的 `chrome.storage.local` 中。扩展设置页可配置 `silent`、`per_domain`、`always` 三种确认模式。

安装或升级扩展后，需要在 Chrome 的扩展管理页面手动重新加载解压扩展；Chrome 不允许命令行自动加载扩展。

## 退出码

```text
0  成功
1  指令失败或等待超时
2  参数错误或目标有歧义
3  浏览器未连接
4  用户拒绝确认
```
