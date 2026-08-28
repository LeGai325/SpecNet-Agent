# 协作文档

本目录只保留团队开发和模块合并需要的说明：

- [`ARCHITECTURE.md`](ARCHITECTURE.md)：当前代码结构、运行流程和模块集成位置。
- [`PROJECT_STATUS.md`](PROJECT_STATUS.md)：已实现功能、默认配置、缺失模块和下一步。
- [`TRACE_DRIVEN_V2.md`](TRACE_DRIVEN_V2.md)：当前 trace-driven workload 的数据角色、
  运行方式和边界。
- [`TRACE_DRIVEN_V3_CANDIDATE.md`](TRACE_DRIVEN_V3_CANDIDATE.md)：SWE-chat 转换、组件级
  split、时间清洗、V3 profile 和预检边界。
- [`TRACE_DRIVEN_V3_FORMAL_REPORT.md`](TRACE_DRIVEN_V3_FORMAL_REPORT.md)：V2 vs V3 的
  5-seed、90-episode、10-run 正式配对结果与解释边界。
- [`REAL_AGENT_DATA_V3_SURVEY.md`](REAL_AGENT_DATA_V3_SURVEY.md)：公开真实 Agent 数据的
  V3 候选筛选、全量预检证据和下一步准入方案。
- [`DATA_SETUP.md`](DATA_SETUP.md)：团队成员下载、校验、构建和运行 V1/V2/V3 profile 的
  最短流程。
- [`WORKFLOW_HINT_COLLECTOR.md`](WORKFLOW_HINT_COLLECTOR.md)：Collector 数据契约、启用方式、输出、测试和动态 DAG 衔接。
- [`DYNAMIC_DAG.md`](DYNAMIC_DAG.md)：动态 DAG 执行语义、Flow bridge、fixture、preflight 和边界。
- [`PCRIT_SCORE.md`](PCRIT_SCORE.md)：Pcrit/Score shadow scorer、无泄漏历史、输出、preflight 和限制。

个人实验过程、完整消融报告、调参记录和原始输出不放入协作仓库，由各负责人自行保存。
