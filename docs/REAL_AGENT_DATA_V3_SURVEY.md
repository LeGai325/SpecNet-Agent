# 真实 Agent 数据 V3 调研与预检

更新时间：2026-08-01

状态：候选筛选、SWE-chat 全量 adapter、独立 V3 profile/runtime、paired preflight 和
5-seed、90-episode、10-run 正式配对实验均已完成。V2 未被覆盖，继续作为历史回归基线；
正式结果见 [`TRACE_DRIVEN_V3_FORMAL_REPORT.md`](TRACE_DRIVEN_V3_FORMAL_REPORT.md)。

## 1. 结论先行

本轮没有找到一个能同时提供真实 workflow、网络队列、deadline 和 Controller 反事实的
“完整数据集”。但找到了一个明显值得继续推进的新主候选：

1. **SWE-chat：P0，预检后有限准入。** 它是目前最适合补充真实用户 coding-agent
   workflow、tool、token 和部分时间证据的数据源；下一步只开发独立 V3 candidate adapter，
   暂不直接进入正式训练 profile。
2. **Trace Commons：P1，只作为真实结构和 tool-duration 校验集。** 字段很好，但只有
   30 条 session，不能承担训练分布。
3. **VTCode Sessions：不接入主 workload。** 发布页写 100 sessions，但实际是 100 个
   多格式文件，只对应约 45 个底层 session；逐 step timing 和独立 tool call 都太少。
4. **Exgentic v2：不进入真实 workload。** 它是 benchmark 执行轨迹，而且 v2 主动删除
   `execute_tool` spans；可作为未来外部评估或 LLM replay 数据，但不能冒充真实用户日志。

因此 V3 的合理路线不是立刻替换 V2，而是：保留 TraceLab + BurstGPT 的 V2 基线，增加
一个可配置的 SWE-chat V3 candidate profile，与 V2 做 paired comparison。SWE-chat 只补
它实际拥有的 workflow/tool/token/timing 字段；arrival 仍由 BurstGPT 提供，deadline、网络
与队列仍由模拟器提供。

## 2. 本轮筛选标准

候选数据按四类作用分别判断，不把一个总分当作结论：

| 角色 | 需要的真实字段 | 主要用途 |
| :--- | :--- | :--- |
| Workflow | session、step、tool、顺序或 parents | 构造 Agent workflow |
| Timing/load | arrival、start/end、duration、token | 校准服务时间和压力 |
| Outcome | success、error、用户纠正、代码采纳 | 校准质量和失败场景 |
| Stress | burst、长尾、重试、高 fanout | 补充 tight Slack 场景 |

继续遵守以下边界：

- benchmark 可以做 held-out evaluation，不能作为“真实生产 workload”；
- 没有语义 parents 的日志不能凭空生成动态并行 DAG；
- 没有真实 deadline、queue 或网络 telemetry 时，这些字段继续标为 `simulated`；
- 原始对话、工具参数和路径先进入 `quarantine/`，不提交 Git；
- 同一 repo、user 或 session 不得跨 train/validation/test 泄漏。

## 3. 现有 V2 的基础与缺口

当前 V2 已经有：

- TraceLab：真实 coding-agent round、tool、token、cache 和 tool latency；
- BurstGPT 3：真实 serving arrival、burst、token 与 session；
- RAGPulse：RAG request 组成；
- tau3-bench：只做未来 held-out 外部任务评估。

当前最明显的公开数据缺口是：

1. TraceLab 缺少 tool input 和真实任务结果，难以恢复语义 workflow；
2. 缺少较大规模、带时间戳和结果信号的真实 Agent session；
3. 缺少非 coding 场景的真实生产 Agent 执行日志；
4. 网络 RTT/loss/ECN/queue、真实 deadline 和 action 反事实仍无公开来源。

## 4. 候选适配矩阵

评分为 `0/1/2/3`：没有、间接推断、有限原生字段、较完整原生字段。

| 数据源 | 真实性 | W | L | O | S | 本轮决定 |
| :--- | :--- | :-: | :-: | :-: | :-: | :--- |
| SWE-chat | 真实开发者公开 coding-agent session | 3 | 2 | 2 | 2 | **P0：预检通过，有限准入 adapter** |
| Trace Commons | 开源项目中自愿捐赠的真实 coding-agent session | 3 | 2 | 0 | 2 | **P1：仅校验/fixture** |
| VTCode Sessions | 单个本地 workspace 导出的脱敏 session | 1 | 1 | 1 | 1 | **不接入主 workload** |
| Exgentic agent-llm-traces-v2 | 六类 benchmark 的执行轨迹 | 2 | 2 | 3 | 2 | **benchmark/replay only** |
| cx-cmu agent_trajectories | 多 benchmark、多模型、四次独立运行 | 2 | 1 | 3 | 2 | **held-out only，暂不下载** |
| ThoughtWorks agentic trajectories | 对真实 repo 的合成轨迹 | 2 | 0 | 2 | 1 | **不用于真实 workload** |
| HF Agent Sessions | 来源真实性和许可证未说明清楚 | 2 | 2 | 0 | 1 | **Gate A 不通过** |
| WebChain | 人工标注网页交互，837 GB | 3 | 1 | 2 | 1 | **不是 Agent runtime，暂不采用** |
| HF Agent Usage | Hub 上 Agent 请求份额的月/日聚合 | 0 | 1 | 0 | 0 | **粒度过粗，不采用** |

W/L/O/S 分别代表 Workflow、Timing/load、Outcome 和 Stress。评分只表示对 SpecNet 当前数据
缺口的适配程度，不代表数据集本身的研究质量。

## 5. SWE-chat：最佳新候选

### 5.1 为什么值得做

固定 metadata revision：

```text
f66cca95b14caaa4177f7ed5eaa424608dadcffa
```

官方数据卡当前记录：

- 205 个 repositories；
- 5,851 个 sessions；
- 2,692,480 条 conversation records；
- full transcript、tool use/result、thinking、queue operation 和 git history；
- conversation schema 包含 `timestamp`，assistant response 包含 input/output/cache tokens；
- session 有 duration、tool count、API call count 和代码归属统计。

这正好补 TraceLab 的两个主要短板：真实用户任务语义和结果侧证据。它仍然偏 coding，
但比 benchmark 轨迹更适合作为真实 workload 校准来源。

### 5.2 保守映射

| SWE-chat 字段 | Unified trace 候选字段 | 限制 |
| :--- | :--- | :--- |
| `session_id` | `workflow_id` | split 必须按 repo/user 连通分量隔离 |
| `turn_id` / `tool_call_id` | `step_id` | 只建立日志证明的顺序与 tool pair |
| `turn_type` / `category` | `service_type` | 需先检查类别覆盖，不能强行套固定 DAG |
| `timestamp` | step start/end 候选 | tool duration 需验证 use/result 均有时间戳 |
| input/output/cache tokens | request size | tokenizer 与模型差异需保留 provenance |
| tool/result、system event | error/retry 候选 | 需写稳定 parser |
| `session_success` | auxiliary outcome | 是 LLM annotation，不作为 ground truth |
| code attribution / commits | outcome supplement | 第二阶段才考虑下载 commits 表 |

没有真实 network、deadline 和 Controller action outcome；这些字段仍必须是 simulated。

### 5.3 下载与访问结果

数据集采用 ODC-BY，公开页面可见，但文件需要登录 Hugging Face 并同意共享联系信息。
本轮已由用户完成授权，并固定下载以下两个核心文件到仓库外的 quarantine：

核心文件状态：

| 文件 | 大小 | SHA256 | 用途 |
| :--- | ---: | :--- | :--- |
| `sessions.parquet` | 1,997,377 B | `2ada63973b18...d958a95` | session 级筛选和统计 |
| `conversations.parquet` | 1,311,422,253 B | `9ee1d937dbf7...f4f1c36` | turn/tool/timestamp/token |
| `checkpoints.parquet` | 5,564,169 B | 未下载 | repo/session 关系，可选 |

上表 SHA256 只对已下载的两个文件有效；`checkpoints.parquet` 仍是可选预算，并未在本轮
适配判断中使用。暂不下载 5,851 个 raw transcripts，也暂不下载 1.08 GB 的
`commits.parquet`。

### 5.4 100-session 预检结果

样本由 80 个 agent-stratified 一般会话和 20 个 duration/tool/token 长尾会话组成。它只用于
检查 schema 和尾部，不用于拟合总体分布。为了避开在线 Dataset Viewer 的分页限制，最终
审计直接读取固定 revision 的本地 Parquet；原始 conversation text 没有导出到报告。

主要结果：

- `sessions.parquet` 有 5,851 条，结构化 conversation 覆盖其中 5,785 条（98.87%）；缺失
  66 条中有 48 条来自 Gemini CLI，因此不同 agent 的字段覆盖并不一致；
- 100 个样本共 77,758 条 conversation record，timestamp 覆盖约 94.30%；
- 15,048 个 tool use 中 14,333 个可与 result 配对，整体覆盖约 95.25%；配对间隔
  P50/P95/P99 约为 0.378 s / 70.70 s / 600.07 s；
- Claude Code 样本的 tool-pair 覆盖约 99.88%，但 Gemini CLI、OpenCode 和 Codex 样本
  没有同等可用的逐工具 timing，因此不能把总体 timing 当成跨 agent 通用分布；
- 62/100 个样本按 turn number 检查到 timestamp 回退；极端 session duration 达数周，tool
  wall interval 也可能跨越用户暂停，接入前必须做 idle-gap segmentation 和时间清洗；
- assistant response 的 input/output token 字段在本样本中覆盖完整；保守隐私正则未命中，
  但这不等价于证明原始对话无隐私风险，raw 继续留在 quarantine；
- `session_success` 仍是 LLM annotation，只能作 auxiliary signal。

结论：SWE-chat **通过有限准入**。它适合开发独立 V3 candidate adapter，补充真实 workflow、
tool mix、token 和 Claude Code 为主的 paired-tool timing；它不直接替代 BurstGPT arrival，
也不能补齐真实 deadline、网络 telemetry、队列或 Controller action 反事实。

## 6. Trace Commons：全量预检结果

固定 revision：

```text
112ebd4d03ce852b00e935d523107c3d0c9a65bf
```

本轮完整下载了 30-row Parquet 到项目外隔离目录，并完成聚合审计：

- 70,202,603 B，SHA256
  `7c2c6ee4342ff014c47b906425501b4dc4f368df8af5280c158e827944da11e7`；
- 18,012 个 raw events，78.5% 带 timestamp，timestamp 均可解析；
- 13,461 条 parent link 全部能指向 session 内事件；
- 4,264 次 tool use 中 4,262 次能与 result 配对，覆盖率约 99.95%；
- tool-use/result wall interval P50/P95/P99 约为 1.06 s / 26.07 s / 124.65 s；
- 28 条标为 Claude Code，2 条 harness 缺失，当前并没有实质多 harness 覆盖；
- session gap P95 超过 21 小时，最长约 47 小时，说明存在暂停/恢复，不能把整段 session
  duration 直接当连续服务时间；
- 聚合扫描命中 6,136 个 home-path-like 字符串。它们可能包含已替换的占位符，并不等于
  确认泄露，但结合官方“best-effort anonymization”说明，raw 继续留在 quarantine。

结论：它很适合验证 parent、tool pairing 和真实长尾，但 30 条且自愿捐赠偏差很大，不能
用于拟合主训练 workload。它只作为 parser regression fixture 和 tail sanity check。

## 7. VTCode Sessions：为何不采用

固定 revision：

```text
78049282e2b4fddb2a6d93a0a0e4784a7bd05fc1
```

本轮下载并检查了全部 21,468,595 B 数据。主要问题：

- 发布目录的“100 sessions”实际由 44 个 raw session、44 个 harness export 和 12 个 ATIF
  export 组成；去重后约 45 个底层 session，而不是 100 个独立样本；
- 44 个 raw/harness session 的 message/event 没有逐 step timestamp；只有 12 个 ATIF
  export 的 85 个 step 带 timestamp；
- harness 中大量 `item.updated` 是同一输出的流式更新；去重后只有 22 个独立 tool
  invocation，虽然都能配到 output，但规模过小；
- 41 个 completed thread 的 outcome 只是 `exit` 或 `new_session`，不是任务质量标签；
- 聚合扫描仍检测到 333 个 home-path-like 字符串，继续保留在 quarantine。

结论：ATIF 格式本身可作为 parser 参考，但这批数据不能改善 V2 的 workload 分布、tight
样本或结果标签。因此不写入 V3 profile，也不继续为它开发 adapter。

## 8. 为什么没有下载其他大数据

### Exgentic v2

固定 metadata revision：

```text
4b8ad4ab198438e5a170f9171c19c6a2cf7c1814
```

它有 10,056 个 benchmark run、232 MB、精确 chat span 时间和 token，也有 score/success。
但官方构建流程明确删除 `execute_tool`、`invoke_agent` 等 span，只保留 chat spans。两条
实际 sample 的 `parent_span_id` 都指向被删除的父 span，无法由发布版恢复完整 tool DAG。
项目已有 tau3-bench 作为 held-out evaluation，因此现在下载全量的增量价值不高。

### cx-cmu agent_trajectories

8,653 条、2 GB、六个 benchmark、每任务最多四次运行，结果标签很强，但它仍是 benchmark
轨迹且需要接受 gated 条款。它适合以后扩展外部评估，不进入 Controller 训练 profile。

### SpecStory 公开历史

研究论文曾通过 GitHub Code Search 抓取公开 `.specstory/history/`，规模很大。但目前没有
找到固定、带统一许可和删除机制的官方数据 release。直接自行爬取会增加隐私、许可、版本
漂移和复现风险，因此本轮不做。

## 9. 下一步执行方案

### 阶段 A：SWE-chat 权限与 sample preflight（已完成）

已完成登录授权、revision 固定、核心 Parquet 下载、100-session 聚合审计和 repo/user split
风险检查。预检发现的字段覆盖与时间异常已写入 5.4 节和 candidate manifest。raw 文件留在
quarantine，协作仓库只保存审计脚本与聚合结论。

### 阶段 B：V3 候选 profile（adapter 与入口已完成）

当前实现：

1. 保持 V2 reward、Controller、action 和训练参数不变；
2. 新增独立 `trace_driven_v3_candidate`，不覆盖 V2；
3. SWE-chat 只提供可验证的 session/step/tool/token/timing/outcome auxiliary；
4. BurstGPT 继续负责生产 arrival/burst，网络和 deadline 继续由 simulator 提供；
5. 运行 V2 vs V3 paired preflight，比较 source coverage、Slack bucket、P99、miss、quality
   和 seed stability；
6. 只有 V3 在数据覆盖和实验稳定性上都更好，才考虑冻结为下一版 workload。

adapter 第一版必须额外做到：

- 按 agent 保留字段覆盖与 provenance，不把 Claude Code timing 外推到所有 agent；
- 只使用可配对且非负的 tool interval，并对用户 idle gap、超长 interval 和 timestamp 回退
  做显式清洗与统计；
- split 使用 repo-user 连通分量，先按 content hash 去重，避免同源会话泄漏；
- `session_success` 只保留为 auxiliary，不进入正式 reward 或 checkpoint 选择。

上述 adapter、profile/runtime、paired workload/simulator preflight 和正式实验均已完成。
V3 的 source/split、Slack 覆盖、风险排序、负载梯度和训练稳定性检查通过，可作为当前首选
公开 trace-driven workload 候选。详细复现命令见
[`TRACE_DRIVEN_V3_CANDIDATE.md`](TRACE_DRIVEN_V3_CANDIDATE.md) 与
[`TRACE_DRIVEN_V3_FORMAL_REPORT.md`](TRACE_DRIVEN_V3_FORMAL_REPORT.md)。

### 阶段 C：仍需寻找的数据（未来 V4，当前不阻塞）

继续关注但不阻塞当前工作：

- 非 coding、真实生产 Agent 的 step/tool/timing/outcome 日志；
- 能与 Agent workflow 对齐的真实网络 telemetry；
- 真实 deadline/SLO 或用户取消/超时信号；
- 其他同学模块合并后产生的本项目真实执行日志。

在获得至少一种上述实质新字段前，不建议只调整 source mix 或固定模板公式并命名为 V4。
未来 V4 应使用独立 profile ID，保持 V3 冻结，并重新走数据审计、paired preflight 和正式
实验准入流程。

## 10. 本地文件

大型原始数据不进入 Git：

```text
external_agent_data/
├── quarantine/
│   ├── swe_chat/f66cca95.../{sessions.parquet,conversations.parquet}
│   ├── trace_commons/112ebd4d.../train.parquet
│   └── vtcode_sessions/78049282/repo/
└── reports/
    ├── swe_chat_f66cca95_sample100_preflight.json
    ├── trace_commons_112ebd4_preflight.json
    └── vtcode_sessions_78049282_preflight.json
```

协作仓库只保存本报告、candidate manifest 和不导出原始文本的聚合审计脚本。
