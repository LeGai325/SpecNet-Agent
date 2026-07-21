# 代码架构与模块集成说明

## 当前形态

项目采用 `src` package 结构，历史脚本保留为薄兼容包装：

```text
src/specnet_agent/
├── config.py
├── models.py
├── workload.py
├── policies/
├── simulator.py
├── training.py
├── outputs.py
├── cli/
└── analysis/
```

依赖方向固定为：配置/模型 → workload/策略 → simulator → training/orchestration → analysis。
核心模块不得反向依赖 CLI 或绘图模块。`specnet_agent_experiments/`、
`specnet_plotting/` 和 `tools/` 只负责保持历史命令兼容。

## 工作流模型

每个请求被模拟为：

```text
Planner -> required/speculative branches -> LLM -> Judge -> Complete
```

主要运行时对象：

- `WorkflowSpec`：到达时间、deadline、branch 和各阶段工作量。
- `WorkflowRuntime`：运行阶段、action、decision state、质量和浪费流量。
- `Flow`：单条网络流的类型、角色、剩余工作量和完成状态。
- `Simulator`：到达、flow 创建、链路调度、workflow 状态推进和指标统计。

## Policy 和 Controller

`Policy` 是所有策略的基类，主要扩展点是：

- `decide_action()`：决定 workflow 的 source-control action。
- `flow_weight()`：决定网络调度权重。
- `on_workflow_complete()`：完成后更新学习器。

当前策略包括：

- `FIFOPolicy`
- `StaticPriorityPolicy`
- `CriticalPathOnlyPolicy`
- `RuleBasedFeedbackPolicy`
- `SpecNetAgentBanditPolicy`

Learned Controller 的状态由以下信号组合：

```text
congestion
deadline slack
speculative pressure
```

动作定义在 `ACTION_CONFIG`：

```text
full
moderate
conservative
critical_only
recovery
```

## 网络调度

当前 `Simulator.serve_active_flows()` 使用 weighted max-min 风格的容量分配。
`Policy.flow_weight()` 返回的权重只是 QoS proxy，不是真实硬件队列。

合并真实 QoS 时，建议：

1. 保留现有 weighted allocation 作为 baseline。
2. 通过新配置选择 scheduler，避免直接覆盖默认行为。
3. 明确 queue mapping、调度周期和饥饿保护。
4. 保持相同 workload 和 Controller action，先做 paired regression。

## 源端控制

`Simulator.spawn_branches()` 根据 `ACTION_CONFIG` 决定 fanout、额外 branch 和后台流量。
当前行为是模拟式 fanout 控制。

合并真实源端控制时，应把新的 fanout/top-k/parallel-agent 控制映射集中在这一层，避免
同时修改 reward 或网络 scheduler。

## Slack 和 speculative pressure

- Slack v2 根据 required work、剩余 deadline 和 active queue 估计完成风险。
- Role-aware v2.1 可以通过 `--slack-queue-basis policy_weighted` 启用，但不是默认值。
- `Simulator.speculative_pressure_bucket()` 当前按 active speculative work 比例分桶。

如果其他模块改变了 flow role 或 QoS weight，应重新检查 Slack 和 speculative-pressure
信号的含义，而不是直接复用旧参数。

## 输出与分析

主实验生成：

```text
summary_by_run.csv
summary_aggregate.csv
workflow_results.csv
action_counts.csv
trained_agents.csv
specnet_agent_model.json
```

实现位于 `specnet_agent.analysis`，`specnet_plotting/` 是历史路径包装。实验还会额外写入
`run_manifest.json`，记录解析后的配置、代码版本、seed 矩阵和输出清单；六类历史输出及
schema 不变。所有生成文件放在 `outputs/`，不进入 Git。

## 测试边界

- `test_slack_estimation.py`：Slack 公式、queue diagnostics 和配置回归。
- `test_training_stability.py`：训练 schedule、checkpoint 和 validation 配置。
- `test_slack_calibration.py`：离线 calibration 分组和 role-aware 估算。

标准测试位于 `tests/unit` 与 `tests/integration`，确定性基线位于 `tests/fixtures`。
历史 unittest discovery 命令由兼容测试包装继续支持。新增模块至少应包含一个针对新行为
的测试，以及一个确认旧默认行为不变的回归测试。
