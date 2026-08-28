# HANDOFF

更新时间：2026-08-06

## Pcrit / Score shadow v1 收尾

- Workflow Hint Collector v1、动态 DAG v1、Collector v1.1 和 Pcrit/Score shadow v1 已按
  依赖顺序重放到包含数据 PR #3 的最新主线；Pcrit 功能 checkpoint 为 `e99e748`。
- Pcrit 按 DAG position、连续 Slack 和完成 workflow 的平滑历史采用率计算，并按论文公式
  组合 CostDelay、remaining size、fanout、Age 和 SpecPenalty；所有未公开选择均可配置并
  写入 metadata。
- shadow 默认关闭，启用后只增加评分记录，不进入 Policy、Controller、Guard、reward 或
  Q-table；四个 profile、四类动态 fixture、三档容量共 48 个 preflight 组合通过。
- 合并后的实验模块全套 127 项测试通过。冻结 V3 的 off/shadow 联合 smoke 中，10 个既有
  输出文件逐字节一致；shadow 覆盖 782 条 flow、生成 13,168 条有限评分记录。
- 本阶段已经完成“可解释、无未来标签泄漏、可与 V3 联合运行”的 shadow scorer；尚未做
  Score 到 Traffic Class 的映射，也没有接入 Q0-Q3 或真实 QoS，因此不能声称端到端性能
  收益。
- 下一步决策点是：先做独立 Traffic Classifier shadow，或在课题组已有 QoS 模块可用时
  直接设计接口；在获得真实 parents/retry/pruning 和 Judge selection 日志前，不继续为了
  数值好看调整 Pcrit 权重。

## 本阶段结论

公开真实 Agent 数据路线的 V3 阶段已经完成，可以暂时告一段落。

- `trace_driven_v3_candidate` 已完成数据下载审计、脱敏转换、去重、防泄漏 split、独立
  profile/runtime、paired preflight 和正式实验；
- V3 可作为当前项目首选的公开 trace-driven workload 候选；
- `trace_driven_v2` 继续保留为历史对照与回归基线；
- 当前不建议只靠调整混合比例或映射公式启动 V4；应等真正新增的数据字段或场景；
- 下一阶段更值得优先合并真实 QoS queue、源端控制和 speculative-pressure 模块，再使用
  冻结 V3 配置复验 Controller。

## 仓库与数据边界

V3 调研、adapter、profile/runtime、测试、分析脚本和文档均由本功能分支维护；正式实验
输出位于 Git 忽略目录。继续工作前先运行 `git status --short`，不要把
`external_agent_data`、原始对话或完整实验 CSV 误提交到协作仓库。

## 已实现的 V3 代码

核心入口：

- `specnet_data/swe_chat_v3.py`：SWE-chat 全量脱敏转换、content hash 去重、repo-user
  连通分量 split 和 timing 清洗；
- `specnet_data/build_trace_profile_v3.py`：从冻结 V2 profile 与 SWE-chat records 构建独立
  V3 profile；
- `specnet_data/trace_driven_v3.py`：37.5% TraceLab、37.5% SWE-chat、25% RAGPulse 的
  固定模板 runtime；
- `specnet_data/audit_trace_profile_v3.py`：profile/runtime 准入审计；
- `specnet_data/compare_trace_profiles_v2_v3.py`：相同 seed 的 workload 结构和 arrival
  配对审计；
- `specnet_data/analyze_v2_v3_paired_preflight.py`：preflight/formal simulator 配对分析；
- `specnet_agent_experiments/specnet_agent_experiment.py`：新增显式
  `--workload-profile trace_driven_v3_candidate` 入口；
- `specnet_agent_experiments/test_trace_driven_v3_candidate.py`：profile、split、runtime 和
  CLI 回归测试。

本阶段没有修改 Controller state、reward、`ACTION_CONFIG`、Slack 公式、Guard 或网络调度。
默认 workload 仍为 `synthetic`，V3 必须通过 CLI 显式启用。

## 数据与版本

大型数据统一位于仓库外，由环境变量指向：

```text
${SPECNET_DATA_ROOT}
```

SWE-chat 固定 revision：

```text
f66cca95b14caaa4177f7ed5eaa424608dadcffa
```

核心生成物：

| 文件 | SHA256 |
| :--- | :--- |
| `processed/unified_trace_v3/swe_chat_workflows.jsonl` | `1b2c6044809d1635f6a71640230ffd67b2f41e3e489e300a69702ea218e37bce` |
| `processed/trace_driven_v3_candidate/profile.json` | `926046f52a10ba4b4387fdca3755e092c6245fc922e4a1bee7d8cc472bd144e6` |
| `reports/trace_driven_v3_candidate_preflight.json` | `5f828267b2f1a85aba175ba5b3996882252e48bba0015c6f33912a0c99ad2a6b` |
| `reports/trace_driven_v2_v3_workload_preflight_pressure.json` | `edfba1ad97562a784d79e6d1dcc0b8b4cbafcd6246415af5db12ee78ed05e241` |

SWE-chat 转换得到 5,717 个去重 workflow：train 3,948、validation 894、test 875。
repo-user 连通分量不跨 split；320,671/348,568 个 tool use 得到不超过 5 分钟的可用清洗
interval，覆盖约 92.0%。原始 session duration、超长 interval、负 interval 和无法证明的
DAG parents 不进入 runtime 校准。

当前 source 角色：

- TraceLab：coding-agent 数值与历史 V2 对照；
- SWE-chat：真实用户 coding-agent workflow、tool、token 和部分清洗后 timing；
- RAGPulse：RAG request 构成；
- BurstGPT 3：真实 arrival/burst；
- Trace Commons：仅 parser、parent 和 timing tail 校验；
- tau3-bench / Exgentic：只作 held-out benchmark 或 replay 候选，不进入训练；
- VTCode：不适配主 workload，已拒绝；
- 阿里内部数据：当前无法获得，保持 deferred，不阻塞公开数据路线。

## 实验完成情况

### Paired preflight

30 episodes、3 个训练 seed、3 个 evaluation runs 的高压力预实验已通过。低压力
`duration=960/max_workflows=40` smoke 因 medium 几乎全部 loose，只保留为“压力不足”的
诊断，不用于数据质量结论。

主要输出：

```text
outputs/v2_v3_paired_preflight_pressure/v3_candidate
outputs/v2_v3_paired_preflight_pressure/paired_analysis.json
```

### 正式 V2 vs V3 配对实验

固定配置：训练 seed `11,23,37,53,71`、90 episodes、`30/45/60/75/90` checkpoints、
每 checkpoint 每档 5 个 validation runs、固定 `eval_seed=7007`、每档 10 个 evaluation
runs、full Controller、quality weight 1.6、Slack v2、single bottleneck、legacy action coupling、
Guard off。

```text
outputs/v2_v3_formal90_eval10/v2
outputs/v2_v3_formal90_eval10/v3_candidate
outputs/v2_v3_formal90_eval10/paired_analysis.json
```

`paired_analysis.json` SHA256：

```text
ab4ac3c6b9fb457a5b8a720bc738d20fe602539d8979240c2227470a0ef6472b
```

Learned Controller 的五 seed 均值：

| Profile | Load | P99 | Deadline miss | Avg quality | Waste/workflow |
| :--- | :--- | ---: | ---: | ---: | ---: |
| V2 | light | 56.36 | 0.00% | 0.7727 | 26.71 |
| V3 | light | 48.28 | 0.00% | 0.7692 | 26.24 |
| V2 | medium | 151.68 | 4.03% | 0.7685 | 27.78 |
| V3 | medium | 108.27 | 2.19% | 0.7661 | 25.37 |
| V2 | heavy | 312.31 | 17.85% | 0.7641 | 31.63 |
| V3 | heavy | 234.79 | 12.24% | 0.7630 | 23.18 |

V3 的五个训练 seed 均覆盖 loose/normal/tight，tight 占 7.45%--13.37%；medium/heavy
风险顺序成立，heavy 下每个 seed 单独检查也成立；没有 95% action monopoly。两组各有
330 条 run 汇总和 24,970 条 workflow 记录，无未完成 workflow，evaluation 只使用 test
split。80 项实验侧单元测试通过。

解释边界：V3 的总声明工作量与 V2 接近，但 test/heavy required branch work 约低 16.8%，
optional branch work 约高 7.1%。固定策略也明显变快，因此不能把 V3 的数值描述为
Controller 算法提升；它说明 V3 是结构不同、数据证据更真实、训练结果更稳定的 workload。

详细报告：`docs/TRACE_DRIVEN_V3_FORMAL_REPORT.md`。

## 当前数据方案仍缺什么

V3 不是生产系统回放，以下字段仍由模拟器提供或缺失：

- 真实 deadline/SLO、用户取消和超时原因；
- 与 workflow 对齐的 queue occupancy、RTT、ECN、loss、链路容量和逐路径 telemetry；
- 可证明的动态 DAG parents、并行/依赖关系和多 control epoch；
- Controller action 的真实反事实结果；
- 非 coding 类真实生产 Agent 日志；
- 稳定、可作为 ground truth 的 outcome/quality 标签。

## 数据集下一步改进方向（V4 启动条件）

当前不建议为了版本号继续微调 V3 source mix、deadline 系数或固定模板公式。只有获得至少
一种实质新证据时，V4 才值得启动：

1. **真实 deadline/outcome：** 任务 SLO、取消、超时、成功、用户纠正或代码采纳；
2. **真实网络 telemetry：** 能与 workflow/step 对齐的 queue、RTT、loss、ECN、容量和路径；
3. **动态 DAG：** 原生 `step_id/parents`、开始结束时间、并行关系与多轮控制事件；
4. **场景扩展：** 规模足够且许可清楚的非 coding 生产 Agent 日志；
5. **项目自身日志：** 其他同学的真实 QoS、源端控制和 speculative-pressure 模块合并后，
   采集本框架可复现的执行日志。

如果启动 V4，必须：

- 新建 `trace_driven_v4_candidate`，不得覆盖冻结 V3；
- 固定 revision、license、checksum 和 provenance，raw 继续放在 quarantine；
- 先脱敏和 content hash 去重，再按 repo/user/session/任务连通分量切 split；
- benchmark 始终 held-out，不进入训练、validation 或参数拟合；
- 缺失字段保持 missing/simulated，不从语义猜测 DAG、deadline 或网络状态；
- 依次通过 profile audit、相同 arrival 的 workload paired preflight、3-seed Pilot，最后才运行
  5-seed、90-episode、10-run 正式实验；
- 同时报告 required/optional/total work，避免把 workload 变轻误写成 Controller 变强。

## 推荐的下一步

1. 用户审核本次改动后建立 Git checkpoint；
2. 合并其他同学的真实 QoS queue、源端 fanout/speculation control 和 speculative-pressure；
3. 先在 V2/V3 上运行最小回归，确认默认行为、source/split 和 Slack 覆盖不变；
4. 正式 Controller/ablation 结论优先使用冻结 V3，同时用 V2 保留历史可比性；
5. 没有新的真实字段前，不继续修改 V3 数据公式，也不启动 V4。

## 建议先阅读

1. `docs/TRACE_DRIVEN_V3_FORMAL_REPORT.md`
2. `docs/TRACE_DRIVEN_V3_CANDIDATE.md`
3. `docs/REAL_AGENT_DATA_V3_SURVEY.md`
4. `docs/PROJECT_STATUS.md`
5. `data_catalog/manifests/swe_chat_candidate_f66cca95.yaml`
