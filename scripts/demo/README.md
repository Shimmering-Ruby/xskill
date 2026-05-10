# xskill 端到端演示包

一键跑通 **index → batch → eval → skill list** 全流程，自带 **300 条** SWE-smith 真实修复轨迹（只含原始 `.md` + `.json`，不含 meta / 向量索引）作为输入数据。

## 目录

```
scripts/demo/
├── README.md              本文档
├── pack_demo.sh           维护者脚本：从 data/swe_smith_dataset 生成 trajectories.zip
├── run_demo.sh            演示入口：解压 + 跑通主流水线（CLI）
├── serve_ui.sh            CLI 演示结束后一键起 Web UI
└── trajectories.zip       300 条预打包轨迹（md + json，约 4.9MB；不含 meta / index.pkl）
```

## 快速开始

```bash
# 从仓库根执行：
./scripts/demo/run_demo.sh
```

默认行为：
- `max = 3`（只处理前 3 条轨迹，约 15-20 分钟；受 LLM 串行影响）
- `workspace = /tmp/t2s_demo`（所有产出放这里，不污染仓库）
- 自动 `pip install -e .`（若 `xskill` 不在 PATH）
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
xskill skill list                          # 生成的 skill
xskill skill show <name>                   # 单个 skill 详情
xskill eval --list                         # 所有 eval 历史
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
- `cd $WORKSPACE && xskill serve`，前端页面 http://localhost:8000/、SSE 流 `/api/stream`、OpenAPI `/docs`

前端产物已随包发布在 `src/xskill/web/dist/`，不需要额外 `npm build`。

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
./scripts/demo/pack_demo.sh             # 默认打包全部 300 条
./scripts/demo/pack_demo.sh 20          # 只打前 20 条
./scripts/demo/pack_demo.sh all         # 显式表示全部
```

从 `data/swe_smith_dataset/` 取 N 条（需要同时存在 `.md` 和 `.json`），**只复制原始 `*.md` 和 `*.json`**到临时目录后压成 zip。

**刻意不打包**：
- `*.md.meta` — LLM 抽的结构化元数据，会随模型漂移；接收方首次 `xskill index` 时由他们自己的 LLM 重新生成
- `index.pkl` — 向量索引，接收方首次 index 时用自己的 embedding 重建，和模型对齐

这样 zip 里只含"原料"，和接收方的 LLM/embedding 完全解耦。
