# 数据目录

本目录只保存适合进入 Git 的轻量数据元信息，不保存大型公开数据、完整对话或内部数据。

- `manifests/`：固定数据版本、checksum、许可证和准入边界；
- `schemas/`：统一字段与各数据源的映射、缺失字段和使用限制。

实际数据统一放在仓库外的 `${SPECNET_DATA_ROOT}`。原始数据保持只读，转换结果写到其
`processed/` 目录。未经授权的内部数据、原始文本、工具参数和完整 benchmark 对话不得
提交到 Git。

V2 的代码和方法说明见 [`../specnet_data`](../specnet_data) 与
[`../docs/TRACE_DRIVEN_V2.md`](../docs/TRACE_DRIVEN_V2.md)。
