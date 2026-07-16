"""test_collector_encoding.py — D10:collector 读轨迹的编码降级

回归背景：``pending()`` 里三处 ``md.read_text(encoding="utf-8")`` 与 sidecar 的
``json.loads(jp.read_text(encoding="utf-8"))`` 都没有编码守卫。Windows 上的工具
以 GBK(cp936) 写出的一条轨迹 → UnicodeDecodeError 直接从 ``pending()`` 抛出去，
打断整个 collect/upload 轮询：**一条坏文件就让这台机器再也不上传任何东西**。
（sidecar 那处的 ``except (OSError, json.JSONDecodeError)`` 拦不住——
UnicodeDecodeError 是 ValueError 的子类，不是 JSONDecodeError。）

按 CLAUDE.md 的 GBK 规则：降级解码（errors="replace"）+ 落日志，不拖垮轮询。
"""
from __future__ import annotations

import logging
import time

from xskill.team.client.collector import TeamCollector


def _one_hour_later() -> float:
    return time.time() + 3600.0


def _collector(tmp_path):
    """quiet/debounce 全放行，pending() 只考编码这一件事。

    时钟推到"文件写完 1 小时后"：两道闸都比对真实 mtime，用固定小值当 now 会
    让静默窗口恒不满足，一条都吐不出来。
    """
    return TeamCollector(
        cursor_path=tmp_path / "cursor.json",
        quiet_seconds=0,
        min_change_interval=0,
        home_root=tmp_path,
        time_fn=_one_hour_later,
    )


def _write_gbk_trajectory(tmp_path, name: str = "traj_1") -> None:
    bridge = tmp_path / ".xskill" / "cc_sessions"
    bridge.mkdir(parents=True)
    # GBK 编码的中文正文——严格 utf-8 解码必抛 UnicodeDecodeError
    (bridge / f"{name}.md").write_bytes("用户说：修一下登录".encode("gbk"))


def test_gbk_trajectory_does_not_break_the_poll_cycle(tmp_path, caplog):
    _write_gbk_trajectory(tmp_path)
    collector = _collector(tmp_path)
    with caplog.at_level(logging.WARNING):
        pending = collector.pending()          # 旧代码:UnicodeDecodeError 抛穿
    assert [p.traj_id for p in pending] == ["traj_1"]
    assert "�" in pending[0].content      # 坏字节被替换字符顶掉
    assert pending[0].sha256                   # 仍有稳定 hash 可去重/上传
    assert any("非 utf-8" in record.getMessage() for record in caplog.records), \
        "降级解码必须落日志"


def test_one_bad_trajectory_does_not_starve_the_good_ones(tmp_path):
    """坏编码只该影响它自己那一条,不该连坐同一轮里的正常轨迹。"""
    _write_gbk_trajectory(tmp_path, "traj_bad")
    bridge = tmp_path / ".xskill" / "cc_sessions"
    (bridge / "traj_ok.md").write_text("正常 utf-8 轨迹", encoding="utf-8")
    pending = _collector(tmp_path).pending()   # 旧代码:整轮抛错,好的也上传不了
    assert {p.traj_id for p in pending} == {"traj_bad", "traj_ok"}


def test_gbk_sidecar_does_not_break_the_poll_cycle(tmp_path):
    """sidecar 的 except 只拦 OSError/JSONDecodeError——UnicodeDecodeError 是
    ValueError 的子类,会绕过它炸掉 pending()。"""
    bridge = tmp_path / ".xskill" / "cc_sessions"
    bridge.mkdir(parents=True)
    (bridge / "traj_1.md").write_text("正常正文", encoding="utf-8")
    (bridge / "traj_1.json").write_bytes(
        '{"model": "文心一言"}'.encode("gbk"))
    pending = _collector(tmp_path).pending()   # 旧代码:UnicodeDecodeError 抛穿
    assert len(pending) == 1
    assert pending[0].model                    # 键名是 ASCII,值降级后仍读得到


def test_broken_sidecar_json_is_logged_not_silent(tmp_path, caplog):
    """解析失败 → model 记 unknown,但必须落日志(禁止捕获后静默返默认值)。"""
    bridge = tmp_path / ".xskill" / "cc_sessions"
    bridge.mkdir(parents=True)
    (bridge / "traj_1.md").write_text("正常正文", encoding="utf-8")
    (bridge / "traj_1.json").write_text("{not json", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        pending = _collector(tmp_path).pending()
    assert pending[0].model == ""
    assert any("sidecar" in record.message for record in caplog.records)
