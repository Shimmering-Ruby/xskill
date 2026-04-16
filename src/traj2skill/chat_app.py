"""
chat_app.py -- 最小 Chat LLM 应用
==================================

展示并验证灰度发布的端到端链路。**不是生产对话系统**，目的是：

  1. 接收用户 query → 检索 / 加载指定 skill（带灰度分流）
  2. 按分流决定本条轨迹用 main 还是 staging 版本的 SKILL.md body
  3. 用真实 LLM 作为 agent 产出回复
  4. 将整段对话落盘为 trajectory markdown
  5. 异步投递 ux_score 打分任务；打分完成自动触发灰度 controller 判定

测试时把 ``async_score=False`` 关掉异步，让 ChatApp.handle() 串行等待打分，
便于断言"跑 N 轮对话后 staging 已合入 main"。
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from traj2skill import canary, ux_score
from traj2skill.llm_client import LLMClient

logger = logging.getLogger("chat_app")


def default_agent(llm: LLMClient, system: str, user: str) -> str:
    """最简单的 agent：单轮对话，system=skill body，user=query。"""
    return llm.chat(user, system=system)


class ChatApp:
    def __init__(
        self,
        *,
        skill_dir: Path,
        traj_dir: Path,
        llm: LLMClient,
        skill_slug: str,
        canary_config: canary.CanaryConfig | None = None,
        async_score: bool = True,
        agent_fn: Callable[[LLMClient, str, str], str] | None = None,
    ):
        self.skill_dir = Path(skill_dir)
        self.traj_dir = Path(traj_dir)
        self.llm = llm
        self.skill_slug = skill_slug
        self.canary_cfg = canary_config or canary.CanaryConfig()
        self.async_score = async_score
        self.agent_fn = agent_fn or default_agent

        self._counter = 0
        self._counter_lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=4) if async_score else None
        self._futures: list[Future] = []

    # ---------------------------------------------------------------
    def _next_traj_id(self) -> str:
        with self._counter_lock:
            self._counter += 1
            return f"traj_{self._counter:04d}"

    def _skill_path(self) -> Path:
        return self.skill_dir / self.skill_slug

    def _render_markdown(self, dialog: list[tuple[str, str]]) -> str:
        lines = ["# Chat trajectory", ""]
        for role, text in dialog:
            lines.append(f"## {role}")
            lines.append("")
            lines.append(text.rstrip())
            lines.append("")
        return "\n".join(lines)

    # ---------------------------------------------------------------
    def handle(
        self,
        user_query: str,
        followups: list[str] | None = None,
    ) -> dict:
        """处理一条用户请求，返回 {traj_id, side, reply, ux_score_future/result}。"""
        traj_id = self._next_traj_id()
        skill_path = self._skill_path()

        resolved = canary.resolve_skill_for_traj(
            skill_path,
            traj_id=traj_id,
            skill_name=self.skill_slug,
            probability=self.canary_cfg.probability,
        )
        side = resolved["side"]
        sha = resolved["commit_sha"]
        body = resolved["body"] or ""

        system_prompt = self._build_system_prompt(body)

        dialog: list[tuple[str, str]] = [("user", user_query)]
        reply = self.agent_fn(self.llm, system_prompt, user_query)
        dialog.append(("assistant", reply))

        for follow in followups or []:
            # 多轮：把历史拼接进 user 消息（最小协议）
            composite = "\n\n".join(
                f"[{role}] {text}" for role, text in dialog
            ) + f"\n\n[user] {follow}"
            more = self.agent_fn(self.llm, system_prompt, composite)
            dialog.append(("user", follow))
            dialog.append(("assistant", more))

        traj_md = self._render_markdown(dialog)

        out_dir = self.traj_dir / "chat"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{traj_id}.md").write_text(traj_md, encoding="utf-8")

        result: dict = {
            "traj_id": traj_id,
            "side": side,
            "commit_sha": (sha or "")[:8],
            "reply": reply,
        }

        if self.async_score:
            fut = self._executor.submit(
                self._score_job,
                traj_id=traj_id,
                traj_md=traj_md,
                side=side,
                commit_sha=sha or "",
            )
            self._futures.append(fut)
            result["ux_future"] = fut
        else:
            result["ux_score"] = self._score_job(
                traj_id=traj_id,
                traj_md=traj_md,
                side=side,
                commit_sha=sha or "",
            )
        return result

    # ---------------------------------------------------------------
    def _build_system_prompt(self, skill_body: str) -> str:
        if not skill_body:
            return "你是一个编程助手。严格按用户指令作答。"
        return (
            "你是一个编程助手。回答用户问题时，必须严格遵守以下 SKILL 指令，"
            "不得偏离：\n\n"
            "------ SKILL 开始 ------\n"
            f"{skill_body}\n"
            "------ SKILL 结束 ------\n"
        )

    # ---------------------------------------------------------------
    def _score_job(
        self,
        *,
        traj_id: str,
        traj_md: str,
        side: str,
        commit_sha: str,
    ) -> dict:
        try:
            return ux_score.score_and_record(
                llm=self.llm,
                skill_dir=self._skill_path(),
                skill_name=self.skill_slug,
                traj_id=traj_id,
                traj_md=traj_md,
                side=side,
                commit_sha=commit_sha,
                canary_config=self.canary_cfg,
            )
        except Exception as e:
            logger.exception(f"ux_score job failed: {e}")
            return {"scored": False, "error": str(e), "decision": {"action": "error"}}

    # ---------------------------------------------------------------
    def wait_for_scoring(self, timeout: float | None = None) -> list[dict]:
        """阻塞直到所有已投递的打分任务完成。返回每个任务的结果。"""
        if not self.async_score:
            return []
        start = time.time()
        results = []
        for f in list(self._futures):
            remaining = None if timeout is None else max(0, timeout - (time.time() - start))
            results.append(f.result(timeout=remaining))
        return results

    def close(self):
        if self._executor:
            self._executor.shutdown(wait=True)
