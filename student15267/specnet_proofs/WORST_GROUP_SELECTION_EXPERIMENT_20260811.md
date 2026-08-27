# 最差组约束选择实验（2026-08-11）

为避免全局平均掩盖资源或尾延迟风险，本轮新增 `worst_group_v5_audit.py`。分析器仅读取 validation screen，并把每个 V5 候选与同 seed、同 scenario 的 V4 参考逐对比较。

一个候选需要在以下每一个单位同时通过 quality、P99、miss、served bytes 和 utilization 门：

1. 按 load 的边际组；
2. 按 deadline 的边际组；
3. 按 optional scale 的边际组；
4. 按 capacity scale 的边际组；
5. 每个单独 stress cell。

## V1 validation

`b1,t.75` 与 `b4,t.75` 通过全部最差组门（2/9 可行）。更早触发或更高 base boost 的候选会在至少 3 个组增加 served bytes/utilization 或 P99。

报告：[V1 worst-group audit](results/bold_v5_v1_worst_group_20260811/WORST_GROUP_V5_REPORT.md)

## V2 validation

`b1,t.75`、`b4,t.25`、`b4,t.5`、`b4,t.75` 通过全部最差组门（4/9 可行）。早触发的 `b1` 与高 base boost `b16` 被最差 cell P99 或资源门拒绝。

报告：[V2 worst-group audit](results/bold_v5_v2_worst_group_20260811/WORST_GROUP_V5_REPORT.md)

## 结论与边界

最差组筛选比全局均值更严格，能在 validation 中提前排除一部分资源/尾延迟敏感候选；但此前冻结的 `b1,t.75` 仍在独立 V2 test 出现 P99 失败。因此它是**更可靠的 validation 选择协议**，不是对跨 split 分布漂移的最终解法。

下一轮应把 V1/V2 validation 合并为预注册的 group-robust training pool，并冻结一个 min-max 候选；V3、V2 test 保持不参与选择的确认集。若没有共同可行候选，则应停止扩展静态参数网格，转向在线预算/约束控制。

