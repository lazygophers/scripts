Archery 的命令行客户端。配置 `~/.config/lazygophers/scripts/archery.yaml`（0600），**按域名**存多站点登录；子命令都接受 `--host <domain>` 指定站点。

```bash
archery hosts                      # 列已配置站点，★ 为默认
archery login                      # 录入站点凭据
archery info                       # 站点信息 + 验证 token
archery instance ...               # 数据库实例管理
archery query ...                  # SQLQuery：在线查询与历史
archery workflow ...               # SQL 上线工单
archery user ...                   # 用户管理（group/resourcegroup/twofa）
archery schema                     # 列站点 /api/schema/ 全部端点
archery api <method> <path> [--data JSON|@file]  # 直接调任意端点
```

要点：

- 结果走 **stdout（JSON，pipe 给 jq）**；进度/错误走 stderr。
- 401 自动 refresh token，失败回落密码重登，token 写回配置。
- `show` 与 `code`（TOTP）打印敏感信息，自动经 `sudo` 重执行（root-gated）。
- sudo 下 `$HOME` 变 `/var/root`，路径经 `SUDO_USER` 解析——脚本自己处理，无需干预。
