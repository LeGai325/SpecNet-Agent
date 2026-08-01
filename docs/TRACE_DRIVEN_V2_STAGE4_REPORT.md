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

## 当前结论

V2-A 的代码接入、split 隔离、source mix、压力梯度和训练 bucket 覆盖已经通过第一轮
验证。暂时不直接启动 5-seed 正式实验，原因是 30-episode Pilot 仍有明显策略 seed
敏感性，且 Guard-off 下平均质量没有达到新版 0.95 服务目标。

下一步保持 workload 映射不变，先使用推荐的 90-episode 稳定训练配置做 paired
preflight；同时把 Guard-off 的 Controller 机制结果与 Guard-on 的系统质量约束结果分开
报告。通过后再进入 5-seed、10-run 正式实验。
