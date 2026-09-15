# ovpn

OpenVPN 客户端（自动填账号密码与二步验证码，支持分流）

## 用法

用法：`ovpn COMMAND | <flags> [NAMES]...`

**命令**

| 命令 | 说明 |
| :--- | :--- |
| `code` | 只打印当前二步验证码（不连 VPN），用于验证密钥填对没有 |
| `connect` | 连接 VPN；断线自动重连，配置缺失自动 login，openvpn 缺失自动 brew 安装 |
| `disconnect` | 断开正在运行的 openvpn 连接（需要 sudo） |
| `login` | 交互式录入凭据，写入 ~/.config/lazygophers/scripts/ovpn.yaml |
| `route` | 管理分流规则：只让指定域名 / 网段走 VPN，其余流量走本地网络 |
| `show` | 查看当前配置（密码与二步验证密钥打码） |
| `status` | 查看连接状态：openvpn 进程 + 已配置 IP 的 utun 网卡 |

**参数 / 选项**

| 参数 | 类型 | 默认值 |
| :--- | :--- | :--- |
| `NAMES` | 'str' |  |
| `-v, --verbose=VERBOSE` | 'bool' | False |
| `--reconnect=RECONNECT` | 'bool' | True |
| `--reconnect_max=RECONNECT_MAX` | 'int' | 0 |
| `-s, --split=SPLIT` | 'bool' | True |

## 示例

```bash
ovpn connect
```
