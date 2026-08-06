# Pcrit / Score Shadow Scorer v1

## 目的与边界

Pcrit / Score 位于 Dynamic DAG、Workflow Hint Collector 与未来 Traffic Classifier / QoS
队列之间，用来回答：当前 active flow 对 workflow 最终答案和完成时间有多重要？

本版本只做 `shadow` 观测：

- 默认关闭；
- 计算并输出分数，但不修改 `Policy.flow_weight()`；
- 不改变 Controller state/action、Safety Guard、fanout、reward 或 Q-table；
- 不实现 Traffic Classifier，也不声称端到端 latency 或 quality 收益。

## 论文定义与实现选择

论文明确给出的组合公式为：

$$
Score(f)=
\frac{P_{crit}(f)\cdot Cost_{Delay}(f)}
{\epsilon+Size(f)}
\cdot Fanout(f)
+Age(f)-SpecPenalty(f)
$$

论文还说明 `Pcrit` 来自 DAG position、deadline Slack 和 historical selection rate，但没有
公开具体权重、归一化方法、平滑系数或分类阈值。因此，下列内容属于当前可审计的工程候选，
不是论文未公开参数的精确复现：

```text
Pcrit = 0.45 * structural_prior
      + 0.35 * urgency
      + 0.20 * history_probability
```

- `structural_prior`：优先使用 active DAG 中是否阻塞 final/Judge、hard child 和 downstream
  hard steps；只有找不到图节点时才使用保守的 request-type fallback。
- `urgency`：复用现有 work-and-queue-aware Slack 连续值，并使用有界 logistic 映射；不按
  tight/normal/loose 三档硬切分。
- `history_probability`：optional flow 按
  `template + request_type + dependency_role + optional_rank` 聚合，使用 Beta(1,1) 平滑；
  required flow 使用 1.0。
- `CostDelay`：由当前 urgency、直接 hard blocking 和 hard downstream 数构成。
- `Fanout = 1 + log(1 + downstream_total_reachable)`，避免叶子节点乘数为 0。
- `Size = remaining_size / 16`，保留 `epsilon=0.25` 防止除零。
- `Age = 0.15 * min(age / 25, 2)`。
- `SpecPenalty = 0.40 * speculation_level * (1 - history_probability)`；required flow 为 0。

所有权重和尺度都由 `CriticalityConfig` 管理，并写入 metadata。除了默认 `balanced`，还提供
`structure_heavy`、`urgency_heavy` 和 `no_cost_urgency` 三个预注册敏感性候选。最后一个候选
只用于检查 Slack 同时进入 `Pcrit` 和 `CostDelay` 时是否被重复放大。

## 代码结构

```text
specnet_agent_experiments/
├── criticality_scoring.py       # 数据模型、DAG 特征、Pcrit 与论文 Score 公式
├── criticality_history.py       # 完成边界后的 Beta 平滑采用历史
├── criticality_preflight.py     # 四类动态 DAG 的只读 shadow preflight
├── test_criticality_scoring.py  # 公式、回放、防泄漏和 on/off 回归
└── specnet_agent_experiment.py  # off/shadow 开关、桥接和输出
```

评分输出保留每个 component，而不是只写一个总分。`affects_policy=false` 同时写入每条记录、
summary 和 model metadata。

## 无未来信息泄漏

`SelectionHistory` 没有任意时刻直接追加标签的接口。Judge 的 selected/unselected 结果只有在
当前 workflow 已完成并停止参与评分后，才能通过 `record_finalized_workflow()` 原子加入历史。

因此：

- 当前 workflow 的未来 Judge 结果不能影响它自己的早期分数；
- 同一时刻的评分只读取当时已经存在的 DAG 节点和状态；
- selected/unselected 的 AUC 只在运行结束后作为离线诊断计算，不回流到评分输入；
- history 当前在每个 Simulator run 内从 Beta(1,1) 冷启动，不跨 run 持久化。

## 使用方法

主模拟器默认仍为 `off`。只给 SpecNet learned policy 记录 shadow score：

```bash
python3 specnet_agent_experiments/specnet_agent_experiment.py \
  --criticality-scoring shadow \
  --criticality-profile balanced \
  --criticality-score-epoch 5 \
  --criticality-policies specnet_agent \
  --output-dir outputs/criticality_shadow
```

`--criticality-policies all` 可记录所有 baseline，但输出量和运行开销会明显增加。新 flow 总会
在首次出现时评分；存活 flow 按 `--criticality-score-epoch` 周期重新评分，以更新 Slack、
remaining size 和 Age。

输出：

```text
criticality_scores.jsonl
criticality_summary.json
specnet_agent_model.json  # 增加 criticality_scoring metadata
```

`off` 模式不创建 scorer/history，不增加上述文件或历史 summary 字段。

## Dynamic DAG Preflight

运行默认 balanced profile 的 12 个 fixture/capacity 组合：

```bash
python3 specnet_agent_experiments/criticality_preflight.py \
  --profile balanced \
  --score-epoch 5 \
  --output-dir outputs/pcrit_score_shadow_preflight/balanced
```

本轮对四个 profile、四类 fixture、32/16/8 三档容量共运行 48 个组合，结果为：

- 全部分数为有限值，Collector validation error 为 0；
- RAG supplemental 的 `retrieval:1` 新增后立即进入评分；
- Coding retry 的 Tool attempt 1 继续进入评分；
- Judge pruning 的 A/B/C 都在决策前评分，A 排名高于两个 200-unit 候选，B/C 被剪枝后停止
  评分；
- Parallel join 的 Retrieval、Tool、LLM branch 都识别出直接 hard child；
- shadow 不改变四类 fixture 的完成时间、flow 数、retry 或 cancellation；
- 三个敏感性候选相对 balanced 的 30 个可比较 flow pair 排序和 12 个多-flow 时刻 top-flow
  均保持一致；当前 fixture 上没有发现小范围权重变化导致全面翻转。

本地 ignored 输出位于 `outputs/pcrit_score_shadow_preflight/`，不进入 Git。

另外运行了 10-workflow heavy FIFO shadow smoke。balanced profile 对 proxy Judge 最终采用
标签的 first-observation pairwise AUC 为 0.646，selected optional 的平均首分高于
unselected optional。四个 profile 的 AUC 范围为 0.627–0.671。这个结果说明当前分数已有
弱到中等的排序信号，但区分度还不强；由于样本很小、Judge 本身也是 proxy，不能据此确定
最终权重或声称真实语义关键性已经解决。

## Simulator 等价性与开销

单元测试分别用 rule policy 和不训练的 SpecNet Controller 做相同 seed 的 off/shadow 配对，
确认：

- workflow/flow 生命周期、action、latency、deadline miss、quality、waste 和 path 统计完全
  一致；
- Controller action counter、Q-table 和访问计数完全一致；
- shadow 唯一新增 criticality records、summary 和 metadata。

60 个 heavy workflow、rule-balanced、epoch=5 的本机微基准重复 8 次：

| 模式 | 中位运行时间 | 评分记录 | flow |
|---|---:|---:|---:|
| off | 0.0281 s | 0 | 629 |
| shadow | 0.1379 s | 3,764 | 629 |

绝对增加约 0.11 秒；因为原模拟器本身非常快，相对时间约为 4.9 倍。shadow 只建议在需要采样
的 evaluation policy 上启用，不应无条件覆盖所有训练 episode 和 baseline。该结果是本机小型
实现开销，不代表真实系统 dataplane 开销。

### V3 联合回归

在合并冻结数据 V3 的最新主线后，使用相同 `trace_driven_v3_candidate` workload 分别运行
Collector record + Pcrit off 和 Collector record + Pcrit shadow。两组均使用 3 个训练
episode、三档负载、每档 1 个 evaluation run、最多 30 个 workflow。

- `action_counts.csv`、`raw_action_counts.csv`、`summary_by_run.csv`、
  `summary_aggregate.csv`、`workflow_results.csv`、Collector JSONL/summary 等 10 个既有
  输出文件逐字节一致；
- shadow 共覆盖 3 个 run、782 条 flow，生成 13,168 条评分记录，3/3 run 全部分数有限；
- 只有 shadow 侧新增 criticality JSONL/summary 和 model metadata；
- 该 smoke 证明 V3、Collector、动态 DAG 基础模块和 Pcrit/Score 可以在同一最新代码基线
  中运行，不用于解释参数优劣或端到端性能收益。

本地 ignored 输出位于 `outputs/pcrit_v3_combined_smoke/`，不进入 Git。

## 当前限制

- 默认固定 workflow adapter 只在步骤实际创建时暴露节点，早期 flow 看不到尚未创建的未来
  DAG；动态 DAG fixture 的结构信号更完整。
- 当前历史来自同一 run 中已经结束的 workflow；还没有冻结的独立训练历史或真实 Judge
  selection 日志。
- `CostDelay`、归一化尺度和 Pcrit 权重是候选实现，需要在更真实的动态 trace 上校准。
- Score 可以为负数，这是 `SpecPenalty` 大于前方收益项时的正常结果，不代表 NaN 或错误。
- 当前没有把 Score 映射到论文中的 Traffic Class，也没有接入 Q0-Q3 或真实 telemetry。
- selected/unselected 离线排序只评估当前 proxy Judge 逻辑，不能当作真实语义关键性证据。

## 测试

```bash
python3 -m py_compile \
  specnet_agent_experiments/criticality_history.py \
  specnet_agent_experiments/criticality_scoring.py \
  specnet_agent_experiments/criticality_preflight.py \
  specnet_agent_experiments/specnet_agent_experiment.py

python3 -m unittest discover -s specnet_agent_experiments -p 'test_*.py'
```

合并数据 V3 后，当前实验目录共 127 项测试通过，其中 10 项直接覆盖 Pcrit/Score。v1.0 和 v1.1 Collector
replay 产生相同结构特征，size=0 不会产生 NaN/Inf，且 history 更新边界有独立测试。
