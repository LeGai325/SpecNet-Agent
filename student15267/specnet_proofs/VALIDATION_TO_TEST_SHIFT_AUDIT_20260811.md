# Validation→Test 分布漂移审计（2026-08-11）

## 实验目的

检验 V2 的资源/尾延迟退化能否靠“每 profile 独立选择静态 V5 参数”解决，同时严格避免使用 test split 选参。

## 协议

- 在 V1、V2 各自 validation split 上搜索 `base_optional_boost ∈ {1,4,16}`、`required_progress_trigger ∈ {0.25,0.5,0.75}`；
- 所有候选比较 V4 minimum-quality 参考，硬门保持 quality、每 cell/template quality、p99、miss、served bytes、utilization；
- test split 完全不参与选择。

## 筛选结果

两个 validation split 都选择相同候选：`base_optional_boost=1`、`trigger=0.75`、`terminal_boost=96`。

| Validation profile | Δbytes vs V4 | Δutilization vs V4 | Δp99 vs V4 | Δmiss vs V4 | selected |
|---|---:|---:|---:|---:|---|
| V1 | -5.72 | -0.00031 | -13.50 | -0.01984 | yes |
| V2 | -19.87 | -0.00164 | -7.27 | -0.03755 | yes |

然而，同一冻结候选在独立 V2 test 上变为 `Δp99=+0.69`、`Δmiss=+0.00458`，触发 p99 硬门失败。

## 结论

这不是“候选网格太小”就能被 test 调参修复的问题：validation 与 test 的最优方向不稳定，表现为分布漂移。V2 test 不能被拿来继续选 trigger；应保留为独立失败证据。

下一项实验应采用 **worst-group constrained selection**：validation 内按容量、deadline、optional bytes、load 分组，候选必须在每一个组上均不超过 V4 的 p99/资源预算，而非只满足全局均值。若没有候选通过，应如实报告“静态 staged rule 不足”，再进入受约束在线控制研究。

## 工件

- [V1 validation screen](results/bold_v5_v1_validation_screen_20260811/)
- [V2 validation screen](results/bold_v5_v2_validation_screen_20260811/)
- [V2 independent test](results/bold_v5_v2_test_20260811/)

