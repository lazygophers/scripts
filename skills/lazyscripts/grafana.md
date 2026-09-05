## grafana

Grafana HTTP API 命令行客户端。配置按域名存多站点（`~/.config/lazygophers/scripts/grafana.yaml`），子命令都接受 `--host <domain>`。

```bash
grafana hosts                     # 列已配置站点
grafana login                     # 录入站点凭据
grafana health                    # 健康状态
grafana search 'nginx'            # 搜索仪表盘
grafana api GET /api/dashboards/uid/xxx  # 直接调任意 Grafana API
```

要点：结果走 stdout（JSON，pipe 给 jq）；凭据过期先 `grafana login`。
