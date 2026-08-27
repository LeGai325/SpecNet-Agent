# 实验进展记录（2026-07-23）

## 本轮完成

### 1. Pressure 定义 full 复核

`results/pressure_definition_full_20260722_v3/` 已完整落盘，包含 6 个定义、2 个训练 seed、3 个评估 run 和 27 个评估场景。最值得继续复核的是：

- `active_speculative_backlog`：质量约束 waste delta `+1.292`，95% CI `[0.589, 1.977]`。
- `workflow_optional_ratio`：质量约束 waste delta `+4.919`，95% CI `[3.335, 6.460]`，但质量可行比例只有 `0.278`。
- `expected_waste_risk`：质量约束 waste delta `+5.462`，95% CI `[3.846, 7.121]`，同样存在质量可行比例偏低的问题。
- 原始全局比例在质量约束后为 `-4.868`，说明原定义不能直接宣称支持 H1-P。

### 2. Source-control isolation

脚本：`source_control_isolation.py`；结果：`results/source_control_isolation_full_20260723_v2/`。

采用 18 场景、每场景 45 个 workflow、单次评估的 full-like 预算，交叉比较 5 种源端动作和 3 种队列策略。`full + critical_path` 基线为 p99 `106.16`、deadline miss `0.039`、waste `63.93`、quality `1.000`。

- 固定 critical_path 队列后，moderate/conservative/critical_only 相对 full 分别减少 speculative 生成量约 `100.00/166.61/203.33`，waste 约减少 `26.93/48.85/63.93`，但 quality 也下降。
- 固定 full 源端后，FIFO 相对 critical_path 的 p99 增量约 `+601.18`，static priority 约 `+231.33`，说明队列调度本身也有独立贡献。

### 3. Per-workflow oracle gap

脚本：`oracle_gap_study.py`；结果：`results/oracle_gap_full_20260723/`。

对 282 个 workflow 逐个事后尝试五种动作，其他 workflow 保持冻结 bandit：平均 oracle gap `0.0389`，中位数 `0.0272`，90 分位 `0.0915`，有正 gap 的比例 `0.823`。这说明当前状态/训练仍有可观改进空间，但 oracle 使用事后信息，不能直接当部署收益。

### 4. 有限状态数学验证

脚本：`finite_monotonicity_check.py`；结果：`results/finite_monotonicity_20260723/`。

在单瓶颈、加权 max-min、关键流不改变的假设下枚举 819 个小规模案例，删除可选流后关键流完成轮数的反向增量最大为 `0`，没有找到反例；最小增量为 `-6`。这验证了引理在有限范围内与模拟器逐轮分配一致，但不覆盖多瓶颈和动态网络。

## 当前判断

现在可以严格说：减少可选流量和优先保护关键流分别有机制上的理论依据，也在模拟器中显示出独立效果；还不能说 SpecNet bandit 已全面优于所有规则，更不能说质量代理等价于真实语义质量。

数学边界见 `MATHEMATICAL_GUARANTEES.md`。

## 尚未完成

1. 在多瓶颈、多租户、突发 trace 下重复 isolation 和 oracle。
2. 接入真实 Agent trace 与真实质量指标。
3. 评估与 GPU serving-side 调度（例如 Niyama）的端到端组合。
