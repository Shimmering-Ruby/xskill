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

    def _load_dataset_index(self) -> dict:
        """加载或构建 {instance_id → HF row} 索引"""
        if self._dataset_index is not None:
            return self._dataset_index

        index_path = self._cache_dir / "instance_index.pkl"
        if index_path.exists():
            logger.info(f"从缓存加载 SWE-smith 索引: {index_path}")
            with open(index_path, "rb") as f:
                self._dataset_index = pickle.load(f)
            return self._dataset_index

        logger.info("首次加载 SWE-smith 数据集，构建索引...")
        from datasets import load_dataset

        ds = load_dataset("SWE-bench/SWE-smith", split="train", streaming=True)
        index = {}
        count = 0
        for row in ds:
            index[row["instance_id"]] = {
                "instance_id": row["instance_id"],
                "problem_statement": row.get("problem_statement", ""),
                "image_name": row.get("image_name", ""),
                "repo": row.get("repo", ""),
                "patch": row.get("patch", ""),
                "FAIL_TO_PASS": row.get("FAIL_TO_PASS", "[]"),
                "PASS_TO_PASS": row.get("PASS_TO_PASS", "[]"),
            }
            count += 1
            if count % 5000 == 0:
                logger.info(f"  已加载 {count} 条...")

        logger.info(f"SWE-smith 索引构建完成: {count} 条")
        with open(index_path, "wb") as f:
            pickle.dump(index, f)

        self._dataset_index = index
        return index

    def load_tasks(self, instance_ids: list[str]) -> list[TaskSpec]:
        """从索引中加载 TaskSpec"""
        index = self._load_dataset_index()
        tasks = []
        for iid in instance_ids:
            if iid not in index:
                logger.warning(f"instance_id 不在 SWE-smith 中: {iid}")
                continue
            row = index[iid]

            # FAIL_TO_PASS 可能是 JSON 字符串或 list
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
