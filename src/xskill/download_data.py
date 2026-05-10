#!/usr/bin/env python3
"""
download_data.py — 下载公开 agent 轨迹数据集，转换为统一格式
══════════════════════════════════════════════════════════════

数据集:
  python download_data.py --mode swe_smith       # SWE-smith-trajectories (SWE-bench 代码修复)
  python download_data.py --mode dataclaw        # Claude-Opus-Dataclaw (Claude Code 真实会话)
  python download_data.py --mode sample          # 合成样本 (离线)
  python download_data.py --mode all             # 全部

国内镜像加速:
  python download_data.py --mode all --mirror    # 自动使用 hf-mirror.com

输出: ./data/{dataset_name}/trajectories.json  (统一格式)

依赖: pip install datasets huggingface-hub
"""

import argparse, json, uuid, random, re, os, sys
from pathlib import Path
from datetime import datetime, timedelta

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"


# ═══════════════════════════════════════════════════════════════════
# 统一轨迹格式 v2 — 保留完整时序
# ═══════════════════════════════════════════════════════════════════
#
# {
#   "traj_id":    str,
#   "query":      str,              # 首条用户请求
#   "category":   str,
#   "success":    bool | null,      # null = 数据集未标注
#   "failure_reason": str | null,
#
#   ┌─ 核心: 完整时序事件流 ──────────────────────────────────
#   "timeline": [                   # 按时间顺序的所有事件
#     {"t": 0, "role": "user",           "content": "..."},
#     {"t": 1, "role": "assistant",      "content": "thinking...", "has_tool_call": true},
#     {"t": 2, "role": "tool_call",      "tool": "bash", "input": {"command": "ls"}},
#     {"t": 3, "role": "tool_output",    "tool": "bash", "output": "file1\nfile2"},
#     {"t": 4, "role": "assistant",      "content": "Here are the files..."},
#     {"t": 5, "role": "user",           "content": "next instruction..."},
#     ...
#   ],
#   └──────────────────────────────────────────────────────────
#
#   ┌─ 派生: tool 调用摘要 (向后兼容 pipeline) ───────────────
#   "tool_calls": [
#     {"step": 0, "tool": "bash", "input": {...},
#      "output": "...",             # 可能为空
#      "output_available": true,    # 标记 output 是否真实存在
#      "thought": "..."}
#   ],
#   └──────────────────────────────────────────────────────────
#
#   "tool_names":       [str],
#   "total_tool_calls": int,
#   "total_turns":      int,        # timeline 事件数
#   "source":           str,
#   "data_quality": {
#     "has_tool_outputs":  bool,    # tool output 是否可用
#     "has_success_label": bool,    # success 是否来自数据集标注
#     "output_coverage":   float,   # tool_calls 中有 output 的比例
#   },
#   "raw_metadata":     dict
# }


def save_dataset(trajectories: list[dict], dataset_name: str):
    """
    保存转换后的数据到 ./data/{dataset_name}/
    每条轨迹输出两个文件:
      traj_XXXX.md   — 拍平的对话时序 (人可读)
      traj_XXXX.json — 元信息
    """
    out_dir = DATA_DIR / dataset_name
    out_dir.mkdir(parents=True, exist_ok=True)

    # 清理旧文件
    for old in out_dir.glob("traj_*"):
        old.unlink()

    for i, traj in enumerate(trajectories):
        prefix = f"traj_{i:04d}"

        # ── 生成 markdown ──
        md = render_traj_markdown(traj)
        (out_dir / f"{prefix}.md").write_text(md, encoding="utf-8")

        # ── 生成元信息 json (包含 pipeline 需要的字段) ──
        meta = {
            "traj_id": traj.get("traj_id", ""),
            "query": traj.get("query", ""),
            "source": traj.get("source", ""),
            "category": traj.get("category", ""),
            "success": traj.get("success"),
            "failure_reason": traj.get("failure_reason"),
            "tool_names": traj.get("tool_names", []),
            "tool_calls": traj.get("tool_calls", []),
            "total_tool_calls": traj.get("total_tool_calls", 0),
            "total_turns": traj.get("total_turns", 0),
            "data_quality": traj.get("data_quality", {}),
            "raw_metadata": traj.get("raw_metadata", {}),
        }
        with open(out_dir / f"{prefix}.json", "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

    print(f"  💾 {len(trajectories)} 条 → {out_dir}/ (md+json)")
    return out_dir


def render_traj_markdown(traj: dict) -> str:
    """将一条轨迹渲染为拍平的 markdown 对话"""
    lines = []
    traj_id = traj.get("traj_id", "unknown")
    source = traj.get("source", "")
    lines.append(f"# Trajectory: {traj_id[:12]}")
    lines.append(f"<!-- source: {source} -->")
    lines.append("")

    timeline = traj.get("timeline", [])

    if not timeline:
        # fallback: 从 tool_calls 生成简化版
        query = traj.get("query", "")
        if query:
            lines.append("## User")
            lines.append(query)
            lines.append("")
        for tc in traj.get("tool_calls", []):
            lines.append(f"    Tool: {tc.get('tool', '?')}  {_format_args(tc.get('input', {}))}")
            out = tc.get("output", "")
            if out:
                lines.append(f"    ToolResult:")
                for ol in str(out)[:1000].split("\n"):
                    lines.append(f"        {ol}")
            else:
                lines.append(f"    ToolResult: (missing)")
            lines.append("")
        return "\n".join(lines)

    # 正常路径: 从 timeline 渲染
    for event in timeline:
        role = event.get("role", "")

        if role == "user":
            lines.append("## User")
            lines.append(event.get("content", ""))
            lines.append("")

        elif role == "assistant":
            lines.append("## Assistant")
            content = event.get("content", "")
            if content:
                lines.append(content)
            lines.append("")

        elif role == "tool_call":
            tool = event.get("tool", "?")
            inp = event.get("input", {})
            lines.append(f"    Tool: {tool}  {_format_args(inp)}")

        elif role == "tool_output":
            output = event.get("output", "")
            if output:
                lines.append(f"    ToolResult:")
                for ol in str(output)[:2000].split("\n"):
                    lines.append(f"        {ol}")
            else:
                lines.append(f"    ToolResult: (missing)")
            lines.append("")

    return "\n".join(lines)


def _format_args(args: dict | str) -> str:
    """格式化 tool 调用参数为 key=value 形式"""
    if isinstance(args, str):
        return args[:200]
    if not isinstance(args, dict):
        return str(args)[:200]
    parts = []
    for k, v in args.items():
        v_str = str(v)
        if len(v_str) > 80:
            v_str = v_str[:77] + "..."
        parts.append(f'{k}="{v_str}"')
    return "  ".join(parts)


def print_stats(trajectories: list[dict], name: str):
    total = len(trajectories)
    if total == 0:
        print(f"  ⚠️  {name}: 0 条轨迹")
        return
    labeled = [t for t in trajectories if t.get("success") is not None]
    success = sum(1 for t in labeled if t["success"])
    unlabeled = total - len(labeled)
    tc_counts = [t.get("total_tool_calls", 0) for t in trajectories]
    turn_counts = [t.get("total_turns", 0) for t in trajectories]
    all_tools = set()
    for t in trajectories:
        all_tools.update(t.get("tool_names", []))
    # data quality
    dqs = [t.get("data_quality", {}) for t in trajectories]
    avg_output_cov = sum(d.get("output_coverage", 0) for d in dqs) / total if total else 0

    print(f"\n  {'─'*50}")
    print(f"  📊 {name} 统计")
    print(f"  {'─'*50}")
    if unlabeled == total:
        print(f"  总数: {total}  (success 未标注)")
    else:
        fail = len(labeled) - success
        print(f"  总数: {total}  ✅{success}  ❌{fail}  ❓{unlabeled}")
    print(f"  tool calls: avg={sum(tc_counts)/total:.1f}  min={min(tc_counts)}  max={max(tc_counts)}")
    print(f"  turns:      avg={sum(turn_counts)/total:.1f}  max={max(turn_counts)}")
    print(f"  唯一工具:   {len(all_tools)} 种  {sorted(all_tools)[:8]}{'...' if len(all_tools)>8 else ''}")
    print(f"  output覆盖: {avg_output_cov:.0%}")


# ═══════════════════════════════════════════════════════════════════
# Dataset 1: SWE-bench/SWE-smith-trajectories
# ═══════════════════════════════════════════════════════════════════

def download_swe_smith(max_samples: int = 300, split: str = "tool") -> list[dict]:
    """
    下载 SWE-smith-trajectories
    Phase 1: stream → 保存原始数据到 data/raw/swe_smith/raw_{split}.jsonl
    Phase 2: 从原始数据转换为统一格式
    """
    raw_dir = RAW_DIR / "swe_smith"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"raw_{split}.jsonl"

    # ── Phase 1: 下载原始数据 ──
    if raw_path.exists():
        line_count = sum(1 for _ in open(raw_path))
        print(f"  📂 原始数据已存在: {raw_path} ({line_count} 条)")
        if line_count >= max_samples:
            print(f"  ⏭  跳过下载, 直接转换")
        else:
            print(f"  ⚠️  已有 {line_count} 条 < 需要 {max_samples}, 重新下载")
            _download_raw_swe_smith(raw_path, max_samples, split)
    else:
        _download_raw_swe_smith(raw_path, max_samples, split)

    if not raw_path.exists():
        return []

    # ── Phase 2: 转换 ──
    print(f"  🔄 转换原始数据 → 统一格式...")
    return _convert_swe_smith(raw_path, max_samples, split)


def _download_raw_swe_smith(raw_path: Path, max_samples: int, split: str):
    """下载原始数据并逐条保存"""
    try:
        from datasets import load_dataset
    except ImportError:
        print("  ❌ 需要: pip install datasets")
        return

    print(f"  📥 下载 SWE-bench/SWE-smith-trajectories (split={split})...")
    try:
        ds = load_dataset(
            "SWE-bench/SWE-smith-trajectories",
            split=split,
            streaming=True,
        )
    except Exception as e:
        print(f"  ⚠️  下载失败: {e}")
        return

    count = 0
    with open(raw_path, "w", encoding="utf-8") as f:
        for item in ds:
            if count >= max_samples:
                break
            # 保存原始 item (patch 字段可能很大, 截断保存)
            save_item = dict(item)
            if "patch" in save_item and len(str(save_item["patch"])) > 5000:
                save_item["patch"] = str(save_item["patch"])[:5000] + "...[truncated]"
            f.write(json.dumps(save_item, ensure_ascii=False, default=str) + "\n")
            count += 1
            if count % 50 == 0:
                print(f"    ... 已下载 {count}/{max_samples}")

    print(f"  💾 原始数据: {raw_path} ({count} 条)")


def _convert_swe_smith(raw_path: Path, max_samples: int, split: str) -> list[dict]:
    """从原始 JSONL 转换为统一轨迹格式"""
    trajectories = []
    skipped = 0

    with open(raw_path, encoding="utf-8") as f:
        for line_num, line in enumerate(f):
            if len(trajectories) >= max_samples:
                break

            line = line.strip()
            if not line:
                continue

            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue

            messages = item.get("messages", [])
            instance_id = item.get("instance_id", "")
            resolved = item.get("resolved", False)
            model = item.get("model", "")
            traj_id = item.get("traj_id", str(uuid.uuid4()))

            # 首条: 诊断结构
            if line_num == 0:
                print(f"    字段: {list(item.keys())}")
                if messages:
                    first_msg = messages[0]
                    print(f"    messages[0] 类型: {type(first_msg).__name__}")
                    if isinstance(first_msg, dict):
                        print(f"    messages[0] keys: {list(first_msg.keys())[:8]}")

            # messages 可能是 JSON 字符串
            if isinstance(messages, str):
                try:
                    messages = json.loads(messages)
                except (json.JSONDecodeError, TypeError):
                    skipped += 1
                    continue

            # 规范化: 确保每个 msg 是 dict
            parsed_messages = []
            for msg in messages:
                if isinstance(msg, dict):
                    parsed_messages.append(msg)
                elif isinstance(msg, str):
                    try:
                        parsed_messages.append(json.loads(msg))
                    except (json.JSONDecodeError, TypeError):
                        continue
            messages = parsed_messages

            if not messages:
                skipped += 1
                continue

            # 提取 query
            query = ""
            for msg in messages:
                if msg.get("role") == "user":
                    content = msg.get("content", "")
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                query = block.get("text", "")[:500]
                                break
                    elif isinstance(content, str):
                        query = content[:500]
                    if query:
                        break

            if not query:
                skipped += 1
                continue

            # ── 构建 timeline + tool_calls ──
            timeline = []
            tc_list = []
            step = 0; t = 0; pending_thought = ""

            for msg in messages:
                role = msg.get("role", "")
                content = msg.get("content", "")
                if isinstance(content, list):
                    parts = [b.get("text","") for b in content if isinstance(b,dict) and b.get("type")=="text"]
                    content = "\n".join(parts)
                elif not isinstance(content, str):
                    content = str(content) if content else ""

                if role == "user":
                    timeline.append({"t": t, "role": "user", "content": content[:2000]})
                    t += 1
                elif role == "assistant":
                    thought = msg.get("thought","") or ""
                    pending_thought = thought[:800]
                    visible = content[:1000]
                    tool_calls_raw = msg.get("tool_calls", [])
                    if isinstance(tool_calls_raw, list) and tool_calls_raw:
                        if pending_thought or visible:
                            timeline.append({"t": t, "role": "assistant", "content": pending_thought or visible, "has_tool_call": True})
                            t += 1
                        for tc in tool_calls_raw:
                            if not isinstance(tc, dict): continue
                            func = tc.get("function", {})
                            if not isinstance(func, dict): continue
                            tool_name = func.get("name", "unknown")
                            args_raw = func.get("arguments", "{}")
                            args = args_raw
                            if isinstance(args_raw, str):
                                try: args = json.loads(args_raw)
                                except: args = {"raw": args_raw[:500]}
                            elif not isinstance(args_raw, dict):
                                args = {}
                            timeline.append({"t": t, "role": "tool_call", "tool": tool_name, "input": args, "_tc_id": tc.get("id","")})
                            tc_list.append({"step": step, "tool": tool_name, "input": args, "output": "", "output_available": False, "thought": pending_thought, "_tc_id": tc.get("id","")})
                            step += 1; pending_thought = ""; t += 1
                    elif visible:
                        timeline.append({"t": t, "role": "assistant", "content": visible})
                        t += 1
                elif role == "tool":
                    if isinstance(content, list):
                        parts = [b.get("text","") for b in content if isinstance(b,dict) and b.get("type")=="text"]
                        content = "\n".join(parts)
                    out = content[:2000] if isinstance(content, str) else str(content)[:2000]
                    call_ids = msg.get("tool_call_ids", [])
                    cid = call_ids[0] if isinstance(call_ids, list) and call_ids else ""
                    matched_tool = "unknown"
                    if cid:
                        for e in reversed(tc_list):
                            if e.get("_tc_id") == cid:
                                e["output"] = out; e["output_available"] = True; matched_tool = e["tool"]; break
                    elif tc_list and not tc_list[-1]["output_available"]:
                        tc_list[-1]["output"] = out; tc_list[-1]["output_available"] = True; matched_tool = tc_list[-1]["tool"]
                    timeline.append({"t": t, "role": "tool_output", "tool": matched_tool, "output": out})
                    t += 1

            if not tc_list:
                skipped += 1; continue
            for e in tc_list: e.pop("_tc_id", None)
            for e in timeline: e.pop("_tc_id", None)

            category = instance_id.split("/")[0] if "/" in instance_id else "swe_smith"
            outputs_ok = sum(1 for c in tc_list if c["output_available"])
            total_tc = len(tc_list)

            trajectories.append({
                "traj_id": traj_id,
                "query": query,
                "category": category,
                "success": bool(resolved),
                "failure_reason": None if resolved else "unresolved",
                "timeline": timeline,
                "tool_calls": tc_list,
                "tool_names": list(dict.fromkeys(c["tool"] for c in tc_list)),
                "total_tool_calls": total_tc,
                "total_turns": len(timeline),
                "source": "swe_smith_trajectories",
                "data_quality": {
                    "has_tool_outputs": outputs_ok > 0,
                    "has_success_label": True,
                    "output_coverage": round(outputs_ok / total_tc, 2) if total_tc else 0,
                },
                "raw_metadata": {
                    "instance_id": instance_id,
                    "model": model,
                    "split": split,
                    "resolved": resolved,
                    "patch_length": len(str(item.get("patch", ""))),
                },
            })

    print(f"  ✅ SWE-smith: {len(trajectories)} 条轨迹已转换 (跳过 {skipped} 条)")
    return trajectories


# ═══════════════════════════════════════════════════════════════════
# Dataset 2: TeichAI/Claude-Opus-Dataclaw-Unredacted
# ═══════════════════════════════════════════════════════════════════

def download_dataclaw(max_samples: int = 300) -> list[dict]:
    """
    下载 Claude-Opus-Dataclaw
    Phase 1: 下载原始 JSONL → data/raw/dataclaw/dataclaw_openai_full_unredacted.jsonl
    Phase 2: 转换为统一格式
    """
    raw_dir = RAW_DIR / "dataclaw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / "dataclaw_openai_full_unredacted.jsonl"

    # ── Phase 1: 下载原始数据 ──
    if raw_path.exists():
        size_mb = raw_path.stat().st_size / (1024 * 1024)
        print(f"  📂 原始数据已存在: {raw_path} ({size_mb:.1f}MB)")
        print(f"  ⏭  跳过下载, 直接转换")
    else:
        try:
            from huggingface_hub import hf_hub_download
        except ImportError:
            print("  ❌ 需要: pip install huggingface-hub")
            return []

        print("  📥 下载 TeichAI/Claude-Opus-Dataclaw-Unredacted...")
        try:
            cached_path = hf_hub_download(
                repo_id="TeichAI/Claude-Opus-Dataclaw-Unredacted",
                filename="dataclaw_openai_full_unredacted.jsonl",
                repo_type="dataset",
            )
        except Exception as e:
            print(f"  ⚠️  下载失败: {e}")
            return []

        # 拷贝到 raw 目录
        import shutil
        shutil.copy2(cached_path, raw_path)
        size_mb = raw_path.stat().st_size / (1024 * 1024)
        print(f"  💾 原始数据: {raw_path} ({size_mb:.1f}MB)")

    if not raw_path.exists():
        return []

    # ── Phase 2: 转换 ──
    print(f"  🔄 转换原始数据 → 统一格式...")

    trajectories = []
    count = 0

    with open(raw_path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f):
            if count >= max_samples:
                break

            line = line.strip()
            if not line:
                continue

            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue

            messages = item.get("messages", [])
            messages = [m for m in messages if isinstance(m, dict)]
            tools_def = item.get("tools", [])
            if not isinstance(tools_def, list):
                tools_def = []
            metadata = item.get("metadata", {})
            prompt = item.get("prompt", "")
            model = item.get("original_model", "")

            if not messages or not prompt:
                continue

            # 提取 tool 定义名称列表
            available_tools = []
            for td in tools_def:
                if isinstance(td, dict):
                    func = td.get("function", {})
                    if isinstance(func, dict) and func.get("name"):
                        available_tools.append(func["name"])

            # ── 构建 timeline: 保留完整时序 ──
            timeline = []
            tool_calls = []   # 派生的 tool 摘要
            t = 0
            step = 0

            for msg in messages:
                role = msg.get("role", "")
                content = msg.get("content", "")
                if not isinstance(content, str):
                    content = str(content) if content else ""

                if role == "user":
                    timeline.append({
                        "t": t, "role": "user",
                        "content": content[:2000],
                    })
                    t += 1

                elif role == "assistant":
                    # 提取 think 块
                    think_match = re.search(r'<think>(.*?)</think>', content, re.DOTALL)
                    thought = think_match.group(1).strip()[:800] if think_match else ""
                    # 去掉 think 块后的正文
                    visible_content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()

                    tc_list = msg.get("tool_calls", [])
                    if not isinstance(tc_list, list):
                        tc_list = []

                    if tc_list:
                        # assistant 消息 + 发起 tool call
                        if thought or visible_content:
                            timeline.append({
                                "t": t, "role": "assistant",
                                "content": (thought if thought else visible_content)[:1000],
                                "has_tool_call": True,
                            })
                            t += 1

                        for tc in tc_list:
                            if not isinstance(tc, dict):
                                continue
                            func = tc.get("function", {})
                            if not isinstance(func, dict):
                                continue
                            tool_name = func.get("name", "unknown")
                            args_raw = func.get("arguments", "{}")
                            if isinstance(args_raw, str):
                                try:
                                    args = json.loads(args_raw)
                                except (json.JSONDecodeError, TypeError):
                                    args = {"raw": args_raw[:500]}
                            else:
                                args = args_raw if isinstance(args_raw, dict) else {}

                            timeline.append({
                                "t": t, "role": "tool_call",
                                "tool": tool_name,
                                "input": args,
                                "_tc_id": tc.get("id", ""),
                            })
                            tool_calls.append({
                                "step": step,
                                "tool": tool_name,
                                "input": args,
                                "output": "",
                                "output_available": False,
                                "thought": thought,
                                "_tc_id": tc.get("id", ""),
                            })
                            step += 1
                            thought = ""
                            t += 1
                    else:
                        # 纯文本回复 (无 tool call)
                        if visible_content or thought:
                            timeline.append({
                                "t": t, "role": "assistant",
                                "content": visible_content[:2000] if visible_content else thought[:2000],
                            })
                            t += 1

                elif role == "tool":
                    # tool response — 回填到 timeline 和 tool_calls
                    tc_id = msg.get("tool_call_id", "")
                    output_text = content[:2000]

                    # 找到对应的 tool_call
                    matched_tool = "unknown"
                    if tc_id:
                        for entry in reversed(tool_calls):
                            if entry.get("_tc_id") == tc_id:
                                entry["output"] = output_text
                                entry["output_available"] = True
                                matched_tool = entry["tool"]
                                break
                    elif tool_calls and not tool_calls[-1]["output_available"]:
                        tool_calls[-1]["output"] = output_text
                        tool_calls[-1]["output_available"] = True
                        matched_tool = tool_calls[-1]["tool"]

                    timeline.append({
                        "t": t, "role": "tool_output",
                        "tool": matched_tool,
                        "output": output_text,
                    })
                    t += 1

            # 清理内部字段
            for entry in tool_calls:
                entry.pop("_tc_id", None)
            for entry in timeline:
                entry.pop("_tc_id", None)

            if not timeline:
                continue

            # ── 数据质量评估 ──
            outputs_available = sum(1 for tc in tool_calls if tc["output_available"])
            total_tc = len(tool_calls)
            output_coverage = outputs_available / total_tc if total_tc > 0 else 0.0

            # success: dataclaw 没有标注, 标 null
            stats = metadata.get("stats", {})
            if not isinstance(stats, dict):
                stats = {}
            session_id = metadata.get("session_id", str(uuid.uuid4()))

            trajectories.append({
                "traj_id": session_id,
                "query": prompt[:500],
                "category": "claude_code_session",
                "success": None,  # 无标注, 诚实标 null
                "failure_reason": None,
                "timeline": timeline,
                "tool_calls": tool_calls,
                "tool_names": list(dict.fromkeys(tc["tool"] for tc in tool_calls)),
                "total_tool_calls": total_tc,
                "total_turns": len(timeline),
                "source": "dataclaw_claude_opus",
                "data_quality": {
                    "has_tool_outputs": output_coverage > 0,
                    "has_success_label": False,
                    "output_coverage": round(output_coverage, 2),
                },
                "raw_metadata": {
                    "original_model": model,
                    "source_dataset": item.get("source_dataset", ""),
                    "project": metadata.get("project", ""),
                    "stats": stats,
                    "available_tools": available_tools,
                },
            })
            count += 1

            if count % 50 == 0:
                print(f"    ... {count}/{max_samples}")

    # 统计 output 覆盖率
    if trajectories:
        avg_coverage = sum(
            t["data_quality"]["output_coverage"] for t in trajectories
        ) / len(trajectories)
        print(f"  ✅ Dataclaw: {len(trajectories)} 条轨迹")
        print(f"     tool output 覆盖率: {avg_coverage:.0%} "
              f"({'⚠️ 大部分缺失' if avg_coverage < 0.5 else '✅ 大部分可用'})")
        print(f"     success 标注: ❌ 无 (数据集未提供)")
    return trajectories


# ═══════════════════════════════════════════════════════════════════
# Dataset 3: 合成样本 (离线 fallback)
# ═══════════════════════════════════════════════════════════════════

TEMPLATES = [
    {
        "cat": "knowledge_to_file", "q": [
            "搜索 RAG 最佳实践写总结文档", "查找 transformer 资料整理笔记",
            "查找 asyncio 用法写速查表", "搜索 Docker 教程整理保存",
        ],
        "ok": [["afs_search","afs_read","afs_read","afs_write"], ["afs_search","afs_read","afs_write"]],
        "fail": [(["afs_search","afs_write"], "skipped_read"), (["afs_search"], "no_write")],
    },
    {
        "cat": "file_edit", "q": [
            "读取 config.yaml 改端口", "检查 main.py 修复 import", "读取 README 补安装步骤",
        ],
        "ok": [["afs_list","afs_read","afs_write"], ["afs_read","afs_write"]],
        "fail": [(["afs_write"], "write_without_read"), (["afs_list","afs_read"], "no_write")],
    },
    {
        "cat": "multi_file_agg", "q": [
            "合并 workspace 所有 .md 文件", "读取三个配置文件生成对比表", "汇总 TODO 输出清单",
        ],
        "ok": [["afs_list","afs_read","afs_read","afs_read","afs_write"]],
        "fail": [(["afs_list","afs_read","afs_write"], "partial_read"), (["afs_read","afs_read"], "no_write")],
    },
]


def generate_sample_data(n_success=40, n_failure=20) -> list[dict]:
    trajs = []
    for _ in range(n_success):
        t = random.choice(TEMPLATES)
        seq = random.choice(t["ok"])
        trajs.append(_make_traj(t, seq, True, None))
    for _ in range(n_failure):
        t = random.choice(TEMPLATES)
        seq, reason = random.choice(t["fail"])
        trajs.append(_make_traj(t, seq, False, reason))
    random.shuffle(trajs)
    return trajs


def _make_traj(t, seq, success, reason):
    query = random.choice(t["q"])
    timeline = [{"t": 0, "role": "user", "content": query}]
    tc_list = []
    ti = 1
    for i, name in enumerate(seq):
        timeline.append({"t": ti, "role": "tool_call", "tool": name, "input": {}})
        ti += 1
        timeline.append({"t": ti, "role": "tool_output", "tool": name, "output": "ok"})
        ti += 1
        tc_list.append({"step": i, "tool": name, "input": {}, "output": "ok",
                         "output_available": True, "thought": ""})
    timeline.append({"t": ti, "role": "assistant",
                      "content": "done" if success else f"fail: {reason}"})
    return {
        "traj_id": str(uuid.uuid4()),
        "query": query,
        "category": t["cat"],
        "success": success,
        "failure_reason": reason,
        "timeline": timeline,
        "tool_calls": tc_list,
        "tool_names": list(dict.fromkeys(seq)),
        "total_tool_calls": len(seq),
        "total_turns": len(timeline),
        "source": "synthetic",
        "data_quality": {"has_tool_outputs": True, "has_success_label": True, "output_coverage": 1.0},
        "raw_metadata": {},
    }


# ═══════════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="XSkill 轨迹数据下载 & 统一格式化")
    parser.add_argument("--mode", choices=["swe_smith", "dataclaw", "sample", "all"], default="all")
    parser.add_argument("--max-samples", type=int, default=300, help="每个数据集最多下载条数")
    parser.add_argument("--swe-split", default="tool", choices=["tool", "xml", "ticks"],
                        help="SWE-smith 使用的 split")
    parser.add_argument("--mirror", action="store_true",
                        help="使用 hf-mirror.com 国内镜像加速下载")
    parser.add_argument("--mirror-url", default="https://hf-mirror.com",
                        help="自定义镜像 URL (默认 hf-mirror.com)")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    random.seed(args.seed)

    # ── 设置国内镜像 ──
    if args.mirror:
        os.environ["HF_ENDPOINT"] = args.mirror_url
        print(f"🪞 使用镜像: {args.mirror_url}")
    elif os.environ.get("HF_ENDPOINT"):
        print(f"🪞 检测到镜像环境变量: {os.environ['HF_ENDPOINT']}")

    print("=" * 55)
    print("  XSkill 轨迹数据准备")
    print("=" * 55)

    if args.mode in ("swe_smith", "all"):
        print(f"\n[1] SWE-bench/SWE-smith-trajectories (split={args.swe_split})")
        trajs = download_swe_smith(args.max_samples, args.swe_split)
        if trajs:
            save_dataset(trajs, "swe_smith_dataset")
            print_stats(trajs, "SWE-smith")

    if args.mode in ("dataclaw", "all"):
        print(f"\n[2] TeichAI/Claude-Opus-Dataclaw-Unredacted")
        trajs = download_dataclaw(args.max_samples)
        if trajs:
            save_dataset(trajs, "dataclaw_dataset")
            print_stats(trajs, "Dataclaw")

    if args.mode in ("sample", "all"):
        print(f"\n[3] 合成样本 (offline)")
        trajs = generate_sample_data(40, 20)
        save_dataset(trajs, "sample_dataset")
        print_stats(trajs, "Sample")

    # 生成 index 文件：扫描每个数据集目录的 traj_XXXX.json
    datasets_index = {}
    for d in DATA_DIR.iterdir():
        if d.is_dir() and d.name != "raw":
            meta_files = sorted(d.glob("traj_*.json"))
            if not meta_files:
                continue
            # 读取所有元信息
            metas = []
            for mf in meta_files:
                try:
                    with open(mf) as f:
                        metas.append(json.load(f))
                except (json.JSONDecodeError, OSError):
                    continue

            labeled = [m for m in metas if m.get("success") is not None]
            datasets_index[d.name] = {
                "path": str(d),
                "count": len(metas),
                "success": sum(1 for m in labeled if m["success"]),
                "failure": sum(1 for m in labeled if not m["success"]),
                "unlabeled": len(metas) - len(labeled),
                "sources": list(set(m.get("source", "?") for m in metas)),
            }

    index_path = DATA_DIR / "datasets_index.json"
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(datasets_index, f, ensure_ascii=False, indent=2)
    print(f"\n📋 数据集索引: {index_path}")
    for name, info in datasets_index.items():
        label = f"✅{info['success']} ❌{info['failure']}"
        if info['unlabeled']:
            label += f" ❓{info['unlabeled']}"
        print(f"   {name}: {info['count']} 条 ({label})")

    print(f"\n{'='*55}")
    print("  完成! 数据目录: ./data/")
    print(f"{'='*55}\n")


if __name__ == "__main__":
    main()
