# 轨迹 CLI 命令面评审与命名决定

日期：2026-08-27。评审对象：PR #357 的 `xskill search traj` 与
`xskill search atom`；同时决定即将引入的「读取单条轨迹」命令的语法。
结论先说：#357 的命令面确实暴露过度，建议改成名词在前的
`xskill traj search`、`xskill traj read`、`xskill atom search`，
趁 PR 未合直接改，不留旧拼法别名。

## 一、#357 现状哪里暴露过度

### 1. 首词魔法字吃掉了查询词的命名空间

现实现是在 `cmd_search` 里看第一个位置词：等于 `traj` 走轨迹检索，
等于 `atom` 走 Atom 检索，其余当 skill 搜索。这意味着：

- `xskill search traj` 想搜「和轨迹处理有关的 skill」的用户，
  会被静默改道去搜轨迹库，还报「error: 用法 xskill search traj <query>」。
- `xskill search atom 编辑器` 想找 Atom 编辑器相关 skill 的用户同理。
- 查询词和子命令选择器共用一个位置参数空间，今后每加一种可搜对象
  就多烧掉一个普通英文单词，且无法给用户任何转义手段。

### 2. 三种检索共用一个 parser，语义不合的 flag 靠 warning 兜底

`--download`、`--min-similarity` 等只对 skill 搜索有意义的选项，
在 traj 与 atom 路径上要么忽略加 warning，要么无声无效。#357 里
「--download 只对 skill 搜索有效」「--name 仅 team 检索有效」这类
运行时警告，本质是一个 parser 硬撑三个命令的补丁。命令一旦拆开，
这些 warning 大部分直接消失：不该有的 flag 在 argparse 层就不存在，
`--help` 也各自干净。

### 3. 内部概念与内部字段进入公开契约

Atom 是拆分代理的中间产物，本来是流水线内部粒度。`search atom`
把 atom_id、行号区间、vector_similarity、bm25_score 全部写进
`--json` 公开契约。同事的脚本一旦依赖 atom_id 与 offset，
拆分粒度、重拆策略就再也不能动。建议：

- 保留 atom 检索能力（agent 先搜 traj_id 的场景确实需要），
  但把它明确定位为面向 agent 与脚本的低层命令，帮助文本写明
  「Atom 为系统拆分产物，粒度可能随版本变化」。
- vector_similarity、bm25_score 这类检索内部分数只留在 `--json`；
  人读卡片维持「匹配：0.91（语义、关键词）」的抽象即可（现状已如此）。
- 人的默认入口只推 traj 检索；文档与 `/xskill-helper` 里把 atom
  检索放到进阶一节。

### 4. 即将引入的 read 会撞现有命令

`xskill read` 已被占用：`xskill read <PATH> --eco ngagent` 是把
db 文件桥接入库的批量命令，位置参数是路径。若引入
`xskill read traj <id>`，argparse 会把 `traj` 当成路径，只能再上
一层首词魔法字判断，把第 1 条的坑原样复制到 read 下面。

## 二、动词在前还是名词在前

两种候选：

- 动词在前：`xskill search traj`、`xskill read traj`
- 名词在前：`xskill traj search`、`xskill traj read`

对比：

1. 冲突。动词在前必须解决 `read` 已被 db 桥接占用的冲突，
   以及 search 首词魔法字问题；名词在前天然无冲突，`traj` 作为
   顶层子命令是新增名字，不与任何现有命令、任何查询词打架。
2. 扩展方向。轨迹这个资源的操作会持续增长（search、read，
   以后可能 list、stats、export）。名词在前时新操作都挂在
   `xskill traj` 底下，一个子 parser 自然收拢，flag 各归各；
   动词在前时每加一个操作都要去一个顶层动词里塞首词分派。
3. 先例。业界两种都有（kubectl 是动词在前，git、gh、docker
   是名词在前：gh pr view、docker image ls）。差别在于对象少、
   动词少时动词在前更口语；资源多、每资源操作多时名词在前
   更可维护。xskill 正在从「一个 search」长成「多种可检索、
   可读取的资源」，属于后者。
4. 本仓库现状。`xskill registry add|remove|list` 已经是
   名词在前的形态，选名词在前与现有命令面一致。

结论：名词在前。

## 三、建议的命令面

```
xskill traj search <query> [--name 甲,乙] [-k N] [--json] [--team|--local]
xskill traj read   <traj_id> [--json]
xskill atom search <query> [--name 甲,乙] [-k N] [--json] [--team|--local]
xskill search <query> ...        # 不变，只搜 skill，不再看首词
```

- `traj` 与 `atom` 是两个顶层名词子命令，各自独立 parser。
  atom 不挂在 traj 下面（`xskill traj atom search` 太深），
  但帮助文本里互相指路。
- `xskill search` 恢复为纯 skill 搜索，`traj`、`atom` 两个词
  还给查询词空间。#357 引入的首词分派删除。
- 卡片文案、`--json` 字段、错误话术沿用 #357 已定稿的设计，
  只把里面的用法字符串换成新拼法。

## 四、traj read 的暴露控制（比命名更要紧）

检索卡片只透出首问，而 read 是把整份 traj_*.md 原文给出去。
同事的会话里常有贴出来的日志、配置、密钥片段，read 的暴露面
比 search 大一个量级，必须先定权限语义再实现：

- 默认只允许读自己工号目录下的轨迹；读他人轨迹需要 server 侧
  显式开关（团队自行决定开不开）。
- 输出不含 server 侧绝对路径（与 #357 同一条红线）。
- 全文输出默认截断（如前 N 行加提示），`--full` 才给全文。
- server 未开放他人轨迹时报一行权限说明，不甩 traceback。

具体的敏感内容脱敏（密钥模式识别之类）本期不做，标注以后再说。

## 五、迁移

- PR #357 尚未合并：直接在该 PR（或叠加 commit）里把命令面改成
  名词在前，`xskill search traj|atom` 这个拼法不发布、不留别名。
- server 端两个 HTTP 入口路径不动（/api/v1/team/trajectories/search、
  /api/v1/team/atoms/search），本文只改 CLI 面。
- `/xskill-helper` 与三份 README 里的示例同步换拼法。

## 六、不做与以后再说

- 现有 `xskill read`（db 桥接）本期不改名，避免牵连已有用户习惯；
  它与 `xskill traj read` 的语义混淆问题记录在案，以后再说
  （候选方向：并入 `xskill import` 或改名 bridge，第二期评估）。
- `xskill traj list`、`xskill traj stats` 等新操作本期不加，
  只保证命名空间留好位置。
- 敏感内容脱敏见第四节，以后再说。

## 七、本机未 connect 也可以搜（本期）

只需要本机轨迹时，不必先 `xskill connect`。`pip install xskill` 之后：

- 首次 `xskill traj search`（standalone，或显式 `--local`）若还没有
  `~/.xskill/local_init.json`，或本机索引仍空，就现场扫描已探测到的
  harness，把会话转成 `~/.xskill/*_sessions`，并建会话索引。
- `xskill init` 是同一套本机引导的手动入口：列出扫到的 harness、开启
  轨迹处理、询问要把 `/xskill-helper` 装到哪几个 harness。不要求
  server 地址或 token。`-y` 无头全做；`--no-skill` 只扫不装 helper；
  `--skills-only` 只装 helper。
- 引导结束会提醒：`xskill connect` 连远端拿共享 skill 和同事轨迹；
  `xskill serve --server` 自己起团队服务。
- 已 connect 时默认仍走 team；`--local` 才走本机，并在尚未初始化时
  补一次扫描。
- team server 进程所在机器的自动扫描默认跳过，避免把操作员 HOME
  扫进 serve 仓库。`xskill init` 本身会扫（`skip_if_server=False`）。

以后再说：本机常驻、不靠首次 search 触发的增量采集；把 collector
后台线程也提供给未 connect 的用户。
