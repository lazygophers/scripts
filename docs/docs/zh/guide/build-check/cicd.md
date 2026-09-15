# cicd

轮询当前分支 CI/CD，完成后输出最终结果

## 用法

用法：`cicd COMMAND | <flags>`

**命令**

| 命令 | 说明 |
| :--- | :--- |
| `fail` | 看失败日志（短命令）。 |
| `id` | 监听某个 CI/CD ID（短命令）。 |
| `log` | 看日志（默认不过滤失败）。 |
| `logs` | 查看某个 CI/CD 的日志/错误输出 |
| `now` | 查看当前分支的最新 CI/CD（短命令）。 |
| `play` | 启用 manual job（只点掉已有流水线里的手动任务，不新建流水线）。 |
| `status` | 查看某个分支的最新 CI/CD |
| `watch` | 监听分支或某个 CI/CD，完成后输出最终结果 |

**参数 / 选项**

| 参数 | 类型 | 默认值 |
| :--- | :--- | :--- |
| `-r, --ref=REF` | Optional['str \| None'] | None |
| `-p, --project=PROJECT` | 'str' | '' |
| `--min_interval=MIN_INTERVAL` | 'float' | 5.0 |
| `--max_interval=MAX_INTERVAL` | 'float' | 30.0 |
| `-t, --timeout=TIMEOUT` | Optional['float \| None'] | None |
| `-v, --verbose=VERBOSE` | 'bool' | False |

**常用**

```bash
cicd                  # 等当前分支 CI/CD 跑完（流水线由 push 自动创建）
cicd now              # 看当前分支最新状态
cicd play 90947       # 启用 GitLab manual job
cicd id 123           # 等某个 run/pipeline 跑完
cicd fail 123         # 看失败日志
```

## 示例

```bash
cicd
```
