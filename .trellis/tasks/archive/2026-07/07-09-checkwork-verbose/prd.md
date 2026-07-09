# PRD: checkwork 增强多语言框架检测与 verbose 进度

## 背景

`bin/checkwork` 当前仅支持 Go build（`go build -v -o /dev/null`）和 Node.js 检测（直接跳过，不编译）。用户需要 checkwork 覆盖更多语言/框架，成为 CI/CD build 前置拦截器——防止 push 后 CI 连 build 都过不去。

## 目标

增强 `lib/build.py`，使其：

1. **多语言**：支持 Go / Rust / Python / Java / C/C++ / Node.js 的编译检查
2. **多框架**：Node.js 按 lockfile 自动选 bun/yarn/pnpm/npm 跑 build/typecheck script
3. **verbose**：所有检查命令带 `-v`/`--debug`/`--verbose`，实时输出进度（不干等）
4. **零产物**：check-only 或 build 到 `/dev/null`，绝不产生二进制/构建产物
5. **可选并行**：默认串行，`CHECKWORK_PARALLEL=1` 开启多检查点并行（脚本提示）

## 功能需求

### FR1 多语言检测与检查

| 语言 | 检测文件 | 检查命令 | 产物处理 |
| --- | --- | --- | --- |
| Go | `go.mod` | `go build -v -o /dev/null .`（现状保留 cmd/app 子目录逻辑） | `/dev/null` |
| Rust | `Cargo.toml` | `cargo check --verbose` | 不生成二进制 |
| Python | `pyproject.toml`/`setup.py`/`requirements.txt` | `python3 -m py_compile` 批量编译所有 `.py`（顶层 + src/） | 无（py_compile 只写 `__pycache__`，可接受） |
| Java | `build.gradle`/`build.gradle.kts`/`pom.xml` | gradle: `./gradlew compileJava --console=plain`; maven: `mvn compile -q` | gradle/maven 自管 build/，不动 |
| C/C++ | `Makefile`/`CMakeLists.txt` | make: `make -n`（dry-run，不真正 build）; cmake: `cmake -B build_check && cmake --build build_check` 到 `$TMPDIR` 后删 | tmp + 清理 |
| Node.js | `package.json` | 见 FR2 | 无 |

### FR2 Node.js 框架检测

按 lockfile 优先级选包管理器（`bun.lockb` → bun, `yarn.lock` → yarn, `pnpm-lock.yaml` → pnpm, `package-lock.json` → npm），跑 `package.json` 中存在的 script：

1. 优先 `build` script（若存在）
2. 次选 `typecheck` script（若存在）
3. 若都没有，仅跑 `tsc --noEmit`（检测 `tsconfig.json`）

**类型错误（tsc/类型检查失败）只警告不中止**——记录到 log，继续后续检查，不抛 `BuildError`。

### FR3 verbose 进度输出

- 所有检查命令透传 stdout/stderr 到终端（非 capture），让用户实时看到进度
- 各检查点开始时 `log()` 打印检测到的语言/框架 + 即将执行的命令
- Go 已用 `-v`；Rust `--verbose`；make/cmake 透传输出；gradle `--console=plain`

### FR4 零产物保证

- Go: `-o /dev/null`（现状）
- Rust: `cargo check`（不 build，不生成可执行文件）
- C/C++: make 用 `make -n` dry-run；cmake 构建到 `$TMPDIR/checkwork_<random>/` 后清理
- Node: tsc `--noEmit`，包管理器 build script 原样跑（用户自行决定，但 checkwork 不强制产物）
- Java: gradle/maven `compile` 目标，产物留在项目自管 `build/`/`target/`（不改项目结构）

### FR5 可选并行

- 默认串行执行各检查点
- `CHECKWORK_PARALLEL=1` 环境变量开启多检查点并行（`concurrent.futures`）
- `run_checkwork()` 启动时输出提示：「提示: 设 `CHECKWORK_PARALLEL=1` 可并行加速多语言检查」
- 单语言内部子检查（如 Go 多个 main 包）仍串行，避免锁竞争

### FR6 保持现有行为

- Go 的 `pay-core`/`*dao-*` 排除逻辑保留
- Go 的 `cmd/`/`app/` 子目录 main 包扫描保留
- `BuildError` 异常类型保留，`run_checkwork()` 退出码语义保留（0=通过, 2=失败）
- Rich Reporter 输出风格保留

## 非功能需求

- 保持 `from __future__ import annotations`（Python<3.10 兼容）
- 不引入新运行时依赖（仅 stdlib + 已有 lib/exec、lib/ui、lib/notify）
- ruff clean

## 验收标准

1. Go 项目行为不变（回归）
2. Rust 项目执行 `cargo check --verbose`，无二进制产物
3. Python 项目执行 `py_compile`，mypy/ruff warn-only
4. Java 项目执行 gradle/maven `compile`
5. C/C++ 项目执行 make dry-run / cmake tmp build
6. Node.js 项目按 lockfile 选包管理器跑 build/typecheck，类型错误只警告不中止
7. 所有检查过程实时输出（不干等）
8. `CHECKWORK_PARALLEL=1` 时多检查点并行
9. 无任何二进制/构建产物残留（Go `/dev/null`，Rust check，C/C++ 清理）
10. ruff clean

## 范围

- 仅改 `lib/build.py`（核心逻辑）
- `bin/checkwork` 薄壳不变
- 可选：`tests/` 增补各语言检测单测（若成本可控）
