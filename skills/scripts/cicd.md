## cicd

查看、监听、启用 GitHub/GitLab CI/CD（支持双平台，按当前 git remote 自动识别）。

```bash
cicd                  # 等当前分支 CI/CD 跑完（流水线由 push 自动创建）
cicd now              # 看当前分支最新 CI/CD 状态
cicd play 90947       # 启用 GitLab manual job（只点已有流水线里的手动任务，不新建）
cicd id 123           # 等某个 run/pipeline 跑完
cicd fail 123         # 看失败日志；完整日志 cicd log 123
cicd status <branch>  # 查看某个分支的最新 CI/CD
```

要点：禁止手动触发流水线；`--project owner/repo` 操作别的项目。
