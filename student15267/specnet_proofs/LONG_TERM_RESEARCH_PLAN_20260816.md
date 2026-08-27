# SpecNet-Agent 长线研究计划（2026-08-16）

## 研究主线

当前问题不是“再找一个更好的固定 boost”，而是建立一个能解释、能限额、能在分布漂移下退化可控的资源感知控制器：

```text
状态观测 -> 质量/资源预测 -> 预算账户 -> 动作选择 -> 结果记账 -> 更新预算
```

## 阶段一：Quality Contract + Virtual Byte-Debt（基础层已实现）

### 2026-08-16 进展

- 已实现 exact minimum-byte portfolio、0.95/0.94 tier negotiation、byte-debt ledger 和 quality/byte/congestion/fairness shadow prices；
- 已修正 debt 注销后的守恒式与 fairness debt 的优先级符号；
- V1/V2 validation 的点估计结果与 V4 100% 一致；强折扣暴露了固定 0.95 契约的不可行区；
- 分层契约在纯准入审计中收回全开回退的 17.20%/26.12% bytes，但尚未进入事件模拟器。

详细结果见 [`QUALITY_CONTRACT_BROKER_EXPERIMENT_20260816.md`](QUALITY_CONTRACT_BROKER_EXPERIMENT_20260816.md)。

### 目标

把资源开销从实验结束后的统计指标，变成运行时可见的控制信号。

### 账户

- `workflow_debt`：当前 workflow 已消耗的 speculative/background bytes 相对预算的欠额；
- `global_debt`：所有活跃 workflow 的总欠额；
- `quality_credit`：已完成 optional 分支带来的质量收益；
- `expiry_debt`：接近 TTL 但仍未完成的 background 欠额。

### 动作规则

```text
若 quality_credit 不足：允许增加 optional priority；
若 global_debt 超预算：停止新增可选流，回退 V5；
若 workflow_debt 超预算：只服务关键路径；
若 expiry_debt 接近 TTL：启用 deferred background；
```

这里的关键不是一个新阈值，而是让每一次增加质量的资源消耗都有账可查。

### 验收门

- quality target 和 hard floor 不下降；
- mean/P95/P99 served bytes 不超过 V5 预算；
- 每个压力组的 P99 不比 V4 增加；
- debt 账户在仿真结束时守恒：新增、偿还、过期三项可对账。

## 阶段二：鲁棒策略选择

将 V1/V2 validation 合并成 group-robust training pool，按以下维度分组：

- load；
- deadline scale；
- optional scale；
- capacity scale；
- trace profile。

候选选择使用最差组约束，而不是总平均。V2 test 和 V3 test 只做一次冻结后的确认，不能用于回调参数。

若没有静态候选通过，结论应是“固定 staged rule 不足”，转向 constrained contextual bandit，而不是继续扩大参数网格。

## 阶段三：真实系统验证

模拟器结果只能证明机制在代理环境中的行为。进入论文最终实验前必须加入：

1. 真实 token/byte 计数；
2. 真实答案质量评测；
3. burst、capacity drop、arrival blackout 故障注入；
4. tenant-level fairness；
5. CPU/NIC/显存资源统计；
6. trace replay 的时间顺序与原始数据许可证记录。

## 论文里程碑

### M1：机制论文

可声称：三个观测信号在隔离模拟器中有可辨识贡献，且 admission 与 queue scheduling 是不同机制。

### M2：资源论文

只有在 byte-debt controller 通过跨 profile、跨 seed 和最差组硬门后，才能声称质量安全机制具有资源鲁棒性。

### M3：系统论文

只有真实 trace replay、真实质量指标和真实资源成本都通过后，才能讨论生产部署或端到端 QoS。

## 目前不应做的事

- 不用 test 结果调 V6/V7；
- 不把 TTL deferred drain 的 simulator 结果写成生产 SLO；
- 不用平均 quality 掩盖某个 profile 的资源回归；
- 不再把一次 seed 的漂亮结果称为三项独立证明；
- 不把 `total_served_bytes` 直接称为真实能耗。

## 当前决策

V5 保留为主 baseline，V6/V7 保留为探索分支。Quality-Contract Broker 的纯规划器和账务不变量已通过测试，下一次代码变更应把“分层契约 + debt 记账”接入派生事件模拟器，并以 V5/V4 为双参考；在实际 quality/P99/served-byte 门通过前，不替换 V5。
