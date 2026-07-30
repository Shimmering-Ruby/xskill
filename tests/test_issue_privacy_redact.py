"""Unit tests for issue PII redaction helper."""
from __future__ import annotations

import importlib.util
from pathlib import Path


def _load():
    path = Path(__file__).resolve().parents[1] / ".github" / "scripts" / "redact_issue_pii.py"
    spec = importlib.util.spec_from_file_location("redact_issue_pii", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_redacts_employee_desktop_client_and_ip():
    mod = _load()
    raw = (
        "user c00946268 on DESKTOP-4CKA0N3 "
        "client_id=37b72fa3d869211a at 10.1.2.3"
    )
    out, hits = mod.redact(raw)
    assert "c00946268" not in out
    assert "DESKTOP-4CKA0N3" not in out
    assert "37b72fa3d869211a" not in out
    assert "10.1.2.3" not in out
    assert "user-<redacted>" in out
    assert "host-<redacted>" in out
    assert "client-<redacted>" in out
    assert "<redacted-ip>" in out
    assert "employee_id" in hits
    assert "desktop_hostname" in hits
    assert "client_id" in hits
    assert "private_ip" in hits


def test_leaves_clean_text_alone():
    mod = _load()
    raw = "Install ledger should supersede pending removal jobs."
    out, hits = mod.redact(raw)
    assert out == raw
    assert hits == []
