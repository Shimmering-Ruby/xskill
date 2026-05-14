"""git_bundle.py — skill git 仓的 bundle 传输封装（SP1）

skill 分发本质是 git 分布式。SP1 不跑独立 git http daemon，而是把每个
skill 子仓打成 git bundle 走普通 HTTP body 传输：

- server → client：``make_repo_bundle`` 打全分支 → client ``apply_repo_bundle``
  克隆/fetch 落到本地 working copy。
- client → server：client ``make_branch_bundle`` 打 ``_useredit`` 分支 →
  server ``fetch_branch_from_bundle`` 收进 ``user-staging/<client_id>``。

SP1 每次传全量 bundle（skill 仓很小：SKILL.md ≤400 行 + 几个 script）。
增量 bundle 是后续优化。遇到 git 失败一律 throw（CLAUDE.md：不写 fallback）。
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path


def _run(args: list[str]) -> str:
    r = subprocess.run(["git"] + args, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout.strip()


def make_repo_bundle(repo_dir: Path | str) -> bytes:
    """把一个 skill git 仓的所有本地分支打成 bundle 字节。"""
    repo_dir = Path(repo_dir)
    if not (repo_dir / ".git").is_dir():
        raise NotADirectoryError(f"not a git repo: {repo_dir}")
    with tempfile.NamedTemporaryFile(suffix=".bundle", delete=False) as tf:
        bundle_path = Path(tf.name)
    try:
        _run(["-C", str(repo_dir), "bundle", "create", str(bundle_path), "--branches"])
        return bundle_path.read_bytes()
    finally:
        bundle_path.unlink(missing_ok=True)


def apply_repo_bundle(bundle_bytes: bytes, dest_dir: Path | str) -> None:
    """用 bundle 在本地物化/刷新一个 skill working copy。

    dest_dir 不是 git 仓 → ``git init`` 一个 HEAD 指向 ``_scratch``（不会被
    bundle 创建）的空仓，让 main/staging 永远不是"当前 checked-out 分支"——
    否则后续 ``git fetch`` 会拒绝更新当前分支的 ref。

    统一走 ``git fetch <bundle> +refs/heads/*:refs/heads/*``：把 bundle 的
    ``refs/heads/*`` 强制覆盖本地同名分支（main/staging/baby）。工作树留给
    reconcile 去 checkout ``_active``。
    """
    dest_dir = Path(dest_dir)
    with tempfile.NamedTemporaryFile(suffix=".bundle", delete=False) as tf:
        tf.write(bundle_bytes)
        bundle_path = Path(tf.name)
    try:
        if not (dest_dir / ".git").is_dir():
            dest_dir.mkdir(parents=True, exist_ok=True)
            _run(["-C", str(dest_dir), "init", "-q", "-b", "_scratch"])
            _run(["-C", str(dest_dir), "config", "user.email", "xskill@local"])
            _run(["-C", str(dest_dir), "config", "user.name", "xskill"])
        _run(["-C", str(dest_dir), "fetch", "-q", str(bundle_path),
              "+refs/heads/*:refs/heads/*"])
    finally:
        bundle_path.unlink(missing_ok=True)


def make_branch_bundle(repo_dir: Path | str, branch: str) -> bytes:
    """把一个分支（含完整历史）打成 bundle 字节。client 推手改用。"""
    repo_dir = Path(repo_dir)
    if not (repo_dir / ".git").is_dir():
        raise NotADirectoryError(f"not a git repo: {repo_dir}")
    with tempfile.NamedTemporaryFile(suffix=".bundle", delete=False) as tf:
        bundle_path = Path(tf.name)
    try:
        _run(["-C", str(repo_dir), "bundle", "create", str(bundle_path), branch])
        return bundle_path.read_bytes()
    finally:
        bundle_path.unlink(missing_ok=True)


def fetch_branch_from_bundle(
    bundle_bytes: bytes, dest_repo: Path | str, src_branch: str, dest_ref: str,
) -> str:
    """把 bundle 里的 ``src_branch`` fetch 进 ``dest_repo`` 的 ``dest_ref``。

    返回 ``dest_ref`` 的新 sha。server 收 client 手改时用——dest_ref 形如
    ``refs/heads/user-staging/<client_id>``，永远不碰 main。
    """
    dest_repo = Path(dest_repo)
    if not (dest_repo / ".git").is_dir():
        raise NotADirectoryError(f"not a git repo: {dest_repo}")
    with tempfile.NamedTemporaryFile(suffix=".bundle", delete=False) as tf:
        tf.write(bundle_bytes)
        bundle_path = Path(tf.name)
    try:
        _run(["-C", str(dest_repo), "fetch", "-q", str(bundle_path),
              f"refs/heads/{src_branch}:{dest_ref}"])
        return _run(["-C", str(dest_repo), "rev-parse", dest_ref])
    finally:
        bundle_path.unlink(missing_ok=True)
