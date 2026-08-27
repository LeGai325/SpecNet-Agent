# Trace V4 部署优化迭代报告

更新日期：2026-08-06  
性质：这是 V3 三信号证明之后独立开展的部署代价优化。它不改写 V3 三项消融的结论，也不把不同 profile 的统计结果合并。

## 一句话结论

当前最稳妥的候选是 **V4-minQ-96**：先为每个 workflow 精确选择达到预测 `quality >= 0.95` 的最小 optional 分支集合，再使用 `96x` 完成优先级和原有 tight 补偿。它在新的 V3 validation 确认及未参与 V4 选择的 V1/V2 test 上均通过逐 cell 质量门，同时明显低于旧 V3 `100x` 全 optional 护栏的资源代价。

它是目前的**部署候选**，不是“已保证生产最优”。原因是 V3 test 已被早期探索候选使用，V4-minQ-96 没有剩余的干净 V3 test holdout；并且 simulator 的质量仍是 retained-branch proxy，尚非真实任务正确率。

## 机制改动

旧 V3 机制对每条已选择 optional 分支施加固定 `100x` 优先级。它保证质量，但会让所有分支持续抢占容量。

V4 将问题拆开：

1. **最小质量准入。** 对 optional 分支枚举不超过 judge retain limit 的子集，选择预测 utility 足以达到 `quality=0.95` 且总字节最小的一组，而不是先启动所有可选分支。
2. **完成优先级。** 只有这个质量必要集合得到 completion priority；原有 congestion/slack 规则和 tight-workflow 补偿保持不变。
3. **严格部署门。** 除平均质量外，要求每个 `scenario x run` cell 的 workflow quality 达标率均为 `1.0`，再检查 p99、deadline miss、served bytes、link utilization 和 waste。

实现位于 [`trace_deployment_v4_study.py`](trace_deployment_v4_study.py)。它在不修改 trace upstream 快照的前提下，以派生 simulator 实现最小集合准入。

## 迭代过程

| 候选 | 筛选/确认结论 | 应保留的解释 |
|---|---|---|
| `32x, urgency=0` | 聚合质量门通过，但 V3 test 探索中最差 cell 仅 `0.7692` 达标 | 不能用均值质量代替逐 cell 约束；该 test 仅保留为探索性诊断 |
| `64x, urgency=0` | 初始 9-cell validation 通过；独立 validation 确认最差 cell `0.8929` | `64x` 在 high-pressure cell 仍不够 |
| `48x, urgency=1.5, margin=0.05` | 动态筛选通过；独立 validation 确认最差 cell `0.9643` | 当前完成窗口估计不足以替代稳定基础预留 |
| **`96x, urgency=0`** | 18-cell validation 筛选与 36-cell 独立 validation 确认均通过 | 当前最佳、可复查的部署候选 |

因此本轮真正被数据支持的创新不是“已成功的动态 reservation”，而是**精确最小质量集合准入**。动态 deadline reservation 仍是下一阶段待改进机制，不能提前写成有效结论。

## 主结果

V3 validation 独立确认使用 18 个平衡场景 x 2 runs，seed 规则为 `2490000 + run*10000 + scenario`。

| 指标 | V3 质量护栏 `100x` | V4-minQ-96 | V4 - V3 |
|---|---:|---:|---:|
| 平均 quality | `0.999505` | `0.975778` | `-0.023727`，仍高于 `0.95` |
| 平均 workflow 达标率 | `1.000000` | `1.000000` | `0.000000` |
| 最差 cell 达标率 | `1.000000` | `1.000000` | `0.000000` |
| p99 latency | `256.505` | `167.391` | `-89.114` (`-34.7%`) |
| deadline miss ratio | `0.337651` | `0.182419` | `-0.155232` |
| link utilization | `0.707863` | `0.586395` | `-0.121468` |
| total served bytes | `8701.22` | `6785.34` | `-1915.88` (`-22.0%`) |
| speculative waste/workflow | `88.945` | `0.000` | `-88.945` |

正式确认产物：[V3 validation confirmation](results/trace_deployment_v4_validation_refinement_confirmation_20260806/)。

## 外部稳健性

V4-minQ-96 的参数仅由 V3 validation 冻结。随后在 V1/V2 的 test splits 上各运行 18 场景 x 2 runs；两份 profile 的每个 cell quality target ratio 都是 `1.0`。

| Profile test | V3 p99 -> V4 p99 | served bytes 变化 | utilization 变化 | waste/workflow 变化 |
|---|---:|---:|---:|---:|
| V1 | `242.579 -> 160.311` | `-1668.86` (`-20.6%`) | `-0.107255` | `-80.096 -> 0.000` |
| V2 | `213.678 -> 124.502` | `-1620.26` (`-20.8%`) | `-0.111033` | `-75.300 -> 0.000` |

产物：[V1 external test](results/trace_deployment_v4_v1_external_confirmation_20260806/)；[V2 external test](results/trace_deployment_v4_v2_external_confirmation_20260806/)。这构成外部 transfer 支持，但不能替代一个未被任何 V4 探索使用的 V3 holdout。

## 当前证据边界

- 早期 `32x` 已读取 V3 test，因此不得将之后的 `96x` 写成在该 split 上的无污染 confirmatory result。
- `quality` 是完成并被 judge retain 的 optional utility proxy，不是人工或端到端任务正确率。
- 当前资源门使用总 served bytes 和平均 link utilization；尚缺 p95/p99 path utilization、NIC/GPU 实测能耗、background service floor 与 tenant/template 公平性门。
- `96x` 仍接近旧 `100x`。节省来自不启动不必要的 optional 分支，而不是证明固定优先级本身已经最优。

## 下一步

1. 获取新的 V3 holdout 或新 trace slice，只评估冻结的 V4-minQ-96，不再调节任何参数。
2. 为每个 path 记录 utilization 分位数、background service floor、per-template 最差质量和 bytes；它们应成为部署硬门。
3. 改进 dynamic reservation：用实际分支完成 ETA 和虚拟队列预算校准，而不是当前的启发式 branch barrier；在新的 validation 上重新筛选后才能替代 `96x`。
4. 将 retained-branch proxy 与端到端任务成功率、真实网络/GPU telemetry 对齐，评估 simulator 映射是否有效。

