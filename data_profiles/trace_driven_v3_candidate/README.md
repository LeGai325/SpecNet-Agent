# trace_driven_v3_candidate Profile

本目录只保存冻结 profile 的轻量元数据。31.4 MB 的实际文件不进入 Git：

```text
${SPECNET_DATA_ROOT}/processed/trace_driven_v3_candidate/profile.json
```

V3 保留 V2 的 BurstGPT arrival window，并使用固定的 37.5% TraceLab、37.5% SWE-chat、
25% RAGPulse source mix。SWE-chat 只输出脱敏后的聚合 token、tool、清洗 timing 和加盐哈希
ID，不输出原始对话、路径、tool arguments、session/repo/user ID。

冻结 profile：

```text
bytes  = 31417085
sha256 = 926046f52a10ba4b4387fdca3755e092c6245fc922e4a1bee7d8cc472bd144e6
```

完整下载、构建、审计和 smoke 流程见
[`../../docs/DATA_SETUP.md`](../../docs/DATA_SETUP.md)，方法与实验边界见
[`../../docs/TRACE_DRIVEN_V3_CANDIDATE.md`](../../docs/TRACE_DRIVEN_V3_CANDIDATE.md)。
