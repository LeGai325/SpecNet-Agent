# Trace Profile 数据包获取与验证记录

验证日期：2026-08-06  
上游项目：`LeGai325/SpecNet-Agent` 的 `main`（验证时 tree SHA：`6dacbcf449e4c3ab099e66b9bb901b46a2ae666b`）

## 1. 本地保存位置

- 冻结压缩包：`data_profiles/bundles/SpecNet-Agent-Trace-Profiles-20260801.zip`
- 包内说明副本：`data_profiles/bundles/DATA_PACK_README.md`
- 已解压 profile：`../external_agent_data/processed/`
  - `trace_driven_v1/profile.json`
  - `trace_driven_v2/profile.json`
  - `trace_driven_v3_candidate/profile.json`

数据包只包含脱敏、数值化的 simulator profile；不含原始 prompt、完整对话、tool arguments、用户/仓库 ID 或原始 session。它是由真实数据校准的固定模板 workload，不是生产日志回放。

## 2. 完整性与隐私检查

| 检查项 | 结果 |
|---|---|
| 压缩包 SHA-256 | `51cb7ca25623e77c70ceb00178ea681bc4a465423b949d17aa640d0ff3a66bcf`，与 README 冻结值一致 |
| ZIP CRC / 解压测试 | 通过；包含 V1、V2、V3 profile、`SHA256SUMS`、`audit_profiles.py` 与说明文件 |
| 内部三份 profile checksum | 全部通过 |
| V1 隐私审计 | `17,877,097` bytes，`suspicious_strings=0`，最长字符串 `64` |
| V2 隐私审计 | `22,249,340` bytes，`suspicious_strings=0`，最长字符串 `73` |
| V3 隐私审计 | `31,417,085` bytes，`suspicious_strings=0`，最长字符串 `74` |

## 3. 实际加载测试

测试采用上游 `main` 的 trace-driven simulator 接口，设置 `SPECNET_DATA_ROOT` 指向已解压的 `external_agent_data`。三份 profile 均成功完成加载、训练与评估，并写出结果文件：

| Profile | 测试参数 | 结果 |
|---|---|---|
| V1 `trace_driven_v1` | `train-episodes=1`、`eval-runs=1`、`duration=400`、`max-workflows=12` | 通过 |
| V2 `trace_driven_v2` | `train-episodes=1`、`eval-runs=1`、`duration=400`、`max-workflows=12` | 通过 |
| V3 `trace_driven_v3_candidate` | 上游 README 指定 smoke：`train-episodes=3`、`eval-runs=1`、`duration=800`、`max-workflows=30`、`max-time=2500` | 通过 |

V3 smoke 覆盖 light、medium、heavy 三种负载，以及所有内置基线和 `specnet_agent` 策略；运行成功不代表已有三变量结论自动迁移到新数据，只证明数据接口、profile 解析和端到端执行是可行的。

## 4. 与当前 proof workspace 的兼容性结论

**数据包：通过。当前冻结 proof simulator：暂不能直接接入。**

当前 proof workspace 使用的只读快照
`../../organized_code_files/source_snapshot/specnet_agent_experiments/specnet_agent_experiment.py`
的命令行没有 `--workload-profile` 或 `--trace-profile-path` 参数，也没有 `SPECNET_DATA_ROOT` 支持。因此，不能把新数据直接塞入现有三变量/TTL 结果并声称完成真实 trace 验证。

## 5. 正确的接入顺序

1. 新建版本化的 trace-driven simulator snapshot，保留当前 synthetic proof snapshot 和全部已冻结结果不变。
2. 用相同 seeds 先重跑 synthetic baseline，确认升级代码没有改变旧协议的测量语义。
3. 以 V3 为主、V1/V2 为稳健性检查，预先冻结 trace-driven 场景、主指标、质量门和资源门。
4. 重新执行三变量消融与 TTL 实验；新数据实验必须独立报告，不能与旧 synthetic confirmation 合并计算显著性。
5. 继续保留边界：deadline、网络 telemetry、queue 和反事实 action 仍由模拟器生成，不能表述为生产网络回放结果。

## 6. 数据来源与再分发

包内说明列出的来源/许可证为 TraceLab v0.0.1（CC-BY-4.0）、BurstGPT v2.0（CC-BY-4.0）、RAGPulse 固定 revision（MIT）和 SWE-chat 固定 revision（ODC-BY）。仅用于课题组内部复现实验；若公开再分发，必须保留来源、版本与许可证说明。
