# traj2skill 端到端演示包

一键跑通 **index → batch → eval → skill list** 全流程，自带 10 条 SWE-smith 真实修复轨迹作为输入数据。

## 目录

```
scripts/demo/
├── README.md              本文档
├── pack_demo.sh           维护者脚本：从 data/swe_smith_dataset 生成 trajectories.zip
├── run_demo.sh            演示入口：解压 + 跑通主流水线（CLI）
├── serve_ui.sh            CLI 演示结束后一键起 Web UI
└── trajectories.zip       10 条预打包轨迹（md + json + meta，约 160KB）
```

## 快速开始

```bash
# 从仓库根执行：
./scripts/demo/run_demo.sh
```

默认行为：
- `max = 3`（只处理前 3 条轨迹，约 15-20 分钟；受 LLM 串行影响）
- `workspace = /tmp/t2s_demo`（所有产出放这里，不污染仓库）
- 自动 `pip install -e .`（若 `t2s` 不在 PATH）
- 自动解压 `trajectories.zip` → `$WORKSPACE/data/swe_smith_demo/`
- 自动调用 `scripts/pipeline.sh` 执行 5 阶段流水线

## 自定义参数

```bash
./scripts/demo/run_demo.sh 5                       # 处理 5 条
./scripts/demo/run_demo.sh 10 /tmp/my_run          # 10 条 + 自定义 workspace
DRY_RUN=1 ./scripts/demo/run_demo.sh               # 只回显命令
SKIP_INSTALL=1 ./scripts/demo/run_demo.sh          # 跳过 pip install
INTERACTIVE=1 ./scripts/demo/run_demo.sh           # 每步等确认
```

## 查看产出

```bash
cd /tmp/t2s_demo
t2s skill list                          # 生成的 skill
t2s skill show <name>                   # 单个 skill 详情
t2s eval --list                         # 所有 eval 历史
cd skill && git log --all --oneline     # skill 仓库的版本历史
```

## 启动前端面板

CLI 演示结束后，一键拉起内置 Web UI：

```bash
./scripts/demo/serve_ui.sh                       # 默认 port=8000, workspace=/tmp/t2s_demo
./scripts/demo/serve_ui.sh 8080                  # 换端口
./scripts/demo/serve_ui.sh 8080 /tmp/my_demo     # 端口 + 自定义 workspace
OPEN_BROWSER=1 ./scripts/demo/serve_ui.sh        # 启动后自动开浏览器
```

脚本会自动：
- 检查 workspace 里是否有 `config.yaml` 和 `skill/` 目录
- 检测端口冲突（ss / lsof）
- `cd $WORKSPACE && t2s serve`，前端页面 http://localhost:8000/、SSE 流 `/api/stream`、OpenAPI `/docs`

前端产物已随包发布在 `src/traj2skill/web/dist/`，不需要额外 `npm build`。

产出目录结构：

```
/tmp/t2s_demo/
├── config.yaml                         # pipeline.sh 自动生成
├── data/swe_smith_demo/                # 解压出的轨迹
│   ├── traj_0000.md / .json / .md.meta
│   └── ...
│   └── index.pkl                       # 向量索引（index 阶段生成）
├── skill/                              # skill 仓库（带 .git）
│   ├── <skill_name>/SKILL.md
│   └── ...
└── output/                             # 每条轨迹的结构化事件日志
```

## 更换模型 / API key

所有 LLM/Embedding 配置硬编码在 `scripts/pipeline.sh` 顶部（约第 60 行起）：

```bash
LLM_MODEL="doubao-seed-2-0-mini-260215"      # index 阶段轻量模型
SKILL_LLM_MODEL="doubao-seed-2-0-pro-260215" # skill 生成 + 7 维打分模型
EMBED_MODEL="doubao-embedding-vision-251215" # 向量模型
```

想换 OpenAI / DeepSeek / Moonshot / 本地 vLLM，直接改那几行；兼容所有 OpenAI-style endpoint。

跑过一次之后也可以直接编辑 `$WORKSPACE/config.yaml`，pipeline.sh 会优先复用已有 config（除非 model 字段和脚本里不一致才会备份重写）。

## 重新打包轨迹（维护者）

```bash
./scripts/demo/pack_demo.sh             # 默认打 10 条
./scripts/demo/pack_demo.sh 20          # 改为 20 条
```

会从 `data/swe_smith_dataset/` 取前 N 条（需要同时存在 `.md` 和 `.json`），复制 `*.md / *.json / *.md.meta` 到临时目录后压成 zip。`index.pkl` 不打包——接收方运行时会重新生成，和他们用的 embedding 模型对齐。
