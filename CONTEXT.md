# CONTEXT

本仓领域词汇表。只放词与定义，不放实现细节。

## graphwatch

- **注册表（registry）**：被监听目录的清单，add/remove 读写，daemon 热加载。
- **补课（catchup）**：daemon 启动或热加载新目录时，发现图谱落后于源码就入队重建的行为。只在挂载那一刻发生一次，之后靠监听。
- **新鲜度（freshness）**：单目录图谱是否落后于源码的状态。四态：新鲜 / 未构建 / 过期 / 更新中。daemon 在跑且源码有变更 = 更新中；没跑才是过期。
- **监听与重建分离**：watchdog 线程只负责发现变更（轻），重建走全局队列由 worker 执行（重）。同一目录的变更在队列里去重合并。
- **重建并发（rebuild concurrency）**：同时执行重建的目录数，最小 1，默认 1（串行）。
- **服务管理（service management）**：把 daemon 注册成用户级系统服务的动作集合——install / uninstall / start / stop / restart / registered / state。与 daemon 内部逻辑互不感知。
- **平台 adapter**：服务管理在某平台的具体实现（macOS launchd / Linux systemd --user / Windows schtasks）。平台差异只存在于 adapter 内部。

## git 工作流

- **分支会话（branch session）**：切到目标分支（不存在则建跟踪分支）并在退出时按路径决定是否回原分支的保证。错误路径默认回滚，成功路径默认留在目标分支。回滚幂等：已在原分支则跳过。
