"""
t2s.py — T2S 顶层门面
═══════════════════════════════════════════════════════
唯一对外入口。持 config + registry + skill_repo，
提供 search / serve / score_trajectory_ux 三个动作方法。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from traj2skill.config import load_config, get_skill_dir
from traj2skill.entities.registry import Registry
from traj2skill.entities.skill import Skill
from traj2skill.entities.skill_repo import SkillRepo
from traj2skill.entities.trajectory import Trajectory
from traj2skill.types import SkillHit, TrajectoryHit, UxScoreResult


class T2S:
    """t2s 顶层门面。

    用法：
        from traj2skill import T2S
        t2s = T2S()                       # 默认 ~/.t2s/config.yaml
        t2s = T2S(config_path=Path(...))  # 显式

        # 检索
        hits = t2s.search_skills("django form")
        hits = t2s.search_trajectories("query")

        # daemon
        t2s.serve(host="0.0.0.0", port=8000)

        # 主动 UX 打分（维护性，watcher 会自动跑）
        t2s.score_trajectory_ux(traj)

        # 子系统访问
        t2s.registry.list()
        t2s.skill_repo["fix-foo"]
    """

    def __init__(self, config_path: Optional[Path] = None):
        self.config = load_config(config_path)
        self.registry = Registry()
        self.skill_repo = SkillRepo(get_skill_dir(), registry=self.registry)
        self._llm = None
        self._embed = None

    # ─── lazy LLM / embed clients ──────────────────────────────
    @property
    def llm(self):
        if self._llm is None:
            from traj2skill.llm_client import create_llm_client
            self._llm = create_llm_client(self.config)
        return self._llm

    @property
    def embed(self):
        if self._embed is None:
            from traj2skill.llm_client import create_embed_client
            self._embed = create_embed_client(self.config)
        return self._embed

    # ─── 检索（跨所有 registry）─────────────────────────────────
    def search_trajectories(self, query: str, top_k: int = 5,
                            min_similarity: float = 0.0) -> list[TrajectoryHit]:
        """跨所有注册目录搜索轨迹。"""
        from traj2skill.search import search_all
        results = search_all(
            query, top_k=top_k,
            min_similarity=min_similarity,
            success_filter="all",
            config=self.config,
        )
        out: list[TrajectoryHit] = []
        for r in results:
            md = r.get("md_path") or r.get("traj_path")
            if not md:
                continue
            try:
                traj = Trajectory.load(md, registry=self.registry)
            except FileNotFoundError:
                continue
            out.append(TrajectoryHit(trajectory=traj,
                                     similarity=float(r.get("similarity", 0.0))))
        return out

    def search_skills(self, query: str, top_k: int = 5) -> list[SkillHit]:
        """跨 skill_repo 搜索 skill。"""
        import json
        from traj2skill import skill_tools
        # data_dir 在 skill 搜索路径上不读，传 skill_repo.root 占位
        skill_tools.init_context(
            self.skill_repo.root, self.skill_repo.root,
            self.llm, self.embed, self.config,
        )
        raw = skill_tools.search_skills(query, top_k=top_k) or "[]"
        try:
            items = json.loads(raw)
        except json.JSONDecodeError:
            return []
        out: list[SkillHit] = []
        for item in items:
            name = item.get("skill_name")
            if not name:
                continue
            skill = self.skill_repo.get(name)
            if skill is None:
                continue
            out.append(SkillHit(
                skill=skill,
                similarity=float(item.get("similarity", 0.0)),
            ))
        return out[:top_k]

    # ─── UX 打分（主动）─────────────────────────────────────────
    def score_trajectory_ux(self, traj: Trajectory) -> UxScoreResult:
        """主动给一条轨迹补 UX 分（幂等：已存在返回 scored=False）。

        watcher 会自动调；本方法用于 watcher 漏打 / 手动重打。"""
        from traj2skill.ux_score import score_and_record
        from traj2skill.canary import CanaryConfig
        from traj2skill.traj_meta import parse_traj_header

        md = traj.md_text
        header = parse_traj_header(md)
        if not header or not header.get("skill") or not header.get("side"):
            return UxScoreResult(
                scored=False, score=None,
                reasons="trajectory missing t2s header (skill/side)",
                decision={"action": "no_header"},
            )
        skill_name = header["skill"]
        skill = self.skill_repo.get(skill_name)
        if skill is None:
            return UxScoreResult(
                scored=False, score=None,
                reasons=f"skill not found in repo: {skill_name}",
                decision={"action": "skill_missing"},
            )
        canary_cfg = CanaryConfig.from_dict(self.config.get("canary", {}))
        d = score_and_record(
            llm=self.llm,
            skill_dir=skill.path,
            skill_name=skill_name,
            traj_id=traj.path.stem,
            traj_md=md,
            side=header["side"],
            commit_sha=header.get("sha", ""),
            canary_config=canary_cfg,
        )
        return UxScoreResult(
            scored=bool(d.get("scored")),
            score=d.get("score"),
            reasons=d.get("reasons", ""),
            decision=d.get("decision", {}),
        )

    # ─── daemon ────────────────────────────────────────────────
    def serve(self, host: str = "0.0.0.0", port: int = 8000) -> None:
        """启动 FastAPI server（含 watcher 后台线程）。阻塞。"""
        import uvicorn
        from traj2skill.server import create_app
        app = create_app()
        print(f"t2s serve at http://{host}:{port}/")
        uvicorn.run(app, host=host, port=port)

    def __repr__(self) -> str:
        return (f"T2S(skill_repo_root={self.skill_repo.root}, "
                f"registry_dirs={len(self.registry.list())}, "
                f"skills={len(self.skill_repo)})")
