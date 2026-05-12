"""SkillEditAgent —— SKILL.md 自主整理（candidates buffer 累计阈值触发）
========================================================================

何时触发：单个 skill 的 ``.candidates.yml`` 中所有 **未 promoted** 的 candidate
``weightscore_total`` 累加 ≥ ``ATOM_PROMOTION_THRESHOLD`` (=10) 时。

与旧 ``candidates._run_skill_edit_agent`` 的差异：
- 触发器从"≥N 条 supporting_trajs"换成"buffer 累计 weightscore ≥10"
- 候选条目字段是 ``atom_id`` 而非 ``pattern``；agent 负责通过 AtomTaskRead /
  ReadTraj 工具拿到真实内容来撰写 SKILL.md
- 类化封装，便于注入 stub agno agent 工厂做单测
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from xskill import candidates as C

logger = logging.getLogger("xskill.skill_edit_agent")


SYSTEM_PROMPT = """你是 SkillEditAgent。一个 skill 的 candidates buffer 已积累了
若干 atom 贡献（累计 weightscore ≥ 10），系统判定该触发一次 SKILL.md 整理。

# 你的输入
我会列出 candidates 的 atom_id + 各自的 weightscore_total（高分代表 cluster agent
更确信它对此 skill 有贡献）。

# 你的目标
读 atom 内容（AtomTaskRead），必要时读 traj 原文（ReadTraj），按 SKILL.md v2
schema 写或更新 SKILL.md：

```
---
name: <英文 slug>
description: <2-5 句中文：干什么、典型触发表述（引号引原文）、需要的工具/权限>
compatibility: <环境/版本/权限 + 至少一条负向硬约束>
metadata:
  version: 1
  created: "<AUTO>"
  last_updated: "<AUTO>"
  source_atoms: ["atom_xxx_0001", ...]
  frozen: false
  use_count: 0
---

# <中文标题>

<开头一段：这个 skill 解决什么，核心原则>

## <阶段名-1>
1. <步骤：精确到命令/文件/函数>
2. <步骤>

   > ⚠️ <警告：引用 atom 证据如"见 atom_xxx_0001"或"3/5 atoms 表明..."，
   > 不要凭空猜>

## <阶段名-2>
3. ...
```

# 你可以
- AtomTaskRead(atom_id) — 读 atom JSON
- ReadTraj(traj_id, offset_start, offset_end) — 按 offset 取轨迹原文片段
- SkillRead(name) — 读现有 SKILL.md（若有，用于更新场景）
- write_file(path, content) — 只允许写在该 skill 目录下

# 你不要
- 不要随便引用没在 atom 中出现过的命令/函数；以 AtomTaskRead 为唯一可信来源。
- 不要在描述里发明用户群体或场景。
- SKILL.md 不超过 400 行；长参考材料放 references/，脚本放 scripts/。
- 不要写 `## trigger` / `## 触发条件` / `## pitfalls` / `## 陷阱` 这种段；
  触发信号在 frontmatter.description，pitfalls 用内联 ``> ⚠️`` blockquote。
"""


@dataclass
class SkillEditAgent:
    """每个实例服务**一个**具体 skill 子目录。"""
    skill_dir: Path
    store: Any  # AtomTaskStore (only needed by atom_task_read tool indirectly)
    agno_agent_factory: Callable[..., Any]
    llm_cfg: dict
    traj_root: Path
    threshold: int = C.ATOM_PROMOTION_THRESHOLD

    def maybe_run(self) -> bool:
        """检查 buffer，达阈值则跑 agent 编辑 SKILL.md。

        返回 ``True`` 表示**确实把候选 promote 了**（即 SKILL.md 实际落盘且
        非空）；``False`` 表示门槛未到、或 agent 跑完但没产出 SKILL.md
        （LLM 失败 / agno 静默吞错误 / agent 想写但被工具拒）——这种情况下
        candidates 保留 promoted=false，下一轮 watcher 扫描时会再次重试。

        关键设计：**不能无条件 finally 标 promoted**。曾经被 agno 内部静默
        吞 402 错误坑过——agent.run 看似成功返回，但 SKILL.md 根本没写；
        如果那时仍标 promoted=true，candidates 永久污染，无法再重试。
        所以现在以"SKILL.md 实测落盘"作为 promote 判据，agent 不写就不标。
        """
        data = C.load_candidates(self.skill_dir)
        ready = C.ready_for_promotion_v2(data, threshold=self.threshold)
        if not ready:
            return False

        skill_md = self.skill_dir / "SKILL.md"
        mtime_before = skill_md.stat().st_mtime if skill_md.is_file() else 0.0
        size_before = skill_md.stat().st_size if skill_md.is_file() else 0

        try:
            self._run(ready)
        except Exception:
            logger.exception("SkillEditAgent _run failed: %s", self.skill_dir.name)

        # 真实落盘检查：mtime 推进 + 文件非空 = agent 真的写了
        wrote = (
            skill_md.is_file()
            and skill_md.stat().st_size > 0
            and (
                skill_md.stat().st_mtime > mtime_before
                or skill_md.stat().st_size != size_before
            )
        )
        if not wrote:
            logger.warning(
                "SkillEditAgent ran but SKILL.md not written/empty: %s — "
                "candidates 保留 promoted=false 等下轮重试",
                self.skill_dir.name,
            )
            return False

        now = datetime.now().isoformat(timespec="seconds")
        for c in data.get("candidates", []):
            if not c.get("promoted"):
                c["promoted"] = True
                c["promoted_at"] = now
        C.save_candidates(self.skill_dir, data)
        return True

    def _run(self, ready: list[dict]) -> None:
        from xskill import skill_tools as ST
        lines = ["# 待整理 candidates (按 weightscore_total 倒序)"]
        for c in sorted(
            ready, key=lambda x: x.get("weightscore_total", 0), reverse=True,
        ):
            lines.append(
                f"- atom_id={c['atom_id']}  weightscore_total={c['weightscore_total']}  "
                f"contributions={len(c.get('contributions', []))}"
            )
        lines.append("")
        lines.append(f"目标 skill 目录: {self.skill_dir}")
        lines.append(f"目标 SKILL.md 路径: {self.skill_dir / 'SKILL.md'}")
        user_msg = "\n".join(lines)

        agent = self.agno_agent_factory(
            instructions=[SYSTEM_PROMPT],
            tools=[
                ST.atom_task_read,
                ST.read_traj,
                ST.skill_read,
                ST.write_file,
            ],
        )
        agent.run(user_msg)
