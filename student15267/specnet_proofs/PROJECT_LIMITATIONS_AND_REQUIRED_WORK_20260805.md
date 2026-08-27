# 当前项目的限制与必须攻克的问题

更新时间：2026-08-05

## 已知但暂时无法克服的限制

1. **资源成本尚不能逐场景保证。** `TTL=1536` 的平均 link-utilization 增量为 `+0.052574`，低于当前均值门 `+0.08`；但最坏单 cell 为 `+0.181218`。因此现阶段只能说明“平均资源可接受”，不能证明每个场景都满足资源预算。
2. **结论仍局限于模拟器。** epoch、deadline、link utilization 与 retained-branch quality 都是模型内指标，尚不能直接映射为真实网络的毫秒延迟、带宽费用、能耗或任务正确率。
3. **有限实验覆盖不能构成普适保证。** `81` 个独立 cell、`4,613` 条 workflow 的 confirmation 只证明预定义分布内的稳健性；对更极端拥塞、突发流量、不同拓扑和多租户竞争尚无保证。
4. **TTL 边界不是理论最优。** `1536` 仅是本轮候选表中最小的通过点，不能称为全局最小 TTL，更不能作为线上 SLO。
5. **公平性与长期代价未被完整衡量。** 当前没有 tenant 标签、总 bytes、能耗和长期 backlog 指标；background 最终被服务不等于不同任务或租户获得公平服务。

## 三项变量中的负结果：需要区分版本

**当前可引用的因子化三变量控制器没有出现负效果。** 在冻结的 `congestion`、`slack`、`active speculative backlog` 三项机制中，移除任一变量都会使其预定义主指标变差：拥塞变量对应 p99 latency `+33.6455`，slack 对应 normalized latency `+0.12629`，backlog 对应 waste `+5.4317`；三个 95% CI 均为正。

但历史实验中有两项必须保留的负结果：

1. **原始 `spec_pressure`（speculative ratio）是负效果。** 移除它后，预注册的 waste 指标反而降低 `-3.0064`，95% CI 为 `[-4.2120, -1.8489]`。因此原始 pressure 定义不支持“它能降低 waste”的主张；后来改用 `active speculative backlog`，这是一个新定义、新假设，不能回填为原实验成功。
2. **共享风险分数规则中的 broad slack 效应为负。** 在高功效复核中，移除 slack 的 broad normalized-latency delta 为 `-0.000865`，95% CI `[-0.001108, -0.000621]`，说明该规则下 slack 的 broad 主效应不成立。其原因是共享阈值会被 congestion/backlog 的动作掩盖；这也是后来改为“slack 独立控制 deadline-aware critical-flow weight”的直接动机。

因此，正确表述是：**当前因子化机制的三项变量都有效；原始 pressure 定义与早期共享规则的 broad slack 检验曾出现负效果，已被保留并用于指导机制重构。**

## 当前三项变量：消融预期与实际影响

以下结果来自当前可引用的因子化控制器的独立 confirmation（`81` 场景 × `5` runs）。差值定义为“消融策略减完整策略”；对各自主指标，**正值表示去掉变量后变差**。

| 变量与作用路径 | 消融前预期影响 | 消融后的实际影响 | 质量变化（Full → Ablation） |
|---|---|---|---|
| `congestion`：全局关键/可选流权重 | 看不到拥塞时，系统不会在全局队列竞争升高时保护关键流，high-congestion p99 latency 应上升。 | p99 latency `+33.6455`，95% CI `[+30.9269, +36.3372]`；非联合切片 `+32.2281`。预期成立，且效应最大。 | `0.9835 → 0.9836`，基本不变。 |
| `slack`：tight workflow 的 deadline-aware critical-flow boost | 看不到时间余量时，临近 deadline 的 workflow 不会得到额外关键流保护，tight-slack normalized latency 应上升。 | normalized latency `+0.1263`，95% CI `[+0.1151, +0.1373]`；非联合切片 `+0.0907`。预期成立。 | `0.9881 → 0.9879`，基本不变。 |
| `active speculative backlog`：source admission（`full/recovery`） | 看不到在途 speculative debt 时，来源端会在高风险阶段继续过度 admission，waste 应上升。 | waste `+5.4317`，95% CI `[+5.2615, +5.6066]`；非联合切片 `+5.6725`。预期成立。 | `0.9834 → 1.0000`；完整策略为降低 waste 牺牲了 `0.0166` quality，但两侧仍满足预设 `≥0.98` 下界。 |

简要解释：前两项是**调度信息**，实际没有显著质量代价；backlog 是**准入信息**，实际表现出明确的 waste-quality operating-point 取舍。因而不能把“三项有效”简化为“没有代价”，只能说它们在预先设定的质量安全门内，对各自目标指标有独立贡献。

## 下一阶段必须克服的缺陷

1. **将资源门从均值升级为硬约束。** 对每个 cell、p95/p99 和峰值 utilization 设置预算，同时记录 total bytes 与能耗代理；任何一项超限即拒绝该参数点。
2. **做压力与分布外验证。** 新增 burst、长时间高拥塞、异构 deadline、拓扑变化和多租户竞争场景，并保留独立 validation/confirmation 分离。
3. **建立真实可解释指标。** 用端到端完成时间、有效吞吐、真实任务成功率或更贴近任务价值的 quality 指标，替换或补充 retained-branch proxy。
4. **寻找更稳健的 TTL/调度规则。** 在资源硬门下搜索 TTL、空闲窗口阈值和 earliest-expiry 规则；目标不是继续压低 TTL 数字，而是在所有硬门下取得更小的过期边界和更低尾部资源成本。
5. **补充理论与工程边界。** 明确该机制仅在“foreground 空闲时服务 deferred debt”的语义下保持前台 parity；进一步给出可证明的不干扰条件，或在原型系统中复现关键结果。

## 当前可对外表述

本项目已验证一个可复现的模拟器内可行点：deadline-aware earliest-expiry 在 `TTL=1536` 的独立 confirmation 中实现零 expiry、前台轨迹无差异和全部 workflow floor 通过。该结果尚不等价于逐场景资源受控、真实网络收益或理论最优；以上缺陷是后续实验与论文必须逐项补齐的证据链。
