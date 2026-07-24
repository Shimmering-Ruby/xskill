# SkillEdit BDD specifications

本目录先保存可由产品、开发和测试共同审阅的 Gherkin 规格。
当前阶段只定义行为，不绑定 pytest step definitions，也不引入测试依赖。

## 主用户成功路径

主成功路径是：

`baby_cold_start_golden_path.feature`
中的 `用户等待系统把新知识整理成可用的 main skill`。

它从 watcher 调度已经达到冷启动条件的 baby 开始，经过真实的
OpenAI-compatible HTTP 模型边界、两次分批编辑和 checkpoint，最终得到：

- 所有原子按 FIFO 顺序且只被消费一次；
- 每批改动都有可恢复的 baby commit；
- `candidates.yaml` 被消费为空；
- baby 由框架晋升为 main；
- main 中包含所有批次贡献的知识。

## 测试层次

这些规格后续分成两种执行层：

1. `@http_llm` 场景使用真实 Agno/OpenAI 客户端，模型地址指向本地
   `ai-mocks`，验证请求、tool call、工具执行和后续模型轮次的完整 HTTP 边界。
2. `@state_machine` 场景使用进程内的确定性模型脚本，快速验证批次缩减、
   checkpoint 判定和重启恢复，供日常 pytest 与 mutation testing 重复执行。

Mutation testing 不会为每个 mutant 启动 JVM 或容器。它复用快速的
`@state_machine` 测试杀死状态机 mutant；`@http_llm` 负责证明真实协议集成没有
被测试替身掩盖。

## 依赖边界

BDD、mutation testing 和 `ai-mocks` 都只属于测试环境：

- 不加入 `[project].dependencies`；
- 不会被 `pip install xskill` 安装；
- `pytest-bdd` 与 `mutmut` 后续放入测试专用依赖组；
- `ai-mocks` 由测试 fixture 或 CI sidecar 启动，不进入 xskill wheel。

## 标签

- `@primary`：主用户成功路径。
- `@http_llm`：经过本地 OpenAI-compatible HTTP 后端。
- `@state_machine`：快速、确定性的状态机测试。
- `@recovery`：429、上下文超长、进程重启等恢复路径。
- `@observability`：面向操作者的 SkillEdit trace。
