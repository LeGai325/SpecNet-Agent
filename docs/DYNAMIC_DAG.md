# 动态 DAG 执行器

## 目的与边界

动态 DAG 执行器把 workflow 从固定的
`Planner -> Branches -> LLM -> Judge` 模板扩展为运行过程中可以新增、解锁、失败重试和
剪枝的有向无环图。它位于确定性的 Planner/Judge rule 或未来 Agent Runtime 与网络 Flow
之间：

```text
Planner / Judge policy / trace adapter
                 ↓
          Dynamic DAG Engine
                 ↓
     ready step <-> Simulator Flow
                 ↓
       Workflow Hint Collector
```

执行器负责图和生命周期的正确性，不负责根据 prompt 或回答内容做语义决策。当前
Planner/Judge 行为来自可审计的确定性 fixture，不能称为真实 Agent trace 回放。

## 代码结构

```text
specnet_agent_experiments/
├── dynamic_dag.py             # 数据模型、状态机和 Flow bridge
├── dynamic_dag_fixtures.py    # 不依赖网络调度器的四类确定性 fixture
├── dynamic_dag_preflight.py   # 使用现有 Simulator Flow/调度器的网络 preflight
└── test_dynamic_dag.py        # 核心、fixture、bridge 和容量回归测试
```

默认实验入口和固定 workflow 状态机没有被替换。新增代码通过独立模块接入，因此历史 CLI、
reward、Controller、Slack、ACTION_CONFIG、scheduler 和输出保持不变。

## 核心数据模型

- `StepSpec`：step ID、parents、依赖类型、request type、工作量、推测等级、retry limit
  和来源。
- `StepRuntime`：state、attempt、flow binding、各阶段时间、失败次数、Judge 采用状态和
  取消原因。
- `WorkflowGraph`：steps、children、确定性 ready queue、workflow 状态、统一时钟和
  `graph_version`。
- `FlowBinding`：外部 flow ID 到 workflow、step 和 attempt 的不可变映射。
- `FlowCancellation`：DAG 剪枝后返回给网络运行时的 flow 取消请求。

当前通过 `speculation_level == 0` 表示不可由 Judge 普通剪枝的必要步骤；大于 0 的步骤可
作为 optional/speculative 子图。这个约定与 Collector v1.1 保持一致。

## 生命周期与依赖

步骤生命周期：

```text
created -> ready -> running -> completed
                            |-> failed -> retried -> ready
                            |-> cancelled
```

依赖语义：

- `hard_dependency`：所有 hard parents 完成后才会进入 ready。
- `optional_evidence`：记录可能被使用的证据关系，但不阻塞 ready。
- `control_trigger`：记录 Planner/Judge 等控制步骤触发新步骤，不阻塞 ready。

新增 step 只允许引用已经注册的 parent。执行器拒绝重复 step、缺失 parent、自依赖、环、
非法状态转换、时间倒退、重复 flow binding 和超过 retry limit 的重试。

## 主要接口

`DynamicDAGEngine` 提供：

```text
register_workflow()
add_step()
ready_steps()
start_step()
complete_step() / complete_flow()
fail_step() / fail_flow()
retry_step()
cancel_step() / cancel_flow()
select_step()
prune_subgraph()
snapshot()
validate_workflow()
finalize_workflow()
```

`DynamicDAGFlowBridge` 通过两个 callback 与外部网络运行时解耦：

- `create_flow(workflow_id, step)`：为 ready step 创建 Flow 并返回 ID。
- `cancel_flow(flow_id, reason)`：Judge 剪枝运行中 step 时同步停止 Flow。

Flow 完成或失败后，bridge 将结果反向写回 DAG；hard dependencies 满足后，后继 step 会被
解锁并生成新 Flow。

## 剪枝安全规则

`prune_subgraph()` 只取消 optional root 及其独占 optional descendants：

- 必要步骤不能被普通 Judge pruning 删除；
- 如果图外仍有 active child 通过 hard dependency 依赖待剪枝步骤，则拒绝剪枝；
- 已 running 的 optional step 返回 `FlowCancellation`；
- Collector 同步记录 `cancelled`；
- workflow timeout 仍可通过显式 `cancel_step()` 取消必要步骤，不受普通 Judge 规则限制。

这套规则优先暴露非法图或不完整的 pruning policy，避免静默制造永远无法解锁的步骤。

## 确定性 Fixture

当前包含四类功能场景：

1. `rag_supplemental`：EvidenceCheck 后在线新增第二个 Retrieval。
2. `coding_retry`：Tool 第一次逻辑失败，使用相同 step ID 和递增 attempt ID 重试。
3. `judge_pruning`：Judge 采用已完成的 A，取消仍在运行的 B/C。
4. `parallel_join`：Retrieval、Tool、LLM branch 全部完成后才解锁 Aggregator。

基础 fixture 不模拟网络，只验证 DAG 事件序列：

```bash
python3 specnet_agent_experiments/dynamic_dag_fixtures.py
```

网络 preflight 使用现有 `Flow`、`Simulator.new_flow()` 和 weighted capacity scheduler，
分别在 32、16、8 三档容量运行四类场景：

```bash
python3 specnet_agent_experiments/dynamic_dag_preflight.py \
  --output-dir outputs/dynamic_dag_preflight_v1_1
```

输出：

```text
dynamic_dag_preflight_summary.json
dynamic_dag_preflight_snapshots.json
dynamic_dag_preflight_events.jsonl
```

## v1 Preflight 结果

四类场景乘三档容量共 12 个组合全部完成，Collector validation error 均为 0。

| Fixture | 32 容量 | 16 容量 | 8 容量 | 关键行为 |
|---|---:|---:|---:|---|
| RAG supplemental | 8 | 11 | 20 | EvidenceCheck 后新增 Retrieval:1 |
| Coding retry | 9 | 12 | 23 | 每组一次失败、一次 retry |
| Judge pruning | 3 | 3 | 4 | 每组取消两个运行中 Flow |
| Parallel join | 7 | 11 | 19 | 三个 hard parent 完成后解锁 |

表中数值是确定性 preflight 完成时间，只用于功能与调度联动检查，不是论文性能结果。容量
降低时 RAG、Coding 和 Parallel 的完成时间正常增加；Judge pruning 因短关键分支很快完成
并取消两个大分支，对容量不敏感，符合该 fixture 的设计目的。

## 测试

单独运行：

```bash
python3 -m unittest discover -s specnet_agent_experiments -p 'test_dynamic_dag.py'
```

完整实验模块回归：

```bash
python3 -m unittest discover -s specnet_agent_experiments -p 'test_*.py'
```

测试覆盖 dependency unlock、join、Flow 双向同步、失败重试、retry limit、Judge 采用、
剪枝保护、snapshot、finalize、四类 fixture 和三档容量 preflight。

## Collector v1.1 回放结果

动态 preflight 共产生 285 条 Collector 事件。v1.0 replay 审计的 12 个 workflow 均能
重建，和 Engine snapshot 公共字段为 0 mismatch；但 12 个 failure/retry/cancel 事件都没有
原因。Collector v1.1 因此只增加结构化 `reason`，并提供兼容 v1.0/v1.1 的 active DAG
replay 和 diagnostics。

v1.1 重跑仍为 0 validation error、0 replay error、0 snapshot mismatch，12 个 reasoned
event 全部有原因。JSONL 从 131,024 增至 135,179 bytes，增加约 3.2%。审计也确认当前
`sequence` 已足够重建 active DAG，因此没有把 `graph_version` 或完整 snapshot 复制到每条
事件；attempt-level size 和 workflow final event 继续等待真实消费者需求。

## 当前限制与下一步

- 动态 DAG v1 已完成正式执行语义、Flow bridge、Collector 联动和确定性 preflight。
- 现有大规模 RL 实验仍默认使用固定 workflow；尚未把随机/trace workload 改造成包含真实
  parents、retry 和 pruning 的动态 workload。
- fixture rule 不是语义 Planner/Judge，不能用于宣称真实 Agent Runtime 收益。
- Pcrit/Score shadow v1 已能读取当前 snapshot 并评分，但不改变 QoS queue、Guard 或
  Controller action；详见 `PCRIT_SCORE.md`。

下一步是在包含真实动态图事件的 trace 上校准评分，再单独设计 Traffic Class 映射。
