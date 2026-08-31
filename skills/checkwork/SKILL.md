---
name: checkwork
description: 使用本仓库的 checkwork 脚本做多语言编译检查（Go/Rust/Python/Java/Node/C/C++）。当任务需要在提交或 push 前验证代码能否编译通过时使用。
---

# checkwork

`checkwork`：多语言编译检查（CI/CD 前置拦截器），零产物。

- **Go**: `go build -v -o /dev/null`，覆盖 `cmd/`/`app/` 子目录 main 包 + 根目录，排除 `pay-core`/`*dao-*`
- **Rust**: `cargo check --verbose`
- **Python**: `py_compile`（致命），mypy/ruff 仅警告
- **Java**: gradle `compileJava` / maven `mvn compile`
- **C/C++**: cmake 只 configure 不 build；Makefile 跳过
- **Node**: 按 lockfile（bun > yarn > pnpm > npm）选包管理器；build script 经白名单识别才跑，含 watch/serve/dev 跳过并警告；`CHECKWORK_NODE_BUILD=1` 强制

用法：

```bash
checkwork                # 检查当前目录
checkwork all            # 批量检查子目录所有仓库（push_* 闸门同款）
CHECKWORK_PARALLEL=1 checkwork   # 多语言检查点并行
```

push_* 脚本默认在合并前后各跑一次 checkwork，`--no-check` 跳过。
