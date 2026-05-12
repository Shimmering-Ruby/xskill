"""
watcher.py -- 流水线式目录监听器
==================================

每条轨迹独立流转，不分批不阻塞：

  discovered → meta_extracting → meta_done → indexed → processing → done

每次扫描：
  1. 发现新文件
  2. 对每条 discovered 提交 meta 提取任务（不等待）
  3. 对每条 meta_done 提交 embedding 任务（不等待）
  4. 对每条 indexed 提交 process_traj 任务（不等待）
  5. 收割已完成的 futures，更新状态
  6. 解析 xskill header → ux_score

所有耗时操作都在 ThreadPoolExecutor 中异步执行，扫描本身秒完。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, Future
from pathlib import Path

from xskill.canary import CanaryConfig
from xskill.registry import (
    list_watch_dirs,
    discover_trajectories,
    get_trajs_by_status,
    mark_meta_done,
    mark_indexed,
    mark_skill_used,
    update_traj_status,
    increment_retry,
)
from xskill.traj_meta import parse_traj_header

logger = logging.getLogger("xskill.watcher")

# v2 (AtomTask 流水线) 的 action → status 映射
# splitting → split_done → indexed → clustering → done
_ACTION_STATUS = {
    "clustered": "done",
    "skip": "indexed",
    "error": "error",
}


class DirectoryWatcher:
    """流水线式目录监听器。每条 traj 独立流转，不分批不阻塞。

    v2 状态机：
      discovered → splitting → split_done → indexed → clustering → done

    与 v1 (meta-level) 的差异：
    - splitting 阶段调 TaskAgent 拆 AtomTask，落盘到 ``<traj_root>/<traj_id>/tasks/``
    - indexed 阶段以 AtomTask 为单位整批重建 ``<traj_root>/index.pkl``
    - clustering 阶段对该 traj 所有新拆出的 atom 逐个调 process_atom_task
    """

    def __init__(self, *, llm=None, embed_client=None, config=None,
                 skill_dir=None, poll_interval=30.0, max_concurrent=30,
                 max_retries=3, db_path=None, cold_start_threshold=3,
                 store=None, agno_agent_factory=None):
        self.llm = llm
        self.embed_client = embed_client
        self.config = config or {}
        self.skill_dir = Path(skill_dir) if skill_dir else None
        self.poll_interval = poll_interval
        self.max_concurrent = max_concurrent
        self.max_retries = max_retries
        self.db_path = db_path
        # Cold-start 门控：当某 wd 还有 ≥ N 条 traj 处于"未 indexed"状态时，
        # 本轮 scan 不提交任何 clustering。设计动机：cluster agent 调
        # AtomTaskSearch 找相关 atom 共识，如果向量索引还没建完就跑，看到的
        # 是不完整快照，归类决策会失真。等所有先到的 traj 完成 split + index
        # 落进 <root>/index.pkl 再开 cluster。
        # filtered / error 不计入 pending（防止单条卡死阻断全场）。
        self.cold_start_threshold = cold_start_threshold

        # v2 注入：AtomTaskStore + agno agent 工厂
        # store None 时本 watcher 不能跑 splitting/clustering（仅 ux_score 还能跑）
        self.store = store
        self.agno_agent_factory = agno_agent_factory

        self._stop = threading.Event()
        self._pause = threading.Event()
        self._thread: threading.Thread | None = None
        self._pool = ThreadPoolExecutor(max_workers=max_concurrent)
        self._futures: dict[Future, dict] = {}
        self._last_poll: float | None = None
        self._stats = {
            "polls": 0, "new_trajs": 0,
            "atoms_extracted": 0,    # v2: 累计 atom 数（替代 meta_extracted）
            "indexed": 0,            # 仍记录索引重建次数
            "atoms_clustered": 0,    # v2: 累计 cluster 调用次数
            "skills_edited": 0,      # v2: 触发的 SkillEdit 次数
            "scores": 0, "errors": 0, "retries": 0,
            "cold_start_deferrals": 0,
        }

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="xskill-watcher")
        self._thread.start()
        logger.info("watcher started (interval=%.1fs, concurrent=%d)", self.poll_interval, self.max_concurrent)

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.poll_interval + 5)
        self._pool.shutdown(wait=False)
        logger.info("watcher stopped")

    def pause(self):
        self._pause.set()
        logger.info("watcher paused")

    def resume(self):
        self._pause.clear()
        logger.info("watcher resumed")

    @property
    def is_paused(self):
        return self._pause.is_set()

    @property
    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    @property
    def stats(self):
        return {
            **self._stats,
            "last_poll": self._last_poll,
            "running": self.is_running,
            "paused": self.is_paused,
            "in_flight": len(self._futures),
        }

    def _db_kw(self):
        return {"db_path": self.db_path} if self.db_path else {}

    # ───────────────────────────────────────────────────────────
    # Main loop
    # ───────────────────────────────────────────────────────────

    def _loop(self):
        while not self._stop.is_set():
            if not self._pause.is_set():
                try:
                    self._scan_once()
                except Exception:
                    logger.exception("watcher scan error")
            self._stop.wait(self.poll_interval)

    def _scan_once(self):
        """一次扫描：收割 → 发现 → 提交任务。秒完，不阻塞。"""
        self._last_poll = time.time()
        self._stats["polls"] += 1
        kw = self._db_kw()

        # ── Step 0: 收割已完成的 futures ──
        self._harvest()

        # ── Step 1-4: 对每个目录扫描 + 提交任务 ──
        for wd in list_watch_dirs(**kw):
            if self._stop.is_set():
                break
            if not wd.get("auto_index"):
                continue
            self._scan_dir(wd, **kw)

    # ───────────────────────────────────────────────────────────
    # 收割：检查所有 in-flight futures
    # ───────────────────────────────────────────────────────────

    def _harvest(self):
        """检查已完成的 futures，更新状态。"""
        done = [f for f in self._futures if f.done()]
        for fut in done:
            info = self._futures.pop(fut)
            wd_id, fname, stage = info["wd_id"], info["fname"], info["stage"]
            kw = self._db_kw()
            try:
                result = fut.result(timeout=0)
                if stage == "split":
                    self._on_split_done(wd_id, fname, result, **kw)
                elif stage == "embed":
                    self._on_embed_done(wd_id, fname, result, **kw)
                elif stage == "cluster":
                    self._on_cluster_done(wd_id, fname, result, **kw)
            except Exception as e:
                update_traj_status(wd_id, fname, "error", error_msg=str(e)[:200], **kw)
                self._stats["errors"] += 1
                logger.warning("future failed: %s/%s stage=%s: %s", wd_id, fname, stage, e)

    # ───────────────────────────────────────────────────────────
    # 扫描单个目录：发现 + 提交任务
    # ───────────────────────────────────────────────────────────

    def _scan_dir(self, wd, **kw):
        wd_id = wd["id"]
        dir_path = Path(wd["path"])
        if not dir_path.is_dir():
            return

        # 清理僵尸 in-flight 状态（同 v1 思路；stage 名换成 v2）：
        #   splitting   — _do_split 在跑（stage='split'）
        #   clustering  — _do_cluster 在跑（stage='cluster'）
        # 一旦 DB 里有这两个状态但没对应 in-flight future = 上次 daemon 退出
        # 时 future 被切 / 进程崩。回退到前一阶段让 watcher 下轮重新调度。
        for fname in get_trajs_by_status(wd_id, "splitting", **kw):
            if not any(
                i["fname"] == fname and i["wd_id"] == wd_id and i["stage"] == "split"
                for i in self._futures.values()
            ):
                update_traj_status(wd_id, fname, "discovered", **kw)

        for fname in get_trajs_by_status(wd_id, "clustering", **kw):
            if not any(
                i["fname"] == fname and i["wd_id"] == wd_id and i["stage"] == "cluster"
                for i in self._futures.values()
            ):
                update_traj_status(wd_id, fname, "indexed", **kw)

        # 重试 error
        for fname in get_trajs_by_status(wd_id, "error", max_retries=self.max_retries, **kw):
            update_traj_status(wd_id, fname, "discovered", **kw)
            increment_retry(wd_id, fname, **kw)
            self._stats["retries"] += 1

        # 发现新文件
        new = discover_trajectories(wd_id, dir_path, **kw)
        if new:
            self._stats["new_trajs"] += len(new)
            logger.info("[%s] discovered %d new", dir_path.name, len(new))

        # ── 提交 split 任务（discovered → splitting）──
        # 需要 llm；缺则 traj 留在 discovered 等条件齐备
        if self.llm is not None:
            for fname in get_trajs_by_status(
                wd_id, "discovered", limit=self.max_concurrent * 2, **kw,
            ):
                if self._too_many_in_flight():
                    break
                update_traj_status(wd_id, fname, "splitting", **kw)
                fut = self._pool.submit(self._do_split, dir_path, fname)
                self._futures[fut] = {"wd_id": wd_id, "fname": fname, "stage": "split"}

        # ── 提交 embed 任务（split_done → indexed，整批一个任务） ──
        if self.embed_client is not None:
            split_done_files = get_trajs_by_status(wd_id, "split_done", **kw)
            if split_done_files and not any(
                i["stage"] == "embed" and i["wd_id"] == wd_id for i in self._futures.values()
            ):
                fut = self._pool.submit(self._do_atom_index, dir_path, wd_id,
                                         split_done_files)
                self._futures[fut] = {"wd_id": wd_id, "fname": "_batch_embed", "stage": "embed"}

        # ── Cold-start 门控 + cluster（indexed → clustering）──
        if self.skill_dir:
            pending_index = (
                len(get_trajs_by_status(wd_id, "discovered", **kw))
                + len(get_trajs_by_status(wd_id, "splitting", **kw))
                + len(get_trajs_by_status(wd_id, "split_done", **kw))
            )
            if pending_index >= self.cold_start_threshold:
                self._stats["cold_start_deferrals"] += 1
                logger.info(
                    "[%s] cold-start gate: %d trajs not yet indexed (>= %d threshold), "
                    "defer cluster",
                    dir_path.name, pending_index, self.cold_start_threshold,
                )
            else:
                for fname in get_trajs_by_status(
                    wd_id, "indexed", limit=self.max_concurrent, **kw,
                ):
                    if self._too_many_in_flight():
                        break
                    update_traj_status(wd_id, fname, "clustering", **kw)
                    fut = self._pool.submit(self._do_cluster, dir_path, fname)
                    self._futures[fut] = {"wd_id": wd_id, "fname": fname, "stage": "cluster"}

        # ── ux_score（对有 xskill header 的新轨迹）──
        if self.llm and self.skill_dir and new:
            self._score_new(wd_id, dir_path, new, **kw)

    def _too_many_in_flight(self):
        return len(self._futures) >= self.max_concurrent * 3

    # ───────────────────────────────────────────────────────────
    # Helpers: store / agno factory 按需获取
    # ───────────────────────────────────────────────────────────

    def _store_for(self, dir_path):
        """返回该 dir 对应的 AtomTaskStore。

        测试时显式 inject self.store；生产 watcher 监控多个 dir（registry
        里每个 wd 一份），每个 dir 一个独立 store——按 dir_path 缓存创建。
        """
        from xskill.atom_task import AtomTaskStore
        if self.store is not None and Path(self.store.root) == Path(dir_path):
            return self.store
        if not hasattr(self, "_store_cache"):
            self._store_cache = {}
        key = str(Path(dir_path).resolve())
        if key not in self._store_cache:
            self._store_cache[key] = AtomTaskStore(root=Path(dir_path))
        return self._store_cache[key]

    def _factory(self):
        """返回 agno agent 工厂；优先 inject 的，否则用默认 deepseek 工厂。"""
        if self.agno_agent_factory is not None:
            return self.agno_agent_factory
        from xskill.agno_factory import make_default_factory
        if not hasattr(self, "_default_factory_cache"):
            self._default_factory_cache = make_default_factory(self.config)
        return self._default_factory_cache

    # ───────────────────────────────────────────────────────────
    # 任务执行函数（在线程池中运行）
    # ───────────────────────────────────────────────────────────

    # v2 流水线任务：split / atom_index / cluster

    def _do_split(self, dir_path, fname):
        """跑 TaskAgent 拆 AtomTask。返回 (fname, num_atoms_added, last_offset, last_atom_id, err)。"""
        from xskill.task_agent import TaskAgent
        md_path = dir_path / fname
        if not md_path.is_file():
            return (fname, 0, 0, None, "file not found")
        traj_id = md_path.stem
        store = self._store_for(dir_path)
        atoms = TaskAgent(llm=self.llm, store=store).run(
            traj_id=traj_id, traj_path=md_path,
        )
        last_off = store.last_offset(traj_id)
        last_id = store.last_atom_id(traj_id)
        return (fname, len(atoms), last_off, last_id, None)

    def _do_atom_index(self, dir_path, wd_id, filenames):
        """整批重建 AtomTask 向量索引。返回 (wd_id, filenames)。"""
        store = self._store_for(dir_path)
        store.rebuild_vector_index(self.embed_client)
        return (wd_id, filenames)

    def _do_cluster(self, dir_path, fname):
        """对该 traj 已拆出的每个 atom 调 process_atom_task。

        返回 (fname, [result_dict, ...])。每个 result 含 edited_skills 列表。
        """
        from xskill.process import process_atom_task
        traj_id = (dir_path / fname).stem
        store = self._store_for(dir_path)
        factory = self._factory()
        atoms = store.list_by_traj(traj_id)
        results = []
        for atom in atoms:
            res = process_atom_task(
                atom_id=atom.atom_id,
                config=self.config,
                skill_dir=self.skill_dir,
                store=store,
                embed_client=self.embed_client,
                agno_agent_factory=factory,
            )
            results.append(res)
        return (fname, results)

    # ───────────────────────────────────────────────────────────
    # 收割回调
    # ───────────────────────────────────────────────────────────

    def _on_split_done(self, wd_id, fname, result, **kw):
        from xskill.registry import update_traj_offset
        _fname, n_atoms, last_off, last_id, err = result
        if err is not None:
            update_traj_status(wd_id, fname, "filtered", error_msg=err, **kw)
            return
        update_traj_status(wd_id, fname, "split_done", **kw)
        update_traj_offset(
            wd_id, fname,
            last_offset=last_off, last_atom_id=last_id,
            tasks_extracted=n_atoms, **kw,
        )
        self._stats["atoms_extracted"] += n_atoms

    def _on_embed_done(self, wd_id, fname, result, **kw):
        _wd_id, filenames = result
        for f in filenames:
            update_traj_status(wd_id, f, "indexed", **kw)
            mark_indexed(wd_id, f, **kw)
            self._stats["indexed"] += 1

    def _on_cluster_done(self, wd_id, fname, result, **kw):
        _fname, results = result
        # 收集所有 cluster + edit 信号
        edited_skills: list[str] = []
        for r in results:
            edited_skills.extend(r.get("edited_skills") or [])
        action = "clustered"  # 总有动作；individual error 由 cluster agent 自己消化
        new_status = _ACTION_STATUS.get(action, "error")
        update_traj_status(
            wd_id, fname, new_status,
            process_action=action,
            skill_generated=",".join(edited_skills) if edited_skills else None,
            **kw,
        )
        self._stats["atoms_clustered"] += len(results)
        self._stats["skills_edited"] += len(edited_skills)
        logger.info("%s → clustered (%d atoms, edited skills=%s)",
                    fname, len(results), edited_skills or "-")
        # cluster 完成后该 traj 的所有 atom 都已落盘——这是 ux_score 应当
        # 跑的时机（旧 _score_new 在 traj 发现时跑会看到空 atom 列表）。
        self._score_atoms_for_traj(wd_id, fname, **kw)

    # ───────────────────────────────────────────────────────────
    # ux_score
    # ───────────────────────────────────────────────────────────

    def _score_new(self, wd_id, dir_path, filenames, **kw):
        """v2: 不在发现新 traj 时打分（那时 atom 还没拆）。

        实际打分在 ``_on_cluster_done`` → ``_score_atoms_for_traj`` 触发。
        此方法保留 hook 兼容 ``_scan_dir`` 末尾的调用；只在 traj 没有
        ``xskill:`` header 时早返回，避免无谓 IO。
        """
        return  # noop: 打分时机改到 cluster 完成后

    def _score_atoms_for_traj(self, wd_id, fname, **kw):
        """对一条已跑完 cluster 的 traj 扫所有 atom 打 ux_score。

        前置：
        - traj.md 顶部含 ``<!-- xskill:skill=X side=Y sha=Z -->`` header
        - 该 traj 已拆出 atom

        每个 atom 独立调 ``score_atom`` + ``AtomCanary.append``。同一 atom
        在同 (skill, side) 上幂等：``AtomCanary.append`` 自带去重。
        所有 atom 处理完调一次 ``check_and_decide`` 让 staging 该升的升 /
        该弃的弃。
        """
        if self.llm is None or self.skill_dir is None:
            return
        from xskill.ux_score import score_atom
        from xskill.atom_canary import AtomCanary
        # 找到该 wd 的 dir_path
        for wd in list_watch_dirs(**kw):
            if wd["id"] == wd_id:
                dir_path = Path(wd["path"])
                break
        else:
            return
        md_path = dir_path / fname
        if not md_path.is_file():
            return
        md_text = md_path.read_text(encoding="utf-8")
        header = parse_traj_header(md_text)
        if not header or not header.get("skill") or not header.get("side"):
            return
        skill_name = header["skill"]
        skill_sub = self.skill_dir / skill_name
        if not skill_sub.is_dir():
            return
        traj_id = md_path.stem
        store = self._store_for(dir_path)
        atoms = store.list_by_traj(traj_id)
        if not atoms:
            return
        ac = AtomCanary(skill_dir=skill_sub)
        canary_cfg = CanaryConfig.from_dict(self.config.get("canary", {}))
        for atom in atoms:
            try:
                result = score_atom(
                    llm=self.llm, atom=atom, side=header["side"],
                )
                if result["score"] is None:
                    continue
                ac.append(
                    atom_id=atom.atom_id, skill_name=skill_name,
                    side=header["side"], commit_sha=header.get("sha", ""),
                    score=result["score"], reasons=result["reasons"],
                )
                self._stats["scores"] += 1
            except Exception:
                logger.exception("score_atom failed: %s/%s",
                                 fname, atom.atom_id)
        # 翻牌判定
        try:
            ac.check_and_decide(config=canary_cfg)
        except Exception:
            logger.exception("check_and_decide failed: %s", skill_name)
        mark_skill_used(wd_id, fname, skill_name, header["side"], **kw)
