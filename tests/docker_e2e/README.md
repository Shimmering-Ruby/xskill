# Docker E2E

发版前跑——装真 wheel + 起真 daemon + 走真 HTTP + 真 LLM key，抓单测覆盖不了的"安装/打包/运行时" bug。

## 入口

```bash
make e2e                                          # 跑所有 scenario
tests/docker_e2e/run.sh <scenario_name>           # 跑单个
tests/docker_e2e/run.sh all                       # 等价于 make e2e
```

## 加新 scenario

`scenarios/<name>/` 一个目录，里面四件套（任何缺失文件都用空内容兜底）：

| 文件 | 作用 |
|---|---|
| `scenario.yaml` | 元信息：`description` / `timeout_seconds` / `requires_llm`（true 则跑前检查 DEEPSEEK_API_KEY 环境变量） |
| `pre_state/` | 启动 daemon 前往 `/testhome/` 里 rsync 进去的内容（如 `pre_state/.xskill/config.yaml`） |
| `actions.sh` | daemon 起来后做的事（mkdir、cp fixture、curl 等）。已就绪的 `XSKILL_PORT`、`TESTHOME` 在 env |
| `assertions.sh` | 期望状态校验。任一非零 exit 即视为 scenario 失败，会自动 dump daemon 日志 |
| `fixtures/` | actions.sh 用的静态测试数据（jsonl、配置片段等） |

## 数据资产沉淀原则

- 每修一个 daemon 运行时 / 安装层 bug，就加一个 scenario（命名带 bug 编号或语义关键词）
- scenario 之间不共享 fake home——每跑一个就重建 `/testhome/`
- fixture 文件提交进 git，不要靠运行时生成

## rig 内部

- `rig/Dockerfile` — `python:3.11-bookworm` + `git curl jq build-essential` + `pip install httpx pyyaml`，pip 装挂卷里的 wheel
- `rig/entrypoint.sh` — 容器入口：rsync pre_state → 起 daemon 后台 → 等 `/api/v1/health` 就绪 → 跑 actions.sh → 跑 assertions.sh
- `rig/probe.py` — 公共探针库（HTTP poll、API 断言 helper），actions/assertions 脚本里可 `python /rig/probe.py <cmd>` 用
- `rig/build.sh` — 构镜像并把 `dist/*.whl` 注进去
