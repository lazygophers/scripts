"""AI-facing command skills output."""

from __future__ import annotations

import os


COMMON_SKILLS = [
    "Use `--help` for human-facing usage before composing arguments.",
    "Use `--dry-run` when available to preview side effects without changing state.",
    "Use `--no-say` or `SCRIPTS_NO_SAY=1` to suppress voice notification noise.",
    "Use `--debug` or `SCRIPTS_DEBUG=1` when command output is needed for diagnosis.",
]


COMMAND_SKILLS = {
    "_gitwf": [
        "Internal entry for merge_*/push_* symlinks; prefer invoking the public symlink name.",
        "Dispatches action and target branch from argv[0].",
        "Auto mode runs single-repo here when cwd is a git repo, otherwise batch all.",
    ],
    "archery": [
        "Operate Archery API by host profile; stdout is JSON for piping to tools like jq.",
        "Use `login` before API commands when credentials are missing or expired.",
        "Commands exposing secrets (`show`, `code`) require root and may re-exec via sudo.",
    ],
    "check_ai": [
        "Probe configured AI API endpoint connectivity with a minimal request.",
        "Use to distinguish endpoint/auth/network failures before running AI workflows.",
    ],
    "checkwork": [
        "Run multi-language build/type checks for current repo or child repos.",
        "Use before commit/push when you need local confidence matching CI build basics.",
    ],
    "cicd": [
        "Use `cicd trigger` to start a GitHub workflow_dispatch run or a GitLab branch pipeline.",
        "Use `--project` to target another GitHub/GitLab project; without it the current git remote is used.",
        "Use `cicd status [branch]` to view the latest CI/CD for one branch.",
        "Use `cicd watch [branch]` or `cicd watch --target <id>` to poll silently until done; only errors print during polling unless `--verbose` is set.",
        "Use `cicd logs <id>` or `cicd logs <id> --failed` to inspect logs/error output for a specific CI/CD run/job.",
    ],
    "commit": [
        "Create an automated git commit for current repo or scanned child repos.",
        "Use after local checks pass and only when commit authorization exists.",
    ],
    "cpd": [
        "Deep-copy one or more sources into a destination with update/delete semantics.",
        "Treat last argument as destination and all previous arguments as sources.",
        "Use dry-run first for destructive sync/delete modes.",
    ],
    "delete_branch": [
        "Delete local branches in one repo or across child repos.",
        "Check current branch and worktree cleanliness before invoking.",
    ],
    "delete_branch_remote": [
        "Delete remote branches in one repo or across child repos.",
        "Treat as outward-facing and confirm intended remote/branch before invoking.",
    ],
    "disable-ipv6": [
        "Disable IPv6 for all macOS network services via networksetup.",
        "Requires sudo/root; changes host network configuration.",
    ],
    "enable-ipv6": [
        "Restore automatic IPv6 for all macOS network services via networksetup.",
        "Requires sudo/root; changes host network configuration.",
    ],
    "fetch_all": [
        "Fetch remote updates for all discovered git repositories.",
        "Use to refresh refs before branch sync/merge/push workflows.",
    ],
    "inject": [
        "Install or show shell PATH injection for this scripts bin directory.",
        "Use `show` for no-write inspection before modifying shell rc files.",
    ],
    "ipinfo": [
        "Show local network/IP information and hotspot/VPN clues.",
        "Use for diagnosing network environment before connectivity-sensitive commands.",
    ],
    "issue": [
        "Create an issue with AI-generated title/body from local context.",
        "Requires detected gh/glab provider and external publication confirmation.",
    ],
    "kk": [
        "Find and terminate processes by name pattern.",
        "Review listed processes before confirming termination.",
    ],
    "kkp": [
        "Find and terminate processes listening on a port.",
        "Use when freeing a local development port; review process list first.",
    ],
    "lazyhelp": [
        "Human-facing command catalog; default output is command list and user descriptions.",
        "Use `lazyhelp help <tool>` for that tool's human-facing help.",
        "Use target tool `--skills` for AI-facing command guidance.",
    ],
    "list_branch": [
        "List local branches across repositories.",
        "Use before batch branch cleanup or synchronization.",
    ],
    "loop": [
        "Repeat a command until success, forever, or for a forced count.",
        "Place global flags before the wrapped command to avoid consuming command flags.",
    ],
    "merge_canary": ["Merge current branch into canary using safe git workflow checks."],
    "merge_dev": ["Merge current branch into dev using safe git workflow checks."],
    "merge_develop": ["Merge current branch into develop using safe git workflow checks."],
    "merge_master": ["Merge current branch into detected default branch using safe git workflow checks."],
    "merge_test": ["Merge current branch into test using safe git workflow checks."],
    "mr": [
        "Create a PR/MR with AI-generated title/body from local diff context.",
        "Requires detected gh/glab provider and external publication confirmation.",
    ],
    "n": [
        "Speak a short macOS voice notification with `say`.",
        "Rejects dangerous shell characters and overlong content.",
    ],
    "ovpn": [
        "Connect/disconnect/status/login for OpenVPN with stored credentials and TOTP.",
        "Config-touching commands require root and may re-exec via sudo.",
        "Use `status` for unprivileged inspection.",
    ],
    "push_branch": [
        "Push current branch to same-name remote branch across repositories.",
        "Use after local checks pass; this publishes commits to remotes.",
    ],
    "push_canary": ["Push current branch into canary using safe git workflow checks."],
    "push_dev": ["Push current branch into dev using safe git workflow checks."],
    "push_develop": ["Push current branch into develop using safe git workflow checks."],
    "push_master": ["Push current branch into detected default branch using safe git workflow checks."],
    "push_test": ["Push current branch into test using safe git workflow checks."],
    "squash_pr": [
        "Squash source branch changes into one commit and create or update a PR.",
        "May force-push the PR branch; confirm branch impact before use.",
    ],
    "switch_branch": [
        "Switch all repositories to a target branch, creating/tracking when needed.",
        "Uses remote default branch detection instead of hardcoding master/main.",
    ],
    "sync_branch": [
        "Synchronize repositories to origin/<branch>.",
        "Use before batch work that assumes every repo is on the same branch.",
    ],
    "sync_master": [
        "Synchronize repositories to each repo's detected remote default branch.",
        "Use to refresh default branches before creating feature branches.",
    ],
    "unsleep": [
        "Prevent macOS sleep forever, for seconds, or while a command runs.",
        "Use `with_command` to keep machine awake only for wrapped command duration.",
    ],
    "vpn-prio": [
        "Adjust macOS network service priority to reduce VPN default route problems.",
        "Use when OpenVPN routing interferes with normal network access.",
    ],
}


def command_name(argv0: str) -> str:
    return os.path.basename(argv0) if argv0 else "script"


def render_skills(name: str, description: str = "") -> str:
    """Return AI-facing guidance for one command."""
    skills = COMMAND_SKILLS.get(name, [])
    if not skills and description:
        skills = [description.strip()]
    lines = [f"# {name} skills", "", "Audience: AI agents using this command."]
    if description.strip():
        lines.extend(["", "Summary:", description.strip()])
    lines.extend(["", "Use when:"])
    if skills:
        lines.extend(f"- {skill}" for skill in skills)
    else:
        lines.append("- Need this command's documented behavior; inspect `--help` for human usage details.")
    lines.extend(["", "Common protocol:"])
    lines.extend(f"- {skill}" for skill in COMMON_SKILLS)
    return "\n".join(lines) + "\n"


def consume_skills(argv: list[str], description: str = "") -> list[str]:
    """Print AI-facing command skills for --skills and exit 0."""
    if "--skills" not in argv[1:]:
        return argv
    print(render_skills(command_name(argv[0]), description))
    raise SystemExit(0)
