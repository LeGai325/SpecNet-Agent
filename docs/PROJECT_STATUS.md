# 当前项目状态

更新时间：2026-08-01

## 已实现

- Trace-driven Agentic GenAI workflow 模拟器。
- FIFO、static priority、critical-path-only 三类网络 baseline。
- `rule_aggressive`、`rule_balanced`、`rule_quality_preserving` 三类规则控制器。
- 基于 contextual bandit 的 SpecNet Controller。
- 可参数化 `quality_weight` 和多训练 seed 实验。
- Controller 状态变体：`full`、`congestion_only`、`no_slack`、
  `no_spec_pressure`。
- 训练稳定化：epsilon 衰减、访问次数学习率衰减、独立 validation checkpoint
  选择。
- Work-and-queue-aware Slack v2，以及可配置的 role-aware v2.1 候选。
- Controller ablation、Slack calibration、训练稳定性和 Pareto 分析脚本。
- 可选的三条服务逻辑路径调度、工作守恒的空闲容量借用，以及逐路径容量、利用率、
  队列压力和借用统计输出。
- 基于实际 retained optional utility 的 realized quality，以及 useful/unused
  speculative bytes 分离记账。
- 可选 `decoupled` action 模式将 optional branch fanout 与后台流量独立控制；
  默认 `legacy` 模式保留用于复现旧实验。
- optional branch 在 planner 完成后按 utility/byte 一次性选入；动态追加和停止延期。
- 新增 `path_aware_quality` controller variant，以 required path pressure 和
  optional headroom 表达多路径状态；原有 controller variant 保持兼容。
- 固定平均质量目标 0.95 和单 workflow 硬下限 0.90；新增动作前 Safety Guard、
  完整负载周期级 λ 更新、约束感知 checkpoint 选择及 SpecNet Guard off/on 消融输出。
- Trace-driven V2 数据侧管线：RAGPulse request adapter、tau3-bench metadata adapter、
  固定 75/25 source mix 的 profile 构建、split 防泄漏校验与确定性抽样。
- 渐进式 speculation admission、运行中追加与停止 branch 仍明确延期。

## 当前默认配置

- Slack 使用 `total + 1.0` 的 v2 估算器。
- 网络模型默认使用 `single_bottleneck`；`service_paths` 需通过 CLI 显式启用。
- Role-aware `policy_weighted + 0.5` 仅作为可选候选；它没有在现有 3-seed
  preflight 中稳定超过 v2。
- 稳定训练推荐使用 90 episodes、线性 epsilon 衰减、visit-decay learning rate、
  30/45/60/75/90 checkpoints 和独立 validation 选择。

## 尚未完成或仍为代理实现

- Queue priority 当前是 weighted allocation，不是真实 Q0-Q3 队列。
- `no_source_control` 尚未实现为严格开关，目前由 `critical_path_only` 代理。
- `no_learning` 尚未实现为严格开关，目前由 `rule_balanced` 代理。
- Speculative-pressure、真实源端控制和 QoS 队列模块仍需与其他开发者代码合并。
- `service_paths` 只隔离调度容量；旧 Controller variant 的 congestion、Slack 和
  speculative pressure 仍是全局聚合状态。可选 `path_aware_quality` 已补充紧凑的
  required path pressure 和 optional headroom，但尚未把所有旧状态改为逐路径版本。
- `service_paths_borrowing` 只共享当前周期的剩余容量，不实现动态最短队列选路、flow
  迁移或逐跳路径。
- 服务逻辑路径不是逐跳拓扑，不包含共享核心、ECMP 或路由变化。
- `trace_driven_v2` 尚未注册为模拟器 workload；RAGPulse 的 step、duration、DAG、
  deadline 和 telemetry 均保持缺失，tau3-bench runner 尚未实现。

## 合并时的约束

- 不要在同一个功能 PR 中同时修改 reward、`ACTION_CONFIG`、workload 分布和
  Controller 状态，除非 PR 明确说明原因。
- 新模块应尽量通过配置启用，并保持当前默认行为可回归。
- 合并 QoS 或源端控制后，先运行现有测试和小型 paired preflight，再启动大规模实验。
- 完整实验数据和个人过程报告保存在仓库之外；GitHub 仓库只维护协作所需代码和说明。

## 下一步

1. 合并真实 QoS queue 实现。
2. 合并真实源端 fanout/speculation control。
3. 合并新的 speculative-pressure 信号。
4. 在相同 reward、action 和 workload 下重新运行 Controller ablation。
5. 在独立 PR 中评估 path-aware congestion 与 Slack，避免与本次调度改动混合。
6. 在动态 DAG/映射语义确认后，以独立 PR 接入 `trace_driven_v2` 并做 paired preflight。
