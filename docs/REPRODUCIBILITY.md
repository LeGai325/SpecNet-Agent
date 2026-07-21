# 可复现实验说明

## 安装与运行

在仓库根目录使用 Python 3.10 或 3.12：

```bash
python -m pip install -e ".[plot,dev]"
specnet-run --config configs/smoke.json
```

历史命令与标准入口等价：

```bash
python specnet_agent_experiments/specnet_agent_experiment.py --config configs/smoke.json
```

配置必须包含 `schema_version: 1`。其余键与 CLI 参数目标名一致，命令行显式值优先。
`configs/default.json` 记录历史默认值；`smoke.json` 和 `controller-ablation.json` 分别用于
快速检查与状态消融。

## Seed 与配对比较

每次运行的 `run_manifest.json` 保存完整解析配置、训练 seed、评估 seed、validation seed、
Python/package/Git 版本和输出文件清单。比较策略时应复用相同 workload seed；checkpoint
只由独立 validation workload 选择，不得使用正式 evaluation 数据。

## 输出契约

以下历史输出的文件名、字段名、字段顺序和 JSON 层级保持不变：

```text
summary_by_run.csv
summary_aggregate.csv
workflow_results.csv
action_counts.csv
trained_agents.csv
specnet_agent_model.json
```

`run_manifest.json` 是新增元数据，不影响旧消费者。输出目录和图表属于生成数据，不进入 Git。

## 回归检查

```bash
python -m ruff check src tests
python -m compileall -q src specnet_agent_experiments specnet_plotting tools tests
python -m unittest discover -s tests -p "test_*.py"
python -m unittest discover -s specnet_agent_experiments -p "test_*.py"
python -m unittest discover -s specnet_plotting -p "test_*.py"
```

`tests/fixtures/core_baseline.json` 保存重构前固定 seed 的 workload 和 Simulator 指标。
回归要求核心数值完全一致；浮点输出只允许表示格式差异，不允许统计值差异。
