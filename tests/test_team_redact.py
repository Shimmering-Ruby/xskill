"""team 上传脱敏 redact_text 的行为测试。

脱敏基于 detect-secrets。secret 用**真实格式**（长度/charset 对）——格式不对
检测器不命中，测了等于没测（这正是真实数据串测踩过的坑）。
"""
from __future__ import annotations

from xskill.team.client.redact import redact_text

# 真实格式的脱敏样本（值是编的，但 shape 与真 token 一致）。
_GITHUB = "ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789"   # ghp_ + 正好 36
_AWS = "AKIAIOSFODNN7EXAMPLE"
_SLACK = "xoxb-123456789012-1234567890123-abcdefghijklmnopqrstuvwx"


def test_vendor_tokens_redacted():
    """detect-secrets 厂商检测器：AWS / GitHub / Slack 全匹配替换。"""
    for tok in (_AWS, _GITHUB, _SLACK):
        out = redact_text(f"value here: {tok} end")
        assert tok not in out, f"{tok} 未被脱敏"
        assert "[REDACTED]" in out


def test_github_token_redacted_whole_not_just_prefix():
    """回归：detect-secrets 的 GitHubTokenDetector 正则带捕获组，scan_line 抽
    secret_value 只回 `ghp` 前缀；若按它替换会漏掉 36 位密钥体。redact 改用
    denylist 正则 + re.sub 全匹配，必须把整个 token 换掉。"""
    out = redact_text(f"token = {_GITHUB}")
    assert "ghp_" not in out, "GitHub token 只换了前缀、密钥体泄漏"
    assert _GITHUB[4:] not in out, "GitHub token 密钥体仍在"


def test_quoted_keyword_assignment_redacted():
    """KeywordDetector：带引号的赋值（含小写 keyword）。"""
    out = redact_text('"apiKey": "sk-d0c2bb132dd54f3b8aed0a6297eead19"')
    assert "sk-d0c2bb132dd54f3b8aed0a6297eead19" not in out
    assert "[REDACTED]" in out


def test_unquoted_env_assignment_redacted():
    """大写 env 风格的裸值赋值——agent 轨迹里最常见的密钥泄漏形态，
    detect-secrets 的 KeywordDetector 不抓裸值，靠补充正则覆盖。"""
    for src in ("export OPENAI_API_KEY=sk-1234567890abcdefghijklmnop",
                "DB_PASSWORD=SuperSecretValue123",
                "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMIxK7MDENGbPxRfiCYvalue"):
        out = redact_text(src)
        assert "[REDACTED]" in out, f"{src!r} 未脱敏"
        # keyword 前缀保留，值被换掉
        assert "=" in out
        assert "sk-1234" not in out and "SuperSecret" not in out \
            and "wJalr" not in out


def test_pem_private_key_block_redacted():
    """私钥是多行 PEM 块，逐行扫描跨不了——整块替换。"""
    src = (
        "before\n"
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEpAIBAAKCAQEA0123456789abcdefghijklmnop\n"
        "qrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ012345\n"
        "-----END RSA PRIVATE KEY-----\n"
        "after"
    )
    out = redact_text(src)
    assert "PRIVATE KEY" not in out, "PEM 块未整体脱敏"
    assert "MIIEpAIB" not in out, "私钥体泄漏"
    assert "before" in out and "after" in out


def test_no_false_positive_on_code_and_prose():
    """代码里小写 `token = ...` 变量、`MAX_TOKENS=数字`、中文对话都不该误伤。"""
    src = (
        "我让 agent 列出 skill，它调了 skill 工具\n"
        "token = tokenizer.encode(prompt)\n"
        "secret = compute_secret_sauce()\n"
        "MAX_TOKENS=400000\n"
        "def add(a, b): return a + b\n"
    )
    assert redact_text(src) == src, "把普通代码/对话误当 secret 脱敏了"


def test_idempotent():
    """重复脱敏结果稳定——`[REDACTED]` 不被任何检测器/正则再命中。"""
    src = (
        f"k1 = {_AWS}\n"
        'api_key: "sk-d0c2bb132dd54f3b8aed0a6297eead19"\n'
        "export DB_PASSWORD=SuperSecretValue123\n"
    )
    once = redact_text(src)
    assert redact_text(once) == once
