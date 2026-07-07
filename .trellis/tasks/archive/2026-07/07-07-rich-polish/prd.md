# PRD — rich 输出可读性修复（人类友好）

## 问题（用户原话：对人类阅读不友好，非常不友好）

实跑 sync_branch / fetch_all 暴露的具体缺陷：

### 缺陷 1：信息三重冗余
批量命令同一批仓库信息出现 3 次：
- 扫描后逐行列出（`ℹ   •  repoN`）
- 执行时逐行报状态
- 汇总段再列一遍
→ 人眼重复读 3 次相同仓库名。

### 缺陷 2：图标污染 + 双图标
- 每行前缀 `ℹ`/`✓`/`✗`/`⚠` icon，密集输出眼花
- 失败行 `✗ ✖ repo1`：err 的 `✗` + repo 前缀的 `✖` 重复表达"失败"

### 缺陷 3：汇总段信息密度低
```
═════ 执行汇总 ═════
ℹ 总计 3 个（成功 0 / 跳过 0 / 失败 3）
✗ 失败项目：
ℹ   • repo1 — fetch 失败
ℹ   • repo2 — fetch 失败
```
列表式，没用 Table 压缩，失败原因对不齐。

### 缺陷 4：分隔线刺眼
`═════` 全宽 Rule 在窄终端割裂视觉，标题段过多（fetch_all 3 个 rule）。

## 目标（轴 A）
批量 Git 命令输出对人类友好：
- 消除信息冗余（仓库不再列 3 遍）
- 去双图标，icon 克制（状态用颜色/单图标表达）
- 汇总段用紧凑 Table（仓库 | 状态 | 详情，分色行）
- 分隔线克制，标题层级清晰不刺眼

## 产出（轴 B）
- `lib/ui.py`：Reporter 增强
  - 新增 `status_table(title, items)`：状态汇总表（仓库名/状态/详情，状态列分色）
  - 调整 icon 使用（去冗余双图标）
  - rule 样式可选更轻（非全宽粗线）
- `lib/batch_git.py`：
  - `print_repo_list`：扫描后不再逐行 info（或改为紧凑单行摘要"N 个仓库"）
  - `print_summary`：改用 `status_table`，去三重冗余（执行段已报状态，汇总只做紧凑复盘）
  - 执行段每仓状态行去双图标
- `lib/git.py`：fetch_all 输出对齐上述风格
- `lib/notify.py`：`notify()` 裸 `print(msg)` 收编到 Reporter

## 非目标（OUT）
- cpd.py / cpd_core.py 不动（独立体系）
- 不改 batch_git.py:182 sys.stderr.write（per-repo buffer 防交错，有意）
- 不改 ui.py _eprint 降级路径
- 不引入全新视觉语言（复用现有 Table/Panel/Rule，只调用法）

## 范围决策（用户超时默认 + 本轮反馈修正）
- 美化 = 可读性修复（非视觉重设），聚焦消冗余 + 紧凑汇总
- cpd 不纳入

## 验收（轴 E）
- sync_branch 跑 3 仓库：仓库列表只出现 1 次（扫描摘要或汇总表，非 3 次）
- 无双图标（失败行单 `✗`，非 `✗ ✖`）
- 汇总段为 Table，失败原因对齐可读
- fetch_all 标题段不超 2 个 rule
- notify() 不裸 print
- pytest 全绿（336+ 不回归）
- ruff clean

## 风险
- print_summary / print_repo_list 改动影响所有批量脚本（sync/push/delete/switch/fetch），语义不变只改呈现
- 测试若断言具体输出格式需同步更新

## 布局决策（用户选定：紧凑 Table 汇总）

```
同步 master → origin/master      ← 标题（轻 rule 或无 rule）
扫描 3 个仓库                     ← 单行摘要，禁逐行列仓库
✗ repo1  fetch 失败              ← 执行段：单图标 + 仓库名 + 详情
✓ repo2  快进 2 → origin/master (a1b2c3)
• repo3  无 master 分支

执行结果                          ← 汇总段标题
┌────────┬──────┬─────────────────┐
│ 仓库   │ 状态 │ 详情            │   ← Table，状态列分色
├────────┼──────┼─────────────────┤
│ repo1  │ 失败 │ fetch 失败      │
│ repo2  │ 成功 │ 快进 2 → a1b2c3 │
│ repo3  │ 跳过 │ 无 master 分支  │
└────────┴──────┴─────────────────┘
失败 1/3 · 成功 1/3 · 跳过 1/3    ← 统计 footer 单行
```

### 执行段图标规范（去双图标）
- 成功：`✓`（绿）
- 失败：`✗`（红）
- 跳过：`•`（黄/dim）
- 禁 `✗ ✖` 双图标；状态 icon + 仓库名 + 详情，单行

## 颜色规范（用户反馈：现在完全没处理颜色）

### 现状问题
- Reporter 的 STYLE_* 只给 icon 上色，消息文本全白
- Table 行无分色（成功/失败/跳过行视觉无区分）
- 统计 footer 无色（"失败 1/3" 该红则红）

### 颜色映射（贯穿所有输出）
| 状态 | 色 | 应用点 |
|---|---|---|
| 成功 ok | green | icon ✓ + 整行文本/Table 行 + footer 数字 |
| 失败 fail | red | icon ✗ + 整行文本/Table 行 + footer 数字 |
| 跳过 skip | yellow | icon • + 整行文本/Table 行 + footer 数字 |
| 信息 info | cyan | 扫描摘要等中性信息 |
| 步骤 step | blue | 标题/阶段 |

### 具体落地
- `_icon_msg`：icon + 消息文本**同色**（非仅 icon）
- `status_table`：状态列单元格按状态着色（失败行状态格红字"失败"，成功绿"成功"，跳过黄"跳过"）；详情列保持默认可读色
- 统计 footer：`失败 1/3`（红）· `成功 1/3`（绿）· `跳过 1/3`（黄），各数字按状态色
- err/warn/ok 方法：消息文本随 icon 同色（当前仅 icon 有色）
- 降级路径（无 rich）：纯文本无色，语义不变

## 进度条规范（用户反馈：要进度条不要 1/3 数字）

### 现状问题
- run_batch 用 ThreadPoolExecutor + per-repo buffer flush，**无实时进度反馈**
- 用户只看到逐行仓库状态冒出，不知"还剩几个/总体进度"
- ui.py 有 progress() 返回 rich Progress，但 run_batch 没用

### 目标
执行过程顶部/底部显示**条形进度条**，实时反映 N 个仓库完成进度：
```
同步 master → origin/master
扫描 3 个仓库
⠋ 处理中... ████████░░░░░░░░ 67% 2/3 [00:03]
✗ repo1  fetch 失败
✓ repo2  快进 2 → origin/master (a1b2c3)
...
```

### 落地方案（run_batch 改造）
- 主线程持 `Progress`（SpinnerColumn + TextColumn 描述 + BarColumn + TaskProgressColumn + TimeElapsedColumn）
- `progress.add_task("处理中...", total=len(repos))`
- ThreadPoolExecutor 每个仓库 future 完成时：
  1. `progress.advance(task_id, 1)` 更新条
  2. `progress.console.print(per-repo buffer)` 在条下方输出该仓状态行
  3. 更新 progress 描述（如 "处理中 repo2..."）
- 全部完成后 `progress.stop()`，再输出汇总 Table
- **降级**（无 rich 或非 TTY）：退回逐行 flush（禁 Progress 在非 TTY 报错）

### 关键约束
- 进度条与 per-repo 日志共存：Progress 作为 live widget，仓状态行 print 到其 console
- 并发场景：advance 线程安全（rich Progress 内部有锁）
- per-repo buffer 仍整段 flush（防交错），只是 flush 目标从裸 stderr 改为 progress.console
