# Trace 数据三项消融与后续实验报告

更新日期：2026-08-06  
主结论：使用新获取的三个真实数据校准 trace profile 后，**质量安全后继机制**在隔离的 `test` split 上支持 congestion、slack、active speculative backlog 三项消融；但这一结论依赖较强的 optional-completion 护栏，尚不能解释为可直接部署的资源最优策略。

## 1. 数据与实验隔离

- 数据包：`data_profiles/bundles/SpecNet-Agent-Trace-Profiles-20260801.zip`
- 压缩包 SHA-256：`51cb7ca25623e77c70ceb00178ea681bc4a465423b949d17aa640d0ff3a66bcf`
- 三份 profile：V1、V2、V3；均为脱敏的真实数据校准模板 workload，而非生产日志回放。
- 参数选择：只使用 V3 `validation` split；正式结论只使用 V3 `test` split。V1/V2 `test` 仅作不调参的稳健性复核。
- 所有新证据与旧 synthetic 证据独立保存，不能合并显著性或写成同一项实验。

完整性、隐私审计和上游加载验证见 [`TRACE_DATA_PACK_VERIFICATION_20260806.md`](TRACE_DATA_PACK_VERIFICATION_20260806.md)。

## 2. 先出现的负结果与根因

直接迁移旧的冻结因子化规则时，三项主指标方向均为正，但 V3 `test` 的实际质量仅约 `0.763–0.788`，低于预设 `0.95` 门，因此三项都必须判为 `not_supported`。

根因不是预测 guard 失效，而是 upstream guard 只检查“选了多少 optional branches”，不保证这些分支会在 judge 前完成。原 slack 路径只提高 tight workflow 的关键流权重，个别样本中反而挤压了同 workflow 的可选质量分支：一个定位样本的 quality 为 `0.8551`，移除 slack 后升至 `1.0000`。这是一项必须保留的负迁移/负效应诊断，不能用质量门下调来掩盖。

原始失败证据：

- [`trace_factorized_signal_v3_guard_audit_20260806`](results/trace_factorized_signal_v3_guard_audit_20260806/)
- [`trace_factorized_signal_trace_driven_v3_candidate_smoke_20260806`](results/trace_factorized_signal_trace_driven_v3_candidate_smoke_20260806/)

## 3. 质量安全后继机制

协议：`2026-08-06.trace-factorized-three-signal-v3`，实现：[`trace_factorized_signal_study.py`](trace_factorized_signal_study.py)。

在不改变三项原始控制参数 `{congestion critical=1.50, congestion optional=0.75, slack critical=2.00}` 的前提下，增加两个只为修复“实际完成质量”而设的护栏：

1. 所有被选中的 optional 分支使用与三项状态无关的静态完成优先级 `100×`。
2. 对 `tight` workflow 的 selected optional 分支加 `1.5×` 补偿，抵消 slack 对该 workflow 关键流的单边加速；该补偿属于 slack 机制本身，避免其产生已观测到的质量负效应。

`100×` 与 `1.5×` 只在 V3 validation 的 18 个冻结场景上选择；之后代码、参数、场景、seed 规则全部冻结。validation 合并结果的三项质量可行比例均为 `1.0`，但不作为主结果。

## 4. 正式 V3 测试结果

测试设置：V3 `test`、18 个平衡压力场景 × 2 个独立 runs，`seed = 2260000 + eval_run*10000 + scenario_index`。差值定义为“消融 − 完整”，正值即移除变量后变差。每项都要求 broad 与排除另外两项同时最高状态的 nonjoint 切片，通过 95% CI、随机化检验、Holm 校正、`quality >= 0.95` 和覆盖门。

| 假设 | 主指标 | Broad delta [95% CI] | Holm p | Nonjoint delta | Full/Ablation quality | 结论 |
|---|---:|---:|---:|---:|---:|---|
| H1-C congestion | p99 latency | `+9.1465` [`+5.3670`, `+12.8609`] | `0.00025` | `+5.7147` | `0.9993 / 0.9995` | supported |
| H1-S slack | normalized latency | `+0.0566` [`+0.0450`, `+0.0681`] | `0.00020` | `+0.0412` | `0.9992 / 0.9993` | supported |
| H1-P backlog | speculative waste | `+66.1233` [`+63.6320`, `+68.6647`] | `0.00015` | `+61.3528` | `0.9986 / 0.9998` | supported |

正式可复查产物：[`results/trace_v3_test_v3_merged_20260806`](results/trace_v3_test_v3_merged_20260806/)。其中包含 manifest、两类 paired units、分析 CSV、判定 CSV 和自动报告。

## 5. 后续稳健性实验：V1/V2

不再选择参数，直接将 V3-validation 冻结的护栏用于 V1/V2 `test`。每份 profile 使用相同 18 场景、1 run，故它们是较小样本的外部稳健性检查，不替代 V3 的主测试。

| Profile | H1-C p99 delta | H1-S normalized latency delta | H1-P waste delta | 三项质量门 | 三项判定 |
|---|---:|---:|---:|---|---|
| V1 | `+13.4600` | `+0.0824` | `+71.2240` | 全部 `1.0` | 全部 supported |
| V2 | `+19.9293` | `+0.0672` | `+64.0029` | 全部 `1.0` | 全部 supported |

产物：[V1 merged](results/trace_v1_test_v3params_merged_20260806/)；[V2 merged](results/trace_v2_test_v3params_merged_20260806/)。

## 6. 后续资源代价审计

同一 V3 `test` 的 18 个配对场景表明，质量安全护栏不是免费改进：质量从 `0.784883` 升至 `0.999505`，同时 p99 latency 从 `84.345000` 增至 `202.394444`，link utilization 增加 `+0.221504`，服务字节能耗代理增加 `+3043.709941`，speculative waste/workflow 增加 `+54.753312`。这项诊断不改动三项主判定，但直接否定“已经资源最优/可部署”的过度表述。

详情见 [`TRACE_GUARD_RESOURCE_AUDIT_20260806.md`](TRACE_GUARD_RESOURCE_AUDIT_20260806.md)。

## 7. 创新点与当前边界

### 创新点

1. 将 synthetic 机制证明与 trace-driven 外部验证彻底分离，并使用 profile 内置 train/validation/test split，避免测试集调参。
2. 发现并量化“静态 action-quality 合格但 judge 前实际 optional 未完成”的缺口，而非仅报告漂亮的 latency/waste 方向。
3. 针对该缺口构造可审计的两层质量护栏：全局 completion floor 与 tight workflow 同域补偿；保留原始 slack 单边加速造成的负效应证据。
4. 支持可恢复批次运行和合并：每批次保持全局 scenario/run seed 索引，合并时拒绝重叠单元，再执行完整分层推断。

### 不能过度解释

- `100×` 是很强的优先级。虽然质量门通过，但可能带来 total bytes、链路利用率尾部、能耗代理、background drain 或公平性代价；本轮三项主试验没有把这些资源门误写成已通过。
- trace profile 是真实数据校准的固定模板，不是 production replay；deadline、队列、网络 telemetry 和反事实 action 仍由 simulator 提供。
- V1/V2 每份只运行一个 seed；只能说明没有立即出现方向反转，仍应扩展到多 seed。
- 旧的 background/TTL 结果使用不同的生命周期语义与 synthetic workload。它们不能直接套用为“trace TTL 已验证”。

## 8. 下一步：真正的 trace 后续实验

在不再改动三项主机制的前提下，应该新增独立的 trace-background/TTL 协议，并在 V3 validation 选择 TTL、在 V3 test 确认。每个 cell 至少记录并设硬门：

1. foreground exact parity（action、state、latency、waste）；
2. background 总服务与逐 workflow service floor；
3. TTL 内完成率、expiry shortfall、drain tail；
4. total served bytes、p95/p99 link utilization、能耗代理；
5. `100×` 护栏下的 optional/critical 公平性和 per-template 尾部质量。

在这些门完成前，当前可引用表述应是：**“在真实数据校准 trace profile 的模拟器映射中，质量安全因子化机制支持三项消融；部署资源代价仍待独立验证。”**
