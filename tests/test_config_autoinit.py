"""test_config_autoinit.py -- 首次运行 auto-init 配置模板

`ensure_config_exists` 在 config.yaml 缺失时写出 CONFIG_TEMPLATE，让首次
`xskill serve` 不直接抛 traceback。
"""
from __future__ import annotations

import yaml

from xskill.config import CONFIG_TEMPLATE, ensure_config_exists


def test_creates_template_when_missing(tmp_path):
    cfg = tmp_path / ".xskill" / "config.yaml"
    assert not cfg.exists()

    created = ensure_config_exists(cfg)

    assert created is False          # False = 刚创建
    assert cfg.is_file()
    assert cfg.read_text(encoding="utf-8") == CONFIG_TEMPLATE


def test_noop_when_already_exists(tmp_path):
    cfg = tmp_path / ".xskill" / "config.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("skill_dir: /custom\n", encoding="utf-8")

    existed = ensure_config_exists(cfg)

    assert existed is True           # True = 已存在
    # 不覆盖用户已有内容
    assert cfg.read_text(encoding="utf-8") == "skill_dir: /custom\n"


def test_template_is_valid_yaml_with_required_sections():
    parsed = yaml.safe_load(CONFIG_TEMPLATE)
    # 必填段都在
    for key in ("skill_dir", "llm", "embedding", "canary", "watcher"):
        assert key in parsed, f"template missing {key}"
    # llm / embedding 带 api_key 占位符（用户要填）
    assert parsed["llm"]["api_key"] == "PUT_YOUR_LLM_API_KEY_HERE"
    assert parsed["embedding"]["api_key"] == "PUT_YOUR_EMBEDDING_API_KEY_HERE"
