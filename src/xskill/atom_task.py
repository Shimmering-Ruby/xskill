"""AtomTask 数据模型 + 文件存储 + 向量索引
==========================================

每个 ``AtomTask`` 是一段 multi-chat-turn（1-10 轮），由 ``TaskAgent`` 从
轨迹按"用户意图切换"切出来。落盘到 ``<root>/<traj_id>/tasks/atom_*.json``，
不入 SQLite——保持简单文件方案，``watcher`` 用 ``last_offset`` 决定增量起点。

向量索引（基于 atom 的 ``summary or intent`` 嵌入）持久化在 ``<root>/index.pkl``。
``HybridSearch`` (在 ``xskill.hybrid_search``) 把本模块的 ``vector_search`` 跟
BM25 关键字检索做 union+dedup。
"""
from __future__ import annotations

import json
import pickle
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterator

import numpy as np


@dataclass
class AtomTask:
    """一段完整用户意图的最小提炼单元。

    字段约定：
    - ``offset_start`` / ``offset_end``: 在 ``<traj_id>.md`` 中的 **1-based 行号**
      （半开区间 ``[start, end)``——end 这一行不含；末 atom 的 end = 末行号+1），
      便于 ``ReadTraj`` 按行号读原文。轨迹入库后不变，行号稳定。
    - ``pre_atom_id`` / ``post_atom_id``: 前后 atom 链表，给 cluster/edit
      agent 沿时间线游走。
    - ``context_prefix``: atom 起始行之前内容的省略表示（头 200 字 + 占位）。
    - ``raw_segment``: ``[offset_start, offset_end)`` 行区间内的原文片段。
    """
    atom_id: str
    traj_id: str
    offset_start: int
    offset_end: int
    intent: str
    summary: str
    tags: list[str] = field(default_factory=list)
    used_skills: list[str] = field(default_factory=list)
    ux_score: int | None = None
    pre_atom_id: str | None = None
    post_atom_id: str | None = None
    context_prefix: str = ""
    raw_segment: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, s: str) -> "AtomTask":
        return cls(**json.loads(s))


class AtomTaskStore:
    """文件系统存储：``<root>/<traj_id>/tasks/atom_*.json`` + ``<root>/index.pkl``。

    设计取舍：
    - **不用 SQLite**: traj 数量级 < 数千；文件读写直接、调试方便、跨平台兼容。
    - **每 traj 一个子目录**: 让 watcher 按 traj 粒度做增量处理，``list_by_traj``
      不需要全表扫。
    - **向量索引整批重建**: TaskAgent 每给一条 traj 拆出新 atom 后由 watcher
      触发一次 ``rebuild_vector_index``；增量重建对正确性没好处只增加复杂度，
      暂不做（embed batch API 也支持千条以内的吞吐）。
    """

    INDEX_FILE = "index.pkl"

    def __init__(self, root: Path):
        self.root = Path(root)

    # ── paths ─────────────────────────────────────────────────────

    def _traj_dir(self, traj_id: str) -> Path:
        return self.root / traj_id / "tasks"

    def _path(self, atom: AtomTask) -> Path:
        return self._traj_dir(atom.traj_id) / f"{atom.atom_id}.json"

    def _index_path(self) -> Path:
        return self.root / self.INDEX_FILE

    # ── IO ────────────────────────────────────────────────────────

    def save(self, atom: AtomTask) -> Path:
        p = self._path(atom)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(atom.to_json(), encoding="utf-8")
        return p

    def load(self, atom_id: str) -> AtomTask:
        """跨 traj_id 子目录查找。命中第一条即返回；找不到抛 FileNotFoundError。

        ``watcher`` 的常态调用是 ``list_by_traj``（O(子目录文件数)），``load``
        给 agent 工具 ``atom_task_read(atom_id)`` 用，调用频率低，遍历所有
        traj 子目录可以接受。
        """
        if not self.root.is_dir():
            raise FileNotFoundError(f"atom store root missing: {self.root}")
        for d in self.root.iterdir():
            if not d.is_dir():
                continue
            cand = d / "tasks" / f"{atom_id}.json"
            if cand.is_file():
                return AtomTask.from_json(cand.read_text(encoding="utf-8"))
        raise FileNotFoundError(f"atom not found: {atom_id}")

    def list_by_traj(self, traj_id: str) -> list[AtomTask]:
        d = self._traj_dir(traj_id)
        if not d.is_dir():
            return []
        return [
            AtomTask.from_json(p.read_text(encoding="utf-8"))
            for p in sorted(d.glob("atom_*.json"))
        ]

    def all_atoms(self) -> Iterator[AtomTask]:
        if not self.root.is_dir():
            return
        for traj_dir in sorted(self.root.iterdir()):
            if not traj_dir.is_dir():
                continue
            tasks_dir = traj_dir / "tasks"
            if not tasks_dir.is_dir():
                continue
            for p in sorted(tasks_dir.glob("atom_*.json")):
                yield AtomTask.from_json(p.read_text(encoding="utf-8"))

    # ── offset pointer ────────────────────────────────────────────

    def last_offset(self, traj_id: str) -> int:
        atoms = self.list_by_traj(traj_id)
        return max((a.offset_end for a in atoms), default=0)

    def last_atom_id(self, traj_id: str) -> str | None:
        atoms = self.list_by_traj(traj_id)
        return atoms[-1].atom_id if atoms else None

    # ── vector index ──────────────────────────────────────────────

    def rebuild_vector_index(self, embed_client) -> None:
        """整批 encode 所有 atom 的 ``summary or intent`` → L2 归一 → 落 index.pkl。

        无 atom 时直接返回（不写空索引文件——``vector_search`` 自己处理无索引情况）。
        """
        atoms = list(self.all_atoms())
        if not atoms:
            return
        texts = [a.summary or a.intent for a in atoms]
        vecs = embed_client.encode_batch(texts)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1
        vecs = vecs / norms
        self.root.mkdir(parents=True, exist_ok=True)
        with open(self._index_path(), "wb") as f:
            pickle.dump({
                "atom_ids": [a.atom_id for a in atoms],
                "embeddings": vecs,
                "model": getattr(embed_client, "model", ""),
                "dim": int(vecs.shape[1]),
            }, f)

    def vector_search(self, query: str, embed_client, top_k: int = 5) -> list[dict]:
        p = self._index_path()
        if not p.is_file():
            return []
        with open(p, "rb") as f:
            data = pickle.load(f)
        q = embed_client.encode(query)
        qn = np.linalg.norm(q)
        if qn > 0:
            q = q / qn
        sims = data["embeddings"] @ q
        ranked = sorted(enumerate(sims), key=lambda x: x[1], reverse=True)[:top_k]
        return [
            {"atom_id": data["atom_ids"][i], "similarity": float(s)}
            for i, s in ranked
        ]
