# checkwork 自动识别 Go main 入口

## Goal

checkwork 的 Go 编译检查能自动识别项目 main 入口，避免对无 `.go` 文件的根目录执行 `go build` 导致误报失败。

## Background

当前 `_build_go_project` 逻辑：
1. 遍历 `cmd/`、`app/` 下子目录，逐个编译 ✅
2. 对根目录执行 `go build .`，无论根目录是否有 `.go` 文件 ❌

像 `tmtc_bg`（根目录无 `.go` 源码，都在 `cmd/` 下）就会报 `no Go files in ...`。

## Requirements

- 编译前检查目录是否为 `package main`：找任意 `.go` 文件读第一行，确认 package 声明
- 非 `package main` 的目录跳过编译（无论是否有 `.go` 文件）
- `cmd/`、`app/` 子目录 + 根目录都走同一套检查
- 排除项目（`pay-core`、`dao-*`）仍被跳过

## Acceptance Criteria

- [ ] `tmtc_bg` 类项目（cmd/ 下有 main，根是 lib）编译检查通过
- [ ] 根目录有 `package main` 的传统项目仍正常编译
- [ ] `cmd/` 下非 `package main` 的子目录被跳过
- [ ] 排除项目仍被跳过

## Out of Scope

- 新增非 Go 语言支持
- 修改 project 发现逻辑

## Definition of Done

- 测试验证通过
- 现有行为不变（根有 .go 文件的项目不受影响）
