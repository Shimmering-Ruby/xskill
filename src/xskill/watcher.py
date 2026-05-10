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

_ACTION_STATUS = {
    "merged": "in_skill",
    "staged": "staged",
    "updated_metadata": "in_skill",
    "rejected": "rejected",
    "skip": "indexed",
    "dry_run": "indexed",
    "error": "error",
}


class DirectoryWatcher:
    """流水线式目录监听器。每条轨迹独立流转，不分批不阻塞。"""

    def __init__(self, *, llm=None, embed_client=None, config=None,
                 skill_dir=None, poll_interval=30.0, max_concurrent=5,
                 max_retries=3, db_path=None):
        self.llm = llm
        self.embed_client = embed_client
        self.config = config or {}
        self.skill_dir = Path(skill_dir) if skill_dir else None
        self.poll_interval = poll_interval
        self.max_concurrent = max_concurrent
        self.max_retries = max_retries
        self.db_path = db_path

        self._stop = threading.Event()
        self._pause = threading.Event()
        self._thread: threading.Thread | None = None
        self._pool = ThreadPoolExecutor(max_workers=max_concurrent)
        self._futures: dict[Future, dict] = {}  # future → {wd_id, fname, stage}
        self._last_poll: float | None = None
        self._stats = {
            "polls": 0, "new_trajs": 0, "meta_extracted": 0,
            "indexed": 0, "skills_generated": 0, "scores": 0,
            "errors": 0, "retries": 0,
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
                if stage == "meta":
                    self._on_meta_done(wd_id, fname, result, **kw)
                elif stage == "embed":
                    self._on_embed_done(wd_id, fname, result, **kw)
                elif stage == "process":
                    self._on_process_done(wd_id, fname, result, **kw)
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

        # 清理僵尸 processing
        for fname in get_trajs_by_status(wd_id, "processing", **kw):
            # 检查是否有 in-flight future
            if not any(i["fname"] == fname and i["wd_id"] == wd_id for i in self._futures.values()):
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

        # ── 提交 meta 任务（discovered → meta_extracting）──
        for fname in get_trajs_by_status(wd_id, "discovered", limit=self.max_concurrent * 2, **kw):
            if self._too_many_in_flight():
                break
            update_traj_status(wd_id, fname, "meta_extracting", **kw)
            fut = self._pool.submit(self._do_meta, dir_path, fname)
            self._futures[fut] = {"wd_id": wd_id, "fname": fname, "stage": "meta"}

        # ── 提交 embedding 任务（meta_done → embedding）──
        if self.embed_client:
            meta_done_files = get_trajs_by_status(wd_id, "meta_done", **kw)
            if meta_done_files:
                # embedding 是批量的，提交一个任务处理整个目录
                if not any(i["stage"] == "embed" and i["wd_id"] == wd_id for i in self._futures.values()):
                    fut = self._pool.submit(self._do_embed, dir_path, wd_id, meta_done_files)
                    self._futures[fut] = {"wd_id": wd_id, "fname": "_batch_embed", "stage": "embed"}

        # ── 提交 process_traj 任务（indexed → processing）──
        if self.skill_dir:
            for fname in get_trajs_by_status(wd_id, "indexed", limit=self.max_concurrent, **kw):
                if self._too_many_in_flight():
                    break
                update_traj_status(wd_id, fname, "processing", **kw)
                fut = self._pool.submit(self._do_process, dir_path, fname)
                self._futures[fut] = {"wd_id": wd_id, "fname": fname, "stage": "process"}

        # ── ux_score（对有 xskill header 的新轨迹）──
        if self.llm and self.skill_dir and new:
            self._score_new(wd_id, dir_path, new, **kw)

    def _too_many_in_flight(self):
        return len(self._futures) >= self.max_concurrent * 3

    # ───────────────────────────────────────────────────────────
    # 任务执行函数（在线程池中运行）
    # ───────────────────────────────────────────────────────────

    def _do_meta(self, dir_path, fname):
        """提取单条轨迹的 meta。返回 (fname, valid)。"""
        from xskill.index import _process_one_meta, validate_meta
        md_path = dir_path / fname
        if not md_path.is_file():
            return (fname, False, "file not found")
        _process_one_meta(md_path, self.llm)
        meta_path = dir_path / f"{fname}.meta"
        if meta_path.is_file():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if validate_meta(meta):
                return (fname, True, None)
            return (fname, False, "validation failed")
        return (fname, False, "no .meta produced")

    def _do_embed(self, dir_path, wd_id, filenames):
        """批量 embedding。返回 (wd_id, filenames)。"""
        from xskill.index import build_vector_index_incremental
        build_vector_index_incremental(dir_path, self.embed_client)
        return (wd_id, filenames)

    def _do_process(self, dir_path, fname):
        """执行 process_traj。返回 (fname, result_dict)。"""
        from xskill.process import process_traj
        result = process_traj(
            traj_md_path=str(dir_path / fname),
            config=self.config,
            skill_dir=self.skill_dir,
        )
        return (fname, result)

    # ───────────────────────────────────────────────────────────
    # 收割回调
    # ───────────────────────────────────────────────────────────

    def _on_meta_done(self, wd_id, fname, result, **kw):
        _fname, valid, err = result
        if valid:
            update_traj_status(wd_id, fname, "meta_done", **kw)
            mark_meta_done(wd_id, fname, **kw)
            self._stats["meta_extracted"] += 1
        else:
            update_traj_status(wd_id, fname, "filtered", error_msg=err, **kw)

    def _on_embed_done(self, wd_id, fname, result, **kw):
        _wd_id, filenames = result
        for f in filenames:
            meta_path = Path(list_watch_dirs(**kw)[0]["path"]) if not kw else None
            # Just mark as indexed — the actual check is that index.pkl was updated
            update_traj_status(wd_id, f, "indexed", **kw)
            mark_indexed(wd_id, f, **kw)
            self._stats["indexed"] += 1

    def _on_process_done(self, wd_id, fname, result, **kw):
        _fname, res = result
        action = res.get("action", "error")
        new_status = _ACTION_STATUS.get(action, "error")
        skills = res.get("skills", [])
        update_traj_status(
            wd_id, fname, new_status,
            process_action=action,
            skill_generated=",".join(skills) if skills else None,
            error_msg=res.get("error"),
            **kw,
        )
        self._stats["skills_generated"] += 1
        logger.info("%s → %s", fname, action)

    # ───────────────────────────────────────────────────────────
    # ux_score
    # ───────────────────────────────────────────────────────────

    def _score_new(self, wd_id, dir_path, filenames, **kw):
        from xskill.ux_score import score_and_record
        canary_cfg = CanaryConfig.from_dict(self.config.get("canary", {}))
        for fname in filenames:
            md_path = dir_path / fname
            if not md_path.is_file():
                continue
            md_text = md_path.read_text(encoding="utf-8")
            header = parse_traj_header(md_text)
            if not header or not header.get("skill") or not header.get("side"):
                continue
            skill_name = header["skill"]
            skill_sub = self.skill_dir / skill_name
            if not skill_sub.is_dir():
                continue
            try:
                score_and_record(
                    llm=self.llm, skill_dir=skill_sub, skill_name=skill_name,
                    traj_id=fname.replace(".md", ""), traj_md=md_text,
                    side=header["side"], commit_sha=header.get("sha", ""),
                    canary_config=canary_cfg,
                )
                mark_skill_used(wd_id, fname, skill_name, header["side"], **kw)
                self._stats["scores"] += 1
            except Exception:
                logger.exception("ux_score failed: %s", fname)
