#!/usr/bin/env python3
"""xskill 安装健康诊断（只读，零副作用）——验证孤儿 dest 自愈假设。

设计约束：
- 不修改任何文件；账本 SQLite 先复制到临时目录再只读打开，不碰活库。
- 不向任何 skill 目录、生态目录写数据；stdout 之外唯一输出是 --json 指定的报告。
- 无第三方依赖；有 dulwich 或 git 命令时会额外校验 git 对象与三方比对。

验证的假设（对应 fix/orphan-dest-revsync-storm）：
  H1 存在孤儿 dest：无账本行、无 sidecar。
  H2 孤儿 dest 内有老 meta（.xskill-install-meta.json）且带合法 source_sha。
  H3 source_sha 对应的 git 对象还在源仓里（领养能成功）。
  H4 按重建基线做三方比对，预测回流结果：NO_EDIT / SYNCED(回灌) / CONFLICT。

用法：
  python diagnose_install_health.py            # 人类可读报告
  python diagnose_install_health.py --json report.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

LEDGER_DB = Path("~/.xskill/installations.sqlite").expanduser()
SOURCE_ROOT = Path("~/.xskill/skill").expanduser()
LEGACY_META_NAME = ".xskill-install-meta.json"
SIDECAR_PREFIX = ".xskill-install-meta-"
MARKER_NAME = ".xskill-install-identity.json"
QUIET_SECONDS = 180

ECOSYSTEM_ROOTS = [
    Path("~/.claude/skills").expanduser(),
    Path("~/.agents/skills").expanduser(),
    Path("~/.cac/skills").expanduser(),
    Path("~/.config/opencode/skills").expanduser(),
    Path("~/.cursor/skills").expanduser(),
    Path("~/.trae-cn/skills").expanduser(),
    Path("~/.trae/skills").expanduser(),
]

# 与 reverse_sync_copy_dest 默认 exclude 对齐
FP_EXCLUDE_FIRST_SEGMENT = {".git", LEGACY_META_NAME, MARKER_NAME}


def dest_key(path: Path) -> str:
    """与 install_ledger.dest_key 同算法。"""
    return os.path.normcase(os.path.abspath(os.path.normpath(str(path))))


def sha256_file(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            while True:
                chunk = fh.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def dir_fingerprints(root: Path) -> dict[str, str]:
    """目录逐文件 sha256（POSIX 相对路径），跳过 exclude 第一段与软链。"""
    out: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)
        dirnames[:] = sorted(
            d for d in dirnames
            if not (rel_dir == Path() and d in FP_EXCLUDE_FIRST_SEGMENT)
        )
        for name in sorted(filenames):
            rel = rel_dir / name
            if rel.parts and rel.parts[0] in FP_EXCLUDE_FIRST_SEGMENT:
                continue
            full = Path(dirpath) / name
            try:
                if full.is_symlink():
                    continue
                st = full.lstat()
                if not stat.S_ISREG(st.st_mode):
                    continue
            except OSError:
                continue
            digest = sha256_file(full)
            if digest is not None:
                out[rel.as_posix()] = digest
    return out


def load_ledger_rows() -> tuple[dict[str, dict], dict[str, int], str | None]:
    """复制账本到临时目录后只读打开。返回 (by_dest_key, job_stats, error)。"""
    if not LEDGER_DB.is_file():
        return {}, {"removal_jobs": 0}, f"账本不存在: {LEDGER_DB}"
    tmpdir = Path(tempfile.mkdtemp(prefix="xskill-diag-"))
    try:
        copies = []
        for suffix in ("", "-wal", "-shm"):
            src = Path(str(LEDGER_DB) + suffix)
            if src.is_file():
                dst = tmpdir / (LEDGER_DB.name + suffix)
                shutil.copyfile(src, dst)
                copies.append(dst)
        conn = sqlite3.connect(f"file:{tmpdir / LEDGER_DB.name}?mode=ro", uri=True)
        try:
            rows = {}
            for row in conn.execute(
                "SELECT dest_key, skill_name, mode, source, source_sha,"
                " installed_at, generation FROM installations"
                " WHERE status='active'",
            ):
                rows[row[0]] = {
                    "skill_name": row[1], "mode": row[2], "source": row[3],
                    "source_sha": row[4], "installed_at": row[5],
                    "generation": row[6],
                }
            jobs = conn.execute(
                "SELECT COUNT(*) FROM removal_jobs"
                " WHERE state IN ('pending','deleting')",
            ).fetchone()[0]
            return rows, {"removal_jobs": jobs}, None
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return {}, {"removal_jobs": 0}, f"账本读取失败: {exc}"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def read_legacy_meta(dest: Path) -> dict | None:
    try:
        parsed = json.loads((dest / LEGACY_META_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, UnicodeDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def git_tree_fingerprints(source: Path, sha: str) -> dict[str, str] | None:
    """优先 dulwich，退回 git CLI；都不可用返回 None。"""
    try:
        from dulwich.objects import Commit
        from dulwich.repo import Repo
        repo = Repo(str(source))
        try:
            obj = repo[sha.encode("ascii")]
            if not isinstance(obj, Commit):
                return None
            out: dict[str, str] = {}
            stack = [(obj.tree, Path())]
            while stack:
                tree_id, prefix = stack.pop()
                tree = repo[tree_id]
                for raw_name, mode, entry_id in tree.iteritems():
                    name = raw_name.decode("utf-8", errors="strict")
                    rel = prefix / name
                    if stat.S_ISDIR(mode):
                        stack.append((entry_id, rel))
                    elif stat.S_ISREG(mode):
                        out[rel.as_posix()] = hashlib.sha256(
                            repo[entry_id].data,
                        ).hexdigest()
            return out
        finally:
            repo.close()
    except Exception:
        pass
    git = shutil.which("git")
    if git is None:
        return None
    try:
        listing = subprocess.run(
            [git, "-C", str(source), "ls-tree", "-r", "-z", sha],
            capture_output=True, check=True, timeout=60,
        )
        out = {}
        for entry in listing.stdout.decode("utf-8", "strict").split("\0"):
            if not entry:
                continue
            meta, rel = entry.split("\t", 1)
            mode = meta.split(" ", 1)[0]
            if not stat.S_ISREG(int(mode, 8)):
                continue
            blob = subprocess.run(
                [git, "-C", str(source), "cat-file", "blob", f"{sha}:{rel}"],
                capture_output=True, check=True, timeout=60,
            )
            out[Path(rel).as_posix()] = hashlib.sha256(blob.stdout).hexdigest()
        return out
    except (subprocess.SubprocessError, ValueError, UnicodeDecodeError):
        return None


def predict_revsync(
    dest: Path, source: Path, baseline: dict[str, str], installed_at: float,
) -> dict:
    """按 reverse_sync_copy_dest 的判定顺序做干跑，预测本轮结果。"""
    dest_fp = dir_fingerprints(dest)
    source_fp = dir_fingerprints(source)
    if not dest_fp:
        return {"verdict": "NO_EDIT", "backport": [], "conflict": []}
    max_mtime = 0.0
    for dirpath, dirnames, filenames in os.walk(dest):
        rel_dir = Path(dirpath).relative_to(dest)
        dirnames[:] = [d for d in dirnames if d not in FP_EXCLUDE_FIRST_SEGMENT]
        for name in filenames:
            rel = rel_dir / name
            if rel.parts and rel.parts[0] in FP_EXCLUDE_FIRST_SEGMENT:
                continue
            try:
                max_mtime = max(max_mtime, (Path(dirpath) / name).lstat().st_mtime)
            except OSError:
                pass
    if max_mtime - installed_at < 1.0:
        return {"verdict": "NO_EDIT", "backport": [], "conflict": []}
    backport, conflict = [], []
    for rel, dest_hash in dest_fp.items():
        base_hash = baseline.get(rel)
        if dest_hash == base_hash:
            continue  # 未动（source 前进与否都不回灌）
        src_hash = source_fp.get(rel)
        if src_hash == dest_hash:
            continue  # 已收敛（上次回灌成功）
        if src_hash != base_hash:
            conflict.append(rel)  # 双边分叉
        else:
            backport.append(rel)  # dest 单边变更
    if conflict:
        return {"verdict": "CONFLICT", "backport": backport, "conflict": conflict}
    if not backport:
        return {"verdict": "NO_EDIT", "backport": [], "conflict": []}
    newest = 0.0
    for rel in backport:
        try:
            newest = max(newest, (dest / rel).lstat().st_mtime)
        except OSError:
            pass
    if time.time() - newest < QUIET_SECONDS:
        return {
            "verdict": "RECENT_EDIT(下轮回灌)",
            "backport": backport, "conflict": [],
        }
    return {"verdict": "SYNCED(回灌)", "backport": backport, "conflict": []}


def classify_dest(dest: Path, root: Path, ledger: dict[str, dict]) -> dict:
    name = dest.name
    info: dict = {"dest": str(dest), "name": name}
    row = ledger.get(dest_key(dest))
    if row is not None:
        info["category"] = "HEALTHY(有账本行)"
        info["row"] = row
        return info
    sidecar = root / f"{SIDECAR_PREFIX}{name}.json"
    if sidecar.is_file():
        info["category"] = "SIDECAR(待迁移或迁移失败保留)"
        return info
    try:
        if dest.is_symlink():
            info["category"] = "LINK(软链,不涉及)"
            return info
    except OSError:
        pass
    meta = read_legacy_meta(dest)
    if meta is None:
        try:
            has_marker = (dest / MARKER_NAME).is_file()
        except OSError:
            has_marker = False
        if has_marker:
            info["category"] = "ORPHAN_NO_META(marker在,确为xskill安装,需重装)"
        else:
            info["category"] = "NO_TRACE(无xskill痕迹,可能非受管)"
        return info
    sha = meta.get("source_sha")
    installed_at = meta.get("installed_at")
    if (
        not isinstance(sha, str) or len(sha) != 40
        or any(c not in "0123456789abcdef" for c in sha)
    ):
        info["category"] = "ORPHAN_BAD_SHA(无法自愈,需重装)"
        return info
    if isinstance(installed_at, bool) or not isinstance(installed_at, (int, float)):
        installed_at = None
    info.update({
        "category": "ORPHAN_ADOPTABLE",
        "source_sha": sha,
        "installed_at": installed_at,
    })
    return info


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", metavar="PATH", help="同时写 JSON 报告")
    args = parser.parse_args()

    print("=" * 68)
    print("xskill 安装健康诊断（只读 / 不修改任何文件与账本）")
    print("=" * 68)
    print(f"time        : {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"ledger      : {LEDGER_DB}")
    print(f"source root : {SOURCE_ROOT}")

    ledger, job_stats, ledger_err = load_ledger_rows()
    if ledger_err:
        print(f"账本        : {ledger_err}")
        if "不存在" in ledger_err:
            print("注意        : 本机未跑过新版 daemon, 无账本时 NO_TRACE 类别"
                  "大概率是非受管目录, 不代表故障")
    else:
        print(f"账本        : {len(ledger)} 条 active 安装, "
              f"{job_stats['removal_jobs']} 条未完成卸装")

    report: dict = {"ledger_error": ledger_err, "dests": []}
    summary: dict[str, int] = {}
    orphans: list[dict] = []

    for root in ECOSYSTEM_ROOTS:
        if not root.is_dir():
            continue
        try:
            children = sorted(
                p for p in root.iterdir()
                if p.is_dir() and not p.name.startswith(".")
            )
        except OSError:
            continue
        sidecars = [
            p for p in root.iterdir()
            if p.name.startswith(SIDECAR_PREFIX) and p.name.endswith(".json")
        ]
        print(f"\n[生态根] {root}  ({len(children)} 个 dest, "
              f"{len(sidecars)} 个遗留 sidecar)")
        for dest in children:
            info = classify_dest(dest, root, ledger)
            report["dests"].append(info)
            summary[info["category"]] = summary.get(info["category"], 0) + 1
            mark = {
                "HEALTHY(有账本行)": "  ok ",
                "LINK(软链,不涉及)": "  ok ",
            }.get(info["category"], "  !! ")
            print(f"  {mark}{info['name']:<36} {info['category']}")
            if info["category"] == "ORPHAN_ADOPTABLE":
                orphans.append(info)

    print("\n" + "-" * 68)
    print("[汇总]")
    for category, count in sorted(summary.items()):
        print(f"  {count:>4}  {category}")

    healed, frozen = 0, 0
    if orphans:
        print("\n" + "-" * 68)
        print("[孤儿干跑] 按修复版逻辑预测第一轮回流结果（不执行任何写操作）")
    for info in orphans:
        name = info["name"]
        source = SOURCE_ROOT / name
        line = f"  {name}: "
        if info.get("installed_at") is None:
            line += "老 meta 缺 installed_at → 仍冻结(REVERSE_SYNC 安全跳过)"
            frozen += 1
        elif not source.is_dir():
            line += f"找不到源仓 {source} → 仍冻结"
            frozen += 1
        else:
            baseline = git_tree_fingerprints(source, info["source_sha"])
            if baseline is None:
                line += "git 对象已丢失(或本机无 git/dulwich) → 仍冻结,需重装"
                frozen += 1
            else:
                pred = predict_revsync(
                    Path(info["dest"]), source, baseline, info["installed_at"],
                )
                info["prediction"] = pred["verdict"]
                detail = ""
                if pred["backport"]:
                    detail += f", 回灌 {len(pred['backport'])} 个文件"
                    detail += f" {pred['backport'][:5]}"
                if pred["conflict"]:
                    detail += f", 冲突 {len(pred['conflict'])} 个 {pred['conflict'][:5]}"
                line += f"领养 OK → {pred['verdict']}{detail}"
                if pred["verdict"].startswith("CONFLICT"):
                    frozen += 1
                else:
                    healed += 1
        print(line)

    print("\n" + "=" * 68)
    print(f"[结论] 孤儿 {len(orphans)} 个: 预计首轮自愈 {healed}, 仍冻结 {frozen}")
    if ledger_err is None and not orphans and not any(
        k.startswith("ORPHAN") or k.startswith("SIDECAR") for k in summary
    ):
        print("[结论] 未发现孤儿 dest,DAMAGED 风暴假设在本机不成立")
    print("=" * 68)

    if args.json:
        Path(args.json).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        print(f"JSON 报告已写入 {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
