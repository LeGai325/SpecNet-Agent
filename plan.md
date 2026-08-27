SpecNet-Agent下一步投稿的修改计划方案
1. 修改目标
当前版本已经证明了一个清晰方向：agentic GenAI 服务中的网络 QoS 不应只在流量进入网络后做排队调度，而应把网络状态反馈给 agent runtime，在源端调节 retrieval width、agent parallelism、speculation budget 和 background sync，从而减少低价值推测流量对关键路径的干扰。

下一步核心目标如下：

把问题定义从“一个新场景的 QoS 优化”提升为“agentic GenAI workload 对数据中心网络控制平面提出的新抽象”。
把设计从“bandit + queue mapping 的单点机制”提升为“runtime-network 协同控制框架”。
把实验从“单瓶颈 trace-driven simulator”提升为“多场景、多基线、多消融、多鲁棒性、多租户的系统性评估”。
把质量指标从“推测保留 proxy”提升为“proxy + 真实任务质量验证”的组合。
把论文论证从“某个策略效果好”提升为“为什么源端推测控制是传统网络 QoS 无法替代的新控制点”。
2. 论文主线
2.1 核心论点
智能体式 GenAI 工作流中的网络流量并非全部是不可避免的负载，其中大量流量来自 runtime 的推测性执行决策。传统 QoS 只能调度已经生成的流量，而 SpecNet-Agent 的关键贡献是在流量生成之前，通过 runtime-network 反馈控制低价值推测需求。

2.2 贡献重新定位
Workload abstraction：提出 agentic GenAI workflow traffic abstraction，区分 critical-blocking、near-critical、normal、speculative、background flows，并解释其与传统 flow/coflow/job DAG 的区别。
Control framework：设计 SpecNet-Agent，一个 runtime-network feedback framework，通过 workflow hints、criticality scoring、network telemetry 和 safety-guarded control 调节源端推测。
Deployable mechanism：给出可部署实现路径，包括 HTTP/gRPC metadata、sidecar/service mesh、Linux tc/DSCP queue mapping，以及与 ECN/DCQCN/TCP 的关系。
Comprehensive evaluation：在真实或生产风格 agent traces、多负载、多租户、多瓶颈和多质量目标下证明 SpecNet-Agent 降低尾延迟、deadline miss 和浪费流量，同时保持任务质量和公平性。
3. 技术内容改进
3.1 强化 workload model
Agentic GenAI Traffic Model

需要明确：

Workflow 是在线生成的 dynamic DAG。
Flow 的重要性不是由 service type 决定，而是由 dependency、deadline slack、branch utility 和 downstream fanout 共同决定。
Speculative flows 不是简单低优先级流，而是可取消、可延迟、可降级的源端可控需求。
同一 service type 在不同 workflow 中可能对应不同 criticality。
传统 coflow 假设通信需求相对明确，而 agentic workflow 的分支效用在 judge/aggregator 选择之前是概率性的。
建议增加一个 motivating example：

Coding agent 场景：失败测试日志是关键流，额外文档检索是推测流。
Multi-agent debate 场景：部分 debate branch 可能提升质量，但拥塞时可减少。
RAG 场景：top-ranked retrieval 是 near-critical，low-ranked retrieval 是 quality-elastic。
3.2 明确控制对象
当前论文里 action 包括 top-k、agents、speculation budget、background sync。下一版需要更系统地定义控制对象：

控制对象	作用	网络影响	质量影响
Retrieval top-k	控制检索候选数量	减少 retrieval fanout 和响应字节	可能降低 evidence coverage
Parallel agents	控制并行推理/工具分支	减少 LLM/tool 并发请求	可能降低探索多样性
Speculation budget	控制可选分支启动数量	直接降低 speculative offered load	影响 best-branch selection
Background sync rate	控制日志、记忆、缓存同步	释放关键路径带宽	延迟非当前响应任务
Branch cancellation	取消低价值未完成分支	减少已启动流量继续消耗	需避免取消 near-critical 分支
这部分要强调：SpecNet-Agent 不是低层 packet scheduler，而是 workflow-level offered-load shaper。

3.3 改进 controller 设计
当前 tabular contextual bandit 适合作为初版，下一版本需要更完整地说明为何它足够、何时受限、如何扩展。

建议保留简单可解释 controller，但补充三层结构：

Criticality layer：计算 per-flow criticality，用于 queue mapping 和保护关键路径。
Runtime action layer：根据 workflow slack、congestion、speculative pressure 选择 runtime action。
Safety layer：强制 minimum quality floor、critical path protection、per-tenant quota、action hysteresis。
需要明确：

Score(f) 是 per-flow 信号，用于流分类和队列映射。
Bandit state 是 aggregate signal，用于选择 runtime-level speculation action。
两者不是重复机制，而是分别作用于“已有流如何排队”和“未来流是否生成”。
3.4 增加安全保护和公平性约束
论文需要避免被认为“通过牺牲部分用户质量换取平均时延”。建议把 safety guard 从实现细节提升为设计核心。

新增约束：

Minimum top-k / minimum speculation floor。
Per-tenant quality floor。
Per-workflow starvation protection。
Background minimum service share。
Critical traffic cap，防止所有租户都伪装成 critical。
Action hysteresis，避免频繁切换造成振荡。
Feedback smoothing，避免拥塞反馈被瞬时突发操控。
建议增加公式：

TEXT
复制
action = Guard(policy(state), workflow_constraints, tenant_constraints, network_constraints)
并说明 Guard 的优先级：

不破坏 mandatory critical path。
不低于 workflow/tenant minimum quality。
不超过 tenant speculation quota。
severe congestion 下限制 low-utility speculation。
congestion relief 后逐步恢复 background 和 optional branches。
4. 实验体系升级
4.1 实验总目标
INFOCOM 版本实验不能只证明“heavy load 下 p99 更低”，还要证明：

源端推测控制优于单纯网络调度。
SpecNet-Agent 在不同 workload、负载、拓扑和租户条件下稳定有效。
控制策略具备可解释性和可配置的 latency-quality tradeoff。
不完整 hints、噪声 hints、突发负载和恶意租户下不会失控。
机制可部署，控制开销可接受。
4.2 必须保留和增强的主实验
主结果仍建议保留：

FIFO。
Static service priority。
Critical-path-only。
Rule-based feedback。
SpecNet-Agent。
核心指标：

p95/p99 workflow latency。
Deadline miss ratio。
Wasted speculative bytes。
Average quality。
Link utilization。
Queue pressure。
Background completion delay。
增强点：

增加 error bars 或 95% confidence intervals。
增加更多随机种子。
报告 per-template 结果，而不仅是 aggregate。
将 heavy load 的结果扩展为 load sweep 曲线。
4.3 增加 latency-quality Pareto 实验
这是升级论文说服力的关键实验。

设计：

Sweep quality loss weight。
Sweep deadline miss weight。
Sweep rule-based threshold。
得到多个 operating points。
绘制 p99 latency vs quality。
目标：

证明 SpecNet-Agent 不是固定地牺牲质量，而是提供可配置 tradeoff。
说明默认配置是 balanced point。
展示在 latency-first、balanced、quality-first 三种业务目标下的表现。
建议新增图：

TEXT
复制
Fig. X: Latency-quality Pareto frontier under heavy load.
4.4 增加 controller ablation
需要证明 controller 的每个状态变量和安全保护都有意义。

实验组：

Full SpecNet-Agent。
Without slack。
Without speculative pressure。
Congestion-only。
Without safety guard。
Without queue mapping。
Without source-side control，只保留 criticality queue。
预期论点：

只有 congestion 不足以判断是否应该削减推测，因为 deadline slack 和 speculative pressure 共同决定收益。
只做 queue mapping 不能减少源端 offered load。
去掉 safety guard 会降低质量或造成 background starvation。
4.5 增加 hint robustness 实验
实际系统中 workflow hints 不一定完整。需要证明 SpecNet-Agent 不是依赖完美 metadata。

实验设计：

Missing rate：0%、10%、30%、50%、70%。

Noise：

size hint 偏差。
deadline hint 偏差。
speculation level 误标。
dependency edge 缺失。
Degraded mode：

无 DAG，只用 request type + history。
无 deadline，用 service-level SLO。
指标：

p99 latency degradation。

quality degradation。

wasted bytes。

action distribution。

目标：

给出最低可用 hint 集合。
证明即使 hints 不完整，性能也优于传统 QoS。
4.6 增加多租户公平性实验（可选）
Tenant 类型：

Latency-sensitive tenant。
Quality-sensitive tenant。
Background-heavy tenant。
Bursty tenant。
Adversarial tenant。
对比策略：

Global controller。
Per-tenant controller。
Hierarchical controller：global congestion budget + per-tenant speculation budget。
指标：

Per-tenant p99 latency。
Per-tenant quality。
Quality degradation gap。
Jain's fairness index。
Quality floor violation ratio。
目标：

证明 SpecNet-Agent 可以与租户隔离结合。
证明不会通过系统性降低某类租户质量来换取整体时延。
4.7 增加多瓶颈/多路径网络实验
建议做一个简化多瓶颈 simulator：

Retrieval path。
LLM serving path。
Tool/storage path。
Cross-service shared bottleneck。
Background sync path。
每条 flow 根据 service type 和 workflow stage 走不同 path。Telemetry 从 global congestion 扩展为 path-level congestion。

实验问题：

SpecNet-Agent 在多拥塞点下是否仍有效。
Path-level telemetry 是否优于 global telemetry。
不同服务路径拥塞时，controller 是否能选择不同 action。
建议新增图：

TEXT
复制
Fig. X: Performance under multi-bottleneck topology.
Fig. X: Global telemetry vs path-level telemetry.
4.8 增加真实任务质量验证
当前 quality proxy 必须定义清楚，进一步最好补充真实任务质量验证。

建议至少选择两个任务：

RAG-QA：

retrieval recall。
answer correctness 或 LLM judge score。
evidence coverage。
Coding agent：

unit test pass rate。
patch correctness。
required artifact completion rate。
如果完整执行成本太高，可以做小规模验证：

选取 100-200 个 workflow。
对不同 action 运行真实 agent 或半真实 replay。
比较 proxy quality 与真实 quality 的相关性。
指标：

Pearson/Spearman correlation。
Quality drop under conservative/critical-only action。
Balanced operating point 的真实质量保持率。
目标：

证明 quality proxy 不只是人为设定，而是与真实任务质量有相关性。
4.9 增加 deployment overhead 实验
建议实现轻量原型：

gRPC/HTTP metadata 携带 workflow hints。
Sidecar/controller 收集 hints 和 telemetry。
Linux tc 或 DSCP 映射 Q0-Q3。
Controller 周期性输出 runtime action。
测量：

Header size overhead。
Controller decision latency。
Per-workflow state memory。
Action update frequency。
Queue mapping overhead。
目标：

证明该设计不需要 prompt inspection。
证明控制开销远小于 workflow latency。
证明可以增量部署在 service mesh 或 sidecar 层。
5. 论文结构建议
建议论文版本结构如下：

TEXT
复制
1. Introduction
   - Agentic GenAI workloads introduce source-controllable speculative traffic
   - Traditional QoS cannot distinguish criticality and cannot prevent unnecessary traffic generation
   - Contributions

2. Background and Motivation
   - Agentic workflow model
   - Why flow/coflow/static priority are insufficient
   - Motivating examples and trace observations

3. Agentic Traffic Abstraction
   - Dynamic DAG
   - Flow classes
   - Speculation elasticity
   - Quality-latency tradeoff

4. SpecNet-Agent Design
   - Workflow hint collector
   - Criticality classifier
   - Telemetry collector
   - Runtime speculation controller
   - Safety guard and tenant constraints
   - QoS queue mapping

5. Implementation
   - Metadata format
   - Sidecar/service mesh integration
   - Linux tc/DSCP mapping
   - Controller state and overhead

6. Evaluation Methodology
   - Trace/workload construction
   - Simulator/prototype setup
   - Baselines
   - Metrics

7. Evaluation Results
   - Main performance
   - Pareto tradeoff
   - Ablation
   - Hint robustness
   - Multi-tenant fairness
   - Multi-bottleneck topology
   - Real quality validation
   - Overhead

8. Discussion
   - Deployment regime
   - Interaction with ECN/DCQCN/TCP/RDMA
   - Security and attack surface
   - Limitations

9. Related Work
   - Datacenter QoS
   - Coflow scheduling
   - LLM serving
   - RL for networking/resource control
   - Agentic AI systems

10. Conclusion
