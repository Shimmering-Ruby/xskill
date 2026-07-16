PY := python3.11
PYTEST := $(PY) -m pytest

.PHONY: test e2e e2e-openclaw-real lint help

help:
	@echo "  make test                — 单元测试（pytest 全套）"
	@echo "  make lint                — 交付前门禁（semgrep 自定义规则 + ruff + pylint 命名 + vulture）"
	@echo "  make e2e                 — Docker E2E（发版前，fake LLM）"
	@echo "  make e2e-openclaw-real   — 真 openclaw + 真 DeepSeek e2e（耗 token）"

# 交付前必须跑通。规则本体在 .semgrep/xskill.yml（CLAUDE.md code 规范的固化），
# ruff/pylint 的规则选择与命名正则全部走命令行 flag，不占 pyproject.toml。
# 范围限 src/ + tests/：paper/scripts 是一次性研究脚本，不进门禁。
# 存量违规基线（待清零）见 docs/lint-baseline.md，清零完成前 make lint 为红。
lint:
	semgrep scan --config p/default --config p/python --config p/ai-best-practices \
		--config .semgrep/xskill.yml --error --quiet src tests
	$(PY) -m ruff check src tests --select F401,F841,ARG,E722,S110,S112 \
		--per-file-ignores "tests/*:ARG"
	$(PY) -m pylint src/xskill --disable=all --enable=invalid-name \
		--variable-rgx="[a-z_][a-z0-9_]{2,}$$" --argument-rgx="[a-z_][a-z0-9_]{2,}$$" \
		--attr-rgx="[a-z_][a-z0-9_]{2,}$$" --good-names=i,j,k,v,_ \
		--score=n
	$(PY) -m vulture src/ --min-confidence 80

test:
	$(PYTEST) tests/ --ignore=tests/docker_e2e --ignore=tests/live -q

e2e:
	tests/docker_e2e/run.sh all

e2e-openclaw-real:
	tests/docker_e2e/openclaw_real_llm/run_host.sh
