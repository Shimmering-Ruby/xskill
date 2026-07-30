#!/usr/bin/env python3
"""Redact internal identifiers from GitHub issue/PR/comment text.

Patterns (conservative):
  - employee-style ids: c + 7+ digits (e.g. c00946268)
  - Windows hostnames: DESKTOP-XXXX
  - private IPv4
  - long hex client ids next to client/client_id labels (16+ hex)

Used by .github/workflows/issue-privacy-guard.yml
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

EMPLOYEE_ID = re.compile(r"\bc\d{7,}\b", re.IGNORECASE)
DESKTOP_HOST = re.compile(r"\bDESKTOP-[A-Z0-9]+\b", re.IGNORECASE)
PRIVATE_IP = re.compile(
    r"\b(?:"
    r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3}"
    r")\b"
)
# client `deadbeef...` / client_id: deadbeef... / client_id=`...`
LABELED_CLIENT_HEX = re.compile(
    r"(?i)\b(client(?:_id)?)\s*[`:=\s]+\s*`?([a-f0-9]{16,})`?"
)


def redact(text: str) -> tuple[str, list[str]]:
    if not text:
        return text, []
    hits: list[str] = []
    out = text

    def sub(pattern: re.Pattern[str], repl: str, label: str) -> None:
        nonlocal out
        if pattern.search(out):
            hits.append(label)
            out = pattern.sub(repl, out)

    sub(EMPLOYEE_ID, "user-<redacted>", "employee_id")
    sub(DESKTOP_HOST, "host-<redacted>", "desktop_hostname")
    sub(PRIVATE_IP, "<redacted-ip>", "private_ip")

    def client_repl(match: re.Match[str]) -> str:
        hits.append("client_id")
        return f"{match.group(1)} `client-<redacted>`"

    if LABELED_CLIENT_HEX.search(out):
        out = LABELED_CLIENT_HEX.sub(client_repl, out)

    # dedupe labels preserving order
    seen: set[str] = set()
    uniq: list[str] = []
    for h in hits:
        if h not in seen:
            seen.add(h)
            uniq.append(h)
    return out, uniq


def _api(method: str, path: str, payload: dict | None = None) -> dict:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        raise SystemExit("GITHUB_TOKEN required")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        raise SystemExit("GITHUB_REPOSITORY required")
    url = f"https://api.github.com/repos/{repo}{path}"
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "xskill-issue-privacy-guard",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"GitHub API {method} {path} failed: {exc.code} {detail}") from exc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--event",
        required=True,
        choices=("issue", "pull_request", "issue_comment", "pull_request_review_comment"),
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path or not os.path.isfile(event_path):
        raise SystemExit("GITHUB_EVENT_PATH missing")
    with open(event_path, encoding="utf-8") as fh:
        event = json.load(fh)

    if args.event == "issue":
        item = event["issue"]
        number = item["number"]
        title, title_hits = redact(item.get("title") or "")
        body, body_hits = redact(item.get("body") or "")
        hits = sorted(set(title_hits + body_hits))
        if not hits:
            print("clean: no PII patterns in issue")
            return 0
        print(f"issue #{number} hits={hits}")
        if args.dry_run:
            return 0
        patch: dict = {}
        if title != (item.get("title") or ""):
            patch["title"] = title
        if body != (item.get("body") or ""):
            patch["body"] = body
        if patch:
            _api("PATCH", f"/issues/{number}", patch)
        _api(
            "POST",
            f"/issues/{number}/comments",
            {
                "body": (
                    "Privacy guard: redacted internal identifiers "
                    f"({', '.join(hits)}). "
                    "Please avoid posting employee ids, hostnames, "
                    "client ids, or private IPs in issues."
                ),
            },
        )
        return 0

    if args.event == "pull_request":
        pr = event["pull_request"]
        number = pr["number"]
        title, title_hits = redact(pr.get("title") or "")
        body, body_hits = redact(pr.get("body") or "")
        hits = sorted(set(title_hits + body_hits))
        if not hits:
            print("clean: no PII patterns in pull request")
            return 0
        print(f"pr #{number} hits={hits}")
        if args.dry_run:
            return 0
        patch = {}
        if title != (pr.get("title") or ""):
            patch["title"] = title
        if body != (pr.get("body") or ""):
            patch["body"] = body
        if patch:
            _api("PATCH", f"/pulls/{number}", patch)
        _api(
            "POST",
            f"/issues/{number}/comments",
            {
                "body": (
                    "Privacy guard: redacted internal identifiers "
                    f"({', '.join(hits)}). "
                    "Please avoid posting employee ids, hostnames, "
                    "client ids, or private IPs in pull requests."
                ),
            },
        )
        return 0

    if args.event in {"issue_comment", "pull_request_review_comment"}:
        comment = event["comment"]
        comment_id = comment["id"]
        body, hits = redact(comment.get("body") or "")
        if not hits:
            print("clean: no PII patterns in comment")
            return 0
        print(f"comment {comment_id} hits={hits}")
        if args.dry_run:
            return 0
        path = (
            f"/pulls/comments/{comment_id}"
            if args.event == "pull_request_review_comment"
            else f"/issues/comments/{comment_id}"
        )
        _api("PATCH", path, {"body": body})
        # Prefer not to spam a second comment on every redacted comment.
        print("comment redacted in place")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
