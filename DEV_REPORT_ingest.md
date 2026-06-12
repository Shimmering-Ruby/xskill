# DEV REPORT — 入库完成屏障（settle barrier）+ 去壳掩码（mask_patterns）

分支：`feat/ingest-settle-and-mask`（基于 main @ 7895156，未 push、未 merge，待 review）

## 背景 bug（已实证）

`JsonlIngester.scan_and_bridge` 对各生态 session JSONL 是"出现即读、按 sid
去重后永不回头"：session 刚开跑、文件刚出现就被整读转换入库，sid 进 `seen`
集合后该 session 永不重读。实证案例 session 03fe589e：源 jsonl 52 行 / 124KB /
29 条 assistant 消息，入库 md 仅 20 行题面残骸。

## 改动文件

| 文件 | 改动 |
| --- | --- |
| `src/xskill/config.py` | CONFIG_TEMPLATE 新增 `ingest` 段；新增 `ingest_config()` 免 api_key 读取器（参考 `dashboard_attribution_defaults` 先例，team 瘦客户端可用）；`INGEST_SETTLE_SECONDS_DEFAULT = 120.0` |
| `src/xskill/utils/sanitize.py` | 新增 `MASK_PLACEHOLDER`（`[MASKED_HARNESS_PROMPT]`）与 `apply_mask_patterns(text, patterns)` |
| `src/xskill/ecosystems/_shared.py` | ① `submit_trajectory` 新增 `mask_patterns` 参数，sanitize 之后、写 md 之前应用掩码；② `JsonlIngester` 新增 `settle_seconds` 构造参数；③ `scan_and_bridge` 实装 settle 屏障 + 续写增长重转换（含 `reset_trajectories(traj_id=...)` 重置）；④ 新增 `_bridged_md_by_sid8` 反查索引 |
| `src/xskill/ecosystems/claude_code.py` | `CCSessionIngester.run_once`：`rebridged` 的 session 只补 `<!-- xskill:... -->` header（覆盖写把旧 header 冲掉了），不再翻灰度牌，避免重复消耗灰度配额污染 A/B 分布 |
| `tests/conftest.py` | 新增 autouse fixture `_isolate_ingest_config`：测试默认 settle=0 / 掩码空，不读开发机真实 `~/.xskill/config.yaml`；显式传参的测试不受影响 |
| `tests/test_ingest_settle_mask.py` | 新增 16 例（见下） |
| `tests/test_config_autoinit.py` | 模板顶层键守卫加入 `ingest`（该段确有代码消费：`config.ingest_config`） |

## 配置项、默认值与理由

```yaml
ingest:
  settle_seconds: 120   # 入库完成屏障
  mask_patterns: []     # 去壳掩码（正则列表）
```

- **`settle_seconds: 120`**（任务给定 90~180s 区间的中段）：
  - 源 session 文件最后修改距今 < 120s 视为"还在写"，本轮不入库；停笔满期后
    下一轮 poll 转换。
  - 过短（<60s）：真实用户 session 动辄几十分钟，长思考 / 长工具调用的间隙
    就会被误判"已写完"提前定格；过长：新 session 入库延迟无谓增大。
  - **评测场景建议 5~15s**（脚本批量产 session、写完即定稿），已写进模板注释；
    评测差异全部走配置值，核心路径无任何评测专用逻辑。
- **`mask_patterns: []`**：默认空列表 = 完全不替换，现网用户零影响。命中段
  替换为 `[MASKED_HARNESS_PROMPT]`；跨行匹配用内联 flag（如 `(?s)`）。坏正则
  在 `ingest_config` 编译校验阶段直接抛 ValueError（fail-loud，不静默失效）。

## 设计要点

1. **增长检测判据**：源文件 `mtime` 晚于已桥接 `traj_*.md` 的 `mtime`
   （md 写盘时刻即"转换时刻"的可靠记录）。无状态、不引入游标文件；对升级前
   已入库的历史残骸同样生效（源在转换后还在长 → 下轮被重转换修复）。
2. **重转换的重置**：复用 `registry.reset_trajectories(traj_id=...)`——与
   `xskill rebuild --traj` 同一段逻辑（删 atom 文件 + 删 index.pkl + DB 状态翻
   `discovered`），watcher 下轮从头重拆，不走 last_offset 续接（旧残骸 atom 作废）。
3. **sid → 已桥接 md 反查**：traj 文件名尾段即 sid8（`<prefix><project>_<sid8>.md`），
   每轮一次 glob 建索引，不需要读源文件内容，扫描成本不变。
4. **掩码应用位置**：`submit_trajectory` 内、sanitize 之后写 md 之前（入库
   转换阶段，非拆分阶段）——落盘文本本身已去壳，拆分 / 聚类 / embedding 一律
   看不到 harness 外壳。注意：`.json` sidecar 的 timeline 元数据未掩码（拆分
   聚类不消费它），如后续有消费方需同步掩码再扩。
5. **覆盖范围**：settle 屏障 + 重转换作用于全部 JSONL 生态
   （claude_code / codex / openclaw / cursor，含 team client collector）。
   SQLite 生态（opencode / ngagent / trae）走独立 SqliteIngester 游标机制
   （`time_updated`），不在本次范围。
6. **CC 灰度交互**：重转换的 session 重新判定 `used_skill` 并补注 header
   （触发 ux 评分的唯一门槛），但**不**再翻灰度牌——同一 session 不重复消耗
   灰度配额。

## 测试清单与结果

新增 `tests/test_ingest_settle_mask.py`（16 例，全绿）：

- **T1 配置**（5）：缺省值（120 / 空）、yaml 段读取、坏正则 fail-loud、
  非列表 fail-loud、模板含 ingest 段。
- **T2 settle 屏障**（3）：fresh 文件不入库；停笔满期入库；`settle_seconds: 0`
  关闭屏障（评测用）。
- **T3 续写重转换**（3）：**核心回归测试**"jsonl 先写一半 → 扫描 → 补全 →
  再扫描，最终 md 含补全内容"（实现前实测失败，旧实现永不回头）；settle 期内
  增长等待；无增长幂等不重转。
- **T4 atom 重置**（1）：重转换后旧 atom 文件删除、index.pkl 删除、DB 状态翻
  `discovered`。
- **T5 掩码**（4）：命中替换 + 解题内容无损；默认空不改文本；从 config 取
  patterns；跨行 `(?s)` 掩码。

全量回归：`make test` → **856 passed**（基线 855 passed + 新 16 - 调整前
失败 0；唯一改动的既有测试是模板键守卫，加入 `ingest` 键）。pylint（E/W）
对改动文件无新增告警（仅既有模式）。Docker E2E（`make e2e`）未跑——属发版前
流程，review 后再过。

## 对真实用户行为的影响声明

- **入库延迟**：新 session 从"文件出现即入库"变为"停笔满 120s 后入库"。对真实
  用户这是纯收益方向的修 bug（不再把进行中的 session 定格成残骸）；延迟仅
  影响入库时刻，不丢任何数据。
- **历史残骸修复**：升级后第一轮扫描会把"转换后源仍在增长"的旧残骸轨迹重转换
  并重置其 atom（一次性 LLM 重拆成本，换取轨迹完整性）。源自转换后未再变的
  轨迹不受影响。
- **掩码**：默认空列表，现网用户文本零改动。
- **灰度链路**：重转换 session 不重复翻牌，A/B 分布语义不变。
- 无评测专用逻辑进入核心路径；评测场景全部通过 `ingest.settle_seconds` /
  `ingest.mask_patterns` 配置表达。
