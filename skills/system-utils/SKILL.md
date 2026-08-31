---
name: system-utils
description: 使用本仓库的系统/文件工具：kk（按名杀进程）、kkp（按端口杀进程）、cpd（深度覆盖复制）、loop（循环执行命令）、unsleep（防休眠）、n（语音播报）、ipinfo（内网 IP）、inject（注入 PATH）、enable/disable-ipv6。当任务涉及终止进程、目录同步复制、命令循环重试时使用。
---

# system-utils

```bash
kk <pattern>          # 按进程名（正则）终止进程，先列进程再确认
kkp <port>            # 按端口终止进程
cpd <src...> <dest>   # 深度覆盖复制（只新增/更新；-f 删目标多余文件；--dry-run 预览；默认 md5 校验，CPD_VERIFY_MD5=0 关闭）
loop <n> <cmd...>     # 循环执行命令追踪成功/失败；loop force 失败也继续；loop infinite 无限；--timeout <s>
unsleep -t 3600       # caffeinate 防休眠；forever 无限制
n "文案"              # macOS 语音播报（say）
ipinfo                # 内网 IP + 网络类型（lan / net / all）
inject                # 把 bin/ 注入 shell PATH（幂等；--uninstall 卸载）
enable-ipv6 / disable-ipv6
```

要点：

- `kk`/`kkp` 自带自排除（不会杀自己/父进程），杀前显示进程表并要求确认。
- `cpd`：shell 展开 `src/*` 不含 dotfile，脚本已处理；输出只显示相对目标的路径。
- 所有 `bin/*` 支持 `--no-say`（静音语音）与 `--debug`。
