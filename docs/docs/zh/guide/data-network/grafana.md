# grafana

Grafana HTTP API 命令行客户端（按域名分别登录）

## 用法

```text
─────────────────────────────────── grafana ────────────────────────────────────
→ Grafana HTTP API 客户端
→ 用法: grafana <command> [flags]
                常用                 
╭────────┬──────────────────────────╮
│ login  │ 录入一个 Grafana 站点    │
│ hosts  │ 列出已配置站点           │
│ health │ 查看 Grafana 健康状态    │
│ search │ 搜索仪表盘               │
│ api    │ 直接调用任意 Grafana API │
╰────────┴──────────────────────────╯
→ 提示: 裸跑 `grafana` 会显示 `--skills`。
```

## 示例

```bash
grafana health
```
