# Trace-driven Workload V3 Candidate

更新时间：2026-08-01

状态：SWE-chat adapter、V3 profile、运行时入口、30-episode paired preflight，以及
5-seed、90-episode、10-run 的 V2 vs V3 正式配对实验均已完成并通过。正式结果见
[`TRACE_DRIVEN_V3_FORMAL_REPORT.md`](TRACE_DRIVEN_V3_FORMAL_REPORT.md)。

## 目标与实验边界

`trace_driven_v3_candidate` 是独立候选，不覆盖 `trace_driven_v2`，也不改变默认
`synthetic` workload。它只评估“增加真实 Agent workflow/tool/token/timing 数据”带来的
影响，本次没有修改 Controller、reward、action、网络模型或 Slack。

固定 source mix：

| 数据源 | 比例 | 作用 |
| :--- | ---: | :--- |
| TraceLab | 37.5% | 原 V2 coding-agent 对照 |
| SWE-chat | 37.5% | 真实用户 workflow、tool、token 和清洗后 timing |
| RAGPulse | 25.0% | 保持 V2 的 RAG 场景份额 |

因此总体场景仍是 75% coding、25% RAG。BurstGPT 继续提供 arrival/burst；deadline、网络、
queue 和 Controller 反事实仍由模拟器提供。source mix 在 Controller 指标产生前固定，原始
样本数量不决定权重。

## SWE-chat 转换

固定 revision：

```text
f66cca95b14caaa4177f7ed5eaa424608dadcffa
```

转换流程：

1. 只使用有结构化 conversation 的 session；
2. 先按 `content_hash` 去重；
3. repo 与 user 组成二分图，完整连通分量只能进入一个 split；
4. 大于 holdout 目标 25% 的组件固定进入 train，避免 validation/test 被单一 repo/user
   组件主导；
5. 输出 salted hash ID、聚合 token/tool/timing 和 provenance，不输出原始对话、路径、
   tool arguments、session/repo/user ID；
6. tool 名称保守映射到 `retrieval/tool/storage/llm`，仍使用固定 `coding` 模板，不虚构
   语义 DAG parents。

SWE-chat 内部可以保证 repo-user 组件不跨 split，但当前 TraceLab profile 没有可比较的 repo
identity，因此暂时无法排除 TraceLab 与 SWE-chat 之间的跨数据源 repo 重叠；该限制会保留
在 profile provenance 中。

本轮实际得到 5,717 个去重 workflow：train 3,948、validation 894、test 875。共 171 个
repo-user 组件，其中 6 个大组件固定进入 train；holdout 最大单组件分别为 202 和 172 个
session。

## 时间清洗

逐工具 timing 只在以下条件同时满足时使用：

- tool use 与 result 有相同 `tool_call_id`；
- 两端都有可解析 timestamp；
- interval 非负；
- interval 不超过 300,000 ms。

超过5分钟的 interval 可能包含用户暂停、异步等待或断线恢复，因此保留 tool 类型和计数，
但不用于 timing 校准。原始 `session_duration_seconds` 也不作为连续服务时间。

完整转换共观察到 348,568 次 tool use，其中 320,671 次得到可用清洗后 interval，覆盖约
92.0%；3,441 个超长 interval 和 8 个负 interval 被排除。不同 agent 的字段覆盖继续保留
在记录中，Claude Code timing 不外推给其他 agent。
缺少可用 timing 的 service 使用模拟器固定 anchor，不使用其他 agent 的 timing 填充。

## 构建与预检

大型文件和生成 profile 均位于仓库外：

```bash
export SPECNET_DATA_ROOT="/path/to/external_agent_data"
```

首次准备 TraceLab、BurstGPT、RAGPulse 和 SWE-chat 时，按
[`DATA_SETUP.md`](DATA_SETUP.md) 下载固定版本并校验 checksum。

生成脱敏 SWE-chat records：

```bash
python3 specnet_data/swe_chat_v3.py \
  --sessions \
    "$SPECNET_DATA_ROOT/quarantine/swe_chat/f66cca95b14caaa4177f7ed5eaa424608dadcffa/sessions.parquet" \
  --conversations \
    "$SPECNET_DATA_ROOT/quarantine/swe_chat/f66cca95b14caaa4177f7ed5eaa424608dadcffa/conversations.parquet" \
  --revision f66cca95b14caaa4177f7ed5eaa424608dadcffa \
  --output "$SPECNET_DATA_ROOT/processed/unified_trace_v3/swe_chat_workflows.jsonl" \
  --report "$SPECNET_DATA_ROOT/reports/swe_chat_v3_adapter_preflight.json"
```

构建独立 V3 profile：

```bash
python3 specnet_data/build_trace_profile_v3.py \
  --v2-profile "$SPECNET_DATA_ROOT/processed/trace_driven_v2/profile.json" \
  --swe-chat-records \
    "$SPECNET_DATA_ROOT/processed/unified_trace_v3/swe_chat_workflows.jsonl" \
  --output \
    "$SPECNET_DATA_ROOT/processed/trace_driven_v3_candidate/profile.json"
```

运行 profile/runtime preflight：

```bash
python3 specnet_data/audit_trace_profile_v3.py \
  --profile "$SPECNET_DATA_ROOT/processed/trace_driven_v3_candidate/profile.json" \
  --sample-size 10000 \
  --runtime-count 40 \
  --output \
    "$SPECNET_DATA_ROOT/reports/trace_driven_v3_candidate_preflight.json"
```

当前 preflight 已确认：三种 source 在 train/validation/test 中均存在且比例正确；V2 的
train/validation/test mode mix 保持不变；三种负载的 arrival 有序且位于模拟区间内；每个
workflow 仍有3个 required branch；tau3-bench 没有进入训练 profile。

## 主实验入口

通过 `--workload-profile` 显式启用：

```bash
python3 specnet_agent_experiments/specnet_agent_experiment.py \
  --workload-profile trace_driven_v3_candidate \
  --trace-profile-path \
    "$SPECNET_DATA_ROOT/processed/trace_driven_v3_candidate/profile.json" \
  --output-dir outputs/v3_candidate_smoke \
  --train-episodes 3 \
  --eval-runs 1 \
  --duration 800 \
  --max-workflows 30 \
  --max-time 2500
```

本轮已运行 1-episode、单负载的入口 smoke，训练与 evaluation 可以完成，输出中的 profile、
source、split 和 mapping version 均正确。该 smoke 只验证代码路径，不解释策略优劣。

## V2 vs V3 paired workload preflight

数据层使用相同的 30 个 seed、train/validation/test 三个 split、三档负载和最多 120 个
workflow。V2 与 V3 的每组 workflow 数和 arrival time 完全一致，因此这里只改变
record-to-template 映射，不混入新的到达随机性。报告位于仓库外：

```text
${SPECNET_DATA_ROOT}/reports/trace_driven_v2_v3_workload_preflight_pressure.json
```

复现命令：

```bash
python3 specnet_data/compare_trace_profiles_v2_v3.py \
  --v2-profile "$SPECNET_DATA_ROOT/processed/trace_driven_v2/profile.json" \
  --v3-profile \
    "$SPECNET_DATA_ROOT/processed/trace_driven_v3_candidate/profile.json" \
  --seeds 20260801:30 \
  --duration 2600 \
  --max-workflows 120 \
  --output \
    "$SPECNET_DATA_ROOT/reports/trace_driven_v2_v3_workload_preflight_pressure.json"
```

在 test/heavy 汇总中，V3 相比 V2 的 deadline 均值约低 0.9%，required branch work
约低 16.8%，optional branch work 约高 7.1%，总声明工作量约高 0.7%。train 和 validation
也呈现相同方向。这说明 V3 不是简单增加或减少总工作量，而是用 SWE-chat 的真实 tool
组成把一部分工作从 required 侧移到了 optional 侧。该变化会直接影响 Controller 的
speculation 取舍，因此正式实验需要同时报告 waste 和 quality。

## 30-episode、3-seed simulator preflight

V2 复用已经存在且参数完全一致的 `outputs/trace_driven_v2_stage4_pilot`；V3 输出和配对分析
位于：

```text
outputs/v2_v3_paired_preflight_pressure/v3_candidate
outputs/v2_v3_paired_preflight_pressure/paired_analysis.json
```

V3 运行完成后，可用下列命令复现配对分析：

```bash
python3 specnet_data/analyze_v2_v3_paired_preflight.py \
  --v2-dir outputs/trace_driven_v2_stage4_pilot \
  --v3-dir outputs/v2_v3_paired_preflight_pressure/v3_candidate \
  --workload-report \
    "$SPECNET_DATA_ROOT/reports/trace_driven_v2_v3_workload_preflight_pressure.json" \
  --output outputs/v2_v3_paired_preflight_pressure/paired_analysis.json
```

两组都使用 train seeds `11,23,37`、30 episodes、`15/30` checkpoints、独立 validation
选择、3 个 evaluation runs、duration 2600、最多 120 个 workflow，以及相同的 full
Controller、reward、Slack v2、single bottleneck、legacy action coupling 和 Guard-off。

三个 learned Controller 的均值如下：

| Profile | Load | P99 | Deadline miss | Avg quality | Waste/workflow |
| :--- | :--- | ---: | ---: | ---: | ---: |
| V2 | light | 33.19 | 0.00% | 0.764 | 25.65 |
| V3 | light | 30.87 | 0.00% | 0.770 | 24.82 |
| V2 | medium | 170.60 | 5.25% | 0.765 | 21.42 |
| V3 | medium | 164.95 | 2.47% | 0.764 | 27.30 |
| V2 | heavy | 362.51 | 24.79% | 0.763 | 21.62 |
| V3 | heavy | 365.41 | 20.06% | 0.763 | 27.85 |

这些数值不能解释为“V3 Controller 优于 V2”，因为 workload 映射本身不同。固定规则和
网络 baseline 的 medium/heavy P99 大多也降低约 1%--9%，说明 V3 required path 较轻是
一部分原因。更重要的预检结论是：V3 没有造成延迟、miss 或 quality 的整体退化；同时
medium/heavy waste 增加约 27%--29%，与 optional work 增加一致，需在正式实验中复核。

V3 evaluation 的有限样本 source mix 为 TraceLab 37.9%、SWE-chat 37.0%、RAGPulse
25.1%。SWE-chat 在 medium/heavy 下的 deadline miss 分别约为 0.8%/16.1%，并没有退化成
完全无压力的简单样本。

三个 V3 模型训练状态中的 loose/normal/tight 比例分别为：

| Seed | loose | normal | tight | Selected checkpoint |
| ---: | ---: | ---: | ---: | ---: |
| 11 | 73.5% | 10.3% | 16.3% | 30 |
| 23 | 71.1% | 11.5% | 17.4% | 15 |
| 37 | 74.2% | 10.3% | 15.6% | 30 |

因此 tight 训练样本稀缺没有复发。合并 evaluation 后，medium 的 loose/normal/tight miss
约为 0.7%/20.0%/20.0%，heavy 为 4.4%/27.9%/75.7%；风险分层顺序成立，heavy 下每个
seed 单独检查也成立。V3 还通过了 source/split、负载压力递增以及没有单一 action 占比
达到 95% 的门槛。

跨 seed P99 标准差方面，V3 在 light 下由 V2 的 4.8 降至 2.7，medium 由 16.2 略升至
17.2，heavy 由 22.6 升至 27.3。候选没有出现策略塌缩，但高负载 seed 敏感性也没有被
消除；正式实验仍应使用稳定训练配置和更多 seed。

此前还运行过一轮 duration 960、最多 40 workflow 的小参数 smoke，保存在
`outputs/v2_v3_paired_preflight`。该规模下 V2 与 V3 的 medium 都没有 Slack 压力，不能
用于评价数据或 Controller，因此只保留为“低压力配置不足”的诊断证据。

## 当前结论与尚未完成

V3 candidate 已通过数据、simulator preflight 和 5-seed、90-episode、10-run 正式配对
实验。正式实验中 V3 的 source/Slack/负载压力检查全部通过，heavy seed 波动和
speculative waste 也没有复现短训练 Pilot 的退化，可以作为当前首选公开 trace-driven
workload 候选；V2 继续保留为历史对照和回归基线。

仍需保留以下边界：SWE-chat 偏 coding，缺少真实 deadline、网络 telemetry 和非 coding
生产 Agent 日志；固定模板只使用真实可验证特征，不等价于真实动态 DAG 回放。tau3-bench
继续只作为外部 benchmark 候选，不进入训练、validation 或参数拟合。
