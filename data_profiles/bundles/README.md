# 冻结 Trace Profile 数据包

私有协作仓库内置以下压缩数据包：

```text
SpecNet-Agent-Trace-Profiles-20260801.zip
```

压缩包约 5.1 MB，解压后约 68 MB，包含可直接供模拟器使用的 V1、V2 和 V3 脱敏
profile，不包含 TraceLab、BurstGPT、RAGPulse 或 SWE-chat 的原始文件。

仓库根目录执行：

```bash
python3 tools/install_trace_profiles.py
export SPECNET_DATA_ROOT="$PWD/external_agent_data"
```

安装脚本会把 profile 解压到 Git 忽略的 `external_agent_data/processed/`，并逐个校验
SHA256。重复运行是安全的；已有文件 checksum 不一致时默认拒绝覆盖，可经人工确认后传入
`--force`。

压缩包 SHA256：

```text
51cb7ca25623e77c70ceb00178ea681bc4a465423b949d17aa640d0ff3a66bcf
```

数据来源、固定版本、许可证和完整重建方式见
[`../../docs/DATA_SETUP.md`](../../docs/DATA_SETUP.md)。
