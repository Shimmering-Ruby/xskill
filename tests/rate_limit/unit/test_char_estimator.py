"""字符 → token 数粗估测试。

依据:
- 英文 ASCII: ~4 字符 / token (OpenAI 文档经验值)
- 中文/CJK:   ~1.5 字符 / token (中文 1 字符 ≈ 0.6 token)
- 输出整体 × 1.2 余量,宁多算不少算(限流场景宁可拒不可漏)
"""
from xskill.utils.rate_limit import estimate_tokens


def test_pure_ascii_uses_4_char_per_token():
    # 40 字符英文 → 10 token × 1.2 = 12
    text = "a" * 40
    assert estimate_tokens(text) == 12


def test_pure_cjk_uses_1_5_char_per_token():
    # 30 中文字符 → 20 token × 1.2 = 24
    text = "中" * 30
    assert estimate_tokens(text) == 24


def test_mixed_text_sums_both_categories():
    # 20 英文 + 15 中文 → 20/4 + 15/1.5 = 5 + 10 = 15, × 1.2 = 18
    text = "a" * 20 + "中" * 15
    assert estimate_tokens(text) == 18


def test_empty_string_returns_zero():
    assert estimate_tokens("") == 0


def test_min_one_token_for_nonempty():
    # 1 个英文字符理论 0.25 token,× 1.2 = 0.3,向上取整应 ≥ 1
    assert estimate_tokens("a") >= 1
