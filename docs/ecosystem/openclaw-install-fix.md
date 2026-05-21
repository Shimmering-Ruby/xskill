# openclaw 用 copy 而不是 symlink 的细化方案

## 起因

真 e2e 跑出来的证据：openclaw 在 `agents-skills-personal` 这档对 symlink
做 escape-root 安全检查（resolved realpath 必须留在 `~/.agents/skills/` 内）。
xskill 现在的 install 链接到 `~/.xskill/skill/<name>`，跑出 root → openclaw 直接 skip。

老的 e2e（用 fixture）没发现这个，因为根本没起真 openclaw 进程，只校验了
`~/.agents/skills/<name>/SKILL.md exists`，但没问"openclaw 真看到了吗"。

## 思路

不动 CC / Codex / OpenCode（它们继续 symlink，工作正常）。**只对 openclaw**
改成 copy。代价是 openclaw 这一侧失去 live-update 和用户手改回流自动化，但
这两件事都能用别的办法兜住，下面分别说。

---

## 改 install_to_openclaw

`install_to_openclaw(skill_path, target_root, side)` 直接 copytree，不走
`_install_skill_into` 那条 symlink-first 的三阶 fallback。流程：

1. 按 side 决定源目录（main=`<skill_path>`，staging=`<skill_path>/../.canary/<name>/`）
2. 如果 dest 已存在 —— symlink / 文件 / 目录都 unlink/rmtree 掉
3. `shutil.copytree(src, dest)`，dest 是真目录，不是 symlink
4. 在 dest 里写一个 `.xskill-install-meta.json`，记下 `{source_sha, side,
   installed_at}`，方便后面 canary flip 时判断"现在装的是哪一版"，避免每次
   都无脑重 copy

为什么不复用 `install_fallback.copy` 分支：那条 fallback 是 symlink/junction
**失败时**才走的兜底，我们这里是**主动选择** copy。语义不同，逻辑也只是
shutil.copytree + 元数据写，单独写更清楚。

## standalone 模式的灰度怎么走

现在 standalone 灰度是 `pick_side(traj_id, skill_name, probability)` 哈希
分桶——本质是"这次 session 命中 main 还是 staging"。CC 那边由
CCSessionIngester 在每次 session 开始时决定 + 切 symlink。symlink 切换
是常数时间，所以频繁 flip 没成本。

对 openclaw 不能切 symlink，那 flip 怎么搞？

时机选 **"session 开始之前"** 触发 copy-overwrite：

- xskill 给 openclaw 起一个 OCSessionIngester（或者直接在现有
  `JsonlIngester(OPENCLAW_SPEC)` 的 `_loop` 里加 hook）
- 每轮 poll 时扫 `~/.openclaw/agents/<agent>/sessions/`，对每个**新出现的**
  `.trajectory.jsonl`（也就是新 session）做一次 pick_side 决定该 session 该
  看哪个 side
- 跟 install_history 里上次装的 side 比较，如果变了 → copy-overwrite
- 注意：openclaw skill snapshot 在 session 启动时就冻结，所以严格来说我们
  这个"flip"是给**下一个** session 准备的，不影响当前正在跑的 session。
  这点跟 CC 不同（CC 一次 session 内也能因为 watcher 触发 hot reload 看到新
  skill）。文档化即可

复用 install_history 模式：每次 copy 完，append 一条 `{skill, side, sha,
installed_at, agent="openclaw"}`，跟 CC 现在一模一样。

边界：copy 是有成本的操作（几十 KB ~ 几百 KB），不该频繁触发。判定
"side 是否变了" 用 install_history 最新一条 + 当前 pick_side 结果比较，相同
就 no-op，不无脑 copy。

## team 模式的灰度怎么走

team 模式现状：

- server 按 client_id 哈希决定每个用户看哪个 side / sha，推 manifest 给 client
- client TeamClient daemon 拉 bundle → reconcile_skill_side → checkout 到目标
  sha → 调 `on_changed(repo_dir)` → `_install_to_ecosystems(repo_dir)` 装到所有
  生态

也就是 team 模式 **客户端不再自己 pick_side**，server 已经"翻好牌"，working
tree 内容就是该用户该时刻应当看到的版本。每次 server 决定切换（用户 bucket
变 / staging promoted to main），server 推新 manifest，client 拉 bundle，checkout
新 sha，重新 install。

对 openclaw 来说：只要 `_install_to_ecosystems` 调的是新版 `install_to_openclaw`
（即 copy 而非 symlink），整个 team 链路**自动 work**——每次 server 推切换 →
client checkout → copytree 到 `~/.agents/skills/`，openclaw 下一个 session 看到
新版。

team 模式不需要单独"时间窗"逻辑，因为 team server 已经替每个用户决定好了
当前 side。

## 用户手改怎么办

copy 模式下用户可能改两处：
- `~/.xskill/skill/<name>/`（源仓）—— 原有 absorb 已经处理
- `~/.agents/skills/<name>/`（openclaw dest copy）—— 现状不处理，要做

**不另起新机制，加一座 dest→source 回流桥就行**：让 dest 的用户改先灌回
source，然后原有 absorb（standalone）/ push-edit（team）链路就能像处理普通
源仓改动一样把它收编。

### 检测 dest 有用户改

watcher 现有 loop 已经在扫 `~/.xskill/skill/<name>/`。加一段对 openclaw
dest 的扫描：

每轮 poll：
1. 列出本机所有装到 openclaw 的 skill（看 install_history 里 agent="openclaw"
   且最近一次 action 是 "install" 的）
2. 对每个 dest（`~/.agents/skills/<name>/`），递归找最新 mtime —— 跳过
   `.xskill-install-meta.json`、`README.openclaw.md`、`.git/`（虽然 dest
   不应该有 .git，防御性跳过）
3. 跟 dest/`.xskill-install-meta.json` 里记的 `installed_at` 比较

如果 dest 最新 mtime > installed_at + 3min（用户停手 3 分钟，跟 source 端
absorb 的静默窗口一致），就触发回流。

### 回流操作

回流就是 dest 内容拷回 source，然后走原有 absorb：

1. **抢 source 锁**（`skill_repo_lock(repo_dir)`）—— 跟 CC absorb 共用同一把
   锁，确保不跟 canary flip / cluster init 撞车
2. 对比 source 跟 dest 的每个文件
   - dest 里有、source 里没有 → source 新建
   - dest 里有、source 里也有但内容不同 → source 覆盖
   - dest 里没有、source 里有（非 xskill 自带文件如 `.git/`）→ source 删除
3. 跳过的 xskill 自带文件：`.xskill-install-meta.json`、`README.openclaw.md`
   这两个是 install 时塞的，不属于 skill 本体
4. 把 dest mtime（最新那个文件）也同步到 `<source>/.xskill-meta-dest-mtime`
   （或直接 touch 一下 source 同名文件），让 source 的 mtime 看起来"用户刚
   改完"——这样原有 absorb 的 3min 静默窗口检测就会被触发
5. 释放锁

回流完，watcher 下一轮自然就会看到 source 有新 mtime → 走原有 absorb（standalone）
或 push_user_edits（team）。dest 这边不动，等下次 canary flip 或下次 install
自然会用更新过的 source 覆盖回来。

### 边界：同时改 source 又改 dest

理论上用户可能同时改两边。简单策略：

- 如果 source 也有未 absorb 的改（mtime > 上次 commit time 且距 now ≥ 3min）
  **且** dest 也有改 → 看哪边 mtime 更新，新的胜
- log warning 把这事记下来，让用户知道发生过冲突合并

不引入手工冲突解决 UI，因为这是 corner case；mtime 仲裁不完美但够用。

### 边界：canary flip 撞上 dest 用户改

canary flip 想 copy-overwrite dest，但 dest 有未回流的用户改 → 先做回流
再做 flip。顺序：

```
flip 触发
  → 锁 source
  → 检测 dest 是否有未回流改动
      yes: 先 reverse-copy 到 source，touch source mtime → 释放锁返回
           absorb 在下一轮 watcher poll 自然跑
           本次 flip 跳过（让 absorb 先收编完，下一轮再考虑 flip）
      no:  正常 copytree 新 side → dest
  → 释放锁
```

这样保证用户改不会被静默吞掉。

### team 模式

team 客户端跟 standalone 共用上面这套逻辑——回流的"source"在 team mode 下
就是 client 的 working copy（同一个 `~/.xskill/skill/<name>/` 路径）。回流
完，working copy 有改动 → 下次 `push_user_edits` 自然把它推到
`user-staging/<client_id>` 分支。team 链路零额外改动。

### 关键文件改动

- `src/xskill/agents/user_edit_absorb_agent.py` 加一个新函数
  `reverse_sync_openclaw_dest(dest, source) -> bool` 实现回流操作（return
  True 表示真的有改动 synced 回去）
- `src/xskill/watcher.py` 的 poll 循环里，扫 `~/.xskill/skill/` 之前先扫
  `~/.agents/skills/` 下所有 openclaw 装过的 skill，必要时调
  `reverse_sync_openclaw_dest`
- `src/xskill/ecosystems.py` 的 `install_to_openclaw` 在 copytree 前调
  `reverse_sync_openclaw_dest`，保证 flip 不吞改动

## 改动点清单

1. `src/xskill/ecosystems.py`
   - `install_to_openclaw` 重写：不再走 `_install_skill_into`，自己实现 copy
   - copy 前先调 `reverse_sync_openclaw_dest`（防止吞用户改）
   - 写 dest 时落 `.xskill-install-meta.json`（记 source_sha / side / installed_at）

2. `src/xskill/ecosystems.py`（同文件，JsonlIngester 部分）
   - JsonlIngester 加可选 hook 在 standalone 模式做 canary flip：
     新 session 文件出现 → pick_side → 跟 install_history 对比 → 调
     `install_to_openclaw(side=new_side)` 触发 copy（内部已包含回流保护）

3. `src/xskill/agents/user_edit_absorb_agent.py`
   - 新增 `reverse_sync_openclaw_dest(dest_dir, source_dir) -> bool`：扫 dest
     mtime，发现用户改 → 抢 source 锁 → 灌回 source → touch source 让 watcher
     下轮看到 → 返回 True
   - 跳过 `.xskill-install-meta.json` / `README.openclaw.md` / `.git/`

4. `src/xskill/watcher.py`
   - poll loop 开头加一段：扫 install_history 里 agent="openclaw" 的 skill，
     对每个 dest 调 `reverse_sync_openclaw_dest`。这一步必须在原有"扫 source
     看 mtime 触发 absorb" 之前跑（让回流后的 source mtime 被同一轮看见）
   - `_install_skill_to_all_detected` 加 openclaw（installer_by_ecosystem 字典加一项）

5. `src/xskill/team/client/daemon.py`
   - `_install_to_ecosystems` 加 openclaw 入口（installer 字典加 `"openclaw":
     install_to_openclaw`）
   - team daemon 自己的 poll loop 也要扫 dest 做回流（或者复用 watcher 那
     段——team client 是不是已经在跑 watcher？要确认）

6. 测试
   - `tests/test_openclaw_adapter.py` 加：
     - `TestInstallToOpenClaw`：dest 是真目录、内容一致、改 source 后 dest 不
       自动变（验证不是 symlink）、`.xskill-install-meta.json` 落地
     - `TestReverseSync`：dest 改一个文件 → reverse_sync 后 source 同步、
       source mtime 被 touch；dest 没改 → 返回 False 不动 source
     - `TestCanaryFlipWithPendingDestEdit`：dest 有未回流改 → install_to_openclaw
       先回流再 copy，用户改不丢
   - 真 e2e（`tests/docker_e2e/openclaw_real_llm/`）：
     - 跑真 openclaw，断言 `trace.metadata.data.skills` 含 demo-marker
     - 手动改 dest 的 SKILL.md，等 watcher 跑一轮，断言 source 同步了

7. 文档
   - 更新 `docs/ecosystem/openclaw.md` §4 / §6 / §8

## 不做的事

- 不动 CC / Codex / OpenCode 的 install 路径
- 不引入新的 install 抽象（copy 就是 copy）
- 同时改 source 和 dest 的冲突场景不做手工解决 UI，mtime 仲裁 + warning

## 实施顺序

1. 改 `install_to_openclaw` + meta 文件，单测验 dest 是 copy 不是 symlink
2. 加 `reverse_sync_openclaw_dest`，单测验回流逻辑
3. 改 watcher poll loop 加回流扫描 + 加 openclaw 装入口
4. 改 team daemon 加 openclaw 装入口 + 回流扫描
5. 加 standalone canary flip（JsonlIngester hook 或独立 flipper）
6. 真 e2e 跑通 marker 出现在 trace.metadata + 手改 dest 能回流
7. 更新文档
