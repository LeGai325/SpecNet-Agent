# 当前项目状态

更新时间：2026-07-17

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
- 标准 `src/specnet_agent` package、五个标准 CLI 和旧脚本兼容包装。
- schema v1 JSON 配置、确定性回归 fixture 和 `run_manifest.json`。

## 当前默认配置

- Slack 使用 `total + 1.0` 的 v2 估算器。
- Role-aware `policy_weighted + 0.5` 仅作为可选候选；它没有在现有 3-seed
  preflight 中稳定超过 v2。
- 稳定训练推荐使用 90 episodes、线性 epsilon 衰减、visit-decay learning rate、
  30/45/60/75/90 checkpoints 和独立 validation 选择。

## 尚未完成或仍为代理实现

- Queue priority 当前是 weighted allocation，不是真实 Q0-Q3 队列。
- `no_source_control` 尚未实现为严格开关，目前由 `critical_path_only` 代理。
- `no_learning` 尚未实现为严格开关，目前由 `rule_balanced` 代理。
- Speculative-pressure、真实源端控制和 QoS 队列模块仍需与其他开发者代码合并。

## 合并时的约束

- 不要在同一个功能 PR 中同时修改 reward、`ACTION_CONFIG`、workload 分布和
  Controller 状态，除非 PR 明确说明原因。
- 新模块应尽量通过配置启用，并保持当前默认行为可回归。
- 合并 QoS 或源端控制后，先运行现有测试和小型 paired preflight，再启动大规模实验。
- 完整实验数据和个人过程报告保存在仓库之外；GitHub 仓库只维护协作所需代码和说明。

## 工程验收状态

- 原实验侧 12 项与分析侧 4 项测试保持兼容。
- workload、Policy、Simulator 与训练 checkpoint 均有固定 seed 回归覆盖。
- 核心运行仅依赖标准库；matplotlib 和开发工具通过 extras 安装。

## 下一步

1. 合并真实 QoS queue 实现。
2. 合并真实源端 fanout/speculation control。
3. 合并新的 speculative-pressure 信号。
4. 在相同 reward、action 和 workload 下重新运行 Controller ablation。
