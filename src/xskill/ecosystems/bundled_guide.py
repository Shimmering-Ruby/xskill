"""将随包附带的 /xskill-helper 使用指南安装至本机检测到的 Agent 技能目录。

在环境初始化或客户端接入时，自动识别已安装的 AI Agent 生态（如 Claude Code、Codex、Cursor 等），
并部署使用指南，方便在各 Agent 环境内直接唤起使用指导。
"""
from __future__ import annotations

import sys
from pathlib import Path

from xskill import ecosystems as _ecosystems

# 调用时再按生态 id 取 ecosystems 上的 install_to_*，方便测试打桩。
_INSTALLER_ATTR_BY_ECO = {
    "claude_code": "install_to_claude_code",
    "codex": "install_to_codex",
    "nga3": "install_to_nga3",
    "opencode": "install_to_opencode",
    "ngagent": "install_to_ngagent",
    "openclaw": "install_to_openclaw",
    "cursor": "install_to_cursor",
    "trae": "install_to_trae",
    "deepseek_harness": "install_to_deepseek_harness",
}


def _installer_for(eco: str):
    attr = _INSTALLER_ATTR_BY_ECO.get(eco)
    if attr is None:
        return None
    return getattr(_ecosystems, attr, None)


def bundled_xskill_source() -> Path:
    """获取随包发布的 xskill-helper 指南目录路径。"""
    from importlib.resources import files

    return Path(str(files("xskill") / "data" / "skill" / "xskill-helper"))


def install_bundled_xskill_guide(
    target_root: Path | str | None = None,
    ecosystems: list[str] | None = None,
) -> list[str]:
    """将 /xskill-helper 指南安装至目标目录下检测到的 Agent 环境中。

    若指定 ecosystems 则仅安装至对应生态。返回成功安装的生态列表。
    """
    root = Path(target_root).expanduser().resolve() if target_root else None
    skill_source = bundled_xskill_source()
    if not (skill_source / "SKILL.md").is_file():
        print(
            f"warning: 捆绑的 xskill skill 缺失（{skill_source}），跳过装 skill",
            file=sys.stderr,
        )
        return []

    wanted = set(ecosystems) if ecosystems else None
    installed_ecosystems: list[str] = []
    for detection in _ecosystems.detect_known_ecosystems(home_root=root):
        eco = detection["ecosystem"]
        if wanted is not None and eco not in wanted:
            continue
        install_fn = _installer_for(eco)
        if install_fn is None:
            continue
        try:
            install_fn(skill_source, target_root=root, side="main")
            installed_ecosystems.append(eco)
        except Exception as install_error:  # noqa: BLE001
            print(
                f"warning: 安装至 {eco} 失败：{install_error}",
                file=sys.stderr,
            )
    if installed_ecosystems:
        names = "/".join(installed_ecosystems)
        print(
            f"已把 xskill 使用指南装进 {names} 的 skill 目录，"
            f"在对应 agent 里可直接 /xskill-helper 查用法。"
        )
    else:
        print(
            "未检测到已知 agent 生态（claude_code/codex/opencode/cursor/… "
            "均未发现），跳过装 skill。"
        )
    return installed_ecosystems
