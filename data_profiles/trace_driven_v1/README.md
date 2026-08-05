# trace_driven_v1 Profile

本目录只保存冻结 profile 的轻量元数据。实际 profile 位于仓库外：

```text
${SPECNET_DATA_ROOT}/processed/trace_driven_v1/profile.json
```

V1 使用 TraceLab v0.0.1 的 Agent round、tool、token 和 timing，BurstGPT v2.0 的
`BurstGPT_3.csv` 提供 arrival window。profile 不包含 prompt、tool input、路径、原始
session ID 或用户对话。

重新构建：

```bash
python3 specnet_data/build_trace_profile_v1.py \
  --tracelab "$SPECNET_DATA_ROOT/raw/tracelab/v0.0.1/syfi_coding_trace.jsonl.gz" \
  --burstgpt "$SPECNET_DATA_ROOT/raw/burstgpt/v2.0/BurstGPT_3.csv" \
  --output "$SPECNET_DATA_ROOT/processed/trace_driven_v1/profile.json"
```

完整下载、校验和 V2/V3 构建流程见
[`../../docs/DATA_SETUP.md`](../../docs/DATA_SETUP.md)。
