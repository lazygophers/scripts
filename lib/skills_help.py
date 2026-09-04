"""AI-facing command skills output."""

from __future__ import annotations

import os


COMMON_SKILLS = [
    "组参前先跑 `--help` 看人类可读用法。",
    "有 `--dry-run` 就先预览，不改状态。",
    "用 `--no-say` 或 `SCRIPTS_NO_SAY=1` 关语音通知。",
    "诊断时用 `--debug` 或 `SCRIPTS_DEBUG=1` 看成功命令的输出。",
]


COMMAND_SKILLS = {
    "_gitwf": [
        "merge_*/push_* symlink 的内部入口；优先用公开的 symlink 名调用。",
        "action 和目标分支由 argv[0] 的文件名解析。",
        "auto 模式：cwd 是 git 仓库走单仓 here，否则批量 all。",
    ],
    "archery": [
        "常规 SQL 查询走 `archery query execute 'select 1' --instance-name prod-mysql --db-name orders`；stdout 默认 TSV。",
        "大 SQL 文件走 `archery query execute @query.sql --instance-name prod-mysql --db-name orders --limit-num 100`。",
        "建 SQL 上线工单前先 `archery workflow check 5 orders @change.sql`。",
        "用 `archery workflow submit --data @workflow.json` 创建 SQL 上线工单。",
        "用 `archery workflow list --workflow__status waiting --size 20` 找待审工单，再 `archery workflow audit <engineer> <workflow-id> '看过了'` 或 `archery workflow execute <workflow-id> --engineer <engineer>`。",
        "凭据缺失或过期才 `archery login`；暴露密钥的命令（`show`、`code`）需要 root，可能经 sudo 重启自身。",
    ],
    "grafana": [
        "按 host profile 操作 Grafana HTTP API；stdout 是 JSON，可接 jq。",
        "凭据缺失或过期时先 `login` 再调 API 命令。",
    ],
    "check_ai": [
        "用最小请求探测配置的 AI API 端点连通性。",
        "跑 AI 工作流前用它区分端点/鉴权/网络故障。",
    ],
    "checkwork": [
        "对当前仓库或子仓库跑多语言编译/类型检查。",
        "commit/push 前跑它，拿到对齐 CI 基础构建的本地信心。",
    ],
    "cicd": [
        "用 `cicd` 等当前分支 CI/CD 跑完。",
        "用 `cicd now [branch]` 看当前分支最新 CI/CD 状态。",
        "用 `cicd run ci.yml [branch]` 触发 GitHub workflow；GitLab 用 `cicd run [branch]`。",
        "用 `cicd id <run-id-or-pipeline-id>` 等某个 run/pipeline 跑完。",
        "用 `cicd fail <run-id> [--job job-id]` 看失败日志；完整日志用 `cicd log <id>`。",
        "用 `--project owner/repo` 指定别的 GitHub/GitLab 项目；不指定则用当前 git remote。",
    ],
    "commit": [
        "对当前仓库或扫描到的子仓库做自动 git commit。",
        "本地检查通过且有 commit 授权后才用。",
    ],
    "cpd": [
        "把一个或多个源深度复制进目标，带更新/删除语义。",
        "最后一个参数当目标，之前的全是源。",
        "破坏性的 sync/delete 模式先跑 dry-run。",
    ],
    "delete_branch": [
        "在单仓或跨子仓库删本地分支。",
        "调用前确认当前分支和工作区干净。",
    ],
    "delete_branch_remote": [
        "在单仓或跨子仓库删远端分支。",
        "对外可见的操作：调用前确认 remote/branch 无误。",
    ],
    "disable-ipv6": [
        "用 networksetup 关闭本机所有 macOS 网络服务的 IPv6。",
        "需要 sudo/root；改主机网络配置。",
    ],
    "enable-ipv6": [
        "用 networksetup 恢复本机所有 macOS 网络服务的 IPv6 自动模式。",
        "需要 sudo/root；改主机网络配置。",
    ],
    "fetch_all": [
        "为发现的所有 git 仓库 fetch 远程更新。",
        "分支同步/合并/推送工作流前刷新 refs 用。",
    ],
    "inject": [
        "安装或展示本 scripts bin 目录的 shell PATH 注入。",
        "改 rc 前先 `show` 无写入地查看。",
    ],
    "ipinfo": [
        "展示本地网络/IP 信息和热点/VPN 线索。",
        "跑对连通性敏感的命令前用它诊断网络环境。",
    ],
    "issue": [
        "用本地上下文 + AI 生成标题/正文创建 issue。",
        "需要检测到的 gh/glab 提供方，并向外发布前确认。",
    ],
    "kk": [
        "按名字模式查找并终止进程。",
        "确认终止前先过一遍列出的进程。",
    ],
    "kkp": [
        "查找并终止监听某端口的进程。",
        "释放本地开发端口用；先看进程列表。",
    ],
    "lazyhelp": [
        "人类可读命令目录；默认输出命令清单和用户描述。",
        "用 `lazyhelp help <tool>` 看那个工具的人类帮助。",
        "用目标工具 `--skills` 看 AI 向命令指引。",
    ],
    "list_branch": [
        "跨仓库列出本地分支。",
        "批量分支清理或同步前用。",
    ],
    "loop": [
        "重复执行命令直到成功、永久或强制次数。",
        "全局 flag 放在被包裹命令前面，避免吞掉命令自己的 flag。",
    ],
    "merge_canary": ["用安全 git 工作流检查把当前分支合入 canary。"],
    "merge_dev": ["用安全 git 工作流检查把当前分支合入 dev。"],
    "merge_develop": ["用安全 git 工作流检查把当前分支合入 develop。"],
    "merge_master": ["用安全 git 工作流检查把当前分支合入自动识别的默认分支。"],
    "merge_test": ["用安全 git 工作流检查把当前分支合入 test。"],
    "mr": [
        "用本地 diff 上下文 + AI 生成标题/正文创建 PR/MR。",
        "需要检测到的 gh/glab 提供方，并向外发布前确认。",
    ],
    "n": [
        "用 macOS `say` 播报短语音通知。",
        "拒绝危险 shell 字符和超长内容。",
    ],
    "ovpn": [
        "OpenVPN 连接/断开/状态/登录，凭据和 TOTP 已存好。",
        "碰配置的命令需要 root，可能经 sudo 重启自身。",
        "无特权查看用 `status`。",
    ],
    "push_branch": [
        "跨仓库把当前分支推到远端同名分支。",
        "本地检查通过后用；这会把 commit 发布到远端。",
    ],
    "push_canary": ["用安全 git 工作流检查把当前分支推入 canary。"],
    "push_dev": ["用安全 git 工作流检查把当前分支推入 dev。"],
    "push_develop": ["用安全 git 工作流检查把当前分支推入 develop。"],
    "push_master": ["用安全 git 工作流检查把当前分支推入自动识别的默认分支。"],
    "push_test": ["用安全 git 工作流检查把当前分支推入 test。"],
    "squash_pr": [
        "把 source 分支改动压成单个 commit，创建或更新 PR。",
        "可能 force-push PR 分支；用前确认分支影响面。",
    ],
    "switch_branch": [
        "把所有仓库切到目标分支，不存在则创建并跟踪。",
        "用远端默认分支探测，不硬编码 master/main。",
    ],
    "sync_branch": [
        "把各仓库同步到 origin/<branch>。",
        "批量开工前让所有仓库停在同一分支时用。",
    ],
    "sync_master": [
        "把各仓库同步到各自识别出的远端默认分支。",
        "开 feature 分支前刷新默认分支用。",
    ],
    "unsleep": [
        "永久、按时长或跟随命令阻止 macOS 休眠。",
        "用 `with_command` 只在被包裹命令运行期间保持唤醒。",
    ],
    "vpn-prio": [
        "调 macOS 网络服务优先级，缓解 VPN 默认路由问题。",
        "OpenVPN 路由干扰正常上网时用。",
    ],
}


def command_name(argv0: str) -> str:
    return os.path.basename(argv0) if argv0 else "script"


def render_skills(name: str, description: str = "") -> str:
    """Return AI-facing guidance for one command."""
    skills = COMMAND_SKILLS.get(name, [])
    if not skills and description:
        skills = [description.strip()]
    lines = [f"# {name} skills", "", "受众：使用本命令的 AI agent。"]
    if description.strip():
        lines.extend(["", "概述：", description.strip()])
    lines.extend(["", "何时用："])
    if skills:
        lines.extend(f"- {skill}" for skill in skills)
    else:
        lines.append("- 需要本命令的文档化行为；人类用法细节看 `--help`。")
    lines.extend(["", "通用约定："])
    lines.extend(f"- {skill}" for skill in COMMON_SKILLS)
    return "\n".join(lines) + "\n"


def consume_skills(argv: list[str], description: str = "") -> list[str]:
    """Print AI-facing command skills for --skills and exit 0."""
    if "--skills" not in argv[1:]:
        return argv
    print(render_skills(command_name(argv[0]), description))
    raise SystemExit(0)
