from __future__ import annotations

from pathlib import Path

from xskill.agents import agent_tools
from xskill.agents.generate_agent import (
    ONHOLD_PROMPT_LINE,
    READ_TARGET_DEFAULT,
    SYSTEM_PROMPT,
    _read_target_line,
)
from xskill.agents.traj_tools import read_traj, traj_cards, traj_search

_CC_MD = """# Claude Code Session Trajectory

**session_id**: aaa11111

## Initial Query

为什么本机仓库热路径卡住了

## User

为什么本机仓库热路径卡住了

## Assistant

我先看进程占用。

## Tool Call: Bash
```json
{"command": "ps aux"}
```

## Tool Output: Bash
```
admin 1234 99.0 python hotloop.py
```

## Assistant

_(reasoning)_ 用户其实在问热路径，我应该先定位循环。

卡在 hotloop.py 的重试循环，改成退避即可。

## User

那提交怎么做

## Assistant

跑完单测再 commit。
"""

_OC_MD = """# Trajectory

## Raw Content

# OpenCode session ses_bbb2

- cwd: `/home/admin`

## User

修一下提交失败

## Assistant

先看 git 状态，再决定是补 commit 还是改 hook。这条会话足够长，
不会被短文件下限过滤掉，方便断言目录里能同时看到两个来源的轨迹。

## Tool Call: bash
```json
{"command": "git status"}
```

## Tool Output: bash
```
On branch main
nothing to commit
```

## Assistant

工作区干净，问题在 pre-commit hook 上。
"""


def _ctx(tmp_path: Path, *, wiki: bool = True):
    skill = tmp_path / "skill"
    skill.mkdir()
    live = tmp_path / "sessions"
    live.mkdir()
    held = tmp_path / "held"
    held.mkdir()
    (live / "traj_cc_admin_aaa11111.md").write_text(_CC_MD, encoding="utf-8")
    (live / "traj_oc_work_bbb22222.md").write_text(_OC_MD, encoding="utf-8")
    (held / "traj_cc_held_ccc33333.md").write_text(
        "## User\n\nsecret\n" + "x" * 400, encoding="utf-8",
    )
    wiki_root = None
    if wiki:
        wiki_root = tmp_path / "wiki"
        (wiki_root / "pages").mkdir(parents=True)
    return agent_tools.create_agent_tool_context(
        skill_dir=skill,
        atom_skill_dir=skill,
        default_traj_root=live,
        extra_read_roots=(live, held),
        blocked_read_roots=(held,),
        wiki_root=wiki_root,
        generate_user_id="tester",
    )


def test_search_lists_and_skips_onhold(tmp_path: Path):
    with agent_tools.use_agent_tool_context(_ctx(tmp_path)):
        listing = traj_search.entrypoint()
        hits = traj_search.entrypoint(query="热路径")
        miss = traj_search.entrypoint(query="不存在的词zzz")
    assert "traj_cc_admin_aaa11111" in listing
    assert "traj_oc_work_bbb22222" in listing
    assert "traj_cc_held_ccc33333" not in listing
    assert "首问: 为什么本机仓库热路径卡住了" in listing
    assert "traj_cc_admin_aaa11111" in hits
    assert "traj_oc_work_bbb22222" not in hits
    assert "没有命中" in miss


def test_search_hit_counts_and_context(tmp_path: Path):
    with agent_tools.use_agent_tool_context(_ctx(tmp_path)):
        hits = traj_search.entrypoint(query="热路径")
        ctx_hits = traj_search.entrypoint(query="重试循环", context=2)
    # 热路径在 aaa11111 里出现 3 次（Initial Query、User、Assistant reasoning）
    assert "共命中 3 处" in hits
    # context 模式带出命中行前后原文，命中行标星
    assert "traj_cc_admin_aaa11111" in ctx_hits
    assert "L31*" in ctx_hits
    assert "我应该先定位循环" in ctx_hits


def test_card_keeps_questions_and_drops_tool_output(tmp_path: Path):
    with agent_tools.use_agent_tool_context(_ctx(tmp_path)):
        batch = traj_cards.entrypoint(
            traj_ids="traj_cc_admin_aaa11111 traj_oc_work_bbb22222",
        )
        held = traj_cards.entrypoint(traj_ids="traj_cc_held_ccc33333")
        toomany = traj_cards.entrypoint(traj_ids=",".join(f"id{i}" for i in range(9)))
    assert "cards=2" in batch
    assert "来源: claude-code" in batch
    assert "Bash×1" in batch
    # 用户所有问题带行号
    assert "问: 为什么本机仓库热路径卡住了" in batch
    assert "问: 那提交怎么做" in batch
    # 工具返回结果不进卡片
    assert "admin 1234 99.0" not in batch
    assert "ps aux" not in batch
    # 思维链丢弃，只留正文
    assert "用户其实在问热路径" not in batch
    # 收尾不和已列过的答句重复
    assert batch.count("跑完单测再 commit。") == 1
    # opencode 那条的收尾是最后一段没被当答句列过的回答
    assert "收尾: 工作区干净" in batch
    assert "read_traj(" in batch
    assert "error:" in held
    assert "最多 8 条" in toomany


class _FakeEmbed:
    model = "fake-embed"

    def encode(self, text: str):
        # 简单可分辨的二维向量：含"热路径"偏向轴 0，其余偏向轴 1
        return [1.0, 0.0] if "热路径" in text else [0.0, 1.0]

    def encode_batch(self, texts):
        return [self.encode(t) for t in texts]


def _seed_atom(root: Path, traj_id: str, *, start: int, end: int, summary: str):
    import json

    tasks = root / traj_id / "tasks"
    tasks.mkdir(parents=True, exist_ok=True)
    atom_id = f"atom_{traj_id}_0001"
    (tasks / f"{atom_id}.json").write_text(json.dumps({
        "atom_id": atom_id,
        "traj_id": traj_id,
        "offset_start": start,
        "offset_end": end,
        "intent": summary[:20],
        "summary": summary,
        "tags": ["debug"],
    }, ensure_ascii=False), encoding="utf-8")


def test_atom_search_semantic_and_offset_fallback(tmp_path: Path):
    from xskill.agents.traj_tools import _ATOM_STORES, atom_search

    _ATOM_STORES.clear()
    ctx = _ctx(tmp_path)
    live = tmp_path / "sessions"
    # aaa11111 的原子行号合法；bbb22222 的是旧字符偏移（超过行数）
    _seed_atom(live, "traj_cc_admin_aaa11111", start=1, end=10,
               summary="排查热路径卡死，定位到重试循环改退避")
    _seed_atom(live, "traj_oc_work_bbb22222", start=0, end=14000,
               summary="修复提交失败，pre-commit hook 问题")
    client = _FakeEmbed()
    from xskill.pipeline.atom import AtomTaskStore

    AtomTaskStore(live).rebuild_vector_index(client, force_full=True)
    ctx = agent_tools.create_agent_tool_context(
        skill_dir=tmp_path / "skill",
        atom_skill_dir=tmp_path / "skill",
        default_traj_root=live,
        extra_read_roots=(live,),
        wiki_root=tmp_path / "wiki",
        generate_user_id="tester",
        embed_client=client,
    )
    with agent_tools.use_agent_tool_context(ctx):
        hits = atom_search.entrypoint(query="热路径卡住怎么排查", top_k=4)
        empty = atom_search.entrypoint(query="")
    assert "traj_cc_admin_aaa11111 L1-10" in hits
    assert "行号不可靠" in hits
    assert "排查热路径卡死" in hits
    assert "不算精读" in hits
    assert empty.startswith("error:")


def test_atom_search_without_embed_client(tmp_path: Path):
    with agent_tools.use_agent_tool_context(_ctx(tmp_path)):
        from xskill.agents.traj_tools import atom_search

        out = atom_search.entrypoint(query="任何问题")
    assert out.startswith("error:")
    assert "traj_search" in out


def test_read_traj_counts_and_clamps(tmp_path: Path):
    ctx = _ctx(tmp_path)
    with agent_tools.use_agent_tool_context(ctx):
        agent_tools.reset_generate_session()
        first = read_traj.entrypoint(traj_id="traj_cc_admin_aaa11111", offset=1, limit=5)
        gate_before = agent_tools._generate_traj_read_gate()
        second = read_traj.entrypoint(traj_id="traj_oc_work_bbb22222.md", offset=9999)
        missing = read_traj.entrypoint(traj_id="traj_cc_none")
        blocked = read_traj.entrypoint(traj_id="traj_cc_held_ccc33333")
        ids = agent_tools.generate_read_traj_ids()
    assert "1| # Claude Code Session Trajectory" in first
    assert "已精读不同轨迹 1 条" in first
    assert "已精读不同轨迹 2 条" in second
    assert not second.startswith("error:")
    assert missing.startswith("error:")
    assert blocked.startswith("error:")
    assert ids == ["traj_cc_admin_aaa11111", "traj_oc_work_bbb22222"]
    assert gate_before is not None and "read_traj" in gate_before


def test_wiki_nudge_only_on_new_traj(tmp_path: Path):
    """每满 5 条新轨迹催一次写 wiki；续读同一条不重复催。"""
    skill = tmp_path / "skill"
    skill.mkdir()
    live = tmp_path / "sessions"
    live.mkdir()
    for index in range(5):
        (live / f"traj_cc_x_{index:08x}.md").write_text(
            "## User\n\n问题\n" * 40, encoding="utf-8",
        )
    wiki = tmp_path / "wiki"
    (wiki / "pages").mkdir(parents=True)
    ctx = agent_tools.create_agent_tool_context(
        skill_dir=skill,
        atom_skill_dir=skill,
        default_traj_root=live,
        extra_read_roots=(live,),
        wiki_root=wiki,
        generate_user_id="tester",
    )
    with agent_tools.use_agent_tool_context(ctx):
        agent_tools.reset_generate_session()
        outs = [
            read_traj.entrypoint(traj_id=f"traj_cc_x_{index:08x}", offset=1, limit=3)
            for index in range(5)
        ]
        again = read_traj.entrypoint(traj_id="traj_cc_x_00000000", offset=10, limit=3)
    assert "wiki_edit" not in "".join(outs[:4])
    assert "wiki_edit" in outs[4]
    assert "已精读不同轨迹 5 条" in again
    assert "wiki_edit" not in again


def test_old_entrances_reroute_in_generate_job(tmp_path: Path):
    ctx = _ctx(tmp_path)
    traj = tmp_path / "sessions" / "traj_cc_admin_aaa11111.md"
    with agent_tools.use_agent_tool_context(ctx):
        by_read_file = agent_tools.read_file.entrypoint(path=str(traj))
        by_list = agent_tools.list_files.entrypoint(path=str(tmp_path / "sessions"))
        by_grep = agent_tools.grep_files.entrypoint(
            pattern="热路径", path=str(tmp_path / "sessions"),
        )
    assert "read_traj" in by_read_file and by_read_file.startswith("error:")
    assert "traj_search" in by_list and by_list.startswith("error:")
    assert "traj_search" in by_grep and by_grep.startswith("error:")


def test_read_file_untouched_outside_generate(tmp_path: Path):
    ctx = _ctx(tmp_path, wiki=False)
    ctx = agent_tools.create_agent_tool_context(
        skill_dir=ctx.skill_dir,
        atom_skill_dir=ctx.atom_skill_dir,
        default_traj_root=ctx.default_traj_root,
        extra_read_roots=ctx.extra_read_roots,
        blocked_read_roots=ctx.blocked_read_roots,
    )
    traj = tmp_path / "sessions" / "traj_cc_admin_aaa11111.md"
    with agent_tools.use_agent_tool_context(ctx):
        body = agent_tools.read_file.entrypoint(path=str(traj))
    assert "Claude Code Session Trajectory" in body


def test_prompt_has_flow_and_no_tool_catalog():
    lines = SYSTEM_PROMPT.splitlines()
    assert ONHOLD_PROMPT_LINE in lines
    for name in ("traj_search", "traj_cards", "read_traj", "wiki_write"):
        assert name in SYSTEM_PROMPT
    # 工具清单由 Agno 从 docstring 注入，提示词不再自带「可用工具」一节
    assert "# 可用工具" not in SYSTEM_PROMPT
    assert "list_sessions" not in SYSTEM_PROMPT
    assert (
        SYSTEM_PROMPT.index("优先阅读范围")
        < SYSTEM_PROMPT.index(ONHOLD_PROMPT_LINE)
        < SYSTEM_PROMPT.index("# 你可以读的目录")
    )


def test_read_target_follows_user_then_default():
    assert "50" in _read_target_line("读至少 50 条轨迹再写")
    assert "12" in _read_target_line("参考 12 个会话，归纳脚本化做法")
    fallback = _read_target_line("帮我写个 skill")
    assert str(READ_TARGET_DEFAULT) in fallback
    assert "用户没点条数" in fallback
