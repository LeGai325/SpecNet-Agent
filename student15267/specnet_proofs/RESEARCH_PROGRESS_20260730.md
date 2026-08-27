# SpecNet-Agent 三参数证明与优化实验统一进展报告

> 最新材料为 [`WEEKLY_RESEARCH_BRIEF_20260805.md`](WEEKLY_RESEARCH_BRIEF_20260805.md) 与已校正的 [`THREE_WEEK_PROOF_REPORT_20260802.md`](THREE_WEEK_PROOF_REPORT_20260802.md)。本文件保留 2026-07-30 时点结论；早期 eligible-window v2 已因完成前 target 截断导致前台分叉而撤回其公平结论，当前仅引用 v3。

> 更新日期：2026-07-30  
> 汇总范围：2026-07-19 至 2026-07-30 的证明、pressure 审计、控制器优化、负结果与独立确认。  
> 研究原则：不删不利场景、不在观察 test 后改写同一假设、不降低 `0.95` 质量门、不把条件效应包装成 broad 主效应。

## 1. 当前最准确的结论

1. 原预注册三参数结果不变：congestion 支持，slack 支持但伴随质量取舍，original-ratio pressure 不支持。
2. `active_speculative_backlog` 是 pressure 候选审计中最合理的冻结定义；它兼顾较高质量可行比例和显著正 waste delta，但旧候选筛选结果不能直接当作独立确认。
3. 多 seed 学习型 Q 表未能稳定复现“三项都必要”。严格质量、bounded quality、增加训练量、退火和 unrestricted 五动作路线都保留了负结果；这说明旧 bandit 结论有明显 seed/状态稀疏敏感性。
4. 冻结的单调规则 `risk = congestion + slack + backlog_pressure`、阈值 `1.8`、动作为 `full/recovery`，在首次 full confirm 和 81×5 高功效复核中稳定支持 congestion 与 backlog pressure 的 broad 效应；broad slack 不支持。
5. 重新冻结“非联合最高、可辨识条件切片”为新估计量后，第三组全新 workload seeds 对三项参数均给出支持。这个结果可写成“在可辨识上下文中三项信号均有贡献”，不能写成“三项 broad 主效应均成立”。
6. 后续因子化机制将三项信号映射到不同作用路径，在全 81 场景×5 个新 runs 上首次得到同一质量安全控制器的三项 broad 支持：C `+33.6455`、S `+0.1263`、P `+5.4317`，三项 CI 全正、Holm p=`0.00015`、质量可行比例 `1.000`。
7. 因子化控制器的全局审计显示 p99/miss/waste 明显优于 fixed full/recovery，但 background service 仅 `0.1053`，只有 `18.5%` 单元达到 0.20 floor。因此它是当前最好的机制证明，不是可部署策略；optimization v4.3 的“保留 baseline”结论仍不变。

## 2. 三类结论必须分开

| 结论层级 | Congestion | Slack | Pressure | 可对外强度 |
|---|---|---|---|---|
| 原预注册 bandit full v2 | 支持 | 支持但有质量取舍 | original ratio 不支持 | 旧协议结论，不能改写 |
| 单调规则 broad full | 支持 | 不支持 | backlog 支持 | 当前最稳健 broad 结论为 2/3 |
| 单调规则可辨识条件效应 | 支持 | 支持 | backlog 支持 | 新假设，三项均支持，但必须写明条件切片 |
| 因子化机制 broad full | 支持 | 支持 | backlog 支持 | 当前最强三参数 broad 机制证据；非部署结论 |
| 部署优化 v4.3 | 不适用 | 不适用 | 不适用 | 0 个候选通过 9/9 gate |

## 3. 原始正式证明回顾

产物：[`results/proof_full_v2_20260719/PROOF_REPORT.md`](results/proof_full_v2_20260719/PROOF_REPORT.md)

| 假设 | 主指标 | ablation - full | 95% CI | 原判定 |
|---|---|---:|---:|---|
| H1-C | high-congestion p99 | `+18.8423` | `[10.5700, 26.8353]` | 支持 |
| H1-S | tight-slack normalized latency | `+0.5241` | `[0.4792, 0.5692]` | 支持，但消融质量更高，存在 operating-point 取舍 |
| H1-P | high-pressure waste | `-3.0064` | `[-4.2120, -1.8489]` | original ratio 不支持 |

旧 H1-S 的正结果来自 unrestricted 学习型 bandit，平均质量低于后续质量安全规则；它不能与新规则结果直接拼成“同一控制器三项都支持”。

## 4. Pressure 定义审计

产物：[`results/pressure_definition_full_20260722_v3/PRESSURE_DEFINITION_REPORT.md`](results/pressure_definition_full_20260722_v3/PRESSURE_DEFINITION_REPORT.md)

| 定义 | 质量可行比例 | 质量约束 waste delta | 95% CI | 审计结论 |
|---|---:|---:|---:|---|
| original ratio | `0.248` | `-4.868` | `[-6.291, -3.387]` | 不支持 |
| active speculative backlog | `0.558` | `+1.292` | `[0.589, 1.977]` | 最合理冻结候选 |
| workflow optional ratio | `0.278` | `+4.919` | `[3.335, 6.460]` | 效应大但质量可行率低 |
| cancelable queue length | `0.392` | `+1.010` | `[-0.971, 2.997]` | 区间跨 0 |
| speculative age | `0.623` | `+0.643` | `[-0.470, 1.755]` | 区间跨 0 |
| expected waste risk | `0.309` | `+5.462` | `[3.846, 7.121]` | 效应大但质量可行率低 |

选择 backlog 的理由不是它的点估计最大，而是它是唯一兼顾较高质量可行率和显著正区间的定义。由于六个候选在同一次 full 中比较，后续必须用新 seeds 独立确认。

## 5. 学习型三信号确认：保留的负结果

脚本：[`three_signal_confirmation_study.py`](three_signal_confirmation_study.py)

该路线分别训练 full、no-congestion、no-slack、no-pressure，并用训练 replicate cluster bootstrap。新协议修复了旧 `matrix[::3]` 固定 capacity=`0.7` 的场景混杂问题，改用平衡/正交 holdout。

| 尝试 | 关键变化 | 结果 |
|---|---|---|
| `three_signal_smoke_v1` | 只允许静态质量下界≥0.95的 `full/recovery` | 质量门 100% 通过，但三项主效应都不支持 |
| `three_signal_smoke_bounded_v1` | 加入 `moderate` | pressure 方向转正；C/S 仍被低维消融反超，质量单元不稳定 |
| `three_signal_dev_constant324` | bounded、324 episodes | 增加训练量没有解决 full 状态稀疏问题 |
| `three_signal_dev_annealed162` | bounded、探索率/学习率退火 | H1-S 仅很小正值且区间跨 0；C/P 为负 |
| `three_signal_dev_unrestricted162` | 恢复五动作 | 三项方向为负且平均质量不足 0.95 |

建设性结论：full 有 27 个状态，消融仅 9 个状态；独立训练比较同时测量了“信号价值”和“有限样本复杂度”。继续增加 episode 或手调学习率不能保证 full 受益，不能只挑 seed=7 的旧结果。

## 6. 单调规则路线与选择审计

脚本：[`three_signal_rule_study.py`](three_signal_rule_study.py)

冻结设计：

- pressure 定义：`active_speculative_backlog`，bucket 阈值 `0.15/0.35`；
- 风险分数：`wc*C + ws*S + wp*P`，所有权重非负；
- 无信号消融：该维使用 middle bucket=`0.5`，而不是假定最低风险；
- 动作集：仅 `full/recovery`，静态质量下界分别为 `1.00/0.98`；
- 选择：35 个有限候选，只用开发集，按三项正方向、最弱归一化效应和质量门排序；
- 统计：full-reference workflow ID 配对、场景分层 bootstrap、随机化检验、三主假设 Holm 校正。

### 6.1 被审计拒绝的“漂亮结果”

v1 选到等权阈值 `2.7`，smoke 三项都显著：C `+10.5197`、S `+0.06434`、P `+4.9988`。但该规则只有三个信号同时最高时才让单项消融改变动作，三项结果主要来自相同的三高 workflow。

该结果保留在 [`results/three_signal_rule_smoke_v1_20260730/`](results/three_signal_rule_smoke_v1_20260730/) 中，但不作为“三项独立贡献”证据。新增 pivotal-state gate 后，阈值 `2.7` 的三项 nonjoint pivotal count 均为 `0`，因此被拒绝。

### 6.2 通过可辨识性门的冻结规则

v2 选择等权阈值 `1.8`：

```text
risk = 1.0 * congestion + 1.0 * slack + 1.0 * backlog_pressure
action = recovery if risk >= 1.8 else full
```

三项信号各有 `3` 个 nonjoint pivotal states，即另外两项不同时为最高时，该信号仍能独立改变动作。

## 7. 冻结规则的独立证据链

### 7.1 首次 full confirmation

产物：[`results/three_signal_rule_confirm_v2_20260730/THREE_SIGNAL_RULE_REPORT.md`](results/three_signal_rule_confirm_v2_20260730/THREE_SIGNAL_RULE_REPORT.md)

27 个正交场景 × 3 个新 runs：

| 假设 | Broad delta | 95% CI | 结果 |
|---|---:|---:|---|
| H1-C | `+2.4080` | `[1.5900, 3.0629]` | 支持 |
| H1-S | `+0.000232` | `[-0.000330, 0.000838]` | 不支持 |
| H1-P-backlog | `+3.5765` | `[3.1986, 3.9380]` | 支持 |

非联合 H1-S 为 `+0.000741`，CI `[0.000401, 0.001078]`，但只有 9 个场景层，未达到预定 12 层覆盖门。

### 7.2 事后高功效复核

产物：[`results/three_signal_rule_replication_v2_1_20260730/THREE_SIGNAL_RULE_REPORT.md`](results/three_signal_rule_replication_v2_1_20260730/THREE_SIGNAL_RULE_REPORT.md)

全 `3^4=81` 场景 × 5 个新 runs，参数不变：

| 假设 | Broad delta | 95% CI | Nonjoint delta | 95% CI | 结论 |
|---|---:|---:|---:|---:|---|
| H1-C | `+2.5447` | `[2.1766, 3.0098]` | `+3.1558` | `[2.7903, 3.5529]` | 支持 |
| H1-S | `-0.000296` | `[-0.000668, 0.000085]` | `+0.001218` | `[0.000704, 0.001857]` | broad 不支持，条件效应支持 |
| H1-P-backlog | `+3.3291` | `[3.1247, 3.5358]` | `+3.8974` | `[3.6722, 4.1354]` | 支持 |

高功效复核说明 broad slack 失败不是简单的样本量不足；继续扩大同一 broad 检验不会自然得到稳定正效应。

### 7.3 新条件估计量的独立确认

产物：[`results/three_signal_rule_conditional_v2_2_20260730/THREE_SIGNAL_RULE_REPORT.md`](results/three_signal_rule_conditional_v2_2_20260730/THREE_SIGNAL_RULE_REPORT.md)

在观察前两轮结果后，研究问题明确修改为：**当目标信号为最高，且另外两项不同时为最高时，移除该信号是否使其主指标变差？** 新假设使用第三组全新 seeds `1910000 + run*10000 + scenario`，全 81 场景 × 5 runs。

| 条件假设 | 主指标 delta | 95% CI | Holm p | Full quality | Ablation quality | 质量可行比例 |
|---|---:|---:|---:|---:|---:|---:|
| H1-C identifiable | `+3.4444` | `[3.0387, 3.8604]` | `0.00015` | `0.9838` | `0.9917` | `1.000` |
| H1-S identifiable | `+0.000878` | `[0.000673, 0.001082]` | `0.00015` | `0.9964` | `0.9971` | `1.000` |
| H1-P-backlog identifiable | `+3.8766` | `[3.6428, 4.1485]` | `0.00015` | `0.9857` | `0.9908` | `1.000` |

三项均为 `supported_in_identifiable_context`。同一批新 seeds 的 broad 诊断仍完整保留：C `+2.6269`、P `+3.2510`，而 slack 为 `-0.000865`，CI `[-0.001108, -0.000621]`。因此三项条件支持不能升级为三项 broad 支持。

## 8. 优化实验仍不可部署

产物：[`results/optimization_smoke_v4_3_20260729/OPTIMIZATION_REPORT.md`](results/optimization_smoke_v4_3_20260729/OPTIMIZATION_REPORT.md)

- robust validation rule 在新 holdout 上 fair cost delta `-1.6954`，CI `[-2.4006, -1.1695]`；quality delta `+0.02483`。
- p99 `143.454`，baseline `140.010`，在 10% 非劣界内。
- 只通过 `6/9` gate；deadline miss、background floor、worst-load quality 失败。
- heavy quality=`0.94847`，不能因只差 `0.00153` 就降低 `0.95` 门槛。
- 当前部署决策仍是保留 baseline。

单调三参数规则研究验证的是信号贡献，不等于通过部署 gate；两套结论不能混用。

## 9. 本轮创新点

1. **修复场景混杂。** 识别出旧 `matrix[::3]` 实际固定 capacity scale=`0.7`，新实验使用边际平衡 smoke 和 OA/full Cartesian 场景。
2. **冻结 backlog pressure。** 从六候选审计中选择容量归一化 active backlog，不按最大点估计挑定义。
3. **质量安全动作族。** 单调规则只使用 `full/recovery`，静态质量下界≥0.98，所有主切片质量可行比例均为 1.000。
4. **可辨识性审计。** 新增 pivotal-state gate 和 nonjoint workflow slice，主动拒绝阈值2.7的三高交互“漂亮结果”。
5. **选择/确认分离。** 开发候选、首次 confirm、高功效复核、条件假设确认分别使用不同 seed ledger，旧结果全部保留。
6. **多重比较控制。** 三项主假设采用场景分层 bootstrap、paired randomization 和 Holm correction。
7. **负结果分层表达。** 自动 verdict 区分 `supported`、`not_supported` 和 `supported_in_identifiable_context`，防止条件结果覆盖 broad 负结论。

## 10. 建设性建议

### 10.1 如果目标是论文中的“三参数证明”

- 主表可使用 2026-07-30 条件确认结果，但标题必须写“identifiable-context conditional effects”。
- 同页附上 broad 诊断，尤其是 broad H1-S 的负值；不要只展示三项条件正结果。
- 原始 H1-P 和 backlog H1-P 分成两行：`original ratio: not supported`；`active backlog conditional: supported`。
- 把单调规则视为可解释机制基线，不宣称它证明学习型 bandit 跨 seed 稳定。

### 10.2 如果必须得到三项 broad 效应

- 共享阈值规则不支持 broad slack；因子化机制通过让 tight slack 独立控制 deadline-aware critical-flow weight，已经在新 seeds 上获得三项 broad 支持。
- 该结果说明应优先做作用路径分解，而不是继续对共享风险分数调权重：pressure→source admission，congestion→global queue protection，slack→per-workflow deadline scheduling。
- 如果目标是学习型 bandit 的三项 broad 效应，仍需将这种因子化结构写入 action/mechanism space，再用新的训练与测试 seed ledger；不能把确定性机制结果直接当作 Q 表稳定性证据。
- 下一步不能复用 `2010000/2020000/2110000/2120000` 系列调 boost；任何参数或机制改动都必须开启新 ledger。

### 10.3 最高优先级的模型语义修复

- 将 speculative bytes 拆为 `useful_completed`、`late_unused`、`cancelled_inflight`，不再把所有已服务 speculative bytes 都记为 waste。
- 用任务正确率、检索召回或 preference score 替代 retained-branch quality proxy。
- 让及时完成的 optional result 真正参与 LLM/judge outcome，否则 quality 与网络过程仍是人工并列指标。
- 修复 background eligible window，而不是把不可达的 0.20 floor 事后降到 0.19。

### 10.4 外部有效性

- 在真实 Agent trace 或 packet-level simulator 上复核 backlog threshold、neutral bucket 和条件效应。
- 增加容量估计误差、pressure 观测延迟、突发到达、多瓶颈和多租户敏感性分析。
- 比较 true FIFO、equal-share、static priority 和 critical-path scheduler；当前旧 `FIFOPolicy` 实际是 equal-share。

## 11. 复现与验证

从 `student15267` 目录运行：

```bash
python3 -m unittest -v \
  specnet_proofs.test_proof_harness \
  specnet_proofs.test_pressure_definition_study \
  specnet_proofs.test_optimization_study \
  specnet_proofs.test_oracle_gap_study \
  specnet_proofs.test_source_control_isolation \
  specnet_proofs.test_finite_monotonicity_check \
  specnet_proofs.test_three_signal_confirmation_study \
  specnet_proofs.test_three_signal_rule_study
```

当前结果：`50/50` 通过。

条件确认复现命令：

```bash
python3 -m specnet_proofs.three_signal_rule_study \
  --mode conditional \
  --frozen-candidate specnet_proofs/results/three_signal_rule_smoke_v2_20260730/selected_candidate.json \
  --output-dir specnet_proofs/results/three_signal_rule_conditional_v2_2_20260730
```

## 12. 最终表述边界

### 可以说

- 在当前单瓶颈 trace-driven simulator、冻结的等权单调规则和非联合最高切片中，congestion、slack、active speculative backlog 的移除均显著恶化各自主指标，Holm-adjusted p 均为 `0.00015`，质量可行比例均为 `1.000`。
- 在冻结的因子化控制器中，三项信号分别作用于 global congestion scheduling、per-workflow slack scheduling 和 source admission；全 81 场景×5 runs 的 broad 与 nonjoint 主指标均显著为正，且质量可行比例均为 `1.000`。
- Congestion 和 active backlog 的 broad 效应在首次 confirm、高功效复核和第三组新 seeds 中方向稳定。
- active backlog 比 original ratio 更适合作为 waste-control pressure 信号。
- 学习型 Q 表跨 seed 不稳定，单调规则提供了更可审计的机制基线。

### 不可以说

- 不能说三项 broad 主效应全部支持；broad slack 在两次高预算新 seeds 上不支持或显著为负。
- 不能说 original ratio H1-P 已被证实；旧预注册结论仍是不支持。
- 不能说学习型 bandit 的三项必要性已跨 seed 稳定复现。
- 不能说单调规则或因子化规则已可部署；因子化控制器的 background service 只有 `0.1053`，optimization v4.3 仍是 0 个候选通过全部 9 项 gate。
- 不能把 simulator 的 retained-branch proxy 写成真实答案质量，也不能把条件仿真证据推广为真实网络普适定理。

## 13. 因子化三信号 broad 确认（继续优化结果）

### 13.1 机制设计

脚本：[`factorized_signal_study.py`](factorized_signal_study.py)

共享风险阈值会让 slack 在 congestion/pressure 已经触发 recovery 时失去额外动作影响。新机制将三项信号分开：

- `active_speculative_backlog`：high pressure 时源端 action 从 full 切换到 recovery；
- congestion：high congestion 时全局 critical flow weight 乘 boost，同时 speculative/background weight 乘 scale；
- slack：tight workflow 的 critical flow 额外乘 deadline-aware boost。

18 个有限开发候选全部在新 seed `2010000 + scenario` 上比较，选择前要求 broad/nonjoint 三项方向都为正、质量可行比例≥0.95。冻结参数为：

```text
congestion_critical_boost = 1.50
congestion_optional_scale = 0.75
slack_critical_boost      = 2.00
```

Admission 仍只使用 full/recovery，静态质量下界≥0.98。

### 13.2 Smoke holdout

产物：[`results/factorized_signal_smoke_v1_20260730/FACTORIZED_SIGNAL_REPORT.md`](results/factorized_signal_smoke_v1_20260730/FACTORIZED_SIGNAL_REPORT.md)

12 个不重叠场景×3 个新 runs：

| 假设 | Broad delta | 95% CI | Nonjoint delta | 质量可行比例 |
|---|---:|---:|---:|---:|
| H1-C | `+14.8837` | `[11.7510, 18.1823]` | `+14.2757` | `1.000` |
| H1-S | `+0.09229` | `[0.06690, 0.12572]` | `+0.07322` | `1.000` |
| H1-P-backlog | `+5.4164` | `[4.7163, 6.1022]` | `+5.8318` | `1.000` |

三项 broad/nonjoint、Holm、质量与覆盖门全部通过。

### 13.3 Full broad confirmation

产物：[`results/factorized_signal_confirm_v1_20260730/FACTORIZED_SIGNAL_REPORT.md`](results/factorized_signal_confirm_v1_20260730/FACTORIZED_SIGNAL_REPORT.md)

全 `3^4=81` 场景×5 个全新 runs，seed 规则 `2110000 + run*10000 + scenario`，参数未改：

| 假设 | Broad delta | 95% CI | Holm p | Nonjoint delta | 95% CI | Full/Ablation Q |
|---|---:|---:|---:|---:|---:|---:|
| H1-C p99 | `+33.6455` | `[30.9269, 36.3372]` | `0.00015` | `+32.2281` | `[28.7213, 36.1995]` | `0.9835/0.9836` |
| H1-S normalized latency | `+0.12629` | `[0.11507, 0.13731]` | `0.00015` | `+0.09067` | `[0.07258, 0.11202]` | `0.9881/0.9879` |
| H1-P-backlog waste | `+5.4317` | `[5.2615, 5.6066]` | `0.00015` | `+5.6725` | `[5.4405, 5.9122]` | `0.9834/1.0000` |

这是当前首次在同一冻结、质量安全、可审计控制器上得到三项 broad 与 nonjoint 同时支持的结果。

### 13.4 全局性能与公平代价

产物：[`results/factorized_global_diagnostic_v1_20260730/FACTORIZED_GLOBAL_DIAGNOSTIC.md`](results/factorized_global_diagnostic_v1_20260730/FACTORIZED_GLOBAL_DIAGNOSTIC.md)

第四组新 seeds、27 个平衡场景×3 runs：

| Policy | p99 | Miss | Waste | Quality | Background | BG floor fraction |
|---|---:|---:|---:|---:|---:|---:|
| factorized full | `110.658` | `0.0454` | `37.657` | `0.9926` | `0.1053` | `0.185` |
| fixed full | `199.442` | `0.1401` | `66.617` | `1.0000` | `0.1596` | `0.333` |
| fixed recovery | `172.253` | `0.1209` | `54.225` | `0.9836` | `0.1652` | `0.333` |

相对 fixed full，factorized full 的配对改善为：

- p99 `-88.7847`；对应 fixed-full-minus-factorized CI `[+78.0197, +100.2250]`；
- miss `-0.09470`，CI `[+0.07897, +0.11078]`；
- waste `-28.9600`，CI `[+28.3190, +29.6075]`；
- quality 低 `0.00742`，但绝对质量仍为 `0.9926`；
- background service 低 `0.05430`。

因此新机制同时给出了强三参数 broad 证据和明显总体 latency/miss/waste 改善，但 background starvation 更严重。正确结论是“机制证明优化成功，部署公平性仍失败”，不能把两者合并成全面胜出。

### 13.5 下一步

1. 保持三条信号作用路径不变，在新开发 ledger 上搜索 background-aware congestion scheduling，例如给 background 保留最小权重或 token bucket，而不是降低 0.20 floor。
2. 将 background floor 作为硬约束，只有 validation 上达到 0.20 才允许进入新 confirm；不再使用 scalar reward 抵消 starvation。
3. 加入 congestion/slack 观测噪声与 1–5 epoch 延迟，验证 broad 三项结果是否对测量误差稳定。
4. 将因子化结构迁移到学习型 controller：分别学习 admission、global scheduling、deadline scheduling 参数，避免 27-state 单 Q 表的样本稀疏。
