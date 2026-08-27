# SpecNet-Agent `specnet_proofs` 研究进展、结果与优化建议

> 后续三参数独立确认、全部负结果与 2026-07-30 条件效应结论已统一整理到 [`RESEARCH_PROGRESS_20260730.md`](RESEARCH_PROGRESS_20260730.md)。本文件保留为 optimization v4.3 完成时的历史快照。

> 更新日期：2026-07-29  
> 当前主协议：`2026-07-29.optimization-v4.3`  
> 原则：不删减不利场景、不在看过 test 后筛选结果、不放宽质量与尾延迟门槛来美化数字。

## 1. 当前结论

1. **机制层证据比“bandit 已经全面胜出”更牢靠。** 减少源端可选流量与优先服务关键路径都有独立作用，但会伴随质量、延迟和 waste 之间的取舍。
2. **v4.3 首次得到 validation-feasible 的真自适应规则。** 新增的 bounded recovery/moderate family 选出 2-action 规则，validation 平均/worst-load quality 为 `0.9663/0.9565`，状态为 `adaptive_feasible`。
3. **该 robust rule 是当前最接近部署门槛的候选，但仍不可部署。** 在与 v4.2 test 不重叠的新 holdout 上，fair cost 改善 `-1.6954` CI `[-2.4006, -1.1695]`，quality 提高 `+0.0248` CI `[0.0216, 0.0275]`，p99 在 10% 非劣界内；但只通过 `6/9` 项 gate，miss、20% background floor 和 heavy quality `0.9485` 仍失败。
4. **v4.3 没有任何候选通过全部 9 项分层 gate。** 因此当前正确决策是保留 baseline，把 robust rule 作为下一轮研究起点，不宣称已完成部署优化。
5. **继续堆 reward 权重没有解决 miss。** 预先冻结的 `strict_aligned_scheduled` 虽然 fair cost 改善 `-0.9472`，但 miss delta 仍为 `+0.0106`，p99 增加 `8.7139`，说明下一步应转向显式约束学习和模型语义修复。
6. **v4.2 full 没有可引用产物，v4.3 也不应直接进入论文级 full 主张。** smoke 无人过全部 gate；应先修复 quality/waste 语义或冻结新的 constrained 方法。

## 2. 证据与完成状态

| 研究项 | 状态 | 可引用产物 | 主要结论 |
|---|---|---|---|
| 三项证明 full v2 | 完成 | [`results/proof_full_v2_20260719/PROOF_REPORT.md`](results/proof_full_v2_20260719/PROOF_REPORT.md) | H1-C 支持；H1-S 支持但有质量取舍；H1-P 按预注册主指标不支持。 |
| 优化 full v3 | 完成，旧协议 | [`results/optimization_full_v3_20260719/OPTIMIZATION_REPORT.md`](results/optimization_full_v3_20260719/OPTIMIZATION_REPORT.md) | aligned reward 的方向性收益明显，但场景抽样、CI 和 gate 已被 v4.x 升级，不应单独作为最终主张。 |
| Pressure 定义 full | 完成 | [`results/pressure_definition_full_20260722_v3/PRESSURE_DEFINITION_REPORT.md`](results/pressure_definition_full_20260722_v3/PRESSURE_DEFINITION_REPORT.md) | 原始全局 speculative 比例在质量约束后不支持 H1-P；backlog、workflow optional ratio 和 expected waste risk 更值得复核。 |
| 源端/队列机制隔离 | 完成 | [`results/source_control_isolation_full_20260723_v2/SOURCE_CONTROL_ISOLATION_REPORT.md`](results/source_control_isolation_full_20260723_v2/SOURCE_CONTROL_ISOLATION_REPORT.md) | 少生成可选流与保护关键流各有独立贡献。 |
| Per-workflow oracle gap | 完成，需收紧解释 | [`results/oracle_gap_full_20260723/ORACLE_GAP_REPORT.md`](results/oracle_gap_full_20260723/ORACLE_GAP_REPORT.md) | 282 个 workflow 的平均 gap `0.0389`，`82.3%` 为正；但反事实中其他 workflow 只是继续使用冻结控制器，并非动作逐个冻结。 |
| 有限域单调性 v2 | 完成 | [`results/finite_monotonicity_v2_20260729/FINITE_MONOTONICITY_REPORT.md`](results/finite_monotonicity_v2_20260729/FINITE_MONOTONICITY_REPORT.md) | 完整笛卡尔域 `223,587` 例，`0` violations；仅限单瓶颈、work-conserving weighted max-min 和关键流不变假设。 |
| 优化 smoke v4.2 | 完成，已被严格 gate 取代 | [`results/optimization_smoke_v4_2_20260729/OPTIMIZATION_REPORT.md`](results/optimization_smoke_v4_2_20260729/OPTIMIZATION_REPORT.md) | aligned 通过旧 4 项 gate，但 miss/background/worst-load 风险促成 v4.3 升级。 |
| 优化 smoke v4.3 | 完成，当前主证据 | [`results/optimization_smoke_v4_3_20260729/OPTIMIZATION_REPORT.md`](results/optimization_smoke_v4_3_20260729/OPTIMIZATION_REPORT.md) | 新鲜 holdout；bounded robust rule 在 validation 可行，test 通过 6/9 gate；无候选全部通过。 |
| 优化 full v4.2 | 未完成 | `results/optimization_full_v4_2_20260729/` | 未生成 manifest、CSV 或 report，不得引用。 |

`results/full/` 是废弃协议产物，不应在论文或汇报中引用。`results/finite_monotonicity_20260723/` 仍保留修复前的 `819` 例报告，已被 v2 的 `223,587` 例结果取代。

## 3. 前期 full 证据摘要

### 3.1 RQ1：三个状态信号

| 假设 | 预注册主指标 | 消融 - full | 95% CI | 判定 |
|---|---|---:|---:|---|
| H1-C congestion | high-congestion p99 | `+18.8423` | `[10.5700, 26.8353]` | 支持 |
| H1-S slack | tight-slack normalized latency | `+0.5241` | `[0.4792, 0.5692]` | 支持，但 full 用更低 quality 换延迟 |
| H1-P spec pressure | high-pressure waste | `-3.0064` | `[-4.2120, -1.8489]` | 不支持预注册方向 |

H1-P 不支持不等于 pressure 毫无信息：去掉 pressure 后 p99 增加 `70.35`、miss 增加 `0.0783`，同时 quality 降低 `0.0074`。它说明当前 pressure 更像是调节 latency-quality operating point，而不是已证实的 waste-minimization 信号。

### 3.2 RQ2/RQ3：规则对比和可审计性

- Full v2 中 bandit 平均 p99/quality 为 `103.747/0.9393`，global tuned rule 为 `95.874/0.9410`；但严格四目标逐环境支配仅出现 1 次，不能推广为规则或 bandit 普遍胜出。
- 10 个训练 seed 的平均 action agreement 仅 `0.407`，27/27 状态为 uncertain。Q 表可导出、可反事实检查，但不支持“跨 seed 策略表稳定”的强主张。
- Full v3 的 aligned reward 相对 baseline：fair cost `-1.4068` CI `[-1.8214, -0.9891]`，quality `+0.0173` CI `[0.0115, 0.0235]`，background service `+0.0076` CI `[0.0048, 0.0103]`，p99 `+2.0704` 且 CI 跨 0。这为 v4.2 smoke 的同方向结果提供了先前证据，但不替代升级协议的 full 复核。

### 3.3 机制隔离和 oracle 诊断

- 在同一 critical-path 队列下，`moderate` 相对 `full` 少生成约 `100.00` speculative bytes，waste 下降 `26.93`，p99 下降 `22.62`，但 quality 下降 `0.058`。
- 在同一 full 源端动作下，所谓 `fifo` 相对 critical-path scheduler 的 p99 高 `601.18`，static priority 高 `231.33`。这证明 scheduler 有独立贡献，但 `FIFOPolicy` 实际是等权 processor sharing，不是严格先进先出，报告应更名或实现真 FIFO 后重跑。
- Oracle 实验的平均 gap 为 `0.0389`，说明状态、reward 或训练仍有改进空间。但实现只冻结 base-policy 参数；目标 workflow 改变系统状态后，其他 workflow 仍会重新决策，所以 gap 包含局部 spillover，不是“只改一个动作、其余动作完全不变”的严格 oracle。

## 4. 2026-07-29 审计与已完成修复

### 4.1 发现的主要问题

1. `quality` 由 action 的分支数和内置 floor 直接生成，不是任务答案正确率、检索召回或人类偏好。
2. workflow 完成时，所有 speculative flow 已服务字节都累加到 `wasted_speculative_bytes`；模型没有表达“及时返回并被 LLM/judge 消费的有用 speculative 结果”。
3. 旧 full 场景子集对负载、deadline、optional scale 和 capacity scale 的覆盖不均衡。
4. 训练 replicate 下的多个场景共享同一已训练控制器；如果把它们当作完全独立样本，CI 会过度乐观。
5. finite enumeration 存在迭代器耗尽，修复前只检查 `819` 例，完整域应为 `223,587` 例。
6. v4.1 robust 候选排序把“自适应”和“稳健可行”合并成一个布尔值；无候选同时满足时，最低代价的单动作 recovery 被错误命名为 robust adaptive rule。

### 4.2 已实施的协议升级

- validation/test 场景互不重叠，且各因子边际均衡。
- Full `3^4` 网格使用 `OA(9,4,3,2)` 批次，smoke 使用确定性边际/二阶平衡选择。
- 随机/锚点候选搜索预算从 smoke/full 的 `16/48` 扩展为 `64/192`；v4.3 另加 15 个只使用 recovery/moderate 的受约束单调候选。
- 新增 `performance_by_load.csv`，不再只看总体均值。
- CI 改为按训练 replicate 整体的 cluster bootstrap，保留 replicate 内场景相关性。
- 新增 `fixed_full`、`fixed_recovery`、`robust_validation_rule` 三个对照。
- v4.3 部署 gate 升级为 9 项：selection eligibility、fair-cost CI、相对 quality、平均 quality、p99、deadline miss、background floor、worst-load quality 和 quality-feasible fraction。
- Robust 选择现在只从至少使用两种动作的候选中选择，并记录 `adaptive_candidate`、`meets_robust_feasibility` 和 `selection_status`。未达 validation 约束的规则不参与部署候选排名。
- v4.3 smoke 使用第三个因子均衡场景块和 `92000 + scenario_index` 新种子，与已观察的 v4.2 test 不重叠。
- 新增 `strict_aligned_scheduled` 作为 miss-sensitive reward 的可证伪对照。
- 训练策略现在按 replicate/member/reward 落 checkpoint，`--resume` 会先验证协议与代码指纹。实测加载 21 个策略后，重生的 summary/gate CSV 哈希与首次运行完全一致。
- 当前全部 6 个测试模块合计 `35/35` 通过。

主要修改文件：

- [`optimization_study.py`](optimization_study.py)
- [`test_optimization_study.py`](test_optimization_study.py)
- [`finite_monotonicity_check.py`](finite_monotonicity_check.py)
- [`test_finite_monotonicity_check.py`](test_finite_monotonicity_check.py)

## 5. v4.2 smoke 详细结果

### 5.1 总体指标

| Variant | p99 | Miss | Waste | Quality | Background | Fair cost | Quality>=0.95 单元比例 |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | `107.604` | `0.0325` | `39.020` | `0.9400` | `0.1751` | `4.9450` | `0.361` |
| aligned_scheduled | `108.117` | `0.0424` | `43.813` | `0.9556` | `0.1787` | `4.2461` | `0.722` |
| validation_rule | `103.995` | `0.0246` | `41.706` | `0.9473` | `0.1839` | `4.7468` | `0.417` |
| fixed_moderate | `107.823` | `0.0365` | `36.597` | `0.9420` | `0.1863` | `3.9819` | `0.000` |
| fixed_recovery | `119.732` | `0.0651` | `50.566` | `0.9840` | `0.1909` | `3.8299` | `1.000` |
| fixed_full | `129.847` | `0.0790` | `61.910` | `1.0000` | `0.1864` | `4.3320` | `1.000` |

`robust_validation_rule` 在 v4.2 中与 `validation_rule` 选中同一个 4-action 候选，因 validation 稳健约束失败，只作诊断行，不是第二个独立改进。

### 5.2 相对 baseline 的关键配对差异

| Variant | p99 delta | Miss delta | Quality delta | Fair-cost delta | 解释 |
|---|---:|---:|---:|---:|---|
| aligned_scheduled | `+0.5125` `[-5.2183, 7.6158]` | `+0.00990` `[0.00899, 0.01091]` | `+0.01563` `[-0.00045, 0.03424]` | `-0.6989` `[-1.4822, -0.0078]` | 通过当前 gate，但 miss 显著恶化，必须增加 miss gate。 |
| validation_rule | `-3.6094` `[-6.8250, -1.2992]` | `-0.00788` `[-0.01482, -0.00093]` | `+0.00730` `[0.00518, 0.01035]` | `-0.1983` `[-1.0597, 0.4505]` | latency/miss 方向很好，但绝对 quality 和 fair-cost CI 未过门槛。 |
| fixed_moderate | `+0.2189` `[-2.9967, 2.5292]` | `+0.00397` | `+0.00197` `[-0.00013, 0.00501]` | `-0.9631` `[-1.8246, -0.3144]` | 代价低，但 quality 固定在约 `0.942`，不可以因 cost 好看就忽略质量。 |
| fixed_recovery | `+12.1272` `[8.9117, 14.4375]` | `+0.03262` | `+0.04400` `[0.0419, 0.0471]` | `-1.1151` `[-1.9765, -0.4664]` | 高质量、低 scalar cost，但 p99 超过 10% 非劣界，不是自适应优化。 |

### 5.3 分负载风险

| Variant | Heavy quality / fair cost | Light quality / miss | Medium quality / fair cost |
|---|---:|---:|---:|
| baseline | `0.9362 / 5.9853` | `0.9372 / 0.0797` | `0.9466 / 4.2550` |
| aligned_scheduled | `0.9492 / 5.1080` | `0.9582 / 0.1123` | `0.9595 / 3.5047` |
| validation_rule | `0.9368 / 6.1369` | `0.9490 / 0.0650` | `0.9561 / 3.8549` |

aligned 的主要短板是 heavy quality 仍低于 `0.95`，以及 light-load miss 从 `0.0797` 上升到 `0.1123`。validation rule 的主要短板是 heavy quality 和 heavy fair cost，而它在 light/medium 场景中更有竞争力。

### 5.4 v4.3 新鲜 holdout 结果

| Variant | p99 | Miss | Quality | Background | Fair cost | Gate |
|---|---:|---:|---:|---:|---:|---:|
| baseline | `140.010` | `0.1272` | `0.9363` | `0.1697` | `6.0464` | baseline |
| robust_validation_rule | `143.454` | `0.1357` | `0.9612` | `0.1874` | `4.3510` | `6/9` |
| strict_aligned_scheduled | `148.724` | `0.1377` | `0.9559` | `0.1834` | `5.0992` | `5/9` |
| aligned_scheduled | `145.448` | `0.1347` | `0.9587` | `0.1778` | `5.0284` | `4/9` |
| validation_rule | `133.099` | `0.1110` | `0.9414` | `0.1729` | `5.9845` | `4/9` |

bounded robust rule 相对 baseline 的配对差异为：

- p99 `+3.4439`，95% CI `[2.2500, 5.1808]`，通过 baseline 10% 非劣界；
- deadline miss `+0.00852`，95% CI `[-0.00105, 0.02766]`，未通过 `+0.005` 绝对非劣界；
- quality `+0.02483`，95% CI `[0.02156, 0.02751]`；
- fair cost `-1.69541`，95% CI `[-2.40056, -1.16954]`；
- background service 虽提高 `0.01770`，总体仍只有 `0.18735`；
- heavy-load quality 为 `0.94847`，比 `0.95` 低 `0.00153`。

该规则在 validation 上的 worst-load quality 为 `0.95655`，到新 test 降为 `0.94847`。这个跨集偏移说明，不应因差距只有 `0.00153` 就事后放宽门槛；正确做法是改进方法并使用新的冻结协议再验证。

`strict_aligned_scheduled` 的 fair cost 仍有改善（`-0.9472`，CI `[-1.7411, -0.0259]`），但 p99/miss 都更差。这个负结果否定了“只要加大 miss penalty 就会得到更安全策略”的简单假设。

## 6. 当前仍存在的核心局限

### 6.1 测量有效性是最高优先级

- 当前 quality 是 retained-branch proxy。在修复它之前，“quality 提升”只能解释为“保留了更多可选分支”。
- 当前 optional/speculative branch 不参与关键完成逻辑，已传输 speculative bytes 在 workflow 完成时全被记为 waste。这使“推测带来有用答案改善”只存在于人工 quality 公式，没有存在于流程依赖中。
- 训练使用全部 scenario parameter matrix，validation/test 的“互不重叠”主要隔离规则候选选择；对 learned controller 而言，真正 holdout 的是 workload seed，不是未见过的场景参数组合。

### 6.2 部署 gate 已补齐，但显示出可达性问题

v4.3 已将 deadline miss、background service、worst-load quality 和 quality-feasible fraction 加入硬 gate。新问题不再是“门槛漏了什么”，而是当前模型中没有任何策略达到总体 `background_service_ratio >= 0.20`。这不应通过事后把 floor 改为 `0.19` 解决；应检查 background 在 workflow 完成时立即取消的定义，以及 floor 是否应按 eligible service window 计算。

仍需增加的是 selection-aware 不确定性：79 个规则候选同时搜索后，单个选中候选的普通 CI 没有完全反映 winner's curse。

### 6.3 实现和基线命名

- `FIFOPolicy` 只是所有 flow weight 相等，服务器仍按 weighted max-min 并行分享容量。应更名为 `equal_share` 或实现真正的 arrival-order FIFO。
- Oracle 报告中“其他 workflow 动作固定”的文字比实际实现更强，应改为“其他 workflow 使用同一冻结 base policy 重新决策”。
- `optimization_study.py` 现已支持 checkpoint/resume 和协议指纹校验；仍可进一步将 validation/test 也拆成阶段缓存，避免 resume 时重算全部 79 个候选。

## 7. 建设性优化路线

### P0：先修复“优化的到底是什么”

1. 为 optional branch 增加 `completed_before_cutoff` 和 `consumed_by_llm_or_judge` 语义，将 speculative bytes 分为 `useful_speculative_bytes`、`cancelled_inflight_bytes` 和 `late_unused_bytes`。
2. 用任务级 outcome 替代纯分支数 quality：最低限度可用预设 branch utility + noise；更好的方式是接入真实 Agent trace、答案正确率/检索召回或双盲 preference score。
3. 在修复前，所有结论使用“retained-speculation proxy”，不写“语义质量”。

### P1：把多目标约束写进 gate，不只写进 reward

1. deadline-miss 非劣、background floor 和分层 quality gate 已在 v4.3 实现，后续保持 margin 冻结，不根据 test 结果改动。
2. 将当前 gate table 扩展为 Pareto frontier + 约束可行集，并加入 nested validation 或 bootstrap-after-selection 来评估 winner's curse。
3. 对不可达的 background floor，修改分母/服务窗口的语义，而不是降低 floor。

### P2：在不降低门槛的前提下寻找更好策略

1. **约束学习，而非继续手工堆 reward 系数。** strict aligned 的负结果已表明加权标量不稳定。下一步将 p99/miss 作为主目标，quality 和 background 作为 Lagrangian constraints；在 validation 上更新乘子，最终冻结后只评估一次新 test。
2. **bounded candidate family 已证明有价值。** 它使 robust rule 从 v4.2 的 validation infeasible 变为 v4.3 的 adaptive feasible，并在新 test 上通过 6/9 gate。下一轮不应根据已看到的 test 再挑 threshold，而应将这个 family 冻结为方法组件。
3. **重新设计 pressure。** 优先复核 `active_speculative_backlog` 与 `expected_waste_risk`，但要先固定质量约束和评估预算，不从多个 test 结果中挑最好的定义。
4. **稳定低支持状态。** 对 N 小或 Q margin 小的状态使用 shrinkage/层次池化，或使用安全规则回退；评估标准应是指标改善而不是单纯 action agreement 变高。
5. **对轻载 miss 做针对性诊断。** 分解 aligned 在 light 场景中的 slack bucket、action 频率和 deadline scale，确认是 reward 的 tail threshold、状态离散还是 action 分支数造成 miss 增加。

### P3：升级外部有效性

- 将 single bottleneck 扩展为多瓶颈/多租户，加入突发到达、容量跳变和服务相关性。
- 同时保留 true FIFO、equal-share、static priority 和 critical-path scheduler，避免基线名称与实现不一致。
- 接入真实 Agent trace 和真实 outcome，将模拟结论与答案质量、token/GPU/network 成本联合评估。

## 8. 下一轮实验的无泄漏流程

1. v4.3 的第三 smoke block 现已被观察，不再使用该 test 对 bounded threshold、strict reward 或 gate 调参。
2. 下一轮先修改 quality/waste/background 语义，冻结 constrained method 和新预算；这将是新协议，不与 v4.3 数字直接混合。
3. 如需做新 smoke，应生成新 workload seed ledger 并将场景配置重复与 workload realization holdout 分开报告。
4. 由于 v4.3 无候选通过全部 gate，不将当前结果包装成论文级 full confirmation。Full 可用于诊断，但不应转换为部署主张。
5. checkpoint/resume 已实现；下一步为 candidate validation 和 evaluation 增加阶段缓存。

## 9. 复现与验证

在 `student15267` 目录下运行：

```bash
python -m unittest -v \
  specnet_proofs.test_proof_harness \
  specnet_proofs.test_pressure_definition_study \
  specnet_proofs.test_optimization_study \
  specnet_proofs.test_oracle_gap_study \
  specnet_proofs.test_source_control_isolation \
  specnet_proofs.test_finite_monotonicity_check
```

当前结果：`35/35` 通过。

v4.3 smoke 已完成，产物位于：

```text
results/optimization_smoke_v4_3_20260729/
```

checkpoint 恢复可用以下命令复验：

```bash
python -u -m specnet_proofs.optimization_study --mode smoke \
  --output-dir specnet_proofs/results/optimization_smoke_v4_3_20260729 --resume
```

## 10. 本轮创新点

1. **可审计的 holdout 轮换。** 新 test 在代码中显式排除 v4.2 validation 和已观察 evaluation，同时换用新 workload seeds，使方法改动与 test 观察顺序可追溯。
2. **受约束的自适应规则空间。** 通过限定最低动作为 moderate，规则在高风险状态不再为换延迟而过度牺牲 quality；这一结构先验有效地把 robust 选择从 infeasible 推到 adaptive feasible。
3. **9 项分层部署 gate。** scalar cost 不再能抵消 deadline miss、background starvation 和 worst-load quality 等安全失败；每个候选的失败原因写入 `deployment_gates.csv`。
4. **可证伪的 reward 对齐试验。** strict aligned 在 test 打开前冻结，它的失败反而提供了有价值的负证据：手工加重 miss penalty 不等于得到 miss-safe policy。
5. **指纹校验的 checkpoint/resume。** 恢复运行必须匹配协议、上游模拟器、proof harness、optimization harness 和训练配置；实测重生 CSV 哈希完全一致。

## 11. 展望

### 近期

- 先修复 speculative work 的语义：区分 useful、late-unused 和 cancelled-inflight bytes，并让及时完成的 optional result 真正影响 LLM/judge outcome。
- 用 constrained contextual bandit 替代手工 scalar reward，对 miss、quality 和 background 使用可学习乘子和显式可行集。
- 重新定义 background service 的 eligible window，解决当前 20% floor 对所有策略不可达的问题，但不事后降低 floor。
- 为 candidate validation/evaluation 增加阶段缓存，并用 nested validation 或 bootstrap-after-selection 量化 79 候选搜索的 winner's curse。

### 中期

- 在新语义和冻结约束方法下重做 smoke；只有候选通过所有 gate 后，才进入 5-replicate、18-test-scenario 的 full OA 复核。
- 对 pressure 采用 backlog/expected-waste 新定义，并同时报告 raw 与 quality-constrained 结果。
- 用层次池化或 uncertainty-aware fallback 改善 27-state Q 表的低支持与跨 seed 不稳定。

### 长期

- 从单瓶颈扩展到多瓶颈、多租户、突发容量和服务相关的 trace-driven 系统。
- 接入真实 Agent workflow 与任务级 quality，联合计量 token、GPU、network、tail latency 和答案正确性。
- 比较 true FIFO、equal-share、static priority、critical-path 和 GPU-network 联合调度，建立可跨层解释的基线体系。

## 12. 可对外表述的强度

### 当前可以说

- 在当前单瓶颈仿真中，congestion 和 slack 对预注册切片的延迟指标有证据支持。
- 源端可选流量控制与关键路径队列保护存在可分离的机制收益。
- Bounded recovery/moderate family 使 robust rule 首次在 validation 上同时达到自适应和稳健质量约束，并在新 test 上显著改善 fair cost 和 quality。
- 有限单瓶颈域内，删除可选流未发现使关键流完成更慢的反例（`223,587` 例，`0` violations）。

### 当前不可以说

- 不能说 bandit 在所有场景全面优于规则或固定策略。
- 不能说 robust rule 或 aligned 已可部署；v4.3 无任何候选通过全部 9 项 gate。
- 不能说当前 quality 是真实答案质量，也不能说当前 waste 区分了有用与无用的 speculative work。
- 不能把 v4.1 的 fixed recovery 或 v4.2 的 infeasible robust rule 包装成成功的自适应优化。
- 不能把 single-bottleneck 有限枚举推广为多瓶颈、动态到达和真实网络下的普适定理。
