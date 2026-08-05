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

### Workload profile

`--workload-profile` 支持 `synthetic`、`trace_driven_v1`、
`trace_driven_v1_1`、`trace_driven_v2` 和 `trace_driven_v3_candidate`。默认
`synthetic` 行为保持不变。

V2-A 使用 BurstGPT arrival windows，并按冻结的 75/25 比例从 TraceLab 和 RAGPulse
抽取记录。TraceLab 映射为固定 `coding` 模板，RAGPulse 映射为固定 `rag_qa` 模板；
输出保留数据来源、phase split、脱敏 record ID 和 mapping version。真实 trace 没有的
deadline、网络状态和 action 反事实仍由模拟器产生。

V3 candidate 保持 BurstGPT arrival 和 25% RAG 场景份额，将 75% coding 份额均分给
TraceLab 与 SWE-chat。SWE-chat 先按 content hash 去重，再按 repo-user 连通分量做 split；
只使用清洗后可配对的 tool interval，不把原始 session duration 当连续服务时间。V3 仍是
固定模板候选，不是动态 DAG 回放。

### Workflow Hint Collector

可选 `--workflow-hints record` 在 evaluation 中以 shadow mode 记录 workflow DAG
元数据。Collector 位于 `specnet_agent_experiments/workflow_hints.py`，记录
`workflow_id`、`step_id`、parents、依赖类型、request type、deadline、size、
speculation level 及 created/ready/started/completed/failed/retried/cancelled/selected
事件。默认 `off` 不实例化 Collector，也不改变历史输出。

当前 adapter 将固定 `Planner -> Branches -> LLM -> Judge` 结构转换为 hints，且明确
标记 `fixed_template_adapter`。Collector API 可以记录运行中新增和剪枝事件，但动态
DAG 的创建、解锁、失败重试和 Judge 剪枝由独立 `dynamic_dag.py` runtime 执行器负责。
当前执行器已经通过四类确定性 fixture 和现有网络 Flow 调度器的三档容量 preflight，
但默认实验仍保留固定 workflow。Collector 详细契约见 `docs/WORKFLOW_HINT_COLLECTOR.md`，
执行器说明见 `docs/DYNAMIC_DAG.md`。

### Dynamic DAG Runtime

`DynamicDAGEngine` 维护 `StepSpec`、`StepRuntime`、parent/child 索引、ready queue、
attempt 和 graph version。hard parents 完成后自动解锁 child；optional evidence 与
control trigger 只记录语义，不阻塞执行。`DynamicDAGFlowBridge` 将 ready step 映射到
外部 Flow，并把 Flow 完成、失败或取消反向同步到 DAG。

Planner/Judge policy 与 DAG Engine 解耦。当前 RAG supplemental、Coding retry、Judge
pruning 和 Parallel join 都是确定性功能 fixture，不读取 prompt 或响应内容，也不作为
真实 Agent benchmark。默认固定状态机没有被替换，避免在缺少真实动态图数据时改变历史
实验语义。

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

Simulator 支持三个网络模型：

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

当前多路径实现中的 congestion、Slack、speculative pressure 和 background pressure
仍从全部 active flow 计算；只有显式选择的新 variant 会补充紧凑路径状态。

`path_aware_quality` controller variant 额外提供紧凑的逐路径决策状态：
`slack`、最拥挤 required path pressure、跨路径 optional headroom 和全局
speculative pressure。required pressure 与 headroom 都按 12 个调度周期的逐路径容量
归一化；在单瓶颈模式下会确定性退化为一个 shared path。旧 controller variant
及其状态键保持不变。

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
raw_action_counts.csv
trained_agents.csv
lambda_updates.csv
specnet_agent_model.json
path_results.csv
path_borrowing_results.csv
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

### 质量约束与 Safety Guard

`Q_target=0.95` 是预先规定的服务级平均目标，`Q_hard=0.90` 是单 workflow
硬下限。validation 只能选择超参数和 checkpoint，不能改变目标。Bandit Q 值仍按
workflow 完成结果更新；拉格朗日乘子只在 light、medium、heavy 各完成一个 episode
后更新一次：

$$
g_k=\max_{\ell}\left(Q_{\mathrm{target}}-\overline{Q}_{k,\ell}\right)
$$

$$
\lambda_{k+1}=\operatorname{clip}
\left(\lambda_k+\eta_\lambda g_k,0,\lambda_{\max}\right)
$$

Safety Guard 在动作执行前检查 expected optional utility 对应的 predicted quality。
低于 `Q_hard` 的 raw action 被替换为可行动作；若不存在可行动作，则选择 predicted
quality 最高的动作并记录 `quality_constraint_infeasible`。实际完成质量低于硬下限
记为不可由未来 workflow 补偿的 `quality_violation`，不称为 quality debt。

## 测试边界

- `test_slack_estimation.py`：Slack 公式、queue diagnostics 和配置回归。
- `test_training_stability.py`：训练 schedule、checkpoint 和 validation 配置。
- `test_slack_calibration.py`：离线 calibration 分组和 role-aware 估算。
- `test_multi_path.py`：路径映射、容量隔离、调度权重、默认兼容性和输出契约。
- `test_dynamic_dag.py`：在线增长、依赖解锁、Flow bridge、retry、剪枝、四类 fixture 和
  三档容量 preflight。

新增模块至少应包含一个针对新行为的测试，以及一个确认旧默认行为不变的回归测试。
