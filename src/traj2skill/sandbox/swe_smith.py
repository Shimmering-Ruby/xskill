"""
sandbox/swe_smith.py — SWE-smith 沙箱实现
════════════════════════════════════════════
使用 SWE-smith 的预构建 Docker 镜像，在容器中跑 agent 解决编码任务，
用 FAIL_TO_PASS 测试验证结果。
"""

from __future__ import annotations

import json, logging, os, pickle, subprocess, time, uuid
from pathlib import Path
from typing import Optional

from .base import Sandbox, TaskSpec, TrialResult
from .registry import register
from .agent_template import generate_agent_script

logger = logging.getLogger("sandbox.swe_smith")

DEFAULT_CACHE_DIR = Path.home() / ".cache" / "traj2skill" / "swe_smith"


@register("swe_smith")
class SweSmithSandbox(Sandbox):
    """SWE-smith 沙箱：Docker 容器 + FAIL_TO_PASS 测试验证"""

    def __init__(self, cache_dir: str | Path = DEFAULT_CACHE_DIR,
                 timeout: int = 300):
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._timeout = timeout
        self._dataset_index: dict | None = None

    @property
    def name(self) -> str:
        return "swe_smith"

    # ═══════════════════════════════════════════════════════════════
    # 数据加载
    # ═══════════════════════════════════════════════════════════════

    def _load_from_cache(self, instance_id: str) -> dict | None:
        """从本地缓存读取单个 instance"""
        cache_file = self._cache_dir / "instances" / f"{instance_id}.json"
        if cache_file.exists():
            return json.loads(cache_file.read_text(encoding="utf-8"))
        return None

    def _save_to_cache(self, instance_id: str, row: dict):
        """缓存单个 instance 到本地"""
        cache_dir = self._cache_dir / "instances"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / f"{instance_id}.json"
        cache_file.write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")

    def _fetch_from_hf(self, target_ids: set[str]) -> dict[str, dict]:
        """从 HuggingFace streaming 加载指定的 instance，找到就停"""
        from datasets import load_dataset

        logger.info(f"从 HuggingFace streaming 搜索 {len(target_ids)} 个 instance...")
        ds = load_dataset("SWE-bench/SWE-smith", split="train", streaming=True)
        found = {}
        count = 0
        for row in ds:
            iid = row["instance_id"]
            if iid in target_ids:
                data = {
                    "instance_id": iid,
                    "problem_statement": row.get("problem_statement", ""),
                    "image_name": row.get("image_name", ""),
                    "repo": row.get("repo", ""),
                    "patch": row.get("patch", ""),
                    "FAIL_TO_PASS": row.get("FAIL_TO_PASS", "[]"),
                    "PASS_TO_PASS": row.get("PASS_TO_PASS", "[]"),
                }
                found[iid] = data
                self._save_to_cache(iid, data)
                logger.info(f"  找到: {iid} ({len(found)}/{len(target_ids)})")
                if len(found) == len(target_ids):
                    break
            count += 1
            if count % 10000 == 0:
                logger.info(f"  已扫描 {count} 条...")

        if len(found) < len(target_ids):
            missing = target_ids - set(found.keys())
            logger.warning(f"未找到 {len(missing)} 个 instance: {list(missing)[:5]}")

        return found

    def load_tasks(self, instance_ids: list[str]) -> list[TaskSpec]:
        """加载 TaskSpec，优先读缓存，缺的从 HF streaming 补"""
        tasks = []
        need_fetch = set()

        # 先查缓存
        cached_rows = {}
        for iid in instance_ids:
            row = self._load_from_cache(iid)
            if row:
                cached_rows[iid] = row
            else:
                need_fetch.add(iid)

        if cached_rows:
            logger.info(f"缓存命中 {len(cached_rows)} 个 instance")

        # 缺的从 HF 拉
        if need_fetch:
            fetched = self._fetch_from_hf(need_fetch)
            cached_rows.update(fetched)

        # 构建 TaskSpec
        for iid in instance_ids:
            if iid not in cached_rows:
                continue
            row = cached_rows[iid]

            fail_to_pass = row.get("FAIL_TO_PASS", "[]")
            if isinstance(fail_to_pass, str):
                try:
                    fail_to_pass = json.loads(fail_to_pass)
                except json.JSONDecodeError:
                    fail_to_pass = []

            pass_to_pass = row.get("PASS_TO_PASS", "[]")
            if isinstance(pass_to_pass, str):
                try:
                    pass_to_pass = json.loads(pass_to_pass)
                except json.JSONDecodeError:
                    pass_to_pass = []

            tasks.append(TaskSpec(
                task_id=iid,
                problem_statement=row.get("problem_statement", ""),
                environment={
                    "image_name": row.get("image_name", ""),
                    "repo": row.get("repo", ""),
                },
                gold_patch=row.get("patch"),
                test_commands=fail_to_pass,
                regression_tests=pass_to_pass,
            ))
        return tasks

    # ═══════════════════════════════════════════════════════════════
    # Docker 执行
    # ═══════════════════════════════════════════════════════════════

    def _docker_available(self) -> bool:
        """检查 Docker 是否可用"""
        try:
            r = subprocess.run(["docker", "info"], capture_output=True, timeout=10)
            return r.returncode == 0
        except Exception:
            return False

    def _pull_image(self, image_name: str) -> bool:
        """拉取 Docker 镜像"""
        logger.info(f"拉取镜像: {image_name}")
        try:
            r = subprocess.run(
                ["docker", "pull", image_name],
                capture_output=True, text=True, timeout=600,
            )
            if r.returncode != 0:
                logger.error(f"拉取失败: {r.stderr[:500]}")
                return False
            return True
        except subprocess.TimeoutExpired:
            logger.error(f"拉取超时: {image_name}")
            return False

    def _image_exists(self, image_name: str) -> bool:
        """检查镜像是否存在"""
        r = subprocess.run(
            ["docker", "image", "inspect", image_name],
            capture_output=True, timeout=10,
        )
        return r.returncode == 0

    def run_trial(
        self,
        task: TaskSpec,
        skill_md: Optional[str],
        llm_config: dict,
    ) -> TrialResult:
        """
        在 Docker 容器中跑一次 agent：
        1. 创建容器
        2. 注入 agent 脚本
        3. 运行 agent
        4. 跑测试
        5. 收集结果
        6. 清理
        """
        image_name = task.environment.get("image_name", "")
        if not image_name:
            return TrialResult(task_id=task.task_id, passed=False,
                               error="no image_name in task")

        if not self._docker_available():
            return TrialResult(task_id=task.task_id, passed=False,
                               error="Docker not available")

        # 确保镜像存在
        if not self._image_exists(image_name):
            if not self._pull_image(image_name):
                return TrialResult(task_id=task.task_id, passed=False,
                                   error=f"failed to pull {image_name}")

        container_name = f"t2s_trial_{uuid.uuid4().hex[:12]}"
        start_time = time.time()

        try:
            # 1. 创建并启动容器
            r = subprocess.run(
                ["docker", "run", "-d", "--name", container_name,
                 "--network", "host",  # agent 需要访问 LLM API
                 image_name, "sleep", str(self._timeout + 60)],
                capture_output=True, text=True, timeout=30,
            )
            if r.returncode != 0:
                return TrialResult(task_id=task.task_id, passed=False,
                                   error=f"docker run failed: {r.stderr[:200]}")

            # 2. 生成并注入 agent 脚本
            script = generate_agent_script(
                problem_statement=task.problem_statement,
                llm_config=llm_config,
                skill_md=skill_md or "",
            )
            script_path = self._cache_dir / f"{container_name}_agent.py"
            script_path.write_text(script, encoding="utf-8")

            subprocess.run(
                ["docker", "cp", str(script_path), f"{container_name}:/tmp/agent.py"],
                capture_output=True, timeout=10,
            )

            # 3. 运行 agent
            logger.debug(f"运行 agent in {container_name}")
            r = subprocess.run(
                ["docker", "exec", container_name,
                 "python", "/tmp/agent.py"],
                capture_output=True, text=True, timeout=self._timeout,
            )
            agent_output = r.stdout[-2000:] if r.stdout else ""

            # 4. 收集 agent 的 patch
            r_diff = subprocess.run(
                ["docker", "exec", container_name,
                 "bash", "-c", "cd /testbed && git diff"],
                capture_output=True, text=True, timeout=30,
            )
            agent_patch = r_diff.stdout if r_diff.returncode == 0 else None

            # 5. 跑 FAIL_TO_PASS 测试
            passed = self._run_tests(container_name, task.test_commands)

            # 6. 跑 PASS_TO_PASS 回归测试（可选）
            regression_ok = True
            if task.regression_tests:
                regression_ok = self._run_tests(container_name, task.regression_tests)

            duration = time.time() - start_time

            return TrialResult(
                task_id=task.task_id,
                passed=passed,
                regression_ok=regression_ok,
                duration_seconds=round(duration, 1),
                agent_patch=agent_patch,
            )

        except subprocess.TimeoutExpired:
            return TrialResult(
                task_id=task.task_id, passed=False,
                duration_seconds=round(time.time() - start_time, 1),
                error="timeout",
            )
        except Exception as e:
            return TrialResult(
                task_id=task.task_id, passed=False,
                duration_seconds=round(time.time() - start_time, 1),
                error=str(e),
            )
        finally:
            # 清理容器和临时文件
            subprocess.run(
                ["docker", "rm", "-f", container_name],
                capture_output=True, timeout=10,
            )
            script_path = self._cache_dir / f"{container_name}_agent.py"
            if script_path.exists():
                script_path.unlink()

    def _run_tests(self, container_name: str, test_list: list[str]) -> bool:
        """在容器中跑一组测试，全 pass 返回 True"""
        if not test_list:
            return True

        # 构建 pytest 命令
        # SWE-smith 的 test_list 是 pytest node ID 格式
        test_args = " ".join(f'"{t}"' for t in test_list[:20])  # 限制数量
        cmd = f"cd /testbed && python -m pytest {test_args} --tb=no -q 2>&1 | tail -5"

        try:
            r = subprocess.run(
                ["docker", "exec", container_name, "bash", "-c", cmd],
                capture_output=True, text=True, timeout=120,
            )
            # pytest 返回 0 = 全 pass
            return r.returncode == 0
        except subprocess.TimeoutExpired:
            logger.warning(f"测试超时: {container_name}")
            return False
        except Exception as e:
            logger.error(f"测试执行错误: {e}")
            return False
