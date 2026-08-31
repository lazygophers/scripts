---
name: ovpn
description: 使用本仓库的 ovpn 脚本连接/断开 OpenVPN、管理分流规则（只让指定域名走 VPN）、查看 TOTP 验证码。当任务涉及连 VPN、查连接状态、配置 split tunnel 时使用。
---

# ovpn

通过 management interface 驱动 `openvpn`，自动填账号密码 + TOTP。自动 `brew install openvpn`。

```bash
ovpn connect        # 连接；断线自动重连，配置缺失自动 login，--no-split 跳过分流
ovpn disconnect     # 断开（需要 sudo）
ovpn status         # 查状态：进程 + utun 网卡（不读配置，免 sudo）
ovpn route add '*.example.com' 10.8.0.0/16  # 分流：只有指定域名/网段走 VPN
ovpn route          # 查看分流规则
ovpn login          # 交互式录入凭据
ovpn code           # 只打印当前 TOTP 码（root）
ovpn show           # 查看配置（密码打码）（root）
```

要点：

- 凭据在 `~/.config/lazygophers/scripts/ovpn.yaml`，root:0600；`connect`/`login`/`show`/`code`/`route` 自动经 `sudo` 重执行。
- 分流模式：`--route-nopull` + 本地 DNS 代理 `127.0.0.1:5354` + `/etc/resolver/<domain>` + host 路由；上游梯队严格有序（配置 dns_upstream → 服务端推送 DNS → resolv.conf → 兜底），绝不跨梯队竞速。
- 通用选项 `--no-say` / `--verbose`。
