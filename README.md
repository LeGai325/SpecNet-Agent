# SpecNet-Agent

本仓库包含 SpecNet-Agent 的研究代码。项目目标是为 Agentic GenAI 工作流设计一个
网络感知控制器，根据网络状态动态调整推测执行宽度，在延迟、资源消耗和回答质量之间
进行权衡。

当前实现包括 trace-driven 模拟器、Controller baseline、状态消融、训练稳定性改进，
以及配套的实验分析和绘图脚本。

> 当前代码仍是基于模拟器的研究原型。真实 QoS 队列、严格的源端控制消融，以及最终版
> speculative-pressure 模块仍在等待合并。

## 目录结构

```text
specnet_agent_experiments/   主模拟器、Controller 和相关测试
specnet_plotting/            实验分析与绘图脚本
tools/                       实验复现辅助工具
docs/                        简明架构和当前集成状态
.github/workflows/           Pull Request 自动检查
```

主实验入口：

```text
specnet_agent_experiments/specnet_agent_experiment.py
```

## 快速开始

建议使用 Python 3.10 或更高版本。

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r specnet_plotting/requirements.txt
```

运行测试：

```bash
python3 -m unittest discover -s specnet_agent_experiments -p 'test_*.py'
python3 -m unittest discover -s specnet_plotting -p 'test_*.py'
```

运行小型 smoke 实验：

```bash
python3 specnet_agent_experiments/specnet_agent_experiment.py \
  --output-dir outputs/smoke \
  --train-episodes 3 \
  --eval-runs 1 \
  --duration 800 \
  --max-workflows 30 \
  --max-time 2500 \
  --quality-weight 1.6 \
  --controller-variants full,no_slack \
  --loads light,medium,heavy
```

生成的实验输出默认不会进入 Git。

## Controller 状态消融

当前 learned Controller 支持四种状态配置：

```text
full
congestion_only
no_slack
no_spec_pressure
```

示例：

```bash
python3 specnet_agent_experiments/specnet_agent_experiment.py \
  --output-dir outputs/controller_ablation \
  --controller-variants full,congestion_only,no_slack,no_spec_pressure \
  --train-seeds 11,23,37 \
  --train-episodes 90 \
  --checkpoint-selection best_validation \
  --eval-runs 5
```

## Slack 机制

当前默认使用 Slack v2：

```text
--slack-queue-basis total
--slack-queue-weight 1.0
```

Role-aware Slack v2.1 候选可以通过以下参数显式启用：

```text
--slack-queue-basis policy_weighted
--slack-queue-weight 0.5
```

V2.1 改善了离线估计误差，但在 3-seed 运行时预实验中没有稳定超过 v2，因此目前
不作为默认方案。

## 当前状态与已知限制

- 训练已经支持 epsilon 衰减、按访问次数衰减学习率，以及独立 validation checkpoint
  选择。
- `no_source_control` 当前由 `critical_path_only` 代理，不是严格的单开关消融。
- `no_learning` 当前由 `rule_balanced` 代理。
- Queue priority 目前通过模拟器中的 weighted allocation 实现，不是真实 Q0-Q3 队列。
- 完整实验输出和个人过程报告保存在 Git 之外。

修改 Controller 语义前，请先阅读：

- [`docs/PROJECT_STATUS.md`](docs/PROJECT_STATUS.md)
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)

## 团队协作

请通过 feature branch 和 Pull Request 协作。实验时应尽量分开修改 reward、workload、
action 定义和待研究机制，避免一次改动多个变量。分支、测试和实验数据规范见
[`CONTRIBUTING.md`](CONTRIBUTING.md)。

## 导入来源

本协作基线整理自本地开发 checkpoint `8cd5988`。此前的个人迭代历史继续保留在本地
研究工作区中，没有导入这个新仓库。

## License

当前尚未选择开源许可证。添加 License 前，需要由项目负责人确认仓库的共享和发布范围。
