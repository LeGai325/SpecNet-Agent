# Trace-driven 数据准备与复现

本文面向需要复用 V1/V2/V3 workload 的团队成员。Git 仓库只保存代码、manifest、schema、
checksum、轻量元数据，以及供私有仓库成员直接使用的冻结 profile 压缩包；原始数据和完整
实验输出仍统一放在仓库外。

## 0. 私有仓库快速安装（推荐）

只需运行实验、不需要重新审计原始数据时，在仓库根目录执行：

```bash
python3 tools/install_trace_profiles.py
export SPECNET_DATA_ROOT="$PWD/external_agent_data"
```

安装脚本会从 `data_profiles/bundles/` 解压 V1/V2/V3 到 Git 忽略目录，并校验冻结
checksum。此后可以直接跳到第 4 节运行 smoke。需要从公开原始数据重新生成 profile 时，
再继续执行下面的完整流程。

## 1. 环境与目录

建议使用 Python 3.10 或更高版本。SWE-chat 转换依赖 `pyarrow`，从 Hugging Face 下载时
还需要 `huggingface_hub`：

```bash
python3 -m pip install pyarrow huggingface_hub
export SPECNET_DATA_ROOT="/path/to/external_agent_data"
mkdir -p "$SPECNET_DATA_ROOT/raw/tracelab/v0.0.1"
mkdir -p "$SPECNET_DATA_ROOT/raw/burstgpt/v2.0"
mkdir -p "$SPECNET_DATA_ROOT/downloads/ragpulse/3672232d"
mkdir -p "$SPECNET_DATA_ROOT/quarantine/swe_chat/f66cca95b14caaa4177f7ed5eaa424608dadcffa"
```

`raw/` 与 `quarantine/` 保持只读，生成文件写入 `processed/` 和 `reports/`。不要把这些目录
复制进 Git 仓库。

## 2. 下载固定数据版本

### TraceLab v0.0.1

```bash
curl -L --fail \
  "https://github.com/uw-syfi/TraceLab/releases/download/v0.0.1/syfi_coding_trace.jsonl.gz" \
  -o "$SPECNET_DATA_ROOT/raw/tracelab/v0.0.1/syfi_coding_trace.jsonl.gz"
```

预期 SHA256：

```text
9d265eae69a31cae203848bea936f018148eed7ca8bf56050c5abe96da0b4e6b
```

### BurstGPT v2.0 Trace 3

```bash
curl -L --fail \
  "https://github.com/HPMLL/BurstGPT/releases/download/v2.0/BurstGPT_3.csv" \
  -o "$SPECNET_DATA_ROOT/raw/burstgpt/v2.0/BurstGPT_3.csv"
```

预期 SHA256：

```text
2299986a07388aa303ec2c41d1131e756db650a39ed6ef9dfe7cc3d7f9a43b8f
```

### RAGPulse

```bash
git clone https://github.com/flashserve/RAGPulse.git \
  "$SPECNET_DATA_ROOT/downloads/ragpulse/3672232d/repo"
git -C "$SPECNET_DATA_ROOT/downloads/ragpulse/3672232d/repo" \
  checkout 3672232d45d749fdcf45dbc38cc77e5264af4a32
```

### SWE-chat

SWE-chat 是 gated dataset。先在 Hugging Face 页面同意许可并执行 `hf auth login`，再下载
固定 revision 的两个必要 Parquet 文件：

```bash
hf download SALT-NLP/SWE-chat \
  --repo-type dataset \
  --revision f66cca95b14caaa4177f7ed5eaa424608dadcffa \
  --include sessions.parquet conversations.parquet \
  --local-dir \
  "$SPECNET_DATA_ROOT/quarantine/swe_chat/f66cca95b14caaa4177f7ed5eaa424608dadcffa"
```

两个文件的预期 SHA256：

```text
sessions.parquet      2ada63973b182b691318916ca8c813e694091400e43744eca9cad3da2d958a95
conversations.parquet 9ee1d937dbf7eb73a8dad75071c69a4f6b5aac7f4120bd8ef3799ee50f4f1c36
```

可以使用 `shasum -a 256 <file>` 校验。更多许可证、文件大小和准入限制见
`data_catalog/manifests/`。

## 3. 构建 V1、V2 和 V3

构建 V1：

```bash
python3 specnet_data/build_trace_profile_v1.py \
  --tracelab "$SPECNET_DATA_ROOT/raw/tracelab/v0.0.1/syfi_coding_trace.jsonl.gz" \
  --burstgpt "$SPECNET_DATA_ROOT/raw/burstgpt/v2.0/BurstGPT_3.csv" \
  --output "$SPECNET_DATA_ROOT/processed/trace_driven_v1/profile.json"
```

构建 RAGPulse 脱敏记录。tau3-bench 不进入训练 profile，因此默认不需要下载：

```bash
python3 specnet_data/build_v2_stage2.py \
  --ragpulse-root "$SPECNET_DATA_ROOT/downloads/ragpulse/3672232d/repo" \
  --data-root "$SPECNET_DATA_ROOT"
```

构建 V2：

```bash
python3 specnet_data/build_trace_profile_v2.py \
  --v1-profile "$SPECNET_DATA_ROOT/processed/trace_driven_v1/profile.json" \
  --ragpulse-records "$SPECNET_DATA_ROOT/processed/unified_trace_v2/ragpulse_requests.jsonl" \
  --output "$SPECNET_DATA_ROOT/processed/trace_driven_v2/profile.json"
```

转换 SWE-chat 并构建 V3：

```bash
python3 specnet_data/swe_chat_v3.py \
  --sessions "$SPECNET_DATA_ROOT/quarantine/swe_chat/f66cca95b14caaa4177f7ed5eaa424608dadcffa/sessions.parquet" \
  --conversations "$SPECNET_DATA_ROOT/quarantine/swe_chat/f66cca95b14caaa4177f7ed5eaa424608dadcffa/conversations.parquet" \
  --revision f66cca95b14caaa4177f7ed5eaa424608dadcffa \
  --output "$SPECNET_DATA_ROOT/processed/unified_trace_v3/swe_chat_workflows.jsonl" \
  --report "$SPECNET_DATA_ROOT/reports/swe_chat_v3_adapter_preflight.json"

python3 specnet_data/build_trace_profile_v3.py \
  --v2-profile "$SPECNET_DATA_ROOT/processed/trace_driven_v2/profile.json" \
  --swe-chat-records "$SPECNET_DATA_ROOT/processed/unified_trace_v3/swe_chat_workflows.jsonl" \
  --output "$SPECNET_DATA_ROOT/processed/trace_driven_v3_candidate/profile.json"
```

冻结 profile checksum：

| Profile | SHA256 |
| :--- | :--- |
| V2 | `4dbe8541f9ac8e6b901c165273e18cf169fe02f043d936b3125e622e272ceec2` |
| V3 | `926046f52a10ba4b4387fdca3755e092c6245fc922e4a1bee7d8cc472bd144e6` |

## 4. 审计与 smoke

```bash
python3 specnet_data/audit_trace_profile_v3.py \
  --profile "$SPECNET_DATA_ROOT/processed/trace_driven_v3_candidate/profile.json" \
  --sample-size 10000 \
  --runtime-count 40 \
  --output "$SPECNET_DATA_ROOT/reports/trace_driven_v3_candidate_preflight.json"

python3 specnet_agent_experiments/specnet_agent_experiment.py \
  --workload-profile trace_driven_v3_candidate \
  --trace-profile-path "$SPECNET_DATA_ROOT/processed/trace_driven_v3_candidate/profile.json" \
  --output-dir outputs/v3_candidate_smoke \
  --train-episodes 3 \
  --eval-runs 1 \
  --duration 800 \
  --max-workflows 30 \
  --max-time 2500
```

V3 仍是由真实数据校准的固定模板 workload，不是生产日志回放。真实 deadline、网络
telemetry、queue 和 action 反事实仍由模拟器提供。
