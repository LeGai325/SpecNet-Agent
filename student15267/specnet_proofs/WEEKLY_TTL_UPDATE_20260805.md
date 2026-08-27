# 本周汇报补充：有限 TTL 部署边界

> 后续更新：本文记录的 `TTL=2048` 是等权 deferred scheduler 的独立确认基线。earliest-expiry-first 的新机制已把已确认边界收紧到 `TTL=1536`；请同时阅读 [最新部署附录](DEADLINE_AWARE_TTL_ADDENDUM_20260805.md)。

## 结论

本周在原有三项参数机制证明之外，完成了 background 生命周期的有限 TTL 压力确认。最终可引用结果是：在 quiescent deferred-background 语义和 TTL=`2048` epochs 下，`27 scenarios x 3 runs = 81` 个独立 cells 的 background floor、逐 workflow floor、质量、资源预算和前台 action/state/latency/waste parity 全部通过。

## 汇报数字

| 指标 | 结果 |
|---|---:|
| Mean background | `0.228058` |
| Cell / workflow floor | `1.000 / 1.000` |
| Foreground parity | `81/81` cells，全部 mismatch 为 `0` |
| Quality | `0.992527` |
| Delta utilization | `+0.056229`（预算上限 `+0.08`） |
| Mean p95 completion-to-terminal lag | `189.260` epochs |
| Mean post-foreground drain | `18.889` epochs |

## 这次真正解决了什么

- 将 deferred background 从前台 busy period 的 active set 中完全移出，而不仅是将权重置零，避免它污染 pressure 特征。
- 将公平约束从“cell 平均服务过线”提升为“每个 workflow 均达到 20% floor”。
- 给无界 lifecycle 假设给出最小可行 TTL 边界，并以新 seed confirmation 验证。

## 必须主动说明的负结果

- 首轮最小 TTL `512` 在独立确认中只有 `74/81` cells 通过；因此被拒绝。
- 随后的 `2048` 版本曾在 `1/81` cell 中改变 speculative waste；因此被拒绝并定位为 deferred flow 残留于压力特征的问题。
- 当前通过的是修复后的 v5 quiescent 机制，不是对旧 v3 结果的直接外推。

## 60 秒口径

“我们把上一阶段的无界 background 生命周期假设改成了可验证的有限 TTL 问题。最初 `512` epochs 虽然平均服务达标，但独立确认发现少数 workflow 在 TTL 内仍被饿死；随后又发现仅把 deferred flow 权重设为零还会让它留在压力特征中，导致一条 speculative-waste 轨迹分叉。我们因此将 deferred bytes 在整个前台忙期完全隐藏，只在全局空闲时物化，并将逐 workflow 20% floor 设为硬门。最终在 27 个场景、3 组全新 seeds 的 81 个 cells 中，TTL=`2048` 同时达到 0.228 background、全部 workflow floor、零前台差异和 +0.056 的资源增量预算。这说明可行性存在，但 2048 epochs 的业务价值、能耗和多租户公平仍是下一步部署前提。”

详细证据与限制见 [有限 TTL 部署附录](DEPLOYMENT_TTL_ADDENDUM_20260805.md)。
