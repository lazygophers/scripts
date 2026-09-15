# graphwatch

graphify 全局 watch 守护服务：注册目录自动重建知识图谱

## 用法

用法：`graphwatch COMMAND`

**命令**

| 命令 | 说明 |
| :--- | :--- |
| `add` | 注册一个文件夹，daemon 会自动监听（热加载）。 |
| `config` | 引导式配置向导（backend/api_key/base_url/model/debounce）。 |
| `install` | 注册为用户级系统服务并立即启动（登录自启，无需 root）。 |
| `list` | 列出已注册的文件夹。 |
| `remove` | 注销一个文件夹，daemon 自动停监（热加载）。 |
| `restart` | 重启服务（重读配置，热加载之外的全量刷新）。 |
| `run` | 前台运行守护进程（全局单例，Ctrl-C 退出）。 |
| `start` | 启动已注册的服务（等价于 launchctl load / systemctl start）。 |
| `status` | 查看服务执行状态、各目录图谱新鲜度、最近日志。 |
| `stop` | 停止服务进程（注册保留，下次 start 或重启电脑恢复）。 |
| `uninstall` | 停止并删除服务注册（配置与日志保留）。 |

## 示例

```bash
graphwatch add <dir>
```
