# trace_driven_v2 Profile

本目录只保存冻结 profile 的轻量元数据。22 MB 的实际文件位于仓库外：

```text
${SPECNET_DATA_ROOT}/processed/trace_driven_v2/profile.json
```

当前 profile 已完成数据侧组合和验证，但尚未接入模拟器。TraceLab 与 RAGPulse 在
trace-driven 主体内分别占 75% 和 25%；BurstGPT 提供 arrival windows；tau3-bench
不进入训练数据。

构建、审计命令和方法边界见
[`../../docs/TRACE_DRIVEN_V2.md`](../../docs/TRACE_DRIVEN_V2.md)。
