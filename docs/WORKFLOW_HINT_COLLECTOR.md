# Workflow Hint Collector

## 目的与边界

Workflow Hint Collector 位于 Agent Runtime 与后续关键性判断之间，用于记录不包含用户内容的 workflow 结构、步骤生命周期、依赖、deadline、预计网络工作量和推测程度。

Collector 是 shadow-mode 观测模块：它不选择 Controller action，不改变 branch fanout、flow 权重、网络路径、reward 或质量记账。默认关闭时不创建 Collector，也不增加历史输出字段。

```text
Agent Runtime
    ↓
Workflow Hint Collector
    ↓
Pcrit / Score（后续）
    ↓
Traffic Classifier / Guard / QoS（后续）
```

## 启用方式

默认配置：

```text
--workflow-hints off
```

在 evaluation 阶段记录 hints：

```bash
python3 specnet_agent_experiments/specnet_agent_experiment.py \
  --workflow-hints record \
  --output-dir outputs/workflow_hints_smoke \
  --train-episodes 3 \
  --eval-runs 1 \
  --duration 800 \
  --max-workflows 30 \
  --max-time 2500 \
  --loads light,medium,heavy
```

训练和 checkpoint validation 默认不记录 hints，避免为不参与模型选择的结构日志增加大量重复输出。`record` 默认只记录名称为 `specnet_agent` 或以 `specnet_agent_` 开头的目标策略；每个 load、policy 和 run 都会单独记录上下文。

如需同时记录所有 baseline：

```text
--workflow-hint-policies all
```

也可以用逗号指定策略，例如：

```text
--workflow-hint-policies specnet_agent,critical_path_only
```

策略过滤只控制 hint 输出，不改变任何策略是否执行，也不改变原实验 CSV。默认过滤可避免正式实验为每个 baseline 重复输出相似 DAG，显著降低 JSONL 大小。

## 数据契约

当前 schema version 为 `1.1`。每条 JSONL 事件包含：

| 字段 | 含义 |
|---|---|
| `workflow_id` | workflow 标识 |
| `step_id` | workflow 内的逻辑步骤标识 |
| `attempt_id` | 同一步骤失败重试的次数，初始为 0 |
| `parents` | 前置步骤列表 |
| `dependency_kinds` | 每条父依赖的语义 |
| `request_type` | planner、retrieval、tool、llm、judge 等结构化类型 |
| `deadline_hint` | Collector 时钟域中的绝对完成期限 |
| `size_hint` | 预计网络工作量 |
| `size_unit` | 当前模拟器使用 `normalized_work` |
| `speculation_level` | `[0, 1]`，0 表示必要，1 表示完全可选 |
| `event` | 步骤生命周期事件 |
| `reason` | failed/retried/cancelled 的结构化原因；其他事件为空字符串 |
| `timestamp` | Collector 时钟域中的事件时间 |
| `source` | 数据来源或 adapter |

输出采用字段白名单，不提供任意 `payload` 或 metadata 扩展，因此不会写入 prompt、回答文本、工具参数或用户请求内容。

## 生命周期与依赖类型

Collector 支持以下步骤状态：

```text
created -> ready -> running -> completed
                            |-> failed -> retried
                            |-> cancelled
```

`selected` 是独立事件：表示一个已经完成的 optional step 最终被 Judge 采用，不替代步骤执行状态。

v1.1 为以下事件提供固定 reason 枚举：

- `execution_failed`
- `retry_requested`
- `judge_pruned`
- `workflow_timeout`
- `workflow_completed`
- `policy_cancelled`

reason 只解释已经发生的生命周期事件，不允许携带自由文本或内容数据。

依赖分为三类：

- `hard_dependency`：父步骤完成后，子步骤才能 ready。
- `optional_evidence`：父结果可能被使用，但不阻塞子步骤。
- `control_trigger`：Planner、Judge 或其他控制步骤触发新步骤产生。

Collector 支持 workflow 运行中持续注册新步骤。在线模式要求 parent 先注册；离线 trace adapter 可以允许前向引用，但必须在 workflow finalize 前通过缺失 parent 和环检测。

## 当前固定工作流 adapter

当前模拟器仍使用固定结构：

```text
Planner -> selected branches -> LLM -> Judge
```

adapter 的映射为：

| 模拟器对象 | Hint step | 依赖 |
|---|---|---|
| Planner flow | `planner` | 无 |
| Branch flow | `branch:{branch_index}` | `planner` / control trigger |
| LLM flow | `llm` | selected required branches / hard dependency |
| Judge flow | `judge` | `llm` / hard dependency |
| Background flow | `background:{index}` | `planner` / control trigger |

required branch 的 `speculation_level` 为 0；optional branch 和 background 为 1。完成并被 Judge 采用的 optional branch 会记录 `selected`。

所有当前 adapter 记录明确标记为 `source=fixed_template_adapter`。即使 workload 来自 trace 特征映射，也不能把这些记录称为真实动态 DAG 回放。

## 输出

开启 `record` 后新增：

```text
workflow_hints.jsonl
workflow_hint_summary.json
```

`workflow_hints.jsonl` 每行是一条 hint event，同时包含 load、policy、训练 seed、评估 seed、run 和 workload seed 上下文。默认只包含目标 SpecNet 策略；`--workflow-hint-policies all` 可扩展到所有 baseline。

`workflow_hint_summary.json` 包含逐 run 和聚合统计：workflow/step/event 数量、事件类型、
reason、request type、依赖类型、推测等级、selected 数量、workflow 最终状态、来源和校验
错误数。

`specnet_agent_model.json` 仅在 `record` 模式下增加 schema 和 `affects_policy=false` 说明。默认 `off` 不改变旧 model metadata。

## v1.0 兼容与 Active DAG Replay

`workflow_hint_replay.py` 可以读取 v1.0 和 v1.1 事件。v1.0 缺少 reason 时按空字符串读取，
其余字段含义不变。replay 可以在任意 sequence 或 timestamp 重建：

- active、ready、running 和 terminal steps
- parents、children 和依赖类型
- 当前 attempt 和失败次数
- Judge selected 状态
- 最后一个结构化 reason

回放器会产生带 `code/message/sequence/step_id` 的 diagnostics，包括非法状态转换、悬空
依赖、时间倒退、attempt 不一致、DAG 环和 replay/snapshot mismatch。它同样拒绝
prompt、payload、tool args 等内容字段。

审计动态 preflight：

```bash
python3 specnet_agent_experiments/workflow_hint_replay.py \
  --events outputs/dynamic_dag_preflight_v1_1/dynamic_dag_preflight_events.jsonl \
  --snapshots outputs/dynamic_dag_preflight_v1_1/dynamic_dag_preflight_snapshots.json \
  --output outputs/dynamic_dag_preflight_v1_1/workflow_hint_v1_1_replay_audit.json
```

不在每个事件中复制完整 graph snapshot。审计确认 `sequence` 足以重建 step-level active
DAG，因此 v1.1 暂不增加逐事件 `graph_version`。

## 校验与测试

Collector 会拒绝：

- 重复 step
- 缺失 parent
- 自依赖和 DAG 环
- 非法依赖类型
- 非法 deadline、size、timestamp 或 speculation level
- 非法生命周期转换
- hard parent 未完成时进入 ready
- workflow finalize 时仍存在非终止步骤
- 非法或自由文本 event reason
- v1.1 reasoned event 缺少 reason
- replay 中的非法转换、attempt 漂移和 snapshot mismatch

测试文件：

```text
specnet_agent_experiments/test_workflow_hints.py
specnet_agent_experiments/test_workflow_hint_replay.py
```

测试覆盖动态新增步骤、hard/optional/control 依赖、失败重试、Judge 采用、剪枝取消、超时、
reason、v1.0 兼容读取、active DAG replay、隐私字段、固定模板映射、CLI 输出和 Collector
on/off 等价回归。

## 当前限制与下一步

Collector v1.1 已与动态 DAG 执行器和固定模板 adapter 同时集成，但它尚不负责：

- 决定 Planner 何时增加步骤
- 决定 Judge 何时剪枝
- 影响 Traffic Classifier、Guard 或 QoS

当前 v1.1 preflight 的 12 个组合均为 0 validation error、0 replay error 和 0 snapshot
mismatch。Pcrit/Score shadow v1 已作为 Collector 的独立消费者实现，详见
`PCRIT_SCORE.md`；只有外部日志明确包含 parents 或动态事件时，才映射成真实
trace-driven DAG。
