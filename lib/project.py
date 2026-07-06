"""项目上下文（目录名推断 org + 项目名）。"""
import os

_LANGUAGE_DIRS = frozenset({
    "go", "node", "flutter", "python", "java", "rust", "ruby", "php",
    "swift", "kotlin", "dotnet", "c", "cpp", "web", "frontend", "backend",
    "typescript", "javascript", "dart",
})


def safe_project_context() -> str:
    """生成 "[org] 的 [项目名]" 描述。

    org = 父文件夹名；若父文件夹是编程语言目录名则再上溯一层（最多一次）。
    项目名 = 当前文件夹名。
    """
    cwd = os.getcwd()
    project = os.path.basename(cwd)
    parent = os.path.dirname(cwd)
    org = os.path.basename(parent)
    if org.lower() in _LANGUAGE_DIRS:
        org = os.path.basename(os.path.dirname(parent))
    return f"{org} 的 {project}"
