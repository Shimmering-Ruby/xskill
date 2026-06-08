# SKILL.md 写入后格式校验钩子 — 设计 spec

> 状态:设计待评审(brainstorming 产出)。开发按 CLAUDE.md「先建度量,再迭代收敛」+
> 责任分离执行。本特性是该方法论的首个落地练习。

## 1. 背景与观测到的根因

EditAgent 产出的 skill 偶尔带**非法 YAML frontmatter**(典型:多行 `description`
用了字面换行,没用块标量 `|` 也没加引号)。它能一路活到发布,**不是单纯 agent 的锅,
而是 agent 近端产出 + 我们三处静默放行共同造成**(已读源码确认):

1. `frontmatter.parse()` 撞 `yaml.YAMLError` 时静默吞掉,返回 `({}, 原文)`——把坏
   frontmatter 当"没有 frontmatter"。
2. `skill_tools.write_file()` 写 SKILL.md 时本想 parse→重序列化消毒,但 `fm={}` 时
   `serialize({}, body)` 命中 `if not fm: return body`,退化成**原样写盘**;`except`
   只打 warning 也照写。
3. `SkillEditAgent` 发布门只查 **mtime 推进 + 非空**,不验 YAML。

结论:三处该拦没拦,坏文件逐字写盘并 commit 发布。

## 2. 目标与度量(度量先于实现)

**目标**:坏 frontmatter 在**写入当场**被拦下并把富误差回灌给 agent,agent 在同一轮
修正;**绝不落盘/发布非法 SKILL.md**。

**度量(随迭代单调下降,收敛到 0)**:一套测试数据,两个方向的计数都要降到 0——
- **漏拦数**:本该被判非法的 SKILL.md,校验没拦住的条数。
- **误伤数**:合法 SKILL.md,被校验错误拦下的条数。

度量返回**富误差**(哪条、哪行、判定原因),不只返标量。

## 3. 方案(控制行为:改现有组件,不加新工具)

把第 1 节那三个静默点改"响":

1. **严格 parse 变体**:给写入端用的解析**遇非法 YAML 抛错**(携带 yaml 的行号/原因),
   不再静默返 `{}`。原 `parse()` 的宽松行为(读取端用)保留,二者分开。
2. **`write_file` 写后校验(当场校验)**:写 SKILL.md 时,若 `content` 以 `---` 开头
   (agent 意图写 frontmatter)→ 跑严格校验:
   - frontmatter 是合法 YAML(safe_load 通过);
   - 必填字段存在:`name`、`description`;
   - `description` 是**非空字符串**(专门抓"多行没块标量被解析成残缺/非字符串"的情形);
   - body 非空。
   校验不过 → **不写盘**,返回富误差给 agent("description 第 X 行多行需用块标量 `|`
   或加引号:<yaml 原始报错>"),让它当场改重写。
3. **发布门兜底**:`SkillEditAgent` 发布前再跑一次同一校验,万一漏了 → 标重试、不发布。

> 注:`serialize` 的 `width=100` 会折长中文行(合法但难看),可顺手调宽;非本特性根因,
> 不阻塞。

## 4. 责任分离开发流程

- **度量子代理**:构建测试夹具(坏样本:多行 description 无块标量 / 缺 name / 缺
  description / description 写成 list / 合法 frontmatter 被误判的对照;好样本:各类合法
  SKILL.md),搭最小骨架,确认"漏拦数/误伤数"指标可跑。
- **编程子代理**:实现第 3 节,对指标迭代到双 0。
- **验收子代理(独立、只读)**:仅 `Read/Grep/Glob/Bash`,**无 Edit/Write**;独立复核
  指标、查反"假实现"(防止校验被写成恒真/恒假)。不过持续回退给编程子代理。
- **主代理**:调度,不下场写代码。

## 5. 范围

**v0 做**:SKILL.md frontmatter 合法性 + 必填字段 + `description` 非空字符串校验;
`write_file` 当场拦 + 发布门兜底;严格 parse 变体。

**不做(后续)**:body 的 markdown 结构校验、≤400 行硬约束、scripts/references 校验、
splitter 弃窗重设计(另立 spec)。

## 6. 验收标准

- 测试数据漏拦数 = 0 且误伤数 = 0。
- 反"假实现"闸通过(校验逻辑非桩)。
- `make test` 通过。
