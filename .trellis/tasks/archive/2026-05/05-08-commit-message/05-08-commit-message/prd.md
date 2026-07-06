# PRD: 进一步优化 commit message 规范

## 背景

上一版规范已扩展 type 列表和基本约束，但缺少 footer 规范、type 选择决策树、revert 格式等进阶内容。

## 目标

增强 `COMMIT_SPEC` 和 `CLAUDE_PROMPT`，覆盖更多实际场景，减少 AI 生成 message 时的歧义。

## 需求

### 1. 新增 Footer 规范

- `Closes #123` / `Fixes #456` — 关闭 issue
- `Refs #789` — 关联 issue（不关闭）
- `Co-authored-by: Name <email>` — 多人协作
- `BREAKING CHANGE:` — 破坏性变更说明

### 2. 新增 Type 决策树

帮助 AI 在模糊场景选择正确 type：
- 修改了 package.json/go.mod → deps
- 修改了 .github/workflows → ci
- 修改了测试文件 → test
- 修改了 README/注释 → docs
- 修改了代码格式 → style
- 既修 bug 又加功能 → 拆 commit，或选主要目的

### 3. 新增 Revert 格式

- `revert: 回滚 "feat(auth): 添加 OAuth2 登录支持"`
- 或 `revert: feat(auth): 添加 OAuth2 登录支持`

### 4. 增强示例和约束

- 添加带 body 的完整示例
- 添加带 footer 的示例
- 添加【常见错误】section（与【不要这样做】合并或扩展）

## 验收标准

- COMMIT_SPEC 包含 footer、type 决策树、revert 格式
- CLAUDE_PROMPT 引用完整的 COMMIT_SPEC
- 规范总长度适中，不导致 prompt 过长
