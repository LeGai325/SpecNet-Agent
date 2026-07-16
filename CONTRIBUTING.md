# SpecNet-Agent 协作规范

## 分支和 Pull Request

请从 `main` 创建用途明确的分支，例如：

```text
feature/qos-queues
feature/source-control
feature/spec-pressure
feature/slack
experiment/controller-ablation
docs/reproducibility
```

功能开发不要直接推送到 `main`，请通过 Pull Request 合并。一个 PR 尽量只解决一个
机制或一个实验问题，避免同时改动多个相互独立的模块。

## 提交 PR 前的检查

运行：

```bash
python3 -m unittest discover -s specnet_agent_experiments -p 'test_*.py'
python3 -m unittest discover -s specnet_plotting -p 'test_*.py'
python3 -m compileall -q specnet_agent_experiments specnet_plotting tools
```

如果修改了 Controller，还应运行一次小型 smoke 实验，并在 PR 中写清楚：

- 完整实验命令；
- 训练 seed 和评估 seed；
- Controller variant 和 quality weight；
- 输出目录名称；
- 是否修改了 reward、workload、action 定义或调度机制。

## 实验规范

- 一次实验尽量只修改一个主要机制。
- 研究某个机制时，不要同时悄悄修改 `ACTION_CONFIG`、reward、workload 分布或评估 seed。
- `full` 和相关消融组应使用相同 workload，进行配对比较。
- 报告不同 seed 的结果和方差，不要只报告表现最好的 seed。
- Checkpoint 应由独立 validation workload 选择，正式 evaluation 数据不能参与模型选择。

## 实验数据和生成文件

不要提交完整实验目录、workflow-level CSV、模型 JSON、日志、缓存或本地虚拟环境。
`outputs/` 已被 `.gitignore` 忽略。

实验结果应存放在课题组共享存储或负责人本地目录。需要在 PR 中引用结果时，应提供
生成结果的 commit、完整命令、seed 和外部数据位置，不要把完整数据复制进代码仓库。

## 修改主模拟器时的注意事项

`specnet_agent_experiment.py` 当前仍是多个模块共用的集成入口。QoS、源端控制、
speculative pressure、reward 和 Controller state 都可能修改这个文件，因此进行较大重构前
应先和其他开发者沟通，避免产生难以合并的冲突。

新行为应尽量做成可配置选项，并在可能的情况下保持默认行为不变。同时添加针对新路径
和向后兼容性的测试。

## 文档维护

完成新功能、改变默认方案或调整模块接口时，请更新：

- `docs/PROJECT_STATUS.md`
- `docs/ARCHITECTURE.md`

个人实验报告和调参记录由负责人自行保存，不要求进入协作仓库。
