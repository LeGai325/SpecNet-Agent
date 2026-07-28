# 代码架构与模块集成说明

## 当前形态

项目目前以一个主实验脚本作为集成入口：

```text
specnet_agent_experiments/specnet_agent_experiment.py
```

为了减少多人合并冲突，现阶段不建议在合并 QoS、源端控制等功能的同时拆分整个脚本。
模块合并稳定后，可以再单独进行 package 化重构。

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

Simulator 支持两个网络模型：

- `single_bottleneck`：默认模型，所有 flow 共享一条容量为 16 的 `shared` 路径。
- `service_paths`：`planner/judge -> control`、
  `retrieval/tool/storage/background -> data`、`llm -> model`，三条路径容量均为 16。
- `service_paths_borrowing`：先保证上述每条路径各自最多使用 16，再把当前周期未使用的
  容量汇总，按现有 flow 权重分给仍未完成的 flow。

多路径模式在每条路径内独立执行同一套 weighted max-min 分配。它不模拟 source、
destination、逐跳链路、共享核心或 ECMP。`Flow.path_id` 仅表示逻辑容量池。

borrowing 模式是工作守恒的容量共享，不改变 `Flow.path_id`。`path_results.csv` 继续记录
物理路径服务量；`path_borrowing_results.csv` 额外记录保障容量服务量、借出量、借入量和
借用后的剩余容量。

当前多路径实现有意只修改调度。congestion、Slack、speculative pressure 和 background
pressure 仍从全部 active flow 计算；path-aware Controller state 留作后续独立工作。

合并真实 QoS 时，建议：

1. 保留现有 weighted allocation 作为 baseline。
2. 通过新配置选择 scheduler，避免直接覆盖默认行为。
3. 明确 queue mapping、调度周期和饥饿保护。
4. 保持相同 workload 和 Controller action，先做 paired regression。

## 源端控制

`Simulator.spawn_branches()` 根据 `ACTION_CONFIG` 决定 fanout、额外 branch 和后台流量。
当前行为是模拟式 fanout 控制。

Quality 在 workflow 完成时根据实际完成并被 judge 采用的 optional branch utility
计算，不再在动作生成时直接固定。完成且被采用的 speculative bytes 记为 useful；
未采用、被取消或部分失效的 speculative bytes 才记为 waste。Branch utility 由
template、branch rank 和 service type 确定性生成，不消耗原 workload 随机序列。

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
path_results.csv
```

`path_results.csv` 记录逐路径容量、served、利用率和平均队列压力。历史
`link_utilization` 在多路径模式下使用所有路径的总 served/总容量；`avg_queue_pressure`
仍保留原来的全局 active bytes/16 口径。分析脚本位于 `specnet_plotting/`。所有生成文件
放在 `outputs/`，不进入 Git。

### Action 与 background 解耦

`ACTION_CONFIG` 继续定义兼容的 branch fanout。可选 `decoupled` 模式使用
`DECOUPLED_BACKGROUND_SCALE` 独立控制合成后台流量，降低后台 bytes 不再隐式删除
承载质量收益的 optional branch。默认 `legacy` 模式保留原来由同一个 action 同时
改变 fanout 与后台流量的行为。两种模式均不改变 workload、reward、路由和调度权重。

Optional branch admission 在 planner 完成后只执行一次。Simulator 始终保留全部
required branch，并按 `expected_utility / size` 从高到低选择 action 允许数量的
optional branch；密度相同时按原 `branch_index` 确定顺序。当前版本不会在运行中
追加、停止或重新选择 branch。

## 测试边界

- `test_slack_estimation.py`：Slack 公式、queue diagnostics 和配置回归。
- `test_training_stability.py`：训练 schedule、checkpoint 和 validation 配置。
- `test_slack_calibration.py`：离线 calibration 分组和 role-aware 估算。
- `test_multi_path.py`：路径映射、容量隔离、调度权重、默认兼容性和输出契约。

新增模块至少应包含一个针对新行为的测试，以及一个确认旧默认行为不变的回归测试。
