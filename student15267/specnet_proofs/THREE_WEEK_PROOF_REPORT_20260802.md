# SpecNet-Agent 三周证明、优化与突破报告（2026-08-05 校正更新）

> 更新日期：2026-08-05  
> 覆盖周期：2026-07-19 至 2026-08-05  
> 重点：完整证明链、第三周工作、三参数 broad 结果、background 公平优化及后续突破方向。  
> 研究纪律：不删除负结果，不降低 `quality=0.95` 或 `background=0.20` 门槛，不复用已观察的 confirmation seeds 调参，不把扩展语义结果覆盖成原模型结论。

> 校正说明：2026-07-30 的 eligible-window v2 在 workflow 完成前提前按 20% 截断 background，违反了“完成前保留原调度”的设计意图，且改变了前台轨迹。因此 v2 仅保留为定位问题的历史实验，不能作为公平部署结论。当前扩展语义结论以 2026-08-05 的 v3 独立确认及其数值 floor 审计为准。

## 1. 摘要结论

三周工作形成了四个必须分开的结论层级：

| 层级 | 当前结论 |
|---|---|
| 原预注册学习型证明 | H1-C 支持；H1-S 支持但有质量取舍；original-ratio H1-P 不支持。 |
| 单调共享阈值规则 | congestion 与 backlog pressure 的 broad 效应支持；broad slack 不支持；三项仅在可辨识 nonjoint 条件切片中支持。 |
| 因子化三路径机制 | 同一冻结控制器首次得到 congestion、slack、backlog pressure 三项 broad 与 nonjoint 全支持。 |
| 部署/公平性 | 原 simulator 语义下，常数权重和 deficit-aware 权重均无法同时通过 background、p99、miss 硬门；v3 eligible-window 在扩展生命周期语义中通过全部确认门，并逐 workflow 保持前台轨迹精确一致；代价为 `16.04` epochs drain 与 `+0.05923` utilization，且要求 background 可跨主请求生命周期继续执行。 |

当前最强三参数结果仍来自冻结的因子化机制：

| 假设 | 原因子化 full confirm broad delta | 95% CI | Eligible-window v3 独立确认 broad delta | 95% CI |
|---|---:|---:|---:|---:|
| H1-C p99 | `+33.6455` | `[30.9269, 36.3372]` | `+29.8024` | `[26.6753, 32.8701]` |
| H1-S normalized latency | `+0.12629` | `[0.11507, 0.13731]` | `+0.09299` | `[0.08289, 0.10234]` |
| H1-P-backlog waste | `+5.4317` | `[5.2615, 5.6066]` | `+5.2575` | `[5.0297, 5.4971]` |

两次确认的三项 Holm-adjusted p 均为 `0.00015`，质量门通过。v3 后一次还同时通过 background、p99、miss、三信号与前台 parity gate，但它属于明确标注的新生命周期语义。

## 2. 三周工作主线

### 2.1 第一周：建立隔离证明与原始结论（7月19日前后）

第一周的核心不是追求更漂亮的数字，而是先把可审计证据链建立起来：

1. 建立只读加载 upstream simulator 的隔离 proof harness，所有新策略、状态定义、分析和结果保存在 `specnet_proofs`。
2. 修复 effective slack 对所选 action 的潜在依赖，使状态在决策前可观测且 action-independent。
3. 用 full-reference workflow ID 做配对，避免 full 与 ablation 因完成集合不同而比较不同样本。
4. 使用场景分层 bootstrap、随机化检验和 Holm 校正，而不是只看均值或单 seed。
5. 对规则、Q 表、状态覆盖、稳定性和部署对照分别留存结果。

原正式结果位于 [`results/proof_full_v2_20260719/`](results/proof_full_v2_20260719/)：

| 假设 | 主指标 | ablation - full | 95% CI | 结论 |
|---|---|---:|---:|---|
| H1-C | high-congestion p99 | `+18.8423` | `[10.5700, 26.8353]` | 支持 |
| H1-S | tight-slack normalized latency | `+0.5241` | `[0.4792, 0.5692]` | 支持，但存在质量 operating-point 取舍 |
| H1-P | high-pressure waste | `-3.0064` | `[-4.2120, -1.8489]` | original ratio 不支持 |

第一周同时发现：单 seed Q 表、动作表一致率和部署效果不能互相替代；原 H1-P 不能靠改名或换定义事后改写为支持。

### 2.2 第二周：重新定义 pressure 并隔离机制（7月22日至23日）

第二周集中解决“pressure 到底测量了什么”和“效果来自源端 admission 还是队列 scheduling”两个问题。

六种 pressure 定义审计位于 [`results/pressure_definition_full_20260722_v3/`](results/pressure_definition_full_20260722_v3/)：

| Pressure 定义 | 质量可行比例 | 质量约束 waste delta | 95% CI | 结论 |
|---|---:|---:|---:|---|
| original ratio | `0.248` | `-4.868` | `[-6.291, -3.387]` | 不支持 |
| active speculative backlog | `0.558` | `+1.292` | `[0.589, 1.977]` | 冻结候选 |
| workflow optional ratio | `0.278` | `+4.919` | `[3.335, 6.460]` | 效应大但质量可行率低 |
| cancelable queue length | `0.392` | `+1.010` | `[-0.971, 2.997]` | 区间跨 0 |
| speculative age | `0.623` | `+0.643` | `[-0.470, 1.755]` | 区间跨 0 |
| expected waste risk | `0.309` | `+5.462` | `[3.846, 7.121]` | 效应大但质量可行率低 |

选择 `active_speculative_backlog` 不是因为点估计最大，而是它兼顾正方向、区间显著和相对较高的质量可行率。随后通过 source-control isolation、oracle gap 和 finite monotonicity 检查，进一步确认：

- source admission 与 queue scheduling 必须分别建模；
- 单个共享动作很难让三项信号各自保持可辨识影响；
- corrected finite enumeration 覆盖全部 `223,587` 个组合，没有旧版 iterator exhaustion；
- simulator 的 quality 和 waste 仍是 proxy，不能直接等同真实答案质量和真实无效传输。

### 2.3 第三周：从系统性失败到三参数 broad 突破（7月29日至8月5日）

第三周是本报告重点，工作可分为八步。

#### 第一步：强化部署 gate 与可恢复实验

optimization v4.3 增加第三个未观察场景块、九项部署 gate 和 checkpoint/resume。最佳 validation rule 在 holdout 上仍只通过 `6/9`：deadline miss、background floor 和 worst-load quality 失败，因此保留 baseline。

#### 第二步：修复场景混杂

发现旧 `matrix[::3]` 实际固定 `capacity_scale=0.7`，会把容量水平与切片选择混在一起。后续改用边际平衡、正交数组或全 `3^4=81` Cartesian 场景。

#### 第三步：系统保留学习型路线负结果

分别尝试 strict `full/recovery`、bounded `full/moderate/recovery`、324 episodes、annealed learning 和 unrestricted 五动作。多 seed 下均未稳定得到三项支持。主要原因是 full 有 27 个状态，单项消融只有 9 个状态，有限样本下低维消融反而更稳。

这说明继续只加 episodes 或挑单 seed 不能证明三项必要性。

#### 第四步：拒绝三高交互产生的“漂亮结果”

共享单调规则阈值 `2.7` 一度得到三项显著正结果，但新增 pivotal-state 和 nonjoint 审计后发现：规则只在三个信号同时最高时改变动作，三项结果来自相同 joint-high workflows。该结果被保留但拒绝作为独立三项证明。

#### 第五步：得到可辨识条件三项结果

冻结规则：

```text
risk = congestion + slack + backlog_pressure
action = recovery if risk >= 1.8 else full
```

该规则 broad 支持 C/P，不支持 slack；重新冻结 nonjoint identifiable-context estimand 后，第三组全新 seeds 得到：

| 条件假设 | Delta | 95% CI | Holm p | 质量可行比例 |
|---|---:|---:|---:|---:|
| H1-C identifiable | `+3.4444` | `[3.0387, 3.8604]` | `0.00015` | `1.000` |
| H1-S identifiable | `+0.000878` | `[0.000673, 0.001082]` | `0.00015` | `1.000` |
| H1-P-backlog identifiable | `+3.8766` | `[3.6428, 4.1485]` | `0.00015` | `1.000` |

同批 broad slack 仍显著为负，因此该结果只能称条件三项支持。

#### 第六步：因子化三作用路径，首次实现 broad 3/3

共享阈值让一个信号触发动作后，其他信号常失去额外影响。第三周的关键机制创新是将三个信号映射到不同路径：

```text
pressure   -> source admission: high backlog 使用 recovery，否则 full
congestion -> global scheduling: critical boost + optional scale
slack      -> per-workflow scheduling: tight workflow critical boost
```

开发集冻结参数：

```text
congestion_critical_boost = 1.50
congestion_optional_scale = 0.75
slack_critical_boost      = 2.00
```

在 [`results/factorized_signal_confirm_v1_20260730/`](results/factorized_signal_confirm_v1_20260730/) 的 81 场景×5 全新 runs 中，三项 broad/nonjoint、Holm、质量和覆盖门全部通过。这是首次在同一冻结、质量安全、可审计控制器上得到 broad 三项支持。

#### 第七步：全局审计发现 background starvation

[`results/factorized_global_diagnostic_v1_20260730/`](results/factorized_global_diagnostic_v1_20260730/) 显示，因子化 full 相比 fixed full 明显改善 p99、miss 和 waste，但平均 background 仅 `0.1053`，只有 `18.5%` cells 达到 0.20 floor。

因此第三周没有把“机制证明成功”包装成“部署成功”，而是继续做硬约束优化。

#### 第八步：公平优化与新语义突破

依次完成四轮公平实验：

| 路线 | 最接近结果 | 失败/通过原因 |
|---|---|---|
| 常数 background boost 1–8 | boost=3: background `0.2579` | p99 `1.2258×`、miss `+0.01215`，失败 |
| deficit-aware 粗网格 | target=0.25, boost=3: background `0.2106` | p99 `1.1371×`，失败 |
| deficit-aware 细网格 | target=0.25, boost=2.75: background `0.2011` | p99 `1.1314×`、miss `+0.01272`，失败 |
| eligible-window v1 | background/p99/miss 通过 | 所有 background 改成 idle-only，改变前台竞争且 H1-S 覆盖不足，未接受 |
| eligible-window v2 validation/confirmation | 历史结果 | 完成前 target 截断改变前台路径；保留为缺陷定位，撤回其公平结论 |
| eligible-window v3 validation | background `0.2269`、p99 `1.0000×` | 27 场景×1 新 seeds；前台 parity 100%；H1-S 仅因覆盖不足进入确认 |
| eligible-window v3 confirmation | background `0.2305`、p99 `1.0000×`、三项支持 | 45 场景×3 新 runs；全部 hard gate 和 parity gate 通过 |

原语义下的三轮无可行点证明：继续无限增大权重只会沿同一公平—尾延迟前沿移动。真正突破来自生命周期拆分：

1. 工作流完成前完全保留原 background 调度；
2. 完成时不足原始 background 大小 20% 的欠额进入 deferred queue；
3. deferred background 只在没有任何 foreground flow 时服务；
4. deferred bytes 不进入前台 congestion/slack backlog；
5. 所有 full/ablation 使用相同的公平语义。

独立确认位于 [`results/factorized_background_eligible_confirm_v3_20260805/`](results/factorized_background_eligible_confirm_v3_20260805/)；逐 workflow 数值 floor 审计位于 [`results/eligible_window_floor_audit_v1_20260805/`](results/eligible_window_floor_audit_v1_20260805/)：

| Gate/指标 | 结果 |
|---|---:|
| Mean background | `0.230475` |
| Background floor cells | `1.000` |
| Background floor workflows | `1.000`（`1e-9` 容差审计；最小值 `0.19999999999999971`） |
| Mean quality | `0.992547` |
| p99 ratio vs original semantics | `1.000000×` |
| Miss delta vs original semantics | `0.000000` |
| Mean post-foreground drain | `16.037` epochs |
| Foreground action/state/latency/waste parity | `135/135` cells，均为精确一致 |
| 三信号 gate | 3/3 supported |
| 全部确认 gate | 通过 |

Nonjoint 结果同样全正且覆盖通过：

| 假设 | Nonjoint delta | 95% CI | Scenario strata |
|---|---:|---:|---:|
| H1-C | `+31.8422` | `[27.8031, 36.1441]` | `35` |
| H1-S | `+0.04611` | `[0.04327, 0.04912]` | `15` |
| H1-P-backlog | `+5.5118` | `[5.1818, 5.8561]` | `44` |

确认后的只读配对审计位于 [`results/eligible_window_paired_audit_v2_20260805/`](results/eligible_window_paired_audit_v2_20260805/)：

| Eligible-window - original | Mean delta | 95% CI |
|---|---:|---:|
| p99 | `0.000000` | `[0.000000, 0.000000]` |
| deadline miss | `0.000000` | `[0.000000, 0.000000]` |
| normalized latency | `0.000000` | `[0.000000, 0.000000]` |
| background | `+0.122775` | `[0.121638, 0.123913]` |
| waste | `0.000000` | `[0.000000, 0.000000]` |
| quality | `0.000000` | `[0.000000, 0.000000]` |
| utilization | `+0.059227` | `[0.057465, 0.061013]` |

该表说明公平突破不是免费收益：deferred 工作增加了带宽利用与 drain 时间；但 v3 的 target 在完成后才开放，前台 tail、miss、latency、quality 与 speculative waste 均逐 workflow 保持不变。

## 3. 完整证明定义

### 3.1 三个信号

- `C`：全局 congestion bucket，取 low/medium/high。
- `S`：当前 workflow 的 action-independent slack bucket，取 tight/normal/loose。
- `P`：容量归一化 active speculative backlog，取 low/mid/high。

### 3.2 三个主假设

- H1-C：在 full-reference high-congestion workflows 中，移除 congestion 路径会增大 p99 latency。
- H1-S：在 full-reference tight-slack workflows 中，移除 slack 路径会增大 normalized latency。
- H1-P-backlog：在 full-reference high-backlog workflows 中，移除 pressure 路径会增大 wasted speculative bytes。

### 3.3 因子化控制器

Pressure admission：

```text
action(P) = recovery, if P = high_spec
            full,     otherwise
```

Congestion scheduling：

```text
w_critical <- 1.50 * w_critical, if C = high
w_optional <- 0.75 * w_optional, if C = high
```

Slack scheduling：

```text
w_critical(workflow) <- 2.00 * w_critical(workflow), if S = tight
```

三条路径的单项消融只移除对应信号机制。Admission 只使用 `full/recovery`，静态质量下界分别为 `1.00/0.98`。

### 3.4 配对估计量

对每个假设 `j` 和场景 `s`，以 full policy 的 workflow 集合固定切片：

```text
Delta_j,s = M_j(ablation_j on full-reference workflow IDs)
            - M_j(full on the same workflow IDs)
```

`Delta > 0` 表示移除该信号使主指标变差，即该信号对 full controller 有正贡献。

Broad estimand 使用所有目标信号为 high/tight 的 full-reference workflows。Nonjoint estimand 排除另外两项同时处于最高水平的 workflows，用于降低三高交互混杂。

### 3.5 统计与 gate

1. 场景等权：先在同一 scenario 内汇总，再对 scenario strata 平均。
2. 置信区间：固定场景内对独立 runs 做分层 bootstrap。
3. 显著性：paired sign-flip/randomization test。
4. 多重比较：三项主假设使用 Holm correction。
5. 质量：full 和 ablation 平均质量均需 `>=0.95`，质量可行比例需 `>=0.95`。
6. 覆盖：confirm broad 至少 18 个 scenario strata，nonjoint 至少 12 个。
7. 公平优化：mean background `>=0.20`，p99 不超过原因子化 `1.10×`，miss 不超过原因子化 `+0.005`。
8. 选择与确认：每轮候选开发和独立确认使用不同 seed ledger；确认后不再调参。

## 4. 正负实验总台账

| 实验 | 作用 | 结果状态 |
|---|---|---|
| proof full v2 | 原三项预注册结论 | C/S 支持；original P 不支持 |
| pressure audit v3 | 六种 P 定义比较 | 冻结 active backlog；不能当独立确认 |
| source isolation / oracle | 机制隔离 | 证明 source 与 scheduling 需分开 |
| finite monotonicity v2 | 穷举语义检查 | `223,587` cases 完整通过 |
| optimization v4.3 | 九项部署 gate | `6/9`，保留 baseline |
| learned three-signal variants | 多 seed Q 表 | 均未稳定 3/3，负结果保留 |
| monotone rule threshold 2.7 | 三项漂亮结果 | joint-high 混杂，拒绝作为独立证明 |
| monotone rule threshold 1.8 | broad + nonjoint | broad C/P 支持，broad S 不支持 |
| identifiable-context confirm | 条件估计量 | 条件三项支持，不能升级 broad |
| factorized broad confirm | 三条独立路径 | broad/nonjoint 3/3 支持 |
| factorized global diagnostic | 部署风险 | background starvation，原语义不可部署 |
| constant background boost | 公平权重 | 无可行点 |
| deficit coarse/refined | 有限配额权重 | 无可行点 |
| eligible-window v1 | 生命周期试验 | 改动过宽且覆盖不足，未接受 |
| eligible-window v2 | 早期生命周期试验 | 完成前 target 截断造成前台分叉；撤回公平结论 |
| eligible-window v3 validate | 精确欠额 + parity | 全局门与 parity 通过；H1-S 仅因覆盖不足进入确认 |
| eligible-window v3 confirm | 45×3 独立确认 | 全部 gate、broad/nonjoint 3/3 和 parity 通过；仅适用于扩展语义 |

## 5. 第三周创新点

1. **从调权重转向作用路径分解。** 三项信号分别控制 admission、global scheduling 和 deadline scheduling，解决共享阈值的动作饱和问题。
2. **可辨识性成为硬门。** pivotal-state 与 nonjoint slice 主动拒绝 joint-high 伪独立结果。
3. **严格保留 broad 负结果。** 条件 slack 支持没有覆盖 broad slack 失败；原 H1-P 也没有被 backlog 定义覆盖。
4. **场景设计修复。** 从固定 capacity 的错误切片升级到平衡/OA/full Cartesian 场景。
5. **公平性使用硬约束。** background floor 不再被 scalar reward 抵消；门槛失败就保留不可部署结论。
6. **失败前沿被显式刻画。** 常数与 deficit 权重都证明原生命周期语义下 background 与 tail gate 无交点。
7. **生命周期拆分与精确欠额。** 将完成前 background 调度与完成后精确欠额 drain 分开，在前台轨迹不变的条件下满足 0.20 公平门。
8. **长实验可恢复。** eligible-window confirmation 使用逐场景 checkpoint，避免会话中断丢失整轮计算。
9. **确认后只读审计。** 新增 paired semantic audit 与 workflow floor 数值审计，完整报告 utilization/drain 代价及浮点边界，不修改已冻结判定。

## 6. 当前突破的真实边界

### 可以说

- 在冻结因子化控制器、当前单瓶颈 trace-driven simulator 中，congestion、slack 和 active speculative backlog 的 broad 与 nonjoint 主效应均在独立新 seeds 上得到支持。
- 原 simulator 生命周期内，常数 boost 与 deficit-aware boost 都没有同时通过 background、p99 和 miss 硬门。
- 在允许 background 欠额跨主 workflow 完成点、且只使用 idle capacity 的扩展语义中，v3 的 45 场景×3 runs 在 background、quality、p99、miss、三信号、覆盖与前台 parity gate 上全部通过。
- v3 把平均 background 从 `0.1077` 提高到 `0.2305`；135 cells 均达 0.20，7,873 个 workflow 的最小比率为 `0.19999999999999971`，经 `1e-9` 容差审计后全部达标。

### 不可以说

- 不能说 original-ratio H1-P 已被证实；它仍是不支持。
- 不能说学习型 Q 表已跨 seed 稳定复现三项必要性。
- 不能把 identifiable-context 三项支持写成共享阈值规则的 broad 3/3。
- 不能说原 simulator 的因子化规则已可部署；原语义公平优化仍无可行点。
- 不能把 eligible-window 结果推广到不允许后台任务跨请求生命周期的系统。
- 不能忽略 `+0.05923` utilization、`16.04` epochs drain，以及 background 跨生命周期的业务假设。
- 不能把 retained-branch quality proxy 写成真实回答正确率。

## 7. 最有突破可能的下一步

按优先级排序：

### 7.1 P0：验证 background 生命周期假设

在真实 Agent trace 中回答：background 任务是否仍有价值、是否允许在主请求返回后继续运行、允许延迟多久。若答案是否定，eligible-window 只能作为模型上界，不能进入部署候选。

### 7.2 P0：将前台 parity 固化为回归门

v3 已在 action、decision state、latency 与 speculative waste 上做到逐 workflow 精确一致。下一步应保留该 gate，并补充逐 epoch trace、flow-byte parity 和多租户前台到达时的抢占验证，防止未来优化重新引入隐性分叉。

### 7.3 P1：把 drain 纳入正式部署 gate

当前 gate 只限制 p99/miss/quality/background。下一版应新增：

- mean/p95 drain time；
- 总网络 bytes 或 energy budget；
- idle capacity 占用上限；
- background deadline/TTL；
- 多租户下不得抢占其他租户 foreground。

### 7.4 P1：将因子化结构迁移到学习型 controller

不要恢复单个 27-state Q 表。分别学习 admission、congestion scheduling、slack scheduling 与 deferred-drain 四个低维模块，并对每个模块设置单独 gate 和 replay coverage。

### 7.5 P1：观测误差与外部有效性

加入容量估计误差、1–5 epoch 信号延迟、突发到达、多瓶颈和多租户；随后在 packet-level simulator 或真实 trace 上复核三项 broad 效应。

### 7.6 P2：修复指标语义

- speculative bytes 拆为 useful completed、late unused、cancelled inflight；
- quality 改为任务正确率、检索召回或 judge score；
- background 只统计在 TTL 内仍有业务价值的字节；
- 统一报告 foreground latency、network makespan 和 total bytes。

## 8. 建议的论文结构

1. 原问题与预注册三假设。
2. 失败发现：original pressure 与学习型多 seed 不稳定。
3. 可辨识性方法：full-reference pairing、pivotal/nonjoint、场景分层统计。
4. 核心方法：三信号因子化控制器。
5. broad 3/3 独立确认。
6. 全局风险：background starvation。
7. 原语义公平前沿的负结果。
8. eligible-window 生命周期扩展与独立确认。
9. 限制：proxy quality/waste、生命周期假设、drain/utilization/waste 代价和外部有效性。

论文主贡献应是“可辨识的因子化三信号网络控制 + 公平生命周期拆分”，而不是笼统宣称一个 Q-learning controller 全面优于 baseline。

## 9. 复现命令

从 `organized_code_files/student15267` 目录运行。

因子化三项 broad confirmation：

```bash
python3 -m specnet_proofs.factorized_signal_study --mode confirm \
  --frozen-candidate specnet_proofs/results/factorized_signal_smoke_v1_20260730/selected_candidate.json \
  --output-dir specnet_proofs/results/factorized_signal_confirm_v1_20260730
```

原语义公平负结果：

```bash
python3 -m specnet_proofs.factorized_background_study --mode select \
  --frozen-factorized-candidate specnet_proofs/results/factorized_signal_smoke_v1_20260730/selected_candidate.json \
  --output-dir specnet_proofs/results/factorized_background_select_v1_1_20260730

python3 -m specnet_proofs.factorized_background_deficit_study --grid refined \
  --frozen-factorized-candidate specnet_proofs/results/factorized_signal_smoke_v1_20260730/selected_candidate.json \
  --output-dir specnet_proofs/results/factorized_background_deficit_refined_v1_20260730
```

Eligible-window 独立确认；同一命令会复用逐场景 checkpoints：

```bash
python3 -m specnet_proofs.factorized_background_eligible_window_study --mode confirm \
  --frozen-factorized-candidate specnet_proofs/results/factorized_signal_smoke_v1_20260730/selected_candidate.json \
  --output-dir specnet_proofs/results/factorized_background_eligible_confirm_v3_20260805
```

确认后配对审计：

```bash
python3 -m specnet_proofs.eligible_window_paired_audit \
  --confirmation-dir specnet_proofs/results/factorized_background_eligible_confirm_v3_20260805 \
  --output-dir specnet_proofs/results/eligible_window_paired_audit_v2_20260805
```

当前 package-aware 单元测试：`68/68` 通过。

## 10. 最终判断

三周工作的最重要进展不是把所有旧假设都改成支持，而是建立了清晰的证据层级：

1. 原始不支持结论被保留；
2. 条件效应与 broad 效应被严格分开；
3. 因子化机制真正实现了同一控制器 broad 三项支持；
4. 全局审计及时发现 background starvation；
5. 两轮原语义公平权重失败后，没有降低门槛，而是提出并独立确认了具备严格前台 parity 的 lifecycle-aware eligible-window；
6. 新突破的 utilization、drain、数值 floor 容差和业务生命周期假设仍被完整保留。

因此，当前成果可以作为有说服力的机制论文原型和下一轮系统实验基础；距离真实部署仍需完成生命周期可行性、真实质量指标、TTL/能耗门与多租户外部验证。

## 11. 2026-08-05 有限 TTL 部署附录

在原报告的 v3 无界 lifecycle 确认之后，新增了不调控制器参数的有限 TTL 压力协议。它先发现 TTL=`512` 在独立确认中只通过 `74/81` cell floor，随后将逐 workflow floor 升为硬门；又发现 deferred flow 即使权重为零，若仍保留在 foreground busy period 的 active set 内也可改变 pressure 输入和一条 speculative-waste 轨迹。因此当前 v5 机制只在全局无 foreground flow 的 quiescent epoch 物化 deferred debt，并将未过期欠额纳入结束条件。

新验证从冻结候选 `{0, 64, 256, 512, 1024, 2048}` 中选择最小可行 TTL=`2048`，再在 `27 scenarios x 3` 全新 runs 中独立确认：mean background=`0.228058`，cell/workflow floor=`1.000/1.000`，quality=`0.992527`，utilization delta=`+0.056229`（预算 `+0.08`），action/state/latency/waste parity 全部为零 mismatch。该结论有严格的业务前提，不能把 `2048` simulator epochs 直接写成产品 TTL 或能耗 SLO。

完整证据、失败路径、文献启发和复现命令见 [`DEPLOYMENT_TTL_ADDENDUM_20260805.md`](DEPLOYMENT_TTL_ADDENDUM_20260805.md)；汇报浓缩版见 [`WEEKLY_TTL_UPDATE_20260805.md`](WEEKLY_TTL_UPDATE_20260805.md)。
