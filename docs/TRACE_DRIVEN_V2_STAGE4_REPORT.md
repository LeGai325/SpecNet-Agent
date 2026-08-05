# Trace-driven V2-A 阶段四报告

更新时间：2026-08-01

## 完成内容

- 在最新主线上恢复 `trace_driven_v1`、`trace_driven_v1_1` 运行入口；
- 新增 `--workload-profile trace_driven_v2`；
- TraceLab 保守映射为固定 `coding` workflow；
- RAGPulse 使用 input/output token、retrieval、history、web-search 数量映射为固定
  `rag_qa` workflow；
- BurstGPT 继续提供按自然日隔离的 arrival windows；
- train、checkpoint validation、evaluation 分别固定使用 train、validation、test split；
- workflow CSV 新增 workload profile、mode、record source、split、脱敏 ID、mapping
  version 和声明工作量字段；
- 增加 runtime workload 审计与 paired preflight 分析工具。

本阶段没有修改 reward、`ACTION_CONFIG`、Slack、Controller state、Guard 或网络调度。

## 映射边界

这是 V2-A，也就是“真实数据校准的固定模板模拟 workload”。RAGPulse 没有 step duration、
动态 DAG、真实 deadline 和网络 telemetry，因此这些字段没有伪造为真实数据。tau3-bench
仍完全排除在训练、validation 和参数拟合之外。

## 测试与 Smoke

实验侧单元测试共 74 项通过。V1.1 和 V2 均完成相同参数的三档负载 smoke，默认
synthetic 回归路径保持不变。

V2 smoke 的 evaluation workload 中，TraceLab/RAGPulse 约为 76%/24%；更大的 Pilot
中为 74.9%/25.1%，与冻结的 75/25 比例一致。

## 3-seed paired Pilot

两组都使用 train seeds `11,23,37`、30 episodes、独立 validation checkpoint、3 个
evaluation runs、相同 Controller/reward/Slack/Guard-off 配置。结果位于 Git 忽略目录：

```text
outputs/trace_driven_v1_1_stage4_pilot
outputs/trace_driven_v2_stage4_pilot
```

三个 seed 的 learned Controller 平均结果：

| Profile | Load | P99 | Deadline miss | Avg quality |
| :--- | :--- | ---: | ---: | ---: |
| V1.1 | light | 41.50 | 0.0% | 0.772 |
| V1.1 | medium | 223.48 | 9.41% | 0.770 |
| V1.1 | heavy | 555.63 | 29.01% | 0.765 |
| V2 | light | 33.19 | 0.0% | 0.764 |
| V2 | medium | 170.60 | 5.25% | 0.765 |
| V2 | heavy | 362.51 | 24.79% | 0.763 |

这里不能解释为“V2 Controller 更好”。V2 中约 25% 的 RAG workflow 本身工作量较小且
deadline 也更短；Pilot 的作用是验证场景覆盖和运行稳定性，不是比较两个 profile 的
算法优劣。RAG 与 TraceLab 在 medium/heavy 下的 miss ratio 接近，说明 RAG 映射没有
退化成无压力的简单样本。

## Slack 与训练覆盖

V2 的三个训练 seed 均访问 loose、normal、tight：

| Seed | loose | normal | tight |
| ---: | ---: | ---: | ---: |
| 11 | 82.5% | 7.4% | 10.1% |
| 23 | 69.4% | 10.8% | 19.7% |
| 37 | 77.1% | 9.6% | 13.3% |

因此训练 tight 稀缺问题没有复发。Heavy evaluation 中三个 seed 均保持
`loose < normal < tight` 的 deadline miss 风险顺序；Medium 的 tight 样本仍较少，三组
排序未全部稳定，需在更长训练和更多 evaluation runs 中复核。

## 90-episode paired preflight

随后按预定稳定训练配置完成了第二轮配对预实验。V1.1 与 V2 使用完全相同的：

- train seeds：`11,23,37`；
- 90 个训练 episodes；
- checkpoint：`30,45,60,75,90`，由独立 validation workload 选择；
- 5 个 evaluation runs，evaluation seed 为 `101`；
- `full` Controller、quality weight `1.6`、Guard-off；
- light、medium、heavy 三档负载。

结果与配对分析位于 Git 忽略目录：

```text
outputs/trace_driven_v1_1_stage4_preflight90_eval5
outputs/trace_driven_v2_stage4_preflight90_eval5
outputs/trace_driven_v2_stage4_preflight90_eval5/paired_preflight.json
```

下表使用实验 runner 在 `summary_aggregate.csv` 中报告的口径：先计算每个 evaluation
run 的 P99，再对 5 个 runs 求平均；表内为 3 个训练 seed 的均值。

| Profile | Load | P99 | Deadline miss | Avg quality |
| :--- | :--- | ---: | ---: | ---: |
| V1.1 | light | 54.30 | 0.00% | 0.774 |
| V1.1 | medium | 214.33 | 5.74% | 0.770 |
| V1.1 | heavy | 495.55 | 23.52% | 0.765 |
| V2 | light | 39.95 | 0.00% | 0.768 |
| V2 | medium | 166.17 | 3.89% | 0.768 |
| V2 | heavy | 361.86 | 17.47% | 0.764 |

V2 的数值仍不能被解释为“Controller 比 V1.1 更好”，因为两个 profile 的 workflow
组成不同。V2 中的 RAGPulse 样本占 25.11%，符合冻结的 25% 目标；其余 74.89% 为
TraceLab。Medium/heavy 下两个 source 的 deadline miss 接近，RAGPulse 样本没有退化成
完全无压力的简单任务。

V2 的三个模型分别选择了第 `60/90/60` 个 episode 的 checkpoint，说明延长训练后确实
使用到了后期 checkpoint，而不是总退回早期模型。训练状态中 tight 占比分别为
`12.44%/12.88%/15.00%`，三个 seed 都覆盖所有 Slack bucket，tight 稀缺问题没有复发。
合并三个 seed 后，Slack 风险分层如下：

| Load | loose miss | normal miss | tight miss |
| :--- | ---: | ---: | ---: |
| medium | 0.90% | 31.58% | 46.67% |
| heavy | 3.78% | 28.33% | 67.87% |

因此 medium 与 heavy 都满足 `loose < normal < tight`；heavy 下每个 seed 单独检查也全部
满足。V2 还通过了负载压力递增、test split 隔离、source mix、每个训练 seed 的 tight
比例不低于 5%，以及没有单一 action 占比达到 95% 等预检门槛。

与 30-episode Pilot 相比，90-episode 配置消除了 V2 的 action monopoly，并使 medium
P99 的跨 seed 标准差由 `16.23` 降至 `7.97`。Heavy 的平均 deadline miss 从 `24.79%`
降至 `17.47%`，但 Heavy P99 的跨 seed 标准差由 `22.55` 增至 `35.46`，说明高负载下
仍存在一定策略 seed 敏感性，不能宣称训练已经完全稳定。

## 当前结论

V2-A 已通过 90-episode workload 集成预检：数据 split、source mix、压力梯度、Slack
覆盖和风险排序均符合预期，当前不需要继续调整数据映射。训练稳定性比 30-episode
Pilot 有改善，但 heavy 下仍有 seed 波动。

Guard-off 下平均质量仍只有约 `0.764--0.768`，远低于新版 `0.95` 服务目标；这不是
V2 workload 接入失败，而是因为本轮故意没有启用系统质量保护。下一步应保持 V2
workload 和训练参数不变，单独运行 Guard-on 的系统质量约束预实验；若质量约束与
Controller 行为都正常，再进入 5-seed、10-run 正式实验。
