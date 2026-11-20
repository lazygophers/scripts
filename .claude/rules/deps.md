# 依赖升级（requirements.txt / pyproject.toml）

- 升级前先查 PyPI JSON API 的 `info.requires_python`（`https://pypi.org/pypi/<包名>/json`），别只看版本号。2026-11-20 实测：graphifyy 0.9.113 盲升后包自身封顶 `<3.15`，3.15 上整份 requirements 解析失败，f14dd8d 补 `python_version < "3.15"` 环境标记才兼容 3.11.5~latest。
- 改动后 `uv pip compile` 在 3.11.5（requires-python 下界）/ 3.14（daemon venv）/ 3.15（上界试探）三档各解析一遍，`--index-url https://pypi.org/simple`（默认镜像 403）；3.15 档专门验证 graphifyy 被环境标记跳过、其余包可装。
- graphifyy 双装（pipx CLI + daemon venv `~/.local/share/lazygophers/daemon-python`），升级两边都要动——细节见 CLAUDE.md graphwatch 节。
