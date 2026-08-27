# SpecNet-Agent 三项证明科研执行报告（2026-08-11）

本报告把原方案的三个研究问题（RQ1–RQ3）统一成可复现、可证伪的证据链，并明确哪些结论已经成立、哪些仍不可宣称。运行入口为 `python specnet_proofs/run_three_proofs_smoke.py`；正式结果仍以各版本化 `results/` 报告为准。

## 三项证明与技术增强

| 证明 | 可检验命题 | 当前证据 | 建议采用的成熟技术 |
|---|---|---|---|
| RQ1 状态变量必要性 | 去掉 C/S/P 后，在对应压力切片出现预注册失败模式 | 因子化机制在 81 场景×5 fresh runs 对 C、S、P 均通过 broad/nonjoint；原始共享动作规则的 broad slack 曾失败，说明路径设计影响可辨识性 | 配对实验 + 场景分层 bootstrap（Efron 风格）；Holm 多重检验；正交/全因子压力矩阵 |
| RQ2 规则是否替代 bandit | 相同信息、动作、质量门和调参预算下，是否存在跨场景稳定支配的冻结规则 | 规则可命中单一 operating point，但未在全部 quality/background/p99/miss gates 上支配 bandit；负结果应保留 | LinUCB/上下文 bandit 作为学习基线（Li et al., 2010）；固定规则仅在 validation 冻结；paired randomization test |
| RQ3 可解释性与稳定性 | 同一 state、Q 表、guard 可复现最终动作，且跨种子保持实用等价 | 已分离 action-table agreement 与 held-out performance equivalence；不能以表格可打印代替因果解释 | counterfactual policy evaluation；状态访问置信区间；bootstrap stability；guard/策略日志哈希审计 |

## 从论文技术到本项目的具体落地

1. 调度层采用 earliest-deadline-first 的可解释先验（Liu–Layland），但只作为 `slack` 路径的 tie-breaker；不能让 EDF 改写 source admission。
2. background 使用 deficit/DRR 式欠额账户（Shreedhar–Varghese），并保留当前 eligible-window 的“完成后延期”语义；每个 workflow 记录欠额、TTL、drain epochs，避免把公平性收益误报为前台性能收益。
3. speculative 分支继续使用“先减少 offered load、再调队列”的两层控制，这与 speculative decoding 的 draft/verify 分离思想（Leviathan et al., 2023）一致；报告中必须区分 speculative waste proxy 与真实答案质量。
4. 策略学习可增加 LinUCB 作为第三类基线，用相同 `(C,S,P)` 特征和相同 action set；若其在少量样本下优于 Q-table，应把贡献改写为“可审计上下文控制”，而不是笼统声称 bandit 最优。

## 当前结论边界

- 因子化控制器证明了三条机制路径的可辨识性，不等于已完成生产部署。
- eligible-window v3 的 background floor、前台 parity、quality、p99 和 miss gates 已通过，但它引入跨主请求生命周期的 deferred drain；论文必须将其标成扩展语义。
- 原 simulator 的 quality、waste、background 均为 proxy。需要真实 token/字节计数和答案质量集成测试后，才能声称端到端 QoS 改善。
- 所有 confirmation seed 必须与 validation 完全隔离；任何根据 confirmation 结果调阈值的版本都只能作为探索性结果。

## 项目级意见与下一阶段验收门

1. 把 upstream snapshot、配置、随机种子、代码哈希和 Python 版本写入每个结果目录；CI 中执行 smoke、24 个单元测试和 schema 校验。
2. 将 simulator、policy、guard、metrics、plotting 拆成独立包，当前脚本间的隐式导入会阻碍复用和审稿人复现。
3. 统一指标命名：`wasted_speculative_bytes`、`background_service`、`quality` 必须在 README 中给出数学定义、单位和统计层级。
4. 下一轮正式实验至少报告：paired delta、95% CI、Holm-adjusted p、effect size、每场景支持数、最差 cell，而非只给均值。
5. 部署前增加真实 trace replay、突发流量、容量失效和 guard 误杀率测试；任何一项未通过，都只能标为 mechanism evidence。

## 复现命令

```bash
cd 科研/organized_code_files/student15267
python specnet_proofs/run_three_proofs_smoke.py
python -m unittest discover -s specnet_proofs -t . -p 'test_*.py'
```

