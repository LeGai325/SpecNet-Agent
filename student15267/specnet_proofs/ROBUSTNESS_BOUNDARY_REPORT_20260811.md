# 鲁棒性边界实验报告（2026-08-11）

本轮运行 eligible-window 有限 TTL validation，冻结因子化控制器参数，只扫描 TTL，不在验证集继续调节控制器。

结果目录：[bold_eligible_validate_20260811](results/bold_eligible_validate_20260811/)

## 关键结果

| TTL | background | workflow floor | expiry | drain | utilization 增量 | gates |
|---:|---:|---:|---:|---:|---:|---|
| 0 | 0.1013 | 0.142 | 0.865 | 0.22 | +0.00013 | fail |
| 64 | 0.1969 | 0.799 | 0.201 | 6.00 | +0.05012 | fail |
| 256 | 0.2131 | 0.916 | 0.084 | 7.33 | +0.05983 | fail |
| 512 | 0.2180 | 0.960 | 0.040 | 10.33 | +0.06197 | fail |
| 1024 | 0.2212 | 0.992 | 0.008 | 12.22 | +0.06292 | fail |
| 2048 | 0.2221 | 1.000 | 0.000 | 13.33 | +0.06301 | pass |

## 结论

1. TTL=2048 是本验证集满足全部 background、expiry、quality、foreground parity 和 utilization gates 的最小有限 TTL。
2. TTL=1024 虽然平均 background 达标，但仍有 workflow floor 和 expiry 失败，不能用平均值掩盖最差请求。
3. 当前失效边界不是前台性能退化，而是 background 的业务有效期不足；因此部署配置应把 TTL 作为显式 SLO 参数。

## 下一步必须做的故障注入

- capacity drop：在 workflow 完成后降低容量 30%/50%，测 TTL 是否过期；
- arrival blackout：连续无 foreground 的空窗与 burst 交替，测 drain 是否稳定；
- tenant isolation：给每个租户独立 floor，避免全局均值掩盖单租户饥饿。

本结果仍属于 simulator 语义下的部署边界，不等同于真实生产 SLO；需要 trace replay 与真实 token/字节计数确认。

