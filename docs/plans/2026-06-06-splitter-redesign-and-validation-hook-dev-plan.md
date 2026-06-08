# 开发计划:原子拆分重设计 + SKILL.md 校验钩子

> 日期 2026-06-06。执行按 CLAUDE.md「先建度量,再迭代收敛」+ 责任分离。
> 本文档是任务执行的唯一真相源,勾选跟踪进度。

## 关联 spec
- F1: `docs/superpowers/specs/2026-06-05-skill-frontmatter-validation-hook-design.md`
- F2: `docs/superpowers/specs/2026-06-05-agentic-taskagent-splitter-redesign.md`

## 范围(三块)
| 编号 | 特性 | 性质 |
|---|---|---|
| F0 | ingester 误标 `## User`(助手澄清/commit-SHA 被标成用户轮)排查 | F2 上游污染源,先量化 |
| F1 | SKILL.md frontmatter 写后校验钩子 | 小、独立,方法论热身 |
| F2 | 原子拆分代理 agentic 弃窗 | 大、0.6.0 阻塞项 |

## 数据集与指标
- **代表性合成集 15 条**(c00–c14,每条一个失败模式)+ **真实轨迹集 24 条**(21 直采 + 3 退化隔离)= **39 条,主指标 36 条**。
- **200 条广度集已弃用,不算资产。**
- **指标**:边界 F1/precision/recall + exact_match + **EOF 覆盖率** + 静默产空数 + **Pk/WindowDiff**(学术界标准,近失更公允)。
- **跑两遍 before/after**:① 基线 = 现行窗机制;② 收敛后 = 新 agentic。真实集 / 合成集两边都给。第二遍**由验收子代理跑**(责任分离)。

## 收敛判定
除已知豁免 c07(撤销/反悔):**边界失败数 → 0 + EOF 覆盖 100% + 静默产空 0**;真实集漏拆从 **31 → 接近 87**。

---

## 任务清单

### Phase 0 — 固化度量 + 基线 + F0 排查 ✅ 已完成
- [x] **T0.1** benchmark 固化进 `scripts/bench/`(synthetic 15 + 生成器 + evaluate.py + real/annotations.json;真实原文未入库)。
- [x] **T0.2** 评测器加 Pk/WindowDiff + 14 个 pytest(pylint 10/10,含反假实现闸)。
- [x] **T0.3** 第一遍基线(现行窗机制,无 LLM)见下。
- [x] **T0.4** F0 排查完成,裁决:**不单独修 ingester**(见下)。

#### Phase 0 结果
**基线(现行窗机制)before 数字:**
| 指标 | 合成集(15) | 真实集(21) |
|---|---|---|
| 边界 F1 | 0.923 | **0.000** |
| exact_match | 0.933 | 0.524 |
| EOF 覆盖 | 0.867 | **0.048** |
| Pk / WindowDiff(↓越好) | 0.067 | 0.172 |
- 真实集 F1=0、EOF 4.8%(21 条只 1 条覆盖到末尾):窗机制几乎不切真实大轨迹(31 atom vs 标注 87),且切到的早段也放错位 → TP=0。**这就是 F2 要打败的 before。**

**F0 裁决(纠正早先假设):** `## User` 误标**不是 ingester 系统性 bug**,绝大多数是**用户把日志/SHA 粘进自己的提问**(说话人归属本就对的)。纯机器粘贴块仅 ~0.7%,集中在少数自研项目轨迹。Codex(13)/OpenCode(14)轨迹结构退化无正文、不参与 `## User` 拆分。
→ **不立 F0' 修 ingester**;改为:① benchmark 排除少数"脏样本"(重度粘贴日志);② 拆分器加一道确定性"机器签名块不当边界"的容噪过滤(并入 F2/T2.2)。

### Phase 1 — F1 校验钩子(方法论热身)✅ 已完成
- [x] **T1.1 度量子代理**:12 测试传感器(6 坏 6 好)+ 反假实现闸 + 富误差度量,起点 12 红。接口契约 `parse_strict`/`FrontmatterError`。
- [x] **T1.2 编程子代理**:`parse_strict` 真校验 + `write_file` 当场拦(不写盘回富误差)+ 发布门兜底。12 红→12 绿,漏拦=误伤=0,pylint 10/10。
- [x] **T1.3 验收子代理**:采纳。Devil's Advocate 16 个边界样本零漏网零误伤,集成点真拦,裁判依据未篡改。
- [x] **T1.4** `make test` → **731 passed**。

### Phase 2 — F2 agentic 弃窗拆分器 ✅ 已完成
- [x] **T2.1 度量子代理**:`run_splitter.py` 驱动生产 TaskAgent + 接评测器,smoke 复现通过。
- [x] **T2.2 编程子代理**:弃窗单趟拆;4 工具(`submit_atom`/`look`/`context_budget`/`my_atoms`);EOF 覆盖硬校验;上下文自管理(85% 主动剪裁 + 超长报错兜底重发 + 分母配置默认 200K);`retries>0`+backoff+查 run 状态;增量续拆;打分留 TaskAgent;**删窗机制(`_line_window`/`max_context_chars`/窗循环/吸收,源唯一不留兼容)**;**容噪过滤(F0 并入):某 `## User` 块整块匹配纯机器签名——40-hex SHA / `HH:MM:SS [tag]` 日志 / 独立 JSON / ls 表——且无用户指令特征时,不当作拆分边界(降权,确定性零 LLM)**。
- [x] **T2.3 验收子代理**(独立只读):**采纳**。第二遍见下。
- [x] **T2.4** `make test` ✅ **745 passed**;`make e2e` ✅ **2 scenarios passed**。

#### Phase 2 结果(before/after)
| 维度 | 窗(before) | agentic(after) |
|---|---|---|
| 合成 F1 | 0.923 | **0.96** |
| 合成 EOF | 0.867 | **1.0** |
| 合成 Pk↓ | 0.067 | **0.031** |
| 真实 F1(tol5) | 0.000 | **0.46** |
| 真实 EOF | 0.048 | **1.0** |
| 真实怪物 atom/条 | ~1 | 10 / 20 / 23 |
- 漏拆灾难根治(真实 EOF 5%→100%);四道闸门(agentic 循环/EOF 断言/容噪/0提交 raise)经独立反例验证为真逻辑;`make test` 745 passed。
- 非阻塞遗留:真实集仍有过切(fp),边界精度是后续优化方向,非漏拆回归。
- 改动文件:`task_agent.py`(重写)、`context_budget.py`(新)、`agno_factory.py`、`config.py`、`tests/test_task_agent.py`。

### Phase F0' — 已取消
T0.4 判定 ingester 无系统性 bug,不立项。容噪过滤并入 T2.2,脏样本排除并入 benchmark。

---

## 推进顺序
Phase 0 → Phase 1(热身)→ Phase 2(核心)。F0 排查在 Phase 0 内,结果决定是否插入 F0'。

## 主代理职责
只调度:派子代理、切换目标、不下场写代码。
