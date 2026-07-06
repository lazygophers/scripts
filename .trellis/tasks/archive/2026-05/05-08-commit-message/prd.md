# PRD: 优化 commit 脚本的 message 生成规范

## 背景

当前 `commit` 脚本中的 `COMMIT_SPEC` 规范较为基础，仅列出基本 type 和简单约束。随着项目发展，需要更规范、更符合主流风格的 commit message 生成指导。

## 目标

优化 `commit` 脚本中的 commit message 规范部分（`COMMIT_SPEC` 和 `CLAUDE_PROMPT`），使 AI 生成的 message：

1. 更符合 Conventional Commits 完整规范
2. 支持更丰富的 type 和场景
3. 正确处理 scope、breaking change、body
4. 保持简洁实用的 prompt 长度

## 需求

### 1. 扩展 type 列表

新增现代开发中常用的 type：
- `wip` — 工作进行中（临时提交）
- `security` — 安全相关修复
- `deps` — 依赖更新
- `i18n` — 国际化/本地化
- `a11y` — 无障碍改进
- `analytics` — 埋点/分析
- `config` — 配置变更

### 2. 明确 scope 规范

- scope 使用小写，多个词用连字符（如 `user-auth`）
- 可选，但当变更有明确模块归属时建议使用
- 不要过度使用（避免每个 commit 都带 scope）

### 3. 增加 body 和 breaking change 规范

- 复杂变更需要 body（空一行后写详细说明）
- breaking change 用 `!` 标记（如 `feat(api)!: 移除旧接口`）
- 或在 footer 中加 `BREAKING CHANGE: 说明`

### 4. 优化 prompt 中的 negative examples

添加"不要这样做"的示例，减少常见错误。

### 5. 保持 description 约束

- 中文描述
- 命令式口吻（"添加"而非"添加了"）
- 尾不加句号
- 不超过 50 字
- 首字母不大写（因为前面有 type/scope）

## 验收标准

- `commit` 脚本的 `COMMIT_SPEC` 包含完整规范
- `CLAUDE_PROMPT` 包含足够的上下文和约束
- 生成的 commit message 符合 Conventional Commits 规范
