# 本周科研进展汇报要点（2026-08-05）

## 一句话结论

本周完成了对 eligible-window 公平机制的语义修复与独立确认：在不改变任何前台 workflow 的动作、状态、延迟、deadline miss、质量或 speculative waste 的前提下，将平均 background service 从原语义的 `0.1077` 提高到 `0.2305`，并保持因子化控制器的 congestion、slack、backlog 三项 broad/nonjoint 证明全部支持。

## 本周完成的工作

1. 复核 v2 生命周期机制，发现其在主 workflow 完成前就按 20% target 截断 background，违背“完成前保留原调度”的设计，并会改变前台轨迹。
2. 实现 eligible-window v3：仅在 workflow 完成后开放精确欠额；欠额只在没有 foreground flow 时服务，不计入 congestion/slack backlog。
3. 新增逐 workflow 前台 parity gate：action、decision state、latency、speculative waste 均须与原语义精确一致。
4. 使用验证集 `27 scenarios × 1 run`（`2290000` seed ledger）确认机制可行，再使用完全隔离的确认集 `45 scenarios × 3 runs`（`2300000 + run*10000 + scenario`）完成正式确认。
5. 对确认集的 `7,873` 个 workflow 做数值 floor 审计，区分真实违例与浮点舍入。
6. 更新三周总报告，撤回 v2 的公平结论，保留原语义下常数 boost 与 deficit-aware boost 无可行点的负结果。

## 最重要的新结果

| 指标 | v3 确认结果 | 解释 |
|---|---:|---|
| 场景 / runs | `45 × 3 = 135` cells | 独立 confirmation seeds |
| Mean background service | `0.230475` | 超过冻结门槛 `0.20` |
| Background floor cells | `1.000` | 135/135 cells 均达标 |
| Workflow floor | `1.000`（`1e-9` 容差） | 最小值 `0.19999999999999971`；最大 shortfall 仅 `3.05e-16` |
| Mean quality | `0.992547` | 超过 `0.95` 门槛 |
| p99 / miss / normalized latency / waste | 相对原语义均为 `0` 差异 | 不是“非劣”，而是该 simulator 中逐 workflow 精确一致 |
| Foreground parity | `135/135` cells | action、state、latency、waste 均零 mismatch |
| Mean post-foreground drain | `16.037` epochs | 生命周期扩展的显式代价 |
| Link utilization | `+0.059227` | 95% CI `[+0.057465, +0.061013]` |

背景收益也经确认后配对审计：`+0.122775`，95% CI `[+0.121638, +0.123913]`。因此这不是来自降低前台服务的伪收益。

## 三项参数证明

冻结控制器参数保持不变：

```text
congestion_critical_boost = 1.50
congestion_optional_scale = 0.75
slack_critical_boost      = 2.00
```

| 假设 | broad delta（ablation - full） | 95% CI | Nonjoint delta | 95% CI | Holm p |
|---|---:|---:|---:|---:|---:|
| H1-C：congestion → p99 | `+29.80244` | `[26.67533, 32.87014]` | `+31.84219` | `[27.80315, 36.14410]` | `0.00015` |
| H1-S：slack → normalized latency | `+0.09299` | `[0.08289, 0.10234]` | `+0.04611` | `[0.04327, 0.04912]` | `0.00015` |
| H1-P：backlog → waste | `+5.25746` | `[5.02971, 5.49711]` | `+5.51181` | `[5.18181, 5.85607]` | `0.00015` |

三项均通过 quality、coverage、broad 和 nonjoint gate。这个结论仍只适用于当前单瓶颈、trace-driven simulator 与明确的扩展 background 生命周期语义。

## 本周创新点

1. **从平均 QoS 到反事实 parity。** 不再只检查 p99/miss 是否变差；直接要求每个 workflow 的决策和关键前台结果一致，防止 deferred background 暗中改变前台路径。
2. **精确欠额而非权重补偿。** 主任务完成后只暴露达到 `20%` 所需的剩余字节，避免 v2 的提前截断和服务 overshoot。
3. **证据自我纠错。** 主动发现并撤回 v2 的过强说法，改用新 seed ledger 重做验证与确认，而不是保留“更好看”的旧结果。
4. **数值语义可审计。** 新增 workflow floor 重放审计，证明严格比较中的 `0.199999999999999…` 是浮点舍入而非真实公平失败。
5. **机制证明与部署边界分层。** 原始生命周期下的无可行权重前沿继续保留；v3 只说明允许延迟 background 时存在可行机制，不替代部署论证。

## v2 与 v3 的关键区别

| 项目 | v2（历史） | v3（当前可引用） |
|---|---|---|
| target 截断时机 | workflow 完成前可能提前截断 | 仅 workflow 完成后开放精确欠额 |
| 前台轨迹 | 可发生分叉 | action/state/latency/waste 精确一致 |
| 结论状态 | 保留用于定位缺陷，不作为公平结论 | 独立 confirmation 全部 gate 通过 |
| Mean background | `0.2437` | `0.2305` |
| Drain | `22.76` epochs | `16.04` epochs |
| Waste 差异 | `+0.5145` | `0.0000` |

## 必须诚实说明的限制

- 原 simulator 生命周期下，常数 background boost 和 deficit-aware boost 仍没有同时通过 background、p99、miss；v3 不覆盖这一负结果。
- v3 需要 background 工作在主请求返回后仍有业务价值，并允许使用空闲网络容量继续完成。
- `16.04` epochs drain 和 `+0.05923` utilization 是实际成本；当前 gate 尚未对能耗、总 bytes、TTL 或多租户公平设上限。
- quality 是 retained-branch proxy，不是任务正确率；waste 也不等价于真实网络无效传输。
- 不能把 original-ratio H1-P 写成支持；当前支持的是因子化控制器下的 `active_speculative_backlog` 路径。

## 下周建议

| 优先级 | 工作 | 可交付结果 |
|---|---|---|
| P0 | 在真实 Agent trace 标注 background 的业务价值与 TTL | 判断跨请求生命周期假设是否成立 |
| P0 | 将 drain、total bytes/energy、idle-capacity 占用加入 hard gate | 从“证明可行”走向“部署可评估” |
| P1 | 做 1–5 epoch 信号延迟、容量误差、burst arrival 消融 | 检验三项机制的稳健性 |
| P1 | 加入多租户 foreground 到达 | 验证 idle-only 规则不抢占其他租户 |
| P2 | 将四个因子化模块迁移到低维学习控制器 | 避免恢复不稳定的 27-state 单 Q 表 |

## 90 秒汇报口径

“本周我们没有继续调权重，而是先审计了公平机制的语义。审计发现旧 v2 在主任务结束前提前截断 background，因此会改变前台路径；我们撤回了它的公平结论。随后提出 v3：主任务期间完全按原策略运行，完成后才把不足 20% 的 background 欠额放到 idle capacity 上服务。新 confirmation 使用 45 个场景、3 组独立 seeds。结果显示 background 从 0.1077 提升到 0.2305，同时 135 个 cell 的前台 action、state、latency、miss 和 speculative waste 与原语义全部精确一致。三项信号 congestion、slack、backlog 在 broad 与 nonjoint 下也全部显著支持。代价是 16 个 epoch 的后置 drain 和约 0.059 的链路利用率增加。下周重点是验证 background 跨请求生命周期是否真实有价值，并将 TTL、能耗和多租户公平纳入部署门。”

## 可追溯产物

- [v3 独立确认](results/factorized_background_eligible_confirm_v3_20260805/FACTORIZED_BACKGROUND_ELIGIBLE_WINDOW_REPORT.md)
- [v3 配对语义审计](results/eligible_window_paired_audit_v2_20260805/ELIGIBLE_WINDOW_PAIRED_AUDIT.md)
- [workflow floor 数值审计](results/eligible_window_floor_audit_v1_20260805/ELIGIBLE_WINDOW_FLOOR_AUDIT.md)
- [更新后的三周总报告](THREE_WEEK_PROOF_REPORT_20260802.md)
