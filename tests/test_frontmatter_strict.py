"""
test_frontmatter_strict.py -- SKILL.md frontmatter 写后校验钩子的度量传感器
============================================================================
本文件是 F1 度量子代理产出的"传感器"，只建度量、不实现校验逻辑。

背景
----
`xskill.skill.frontmatter.parse()` 遇到坏 YAML 会**静默返回 ({}, text)**，
`agent_tools.write_file.entrypoint()` 在 fm={} 时退化成原样写盘——两处都是"静默放行"：
LLM 写出残缺的 SKILL.md（最典型是多行 description 没用块标量，被 YAML 解析
成嵌套 mapping/非字符串）会被悄悄写进库，事后才在加载端暴雷。

为堵住这个口子，编程子代理将实现一个**严格校验入口** `parse_strict`：

    from xskill.skill.frontmatter import parse_strict, FrontmatterError
    parse_strict(text: str) -> tuple[dict, str]

合法 -> 返回 (fm, body)；非法 -> 抛 FrontmatterError（带行号/原因）。

校验规则（本文件覆盖）：
  1. frontmatter 必须是合法 YAML 且解析为 mapping；
  2. 必填 name、description；
  3. description 必须是**非空字符串**（专抓多行没块标量被解析成非字符串）；
  4. body 非空。

度量指标
--------
跑全部夹具，输出：
  - 漏拦数（leaks）：坏样本没被 FrontmatterError 拦下 = 静默放行复现；
  - 误伤数（false_rejects）：好样本被 FrontmatterError 拦下 = 过度严格。
两者目标都是 0。`metric_report()` 返回富误差（哪个文件、为什么），不只标量。

现在 parse_strict 是 NotImplementedError 桩，所以本文件**应当全红**——
这就是"度量先于实现"里的传感器起点。编程子代理把 parse_strict 实现到位后，
本文件应当全绿，且 leaks==0 and false_rejects==0。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from xskill.skill.frontmatter import FrontmatterError, parse_strict

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "skill_frontmatter"
BAD_DIR = FIXTURE_ROOT / "bad"
GOOD_DIR = FIXTURE_ROOT / "good"


def _md_files(d: Path) -> list[Path]:
    files = sorted(d.glob("*.md"))
    assert files, f"夹具目录为空，传感器不可用: {d}"
    return files


BAD_FILES = _md_files(BAD_DIR)
GOOD_FILES = _md_files(GOOD_DIR)


# ── 坏样本：每个都必须被 FrontmatterError 拦下（漏拦=失败） ──────────────
@pytest.mark.parametrize("path", BAD_FILES, ids=[p.name for p in BAD_FILES])
def test_bad_fixture_is_rejected(path: Path):
    text = path.read_text(encoding="utf-8")
    with pytest.raises(FrontmatterError):
        parse_strict(text)


# ── 好样本：每个都必须通过，且 description 是非空字符串（误伤=失败） ──────
@pytest.mark.parametrize("path", GOOD_FILES, ids=[p.name for p in GOOD_FILES])
def test_good_fixture_passes(path: Path):
    text = path.read_text(encoding="utf-8")
    fm, body = parse_strict(text)
    assert isinstance(fm, dict), f"{path.name}: 返回的 fm 不是 dict"
    assert "name" in fm and str(fm["name"]).strip(), f"{path.name}: name 缺失/空"
    assert isinstance(fm.get("description"), str), \
        f"{path.name}: description 不是字符串: {type(fm.get('description'))}"
    assert fm["description"].strip(), f"{path.name}: description 是空字符串"
    assert isinstance(body, str) and body.strip(), f"{path.name}: body 为空"


# ── 度量函数：跑全部夹具，输出富误差（漏拦数 + 误伤数 + 明细） ───────────
def metric_report() -> dict:
    """跑全部夹具，返回度量报告。

    Returns
    -------
    dict 形如::

        {
          "leaks": int,            # 坏样本没被拦下的数量（目标 0）
          "false_rejects": int,    # 好样本被误拦的数量（目标 0）
          "leak_detail": [(name, parsed_fm_or_reason), ...],
          "false_reject_detail": [(name, FrontmatterError 文本), ...],
          "bad_total": int,
          "good_total": int,
        }
    """
    leaks: list[tuple[str, str]] = []
    false_rejects: list[tuple[str, str]] = []

    for path in BAD_FILES:
        text = path.read_text(encoding="utf-8")
        try:
            fm, _ = parse_strict(text)
            # 没抛错 = 漏拦；记录它被解析成了什么，便于定位
            leaks.append((path.name, f"未拦下，解析为 fm={fm!r}"))
        except FrontmatterError:
            pass  # 正确拦下

    for path in GOOD_FILES:
        text = path.read_text(encoding="utf-8")
        try:
            parse_strict(text)
        except FrontmatterError as exc:
            false_rejects.append((path.name, str(exc)))

    return {
        "leaks": len(leaks),
        "false_rejects": len(false_rejects),
        "leak_detail": leaks,
        "false_reject_detail": false_rejects,
        "bad_total": len(BAD_FILES),
        "good_total": len(GOOD_FILES),
    }


def test_metric_zero_leaks_zero_false_rejects():
    """度量收敛目标：漏拦数==0 且 误伤数==0。

    parse_strict 未实现前，metric_report() 在 NotImplementedError 处会冒泡
    （本测试随之红）。实现到位后此断言才该绿。
    """
    report = metric_report()
    assert report["leaks"] == 0, (
        f"漏拦 {report['leaks']} 个坏样本（静默放行复现）: {report['leak_detail']}"
    )
    assert report["false_rejects"] == 0, (
        f"误伤 {report['false_rejects']} 个好样本: {report['false_reject_detail']}"
    )


# ── 反"假实现"闸：防止校验被写成恒抛 / 恒过来骗指标 ─────────────────────
def test_anti_fake_implementation_gate():
    """至少一个好样本通过 且 至少一个坏样本被拦——必须同时成立。

    - 若校验被写成"恒抛 FrontmatterError"：好样本无一通过 -> good_passed==0 -> 失败。
    - 若校验被写成"恒不抛/恒过"：坏样本无一被拦 -> bad_rejected==0 -> 失败。
    只有真正区分好坏的实现才能同时满足这两个下限。
    """
    good_passed = 0
    for path in GOOD_FILES:
        try:
            parse_strict(path.read_text(encoding="utf-8"))
            good_passed += 1
        except FrontmatterError:
            pass

    bad_rejected = 0
    for path in BAD_FILES:
        try:
            parse_strict(path.read_text(encoding="utf-8"))
        except FrontmatterError:
            bad_rejected += 1

    assert good_passed >= 1, "没有任何好样本通过——疑似校验被写成恒抛"
    assert bad_rejected >= 1, "没有任何坏样本被拦——疑似校验被写成恒过/恒不抛"
