"""score_atom: atom 粒度打分 + used_skills 信号"""
from __future__ import annotations


from xskill.pipeline.atom import AtomTask
from xskill.pipeline.atom import score_atom


class _StubLLM:
    def __init__(self, response: str):
        self.response = response
        self.last_prompt = None
        self.last_system = None

    def chat(self, prompt, system=""):
        self.last_prompt = prompt
        self.last_system = system
        return self.response


def _atom(used_skills=None, intent="i", summary="s"):
    return AtomTask(
        atom_id="atom_x_0001", traj_id="x",
        offset_start=0, offset_end=10,
        intent=intent, summary=summary,
        tags=[], used_skills=used_skills or [], ux_score=None,
        pre_atom_id=None, post_atom_id=None,
        context_prefix="<sysprompt> headers", raw_segment="actual chat content",
    )


class TestScoreAtom:
    def test_valid_score_parsed(self):
        llm = _StubLLM('{"score": 9, "reasons": "一步到位无需澄清"}')
        out = score_atom(llm=llm, atom=_atom(), side="staging")
        assert out["score"] == 9
        assert "一步到位" in out["reasons"]

    def test_score_out_of_range_returns_none(self):
        llm = _StubLLM('{"score": 99, "reasons": "x"}')
        out = score_atom(llm=llm, atom=_atom(), side="main")
        assert out["score"] is None

    def test_score_below_one_returns_none(self):
        llm = _StubLLM('{"score": 0, "reasons": "x"}')
        out = score_atom(llm=llm, atom=_atom(), side="main")
        assert out["score"] is None

    def test_non_json_returns_none(self):
        llm = _StubLLM('this is not json at all')
        out = score_atom(llm=llm, atom=_atom(), side="main")
        assert out["score"] is None

    def test_embedded_json_extracted(self):
        """{...} 可以嵌在文本中（_parse_score 用 regex 抓首个 JSON 块）"""
        llm = _StubLLM('Some preamble {"score": 7, "reasons": "ok"} trailer text')
        out = score_atom(llm=llm, atom=_atom(), side="main")
        assert out["score"] == 7


class TestPromptShape:
    def test_prompt_includes_used_skills_and_side(self):
        llm = _StubLLM('{"score": 8, "reasons": "x"}')
        score_atom(llm=llm, atom=_atom(used_skills=["my-skill"]), side="staging")
        assert "used_skills" in llm.last_prompt
        assert "my-skill" in llm.last_prompt
        assert "staging" in llm.last_prompt

    def test_system_has_strict_rubric(self):
        llm = _StubLLM('{"score": 6, "reasons": "x"}')
        score_atom(llm=llm, atom=_atom(), side="main")
        sys_text = llm.last_system
        # 严格分档表关键词
        assert "10 一次到位" in sys_text
        assert "used_skills" in sys_text
        # JSON 输出要求
        assert "score" in sys_text and "reasons" in sys_text

    def test_prompt_includes_atom_content(self):
        llm = _StubLLM('{"score": 5, "reasons": "x"}')
        score_atom(llm=llm,
                   atom=_atom(intent="独特意图123", summary="独特摘要456"),
                   side="main")
        assert "独特意图123" in llm.last_prompt
        assert "独特摘要456" in llm.last_prompt
        # 应包含 context_prefix + raw_segment
        assert "headers" in llm.last_prompt or "actual chat" in llm.last_prompt
