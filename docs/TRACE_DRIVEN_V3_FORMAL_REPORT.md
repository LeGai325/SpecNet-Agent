# Trace-driven V2 vs V3 正式配对实验报告

更新时间：2026-08-01

## 实验目的

本实验判断加入 SWE-chat 真实 Agent workflow/tool/token/timing 特征后的
`trace_driven_v3_candidate`，能否作为比 V2 更有代表性的公开数据驱动 workload。

这里比较的是 **workload profile**，不是两个 Controller 算法。V2 与 V3 使用同一套
Controller、reward、action、Slack、网络模型和随机到达；结果变化同时包含数据组成与
record-to-template 映射的影响，不能表述成“V3 Controller 优于 V2 Controller”。

## 固定配置

- 训练 seed：`11,23,37,53,71`；
- 每个 seed 训练 90 episodes；
- checkpoints：`30,45,60,75,90`；
- 每个 checkpoint 使用独立 validation workload、每档负载 5 runs 选择；
- 固定 `eval_seed=7007`，每档负载 10 个 evaluation runs；
- duration 2600，最多 120 workflows，max time 7000；
- full Controller、quality weight 1.6、Slack `total + 1.0`；
- single bottleneck、legacy action coupling、Safety Guard off。

输出与配对分析位于 Git 忽略目录：

```text
outputs/v2_v3_formal90_eval10/v2
outputs/v2_v3_formal90_eval10/v3_candidate
outputs/v2_v3_formal90_eval10/paired_analysis.json
```

数据层继续使用相同 BurstGPT arrival window。30 个独立 workload seed 的审计确认，V2 与
V3 每组 workflow 数和 arrival time 完全一致；train/validation/test split 隔离和 source
mix 均通过。

## Learned Controller 结果

表中为 5 个训练 seed 的均值 ± 样本标准差；每个 seed 先对 10 个 evaluation runs 求平均。

| Profile | Load | P99 | Deadline miss | Avg quality | Waste/workflow |
| :--- | :--- | ---: | ---: | ---: | ---: |
| V2 | light | 56.36 ± 4.08 | 0.00% ± 0.00 pp | 0.7727 ± 0.0023 | 26.71 ± 15.23 |
| V3 | light | 48.28 ± 1.31 | 0.00% ± 0.00 pp | 0.7692 ± 0.0002 | 26.24 ± 8.50 |
| V2 | medium | 151.68 ± 9.75 | 4.03% ± 0.24 pp | 0.7685 ± 0.0015 | 27.78 ± 13.56 |
| V3 | medium | 108.27 ± 0.61 | 2.19% ± 0.43 pp | 0.7661 ± 0.0011 | 25.37 ± 6.84 |
| V2 | heavy | 312.31 ± 34.68 | 17.85% ± 2.30 pp | 0.7641 ± 0.0006 | 31.63 ± 10.27 |
| V3 | heavy | 234.79 ± 4.00 | 12.24% ± 0.74 pp | 0.7630 ± 0.0005 | 23.18 ± 2.16 |

V3 相比 V2：

- light/medium/heavy P99 分别约低 14.3%/28.6%/24.8%；
- medium/heavy deadline miss 分别低约 1.83/5.61 个百分点；
- medium/heavy waste 分别约低 8.7%/26.7%；
- quality 低约 0.0011--0.0035，绝对差异较小；
- 五个训练 seed 的 medium/heavy P99 和 miss 全部同方向改善；
- heavy P99 的跨 seed 标准差由 34.68 降至 4.00，medium 由 9.75 降至 0.61。

正式训练还纠正了 30-episode Pilot 中“V3 waste 增加”的初步现象。90-episode checkpoint
选择后，V3 medium/heavy waste 的均值和跨 seed 波动都低于 V2，说明短训练 Pilot 的策略
尚未稳定，不能代表 V3 的最终 waste 行为。

## 为什么不能解释成 Controller 提升

paired workload 审计显示，V3 的总声明工作量与 V2 基本相同，但 test/heavy 的 required
branch work 约低 16.8%，optional branch work 约高 7.1%。Controller 可以放弃 optional
work，因此 V3 的关键完成路径天然更轻。

固定策略也出现相同方向：medium 下 critical-path-only、rule-aggressive、rule-balanced
和 rule-quality-preserving 的 P99 约低 24.6%/26.3%/25.5%/25.0%；heavy 下约低
19.5%/14.3%/12.6%/14.9%。因此正式结果主要说明 V3 形成了一个不同且更稳定的真实数据
校准 workload，而不是同一 workload 上 Controller 算法性能提高。

## Source 与 Slack 检查

V3 evaluation source mix 为 TraceLab 37.9%、SWE-chat 37.0%、RAGPulse 25.1%，符合冻结的
37.5/37.5/25 目标。SWE-chat 的 medium/heavy deadline miss 分别约为 2.6%/11.1%，随负载
上升，未退化成无压力的简单样本。

五个 V3 训练 seed 的 tight 状态占比为 7.45%--13.37%，均访问 loose、normal、tight。
合并 evaluation 后：

| Load | loose miss | normal miss | tight miss |
| :--- | ---: | ---: | ---: |
| medium | 0.32% | 41.11% | 57.41% |
| heavy | 3.17% | 22.90% | 68.45% |

medium/heavy 风险顺序成立，heavy 下五个 seed 单独检查也全部成立。V3 没有出现单一动作
占比达到 95% 的策略塌缩；V2 的 seed 53/light 出现 conservative 96.4%，但 light 本身没有
deadline miss，主要作为训练稳定性差异记录。

## 当前判断

V3 candidate 已通过完整的 5-seed、90-episode、10-run 正式配对实验，可以作为当前项目
首选的公开 trace-driven workload 候选；V2 应继续保留为历史对照和回归基线。

V3 的价值在于加入真实 Agent tool/token/timing 与 repo-user 级防泄漏 split，并得到稳定的
source、Slack 和压力覆盖。它仍不是生产日志回放：deadline、网络 telemetry、queue 和
反事实 action 结果仍由模拟器提供，固定 coding/rag 模板也不等价于真实动态 DAG。后续若
获得非 coding 生产 Agent 日志或真实 deadline/network telemetry，应作为 V4 数据增强，而
不是重新解释本轮结果。

## 复现命令

V2/V3 分别运行下列公共配置，只切换 workload profile、profile path 和 output dir：

```bash
python3 specnet_agent_experiments/specnet_agent_experiment.py \
  --workload-profile trace_driven_v3_candidate \
  --trace-profile-path \
    "$SPECNET_DATA_ROOT/processed/trace_driven_v3_candidate/profile.json" \
  --output-dir outputs/v2_v3_formal90_eval10/v3_candidate \
  --train-seeds 11,23,37,53,71 \
  --eval-seed 7007 \
  --validation-seed 507007 \
  --train-episodes 90 \
  --checkpoint-episodes 30,45,60,75,90 \
  --checkpoint-selection best_validation \
  --checkpoint-eval-runs 5 \
  --eval-runs 10 \
  --duration 2600 \
  --max-workflows 120 \
  --max-time 7000 \
  --quality-weight 1.6 \
  --controller-variants full \
  --loads light,medium,heavy
```

配对分析：

```bash
python3 specnet_data/analyze_v2_v3_paired_preflight.py \
  --v2-dir outputs/v2_v3_formal90_eval10/v2 \
  --v3-dir outputs/v2_v3_formal90_eval10/v3_candidate \
  --workload-report \
    "$SPECNET_DATA_ROOT/reports/trace_driven_v2_v3_workload_preflight_pressure.json" \
  --experiment-kind formal \
  --output outputs/v2_v3_formal90_eval10/paired_analysis.json
```
