# generate 行为观测实验

给 `xskill generate` 装一个可选的行为记录仪，先量清楚当前版本在干什么，
再动提示词和工具。埋点在产品代码里（`src/xskill/obs/`），默认整条关掉，
只有 `XSKILL_OTEL=1` 才生效。

## 量什么

每趟跑完在 `runs/<job>/features.json` 里得到：

| 字段 | 含义 |
| --- | --- |
| `compact_count` | 这趟压缩了几次；`context.compact_token_limit` 记着当时的阈值（默认 100000） |
| `compact_events` | 每次压缩的前后 token、耗时、重试次数、发生在第几轮 |
| `tool_calls` | 每个工具各调了多少次 |
| `tool_call_total` | 工具调用总次数 |
| `read_tool_calls` | 只看读类工具（`read_file`、`grep_files`、`list_files`、`skill_read`） |
| `read_traj_ids` | 真正读到的轨迹 id 列表，按首次读到的顺序去重 |
| `read_traj_count` | 上面这个列表的长度 |
| `llm_rounds` | 实际发出去的模型请求轮数 |
| `tool_seconds` / `tool_errors` | 每个工具的累计耗时与失败次数 |

同目录还有 `spans.jsonl`（OTel span 原始记录）、`run.json`（入参与结果
摘要）、`trace/`（人读的逐轮 trace）。

## 怎么跑

job 名是必填的，输出目录和 Phoenix 里的标记都按它区分：

```bash
cd scripts/bench/generate_obs

# 不打模型，验装配与埋点（第一次会自动建镜像和 mock 数据）
./run.sh --job baseline-01 --dry-run

# 读得多一点，会真触发一次 compact
./run.sh --job wide-read --dry-run --fake-reads 50

# 真打 DeepSeek（key 从 ~/.aikey 读，不回显）
./run.sh --job baseline-real --instruction "……"
```

产物在 `~/xskill-generate-obs/runs/<job>/`。改 `GOBS_ROOT` 可以换地方。

看面板（可选）：

```bash
./serve_phoenix.sh          # 默认 8873，已经有实例在跑就直接复用
```

`run.sh` 会自己探 8873 和 6006，探到就把 span 送进去，项目名
`xskill-generate`。探不到也不影响，`spans.jsonl` 照写。

## 数据从哪来

`export_cursor_mock.py` 把本机 Cursor 的历史会话全量桥成 xskill 原生的
`traj_*.md`，落在 mock xskill home 里，位置跟现网 server 收上传的一样：

```
~/xskill-generate-obs/mock/.xskill/team_trajectories/clients/cursor-local/sessions/
```

用的是产品自己的 `xskill.ecosystems.cursor` 桥接器，不裁剪、不摘要、不另
造中间格式。本机 428 条源会话桥出 406 条，约 8.2 MB。

容器只读挂 `~/.cursor`，写只写 mock home 和 runs 目录，碰不到真实
`~/.xskill`。

## 容器

镜像只装依赖，不装 xskill 本体——仓库源码在跑的时候挂进 `/repo`，靠
`PYTHONPATH=/repo/src` 生效。所以改提示词、改埋点、改工具之后直接重跑
就行，不用重建镜像。要重建加 `--build`。

基础镜像走 daocloud 镜像站：本机直连 Docker Hub 会被 TLS 拦。

## dry-run 是怎么做到不打模型的

只换掉最底层的 HTTP：把 agno `OpenAIChat.get_client` 换成一个吐预制响应
的替身（`fake_model.py`）。工厂、限流、上下文管理、重试、trace、埋点、
以及 agno 自己的工具分发全是产品原路，所以埋点数出来的数字是可信的。

假模型按剧本走：先 `list_files`，再逐条 `read_file`（条数由
`--fake-reads` 定），然后 `new_skill_folder`、`write_file`、
`commit_generate_main`。轨迹按大小从大到小读——mock 目录里中位数只有 6 KB，
按文件名顺序读几十条也顶不到 100k 阈值。压缩请求会被单独认出来，回一段
摘要文本，所以 compact 那条路也能在 dry-run 里验到。

## 已经看到的两件事

跑之前就发现的，跟 generate 调优直接相关：

- Cursor 轨迹的 traj_id 全是 `traj_cursor_unknown_<sid8>`。Cursor 的 JSONL
  里没有 cwd 字段（`ecosystems/cursor.py` 的 `_read_cwd_from_cursor_jsonl`
  直接返回空串），project 段就退化成 `unknown`。generate 光看文件名分不出
  这条轨迹属于哪个项目。
- 产品的 Cursor 扫盘 glob 是 `*/agent-transcripts/*.jsonl`，本机 Cursor 实
  际写的是 `*/agent-transcripts/<sid>/<sid>.jsonl`，多一层目录，所以
  `~/.xskill/cursor_sessions` 一直是空的。导出脚本两种布局都收（走
  `scan_and_bridge` 的 `candidate_paths` 正式参数）。这个是产品侧的口子，
  还没在 `src/` 里改。

另外 `read_file` 缺省一次给 400 行、约 10k 字符（约 2.5k token），所以要
把 100k 窗口读满得四十来次 `read_file`。

## 埋点自己怎么开（不走容器）

```bash
export XSKILL_OTEL=1
export XSKILL_OTEL_JOB=my-job
export XSKILL_OTEL_OUT=/tmp/my-job
export XSKILL_OTEL_ENDPOINT=http://127.0.0.1:8873   # 可选，送 Phoenix
export XSKILL_OTEL_CAPTURE_CONTENT=1                # 可选，记截断后的提示词正文
```

默认不记提示词正文、不记工具返回内容、不记 API key；span 属性只有工具名、
计数、轨迹 id 和短参数。

依赖：`pip install 'xskill[obs]'`（面板另装 `xskill[phoenix]`）。缺这几个
包时 obs 层自动退回无埋点，不影响正常跑。
