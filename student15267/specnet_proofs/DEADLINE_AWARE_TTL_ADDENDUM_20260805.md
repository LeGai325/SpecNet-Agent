# Deadline-aware TTL 部署附录（2026-08-05）

## 更新结论

在已确认的 quiescent eligible-window 机制上，仅改变**全局 foreground 空闲时** completed-owner deferred debt 的服务顺序：从等权分享改为 earliest-expiry-first。该无参数排序将当前 simulator 内、已经独立确认的有限 TTL 边界从等权机制的 `2048` epochs 收紧至 `1536` epochs，缩短 `25%`。

这是一项新的调度机制，而非对旧 `TTL=2048` 结果的事后重命名。live workflow 的三项控制路径、foreground busy-period 可见性隔离、20% floor 与每个确认 gate 都保持不变。

## 两阶段证据

| 阶段 | 数据隔离 | 结果 |
|---|---|---|
| validation | 27 平衡场景 x 1 run，seed base `2410000` | 预先定义的 `{1536, 1664, 1792, 1920, 2048}` 全部通过；按最小可行规则选择 `1536` |
| confirmation | 27 平衡场景 x 3 fresh runs = 81 cells，seed base `2420000` | `1536` 通过所有冻结 hard gates |

## 独立确认结果

| 指标 | `1536` earliest-expiry-first |
|---|---:|
| Cells / workflows | `81` / `4613` |
| Mean background service | `0.228790` |
| Background floor cells / workflows | `1.000 / 1.000` |
| Deferred expiry fraction | `0.000000` |
| Foreground parity | `81/81` cells，action/state/latency/waste 全零 mismatch |
| Mean quality | `0.992920` |
| Mean delta link utilization | `+0.052574` |
| Mean p99 completion-to-floor lag | `189.589` epochs |
| Maximum observed completion-to-floor lag | `1504` epochs |
| Mean post-foreground drain | `25.506` epochs |

`1536` 只是在本次预定义候选表中最小的通过值，不能写成理论最小 TTL，更不能直接转换成毫秒级线上 SLO。

## 为什么排序能收紧 TTL

等权分享会让临近到期的欠额与更晚到期的欠额同时分走空闲容量。此前 `1920` 的 validation 曾通过，但其独立 confirmation 出现 `2` 个 workflow 过期，故被拒绝。

earliest-expiry-first 只在没有任何 foreground flow 时服务 deferred debt，并优先最早到期的 owner。它借鉴 EDF 的排序思想，但不把 Liu 与 Layland 的单机实时可行性定理外推为本 simulator 的形式化保证。

## 仍需主动说明的边界

- 当前 resource gate 是 **mean** link-utilization 增量 `<= +0.08`；本确认的均值为 `+0.052574`，但单 cell 最大增量为 `+0.181218`。因此不能声称已经满足逐 cell 资源预算，后续应加入尾部/逐 cell resource gate。
- 与 `2048` 等权机制相比，`1536` 提高了 TTL 紧致性，但平均 drain 从 `18.889` 增至 `25.506` epochs；TTL 变短不等于每个生命周期成本都下降。
- 当前没有真实 background 价值、tenant、energy/NIC active time 或非平稳 trace；结论仍局限于单瓶颈 simulator 的扩展生命周期语义。

## 可追溯产物

- [validation 报告](results/deadline_aware_ttl_validate_v1_20260805/DEADLINE_AWARE_TTL_REPORT.md)
- [独立 confirmation 报告](results/deadline_aware_ttl_confirm_v1_20260805/DEADLINE_AWARE_TTL_REPORT.md)
- [确认 summary CSV](results/deadline_aware_ttl_confirm_v1_20260805/deadline_aware_ttl_summary.csv)
- [逐 workflow ledger](results/deadline_aware_ttl_confirm_v1_20260805/deadline_aware_ttl_workflows.csv)
