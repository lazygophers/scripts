"""AI-facing command skills output。

每条 skills 都带可直接照抄运行的命令示例（示例均已对过各 bin 的 --help）。
"""

from __future__ import annotations

import os

COMMON_SKILLS = [
    "组参前先跑 `--help` 看人类可读用法。",
    "有 `--dry-run` 就先预览，不改状态。",
    "用 `--no-say` 或 `SCRIPTS_NO_SAY=1` 关语音通知。",
    "诊断时用 `--debug` 或 `SCRIPTS_DEBUG=1` 看成功命令的输出。",
]

# 示例命令是 AI 可直接照抄跑的；子命令名与各 bin 的 --help 对齐
COMMAND_SKILLS: dict[str, list[str]] = {
    "_gitwf": [
        "merge_*/push_* symlink 的内部入口；优先用公开的 symlink 名调用。",
        "action 和目标分支由 argv[0] 的文件名解析。",
        "auto 模式：cwd 是 git 仓库走单仓 here，否则批量 all。例: `merge_master auto`。",
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
        "健康检查: `grafana health --host grafana.example.com`。",
        "搜仪表盘: `grafana search 'nginx' --host grafana.example.com`。",
        "任意 API: `grafana api GET /api/dashboards/uid/xxx --host grafana.example.com`。",
        "凭据缺失或过期时先 `grafana login` 再调 API 命令；`grafana hosts` 列已配置站点。",
    ],
    "check_ai": [
        "用最小请求探测配置的 AI API 端点连通性: `check_ai probe`。",
        "跑 AI 工作流前用它区分端点/鉴权/网络故障。",
    ],
    "checkwork": [
        "对当前仓库或子仓库跑多语言编译/类型检查: `checkwork run`。",
        "commit/push 前跑它，拿到对齐 CI 基础构建的本地信心。",
    ],
    "cicd": [
        "用 `cicd` 等当前分支 CI/CD 跑完。",
        "用 `cicd now [branch]` 看当前分支最新 CI/CD 状态。",
        "流水线由 push 自动创建，禁止手动触发；只可用 `cicd play <job-id>` 启用 manual job。",
        "用 `cicd id <run-id-or-pipeline-id>` 等某个 run/pipeline 跑完。",
        "用 `cicd fail <run-id> [--job job-id]` 看失败日志；完整日志用 `cicd log <id>`。",
        "用 `--project owner/repo` 指定别的 GitHub/GitLab 项目；不指定则用当前 git remote。",
    ],
    "commit": [
        "当前仓库提交: `commit here`；批量扫描子目录: `commit all`；自动判断: `commit auto`。",
        "本地检查通过且有 commit 授权后才用。",
    ],
    "cpd": [
        "把一个或多个源深度复制进目标，带更新/删除语义；最后一个参数是目标。",
        "先预览: `cpd --dry-run src/ dest/`，确认后去掉 --dry-run。",
        "破坏性的 sync/delete 模式必须先跑 dry-run。",
    ],
    "delete_branch": [
        "单仓删本地分支: `delete_branch here <分支名>`；批量: `delete_branch all <分支名>`。",
        "调用前确认当前分支和工作区干净。",
    ],
    "delete_branch_remote": [
        "单仓/批量删远端分支，参数形如 `delete_branch_remote here <分支名>`。",
        "对外可见的操作：调用前确认 remote/branch 无误。",
    ],
    "disable-ipv6": [
        "用 networksetup 关闭本机所有 macOS 网络服务的 IPv6: `sudo disable-ipv6`。",
        "需要 sudo/root；改主机网络配置。",
    ],
    "enable-ipv6": [
        "用 networksetup 恢复 IPv6 自动模式: `sudo enable-ipv6`。",
        "需要 sudo/root；改主机网络配置。",
    ],
    "fetch_all": [
        "为发现的所有 git 仓库 fetch 远程更新: `fetch_all all`。",
        "分支同步/合并/推送工作流前刷新 refs 用。",
    ],
    "inject": [
        "把 bin/ 注入 shell PATH。",
        "先无写入预览: `inject show`；确认后 `inject run` 写入 rc；`inject uninstall` 撤销。",
    ],
    "ipinfo": [
        "查全部: `ipinfo all`；仅内网 IP: `ipinfo lan`；仅网络类型: `ipinfo net`。",
        "跑对连通性敏感的命令前用它诊断网络环境（含热点识别）。",
    ],
    "issue": [
        "用本地上下文 + AI 生成标题/正文创建 issue: `issue create`。",
        "需要检测到的 gh/glab 提供方，并向外发布前确认。",
    ],
    "kk": [
        "按名字正则终止进程: `kk by_name 'python.*server'`。",
        "确认终止前先过一遍列出的进程。",
    ],
    "kkp": [
        "终止占用端口的进程: `kkp by_port 8080`。",
        "释放本地开发端口用；先看进程列表再确认。",
    ],
    "lazyhelp": [
        "人类可读命令目录；裸跑 `lazyhelp` 输出全部分类速查表。",
        "看某个工具的完整 --help: `lazyhelp help <工具名>`。",
        "看 AI 向指引（含可直接照抄的示例）: `<工具名> --skills`；下方目录列出全部工具。",
    ],
    "list_branch": [
        "跨仓库列出本地分支: `list_branch all`。",
        "批量分支清理或同步前用。",
    ],
    "loop": [
        "循环执行直到成功（成功即停）: `loop run 5 -- curl -sf http://localhost:8080/health`（首 token 数字 = 最多次数）。",
        "失败也继续跑满次数: `loop force 3 -- make test`；无限: `loop infinite -- <命令>`。",
        "全局 flag 放在被包裹命令前面，避免吞掉命令自己的 flag。",
    ],
    "merge_canary": ["单仓合入: `merge_canary here`；批量: `merge_canary all`；自动: `merge_canary auto`。"],
    "merge_dev": ["单仓合入: `merge_dev here`；批量: `merge_dev all`；自动: `merge_dev auto`。"],
    "merge_develop": ["单仓合入: `merge_develop here`；批量: `merge_develop all`；自动: `merge_develop auto`。"],
    "merge_master": [
        "合入自动识别的默认分支（master/main）: 单仓 `merge_master here`；批量 `merge_master all`；自动 `merge_master auto`。",
    ],
    "merge_test": ["单仓合入: `merge_test here`；批量: `merge_test all`；自动: `merge_test auto`。"],
    "mr": [
        "用本地 diff 上下文 + AI 生成标题/正文创建 PR/MR: `mr create`。",
        "需要检测到的 gh/glab 提供方，并向外发布前确认。",
    ],
    "n": [
        "macOS 语音播报: `n say '部署完成'`。",
        "拒绝危险 shell 字符和超长内容。",
    ],
    "ovpn": [
        "OpenVPN 连接/断开/状态/登录: `ovpn connect` / `ovpn disconnect` / `ovpn status`；凭据和 TOTP 已存好。",
        "碰配置的命令需要 root，可能经 sudo 重启自身；无特权查看用 `ovpn status`。",
    ],
    "push_branch": [
        "跨仓库推当前分支（先 pull --ff-only 再 push）: `push_branch current`；指定分支: `push_branch to <分支>`。",
        "本地检查通过后用；这会把 commit 发布到远端。",
    ],
    "push_canary": ["推当前分支到 canary: `push_canary here` / `push_canary all` / `push_canary auto`。"],
    "push_dev": ["推当前分支到 dev: `push_dev here` / `push_dev all` / `push_dev auto`。"],
    "push_develop": ["推当前分支到 develop: `push_develop here` / `push_develop all` / `push_develop auto`。"],
    "push_master": [
        "推到自动识别的默认分支（master/main）: `push_master here` / `push_master all` / `push_master auto`。",
    ],
    "push_test": ["推当前分支到 test: `push_test here` / `push_test all` / `push_test auto`。"],
    "squash_pr": [
        "把 source 分支改动压成单个 commit 并开/更新 PR: `squash_pr run`。",
        "可能 force-push PR 分支；用前确认分支影响面。",
    ],
    "switch_branch": [
        "把所有仓库切到目标分支（不存在则创建并跟踪）: `switch_branch to feat/x`。",
        "用远端默认分支探测，不硬编码 master/main。",
    ],
    "sync_branch": [
        "把各仓库硬对齐到指定分支: `sync_branch to main`；对齐各仓库当前分支: `sync_branch current`。",
        "批量开工前让所有仓库停在同一分支时用。",
    ],
    "sync_master": [
        "把各仓库同步到各自识别出的远端默认分支: `sync_master run`。",
        "开 feature 分支前刷新默认分支用。",
    ],
    "unsleep": [
        "防 macOS 休眠: 按时长 `unsleep timed 2h`；跟随命令 `unsleep with_command -- bash build.sh`；无限 `unsleep forever`（Ctrl+C 结束）。",
    ],
    "vpn-prio": [
        "调 macOS 网络服务优先级，缓解 VPN 默认路由问题。",
        "OpenVPN 路由干扰正常上网时用；具体子命令看 `vpn-prio --help`。",
    ],
    "webgrab": [
        "抓网页转 Markdown 打 stdout（默认不落盘）: `webgrab https://example.com`。",
        "存文件: `webgrab <url> -o page.md`；要原始 HTML 加 `--html`。",
        "反爬处理：curl_cffi 浏览器指纹直抓 → 被拦换指纹 → 仍被拦自动回退 Playwright 渲染；JS 页强制渲染加 `--render`。",
        "交互式 Turnstile/人机验证不会自动点过，如实报错退出。",
    ],
}


def command_name(argv0: str) -> str:
    return os.path.basename(argv0) if argv0 else "script"


def _catalog_lines() -> list[str]:
    """全工具目录（lazyhelp --skills 用）：一行一个工具 = 分类 + 功能 + 查看详情入口。"""
    from lib.lazyhelp import CATEGORIES_ORDER, TOOLS

    lines: list[str] = []
    for cat in CATEGORIES_ORDER:
        items = [(n, d) for n, (c, d) in sorted(TOOLS.items()) if c == cat]
        if not items:
            continue
        lines.append(f"{cat}:")
        for name, desc in items:
            lines.append(f"- {name} — {desc}（详情: `{name} --skills`）")
    return lines


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
    if name == "lazyhelp":
        lines.extend(["", "工具目录（什么场景用什么工具）："])
        lines.extend(_catalog_lines())
    lines.extend(["", "通用约定："])
    lines.extend(f"- {skill}" for skill in COMMON_SKILLS)
    return "\n".join(lines) + "\n"


def consume_skills(argv: list[str], description: str = "") -> list[str]:
    """Print AI-facing command skills for --skills and exit 0."""
    if "--skills" not in argv[1:]:
        return argv
    print(render_skills(command_name(argv[0]), description))
    raise SystemExit(0)
