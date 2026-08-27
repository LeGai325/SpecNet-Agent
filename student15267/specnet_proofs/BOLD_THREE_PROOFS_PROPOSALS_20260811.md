# 三项证明的大胆扩展与已运行实验（2026-08-11）

本轮不再只问“平均指标是否改善”，而是把证明升级为机制、反事实和部署边界三层。

## 证明一：源端 admission 与队列调度的因果独立性

命题：SpecNet 的收益主要来自降低 speculative offered load，而不是把已有流简单排队。

实验采用 5 种 source action × 3 种 queue policy 的全交叉、36 个平衡场景、2 个 runs。结果见 [SOURCE_CONTROL_ISOLATION_REPORT.md](results/bold_source_isolation_smoke_20260811/SOURCE_CONTROL_ISOLATION_REPORT.md)。

关键观察：`full + critical_path` 的 p99 为 111.36、waste 为 63.44；在相同队列下改为 `moderate`，p99 平均下降 19.27、waste 下降 26.75，但 quality 降至约 0.942；仅改变队列策略时，`full + fifo` p99 达 495.00，`full + static_priority` 达 313.57。结论是 source admission 和 queue scheduling 都有独立作用，不能用单一 queue baseline 代替完整机制证明。

下一步大胆实验：将 source action 随机化成 contextual intervention，报告每个状态的 CATE（conditional average treatment effect），并用 doubly robust estimator 检查观测策略偏差。

## 证明二：策略是否接近每工作流可达上界

命题：当前策略的性能差距来自控制器表示能力，而非不可避免的系统约束。

本轮运行 oracle-gap smoke，42 个 workflow。结果见 [ORACLE_GAP_REPORT.md](results/bold_oracle_gap_smoke_20260811/ORACLE_GAP_REPORT.md) 和 `oracle_overall.json`：平均 oracle gap 为 `0.0387`，中位数 `0.0246`，P90 `0.1103`，正 gap 占 `73.81%`。oracle 平均 latency `24.36`，baseline `24.40`，说明平均差距不大，但长尾 workflow 仍存在明显可优化空间。

这支持一个更强、也更诚实的结论：控制器不是“全局最优”，但多数 workflow 已接近局部可达上界；后续应集中优化 P90/P99 gap，而非继续优化整体均值。

下一步大胆实验：增加 constrained contextual bandit、per-workflow oracle action logging 和 regret decomposition，把 gap 按 congestion/slack/pressure 三个维度分解。

## 证明三：有限 TTL 的部署韧性包络

命题：background 公平性可以在不改变 foreground 轨迹的前提下，通过有限 TTL 实现。

本轮 TTL validation 使用 9 个场景、冻结 TTL 网格 `[1024,...,2048]`。结果见 [TTL_RESILIENCE_ENVELOPE_REPORT.md](results/bold_ttl_validate_20260811/TTL_RESILIENCE_ENVELOPE_REPORT.md)：所需 TTL 最大值 476，预注册网格选择 1024；所有 deployment gates 通过，background floor cells/workflows 均为 1.0，quality `0.9915`，foreground parity `1.0`，但 utilization 增量 `+0.0647`、平均 drain `13.33` epochs，说明公平性代价被转移到后台排空时间。

下一步大胆实验：对 TTL 做 adversarial burst、capacity drop 和 arrival blackout 三种故障注入，目标不是重新调参，而是绘制“TTL–drain–expiry”韧性包络；任何过期都应作为失败，而不能用平均 background 抵消。

## 统一验收标准

三项扩展实验都必须保留 paired delta、95% CI、Holm 校正、每场景支持数、最差 cell 和资源代价。任何只在平均值成立、但 P99/最差 cell 失败的结果，标为探索性证据。

复现实验：

```bash
cd 科研/organized_code_files/student15267
python -m specnet_proofs.source_control_isolation --mode smoke --output-dir specnet_proofs/results/bold_source_isolation_smoke_20260811
python -m specnet_proofs.oracle_gap_study --mode smoke --output-dir specnet_proofs/results/bold_oracle_gap_smoke_20260811
python -m specnet_proofs.ttl_resilience_envelope_study --mode validate \
  --frozen-factorized-candidate specnet_proofs/results/factorized_signal_confirm_v1_20260730/selected_candidate.json \
  --output-dir specnet_proofs/results/bold_ttl_validate_20260811
```

