# 跨新数据 Profile 的 V5 资源实验（2026-08-11）

## 问题

检验冻结的 V5 staged minimum-quality 机制是否能在不同真实数据校准 profile 上，同时保持质量与资源约束。所有运行使用 capacity-consistent service pool；不再使用旧版仅缩放状态估计容量的实现。

## 协议

- 冻结策略：`base_optional_boost=1`、required-progress trigger=`0.75`、terminal boost=`96`；
- 对照：V3 static `100x` 和 V4 minimum-quality `96x`；
- 数据：trace-driven V1、V2、V3 candidate 的独立 `test` split；每个 profile 9 个平衡场景×1 run；
- 硬门：quality target、每 cell/template target、p99 不超过 V4、miss 不比 V4 高超过 `0.02`、served bytes 与 mean utilization 不高于 V4。

## 结果

| Profile | Quality | Served bytes | Δbytes vs V4 | Δutil vs V4 | Δp99 vs V4 | Δmiss vs V4 | 判定 |
|---|---:|---:|---:|---:|---:|---:|---|
| V1 | 0.9835 | 6395.21 | -3.50 | -0.00025 | -28.08 | -0.03297 | pass |
| V2 | 0.9779 | 6242.82 | -10.51 | -0.00089 | +0.69 | +0.00458 | fail |
| V3 | 0.9761 | 6165.01 | -21.01 | -0.00156 | -5.93 | -0.01587 | pass |

所有 profile 的 quality target ratio、template target ratio 和每 cell target ratio 均为 `1.0`；V2 的失败不是质量或平均资源问题，而是其相对 V4 的尾延迟 `+0.69`、deadline miss `+0.00458`。虽然该退化小于 protocol 的 miss 容忍量，但 p99 的“不得更差”硬门被触发，因此保留为失败。

## 解释

该结果支持“最小充分集合 + staged completion”通常显著减少相对 static-100x 的 served bytes（分别 `-1486.59`、`-1460.01`、`-1647.66`）和 utilization（约 `-0.09`），但不支持“一个固定 trigger 在所有 profile 上都支配 V4”。

下一步应在 validation split 上训练或选择受约束的 profile-aware controller：状态至少包括 `(congestion, slack, pressure, capacity_scale, optional_bytes)`；目标中纳入 served bytes，约束中纳入 quality、P99、miss 和 epoch-utilization P99。V2 保持独立 test，只能用于最终确认，不能用来回调当前参数。

## 工件

- [V1](results/bold_v5_v1_test_20260811/TRACE_DEPLOYMENT_V5_RESOURCE_REPORT.md)
- [V2](results/bold_v5_v2_test_20260811/TRACE_DEPLOYMENT_V5_RESOURCE_REPORT.md)
- [V3](results/bold_v5_v3_test_20260811/TRACE_DEPLOYMENT_V5_RESOURCE_REPORT.md)

