# Evolving Profile 6.2.0

发布日期：2026-11-24
状态：本地准备完成，推送 `main`、`release/6.2.0` 与 Tag `v6.2.0`（以 GitHub 读回为准）；不创建 GitHub Release 对象，与 6.1.0 一致。

## 解决的问题

长期使用后，记忆库里大量内容从不被检索读取：未确认的助手陈述、碎片、多个宿主重复记下的同一事实、闲置会话的工具调用日志。它们占用向量索引和候选名额，让检索更慢、结果更重复，存储也随使用无限增长。另外，宿主长期挂着旧的 MCP 进程，部署后仍按旧代码回答；Hook 与 MCP 两条偏好入口的检索模式不一致；一次迁移把结构化的偏好 `scope` 改成了字符串，导致读取崩溃。

## 本次变更

- 冷热分层与准入去重：`v6/memory_tiering.py`，调度器作业 `memory-tiering`（仅在配置了 Bank 时启用）。
- 半精度向量：API 迁移 `f3a4b5c6d7e8`；`engine/retain/bank_utils.py` 新建 Bank 索引使用 `halfvec_cosine_ops`；`EVOLVING_PROFILE_EMBEDDING_STORAGE=vector` 可退出。
- 时间关系上限 20 → 10：`engine/retain/link_utils.py`。
- 事件日志保留：`v6/events_retention.py`，调度器作业 `events-retention`；归档表 `ep_events.tool_event_archive`、`ep_events.pruned_tool_sessions`。
- 研究工作区过期清理修复：`host-adapter/evidence_workspace.py`。
- 召回轨迹瘦身：`host-adapter/lib/trace_slim.py`、`recall.py`。
- 存储基准：`v6/storage_bench.py`。
- MCP 适配器：请求前热更新、失败类型审计、`-32602`、Schema 偏差审计、`user_recall` 的 `allOf` 声明：`host-adapter/evolving_profile_controller_mcp.py`。
- 偏好一致性：`guidance/mcp_runtime.py` 的 `preference_semantic_mode()`、`host-adapter/guidance_entry.py`；`guidance/repository.py` 触发器；`v6/ontology_hygiene.py` 改为写新版本。
- Console：统一星座图、偏好分区虚线框、单选时去掉区域圈、全部图谱"加载更多"、窗口文字改为"显示 M / 共 N"。

## 升级与回退

见 [CHANGELOG-6.2.md](../CHANGELOG-6.2.md) 的 Upgrade 一节。要点：先决定是否保留 32 位向量再跑迁移；`events-retention` 在已安装的 `events/ledger.py` 不含归档会话保护时自动跳过；6.1 的常驻 MCP 进程需重连一次才能获得热更新。回退：`alembic downgrade e2f3a4b5c6d7`、`python -m v6.memory_tiering restore --reason <r>`、`python -m v6.events_retention restore <archive_id>`。

## 已知边界

- 实测数字来自一套 macOS 安装的一个约 7.5 万条记忆的 Bank，不代表其他数据。
- 构建 `ep_events` 台账的事件管道属于各安装自身，不在本包内。
- 温数据不参与向量召回；关键词、全量检索与原文回读仍可找到。
- 私有运行目录中的内容寻址增量备份、保留规则执行器与容量预算告警依赖本机目录布局，未随本版发布。
- Windows / Linux 未实测本版新增作业。

## 验证

| 范围 | 命令或真实读回 | 结果 | 时间 |
| --- | --- | --- | --- |
| 脱敏发行包 | `./scripts/verify-package.sh`、`scripts/release-preflight.py --json` | 见发布记录 | 2026-11-24 |
| Host Adapter | `pytest`（发行仓库） | 1841 通过，3 跳过；`test_governed_mcp_http.py` 7 项因测试环境为 fastmcp 3 而排除，本版改动前同样失败 | 2026-11-24 |
| Guidance | `pytest`（发行仓库） | 141 通过 | 2026-11-24 |
| v6 | `pytest v6/tests` | 24 通过（含新增 5 项存储治理测试） | 2026-11-24 |
| API | 与改动相关的单元测试 | 68 通过；另 89 项需独立测试库（未安装 pg0-embedded），未运行 | 2026-11-24 |
| API 迁移 | `f3a4b5c6d7e8` 在约 7.5 万条记忆的实库上执行 upgrade、重复 upgrade（无操作）、downgrade、upgrade | 均成功，单次约 13 秒；召回 hit@10 前后均为 87/100 | 2026-11-24 |
| Console | `vitest`、`tsc --noEmit`、`next build` | 669 通过；类型检查与生产构建通过 | 2026-11-24 |
| 实际宿主链路 | 私有运行环境中 `user_research`、`ep_exhaustive_search` 等 MCP 实调 | 正常 | 2026-11-24 |

## 作者与致谢

沿用 [NOTICE.md](../NOTICE.md)。本版提交由发行仓库现有作者身份提交；Hindsight 致谢不变。

## GitHub 状态

- 上游仓库：`gtkj/evolving-profile`；镜像：`ccygod/evolving-profile`
- 分支和 commit：见[发布记录](RELEASE-LEDGER.md)
- PR：未创建（直接更新 `main`，与 6.1.0 一致）
- Tag：`v6.2.0`
- Release：未创建
