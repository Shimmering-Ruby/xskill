from xskill.team.redact import redact_text


def test_redacts_sk_style_keys():
    src = 'OPENAI_API_KEY = "sk-abcdEFGH1234567890wxyz"'
    out = redact_text(src)
    assert "sk-abcdEFGH1234567890wxyz" not in out
    assert "[REDACTED]" in out


def test_redacts_password_assignments():
    for src in ['password: hunter2supersecret', 'DB_PASS="p@ssw0rd-very-long"',
                "token = 'ghp_0123456789abcdef0123'"]:
        out = redact_text(src)
        assert "[REDACTED]" in out
        assert "hunter2" not in out and "p@ssw0rd" not in out and "ghp_0123" not in out


def test_leaves_ordinary_text_untouched():
    src = "# 这是一段正常的轨迹\nuser: 帮我跑 pytest\nassistant: 好的"
    assert redact_text(src) == src


def test_idempotent():
    src = 'key = "sk-abcdEFGH1234567890wxyz"'
    once = redact_text(src)
    assert redact_text(once) == once
