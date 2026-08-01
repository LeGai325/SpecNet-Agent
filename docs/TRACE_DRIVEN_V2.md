# Trace-driven Workload V2

更新时间：2026-08-01

## 当前状态

V2-A 已注册为模拟器的 `--workload-profile trace_driven_v2`，并完成真实 profile smoke
和 3-seed paired Pilot。它是“真实数据校准的固定模板模拟 workload”，不是完整真实回放。

当前采用保守映射：TraceLab record 映射为 `coding`，RAGPulse record 映射为 `rag_qa`；
两者都只使用原数据中可验证的 token、tool、runtime 或 retrieval 组成。deadline、网络
排队、speculation 后果和缺失的 DAG 仍由模拟器生成并明确标为 simulated。

## 数据角色

| 来源 | 用途 | 是否进入训练 profile |
| :--- | :--- | :--- |
| TraceLab v0.0.1 | Agent step、tool、token、runtime 主体 | 是 |
| RAGPulse `3672232d` | RAG 请求 token、session 与 retrieval 组成 | 是，仅限可验证字段 |
| BurstGPT 3 | 按自然日隔离的 arrival windows | 是 |
| tau3-bench v1.0.1 | 未来 held-out 外部任务评估 | 否 |

Trace-driven 主体内部比例固定为 TraceLab 75%、RAGPulse 25%。总体训练 workload 仍沿用
60% trace、25% empirical-neighborhood augmentation、15% targeted stress。比例在产生
Controller 指标前冻结，不能根据实验结果反向调参。

RAGPulse 缺少 step duration、动态 DAG、outcome、deadline 和网络 telemetry，这些字段
保持缺失。它只有两个原因不明的时间窗口，因此不承担 train/validation/test 的独立
temporal arrival；arrival 继续由 BurstGPT 提供。

## 文件位置

大型原始数据和生成的 profile 不进入 Git，统一放在外部数据根目录：

```bash
export SPECNET_DATA_ROOT="/path/to/external_agent_data"
```

默认 profile：

```text
${SPECNET_DATA_ROOT}/processed/trace_driven_v2/profile.json
```

仓库只保存适配代码、测试、schema、manifest 和 profile checksum 元数据。

## 模拟器运行

```bash
python3 specnet_agent_experiments/specnet_agent_experiment.py \
  --workload-profile trace_driven_v2 \
  --trace-profile-path \
    "$SPECNET_DATA_ROOT/processed/trace_driven_v2/profile.json" \
  --output-dir outputs/trace_driven_v2_smoke \
  --train-episodes 3 \
  --eval-runs 1 \
  --duration 800 \
  --max-workflows 30 \
  --max-time 2500
```

同一个 profile 会按 phase 使用互不重叠的 split：训练使用 `train`，checkpoint validation
使用 `validation`，正式 evaluation 使用 `test`。输出的 `workflow_results.csv` 会记录
profile、mode、record source、split、脱敏 record ID 和 mapping version。

## 构建和审计

先从固定版本的 RAGPulse 与 tau3 生成脱敏的阶段二产物：

```bash
python3 specnet_data/build_v2_stage2.py \
  --ragpulse-root "$SPECNET_DATA_ROOT/downloads/ragpulse/3672232d/repo" \
  --tau-root "$SPECNET_DATA_ROOT/downloads/tau3_bench/v1.0.1/repo" \
  --data-root "$SPECNET_DATA_ROOT"
```

再组合已有 V1 profile 和 RAGPulse request records：

```bash
python3 specnet_data/build_trace_profile_v2.py \
  --v1-profile "$SPECNET_DATA_ROOT/processed/trace_driven_v1/profile.json" \
  --ragpulse-records \
    "$SPECNET_DATA_ROOT/processed/unified_trace_v2/ragpulse_requests.jsonl" \
  --output "$SPECNET_DATA_ROOT/processed/trace_driven_v2/profile.json"
```

运行 profile-only coverage smoke：

```bash
python3 specnet_data/audit_trace_profile_v2.py \
  --profile "$SPECNET_DATA_ROOT/processed/trace_driven_v2/profile.json" \
  --sample-size 10000 \
  --seed 20260801 \
  --output "$SPECNET_DATA_ROOT/reports/v2_stage3_coverage_smoke.json"
```

已冻结的正式 profile SHA256 为：

```text
4dbe8541f9ac8e6b901c165273e18cf169fe02f043d936b3125e622e272ceec2
```

## 当前边界

V2-B 仍需等待动态 DAG、真实 QoS queue、真实 telemetry 和多 control epoch 等模块。
tau3 只有在 SpecNet-to-tau3 runner 完成后才能作为最终外部评估，现有预计算轨迹只用于
adapter 回归。

阶段四 Pilot 结果与正式实验门槛见
[`TRACE_DRIVEN_V2_STAGE4_REPORT.md`](TRACE_DRIVEN_V2_STAGE4_REPORT.md)。
