"""
skill_manager.py -- Skill 版本管理
====================================
基于 git 的 skill 版本管理：list / show / log / diff / rollback / freeze / delete / export / import
"""

import json, shutil, logging
from pathlib import Path

from traj2skill.git_lock import run_git, commit_changes

logger = logging.getLogger("skill_manager")


def list_skills(skill_dir: Path) -> list[dict]:
    """列出所有 skill 及其状态"""
    results = []
    if not skill_dir.exists():
        return results

    for d in sorted(skill_dir.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue

        entry = {
            "name": d.name,
            "version": 0,
            "eval_score": None,
            "tags": [],
            "frozen": False,
        }

        abstract_path = d / ".abstract"
        if abstract_path.exists():
            try:
                abstract = json.loads(abstract_path.read_text(encoding="utf-8"))
                entry["version"] = abstract.get("version", 0)
                entry["eval_score"] = abstract.get("eval_result", {}).get("eval_score")
                entry["tags"] = abstract.get("tags", [])
                entry["frozen"] = abstract.get("frozen", False)
            except (json.JSONDecodeError, Exception):
                pass

        results.append(entry)

    return results


def show_skill(skill_dir: Path, name: str) -> dict:
    """显示 skill 详情: skill.md + abstract + eval"""
    skill_path = skill_dir / name
    if not skill_path.is_dir():
        return {"error": f"skill not found: {name}"}

    result = {"name": name}

    # skill.md
    skill_md_path = skill_path / "skill.md"
    if skill_md_path.exists():
        result["skill_md"] = skill_md_path.read_text(encoding="utf-8")
    else:
        result["skill_md"] = "(skill.md not found)"

    # .abstract
    abstract_path = skill_path / ".abstract"
    if abstract_path.exists():
        try:
            result["abstract"] = json.loads(abstract_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, Exception):
            result["abstract"] = {}
    else:
        result["abstract"] = {}

    # eval info from abstract
    result["eval"] = result["abstract"].get("eval_result", {})

    # files
    result["files"] = [
        str(f.relative_to(skill_path))
        for f in sorted(skill_path.rglob("*"))
        if f.is_file()
    ]

    return result


def skill_log(skill_dir: Path, name: str) -> str:
    """返回 skill 的 git log"""
    skill_path = skill_dir / name
    if not skill_path.is_dir():
        return f"skill not found: {name}"

    code, out, err = run_git(
        ["log", "--oneline", "--follow", "-20", "--", f"{name}/"],
        cwd=str(skill_dir),
    )
    if code != 0:
        return f"git log failed: {err}"
    return out or "(no history)"


def skill_diff(skill_dir: Path, name: str, v1: str = None, v2: str = None) -> str:
    """返回 skill 的 diff。默认: 当前 vs 上一版本"""
    skill_path = skill_dir / name
    if not skill_path.is_dir():
        return f"skill not found: {name}"

    if v1 and v2:
        # diff between two specific commits
        code, out, err = run_git(
            ["diff", v1, v2, "--", f"{name}/"],
            cwd=str(skill_dir),
        )
    else:
        # diff HEAD vs HEAD~1
        code, out, err = run_git(
            ["diff", "HEAD~1", "HEAD", "--", f"{name}/"],
            cwd=str(skill_dir),
        )

    if code != 0:
        return f"git diff failed: {err}"
    return out or "(no diff)"


def rollback_skill(skill_dir: Path, name: str, version: str = None) -> bool:
    """回滚 skill 到指定版本（commit hash）或上一版本"""
    skill_path = skill_dir / name
    if not skill_path.is_dir():
        logger.error(f"skill not found: {name}")
        return False

    if version:
        # checkout specific commit
        code, _, err = run_git(
            ["checkout", version, "--", f"{name}/"],
            cwd=str(skill_dir),
        )
    else:
        # rollback to previous version (HEAD~1)
        code, _, err = run_git(
            ["checkout", "HEAD~1", "--", f"{name}/"],
            cwd=str(skill_dir),
        )

    if code != 0:
        logger.error(f"rollback failed: {err}")
        return False

    # commit the rollback
    target = version or "HEAD~1"
    committed = commit_changes(str(skill_dir), f"rollback {name} to {target}")
    return committed


def freeze_skill(skill_dir: Path, name: str) -> bool:
    """冻结 skill，batch 不自动更新"""
    return _set_frozen(skill_dir, name, True)


def unfreeze_skill(skill_dir: Path, name: str) -> bool:
    """解冻 skill"""
    return _set_frozen(skill_dir, name, False)


def _set_frozen(skill_dir: Path, name: str, frozen: bool) -> bool:
    """设置 skill 的 frozen 状态"""
    abstract_path = skill_dir / name / ".abstract"
    if not abstract_path.exists():
        # create minimal abstract
        abstract = {"frozen": frozen}
    else:
        try:
            abstract = json.loads(abstract_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, Exception):
            abstract = {}
        abstract["frozen"] = frozen

    abstract_path.write_text(json.dumps(abstract, ensure_ascii=False, indent=2), encoding="utf-8")
    action = "freeze" if frozen else "unfreeze"
    commit_changes(str(skill_dir), f"{action} {name}")
    logger.info(f"{action}: {name}")
    return True


def delete_skill(skill_dir: Path, name: str) -> bool:
    """删除 skill 目录并提交"""
    skill_path = skill_dir / name
    if not skill_path.is_dir():
        logger.error(f"skill not found: {name}")
        return False

    shutil.rmtree(skill_path)
    committed = commit_changes(str(skill_dir), f"delete skill: {name}")
    if committed:
        logger.info(f"deleted: {name}")
    return committed


def export_skill(skill_dir: Path, name: str, output_path: Path) -> Path:
    """导出 skill 目录到指定路径"""
    skill_path = skill_dir / name
    if not skill_path.is_dir():
        raise FileNotFoundError(f"skill not found: {name}")

    target = output_path / name if output_path.is_dir() else output_path
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(skill_path, target)
    logger.info(f"exported: {name} -> {target}")
    return target


def import_skill(skill_dir: Path, source_path: Path) -> str:
    """导入 skill 目录"""
    source = Path(source_path)
    if not source.is_dir():
        raise FileNotFoundError(f"source not found: {source}")

    name = source.name
    target = skill_dir / name
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)

    committed = commit_changes(str(skill_dir), f"import skill: {name}")
    logger.info(f"imported: {name}")
    return name
