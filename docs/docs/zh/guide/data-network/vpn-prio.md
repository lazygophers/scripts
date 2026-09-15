# vpn-prio

调整 macOS 网络服务优先级（压低 OpenVPN default 路由）

## 用法

用法：`vpn-prio COMMAND | -`

**命令**

| 命令 | 说明 |
| :--- | :--- |
| `apply` | 按目标顺序重排 Service Order（USB > Wi-Fi > 其他） |
| `reset` | 还原为字母顺序（系统默认顺序） |
| `status` | 显示当前 Service Order + OpenVPN 状态 + bridge100 路由（默认入口） |

## 示例

```bash
vpn-prio status
```
