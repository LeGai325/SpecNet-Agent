# V6 Budgeted-Staged 实现优化实验（2026-08-11）

## 实现改变

新增 [`trace_deployment_v6_budgeted_study.py`](trace_deployment_v6_budgeted_study.py)。V6 保留 V4 的精确最小质量 optional 集合和 V5 的 optional-completion barrier，但把 V5 在 trigger 后固定 `96×` 的优先级替换为：

```text
demand = selected_optional_remaining / (capacity × observable_completion_horizon)
urgency = clip((demand - 0.35) / 0.65, 0, 1)
terminal_multiplier = floor + (96 - floor) × urgency
```

`floor` 是保证 selected optional 不会因过低优先级而延长 workflow 生命周期的下限。该改变也使 V5 simulator 对 `v6_*` 变体强制使用相同 completion barrier，确保质量语义一致。

## 第一轮：无保底（floor=1）失败

在 V1/V2 validation 中，V6 的 p99 和 served bytes 相比 V4 分别增加约 `+22/+678` 与 `+18/+486`。根因是过低的 optional priority 拉长了 workflow，增加在固定 duration 内进入系统的后续工作；“优先级越低越省资源”的直觉在 closed-loop 到达模型中不成立。

## 第二轮：保底 floor=16

在 V1/V2 validation 的 9 场景×3 paired runs 中，V6 的总体均值相对 V4 均改善：

| Profile | V5 Δp99 / Δbytes | V6 floor=16 Δp99 / Δbytes | 质量 |
|---|---:|---:|---:|
| V1 | `-12.359 / -4.13` | `-16.683 / -8.33` | 0.9828 |
| V2 | `-6.068 / -8.12` | `-12.293 / -9.83` | 0.9762 |

质量 target ratio 在每个运行中均为 `1.0`。从平均效果看，V6 同时改善了尾延迟与服务字节。

## 不能忽略的限制

按每个 stress scenario 的三次 paired 均值施加零资源回归硬门后，V1 有 3 个、V2 有 3 个 scenario 仍因少量 served bytes 或 utilization 增量失败。V5 在同一设置下也有少量场景失败。因此：

- V6 是有前景的优化方向，但**尚不满足逐场景资源不回归的部署门**；
- 本轮不访问 test split，也不将 V6 报为跨 profile 成功；
- 当前主候选仍应为已确认的 V5；V6 仅作为 validation-only 分支。

## 下一步实现建议

应将资源门从“单个 seed 单元零差值”提升为预注册的、场景分层 paired CI 与实际资源容忍预算；同时为每个 workflow 增加 virtual byte-debt 账户。只有 byte-debt 未超限时才采用 V6 的低终端优先级，超限立即回退到 V5 completion reservation。该保护器必须在 V1/V2 validation 冻结后，首次只在未参与选择的 V3/test 确认。

## 工件

- [V1 floor=16, 3 runs](results/bold_v6_floor16_v1_validation_r3_20260811/V6_BUDGETED_REPORT.md)
- [V2 floor=16, 3 runs](results/bold_v6_floor16_v2_validation_r3_20260811/V6_BUDGETED_REPORT.md)
- `v6_vs_v4_pairwise_scenarios.csv` 保留每场景的 paired 均值与硬门结果。

