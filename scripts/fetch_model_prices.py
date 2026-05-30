#!/usr/bin/env python3.11
"""build 时拉取最新模型价格,vendor 成 src/xskill/data/model_prices.json。

数据源 = LiteLLM 社区维护的价格表(只取数据,**不引 litellm 包**,见 ADR-0001)。
任何失败(网络错 / 源不存在 / 解析失败)→ **退出码 1,构建报错**(不静默兜底)。

用法(发版前):
    python3.11 scripts/fetch_model_prices.py
    # 然后 python3.11 -m build / twine upload
"""
from __future__ import annotations

import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SRC = ("https://raw.githubusercontent.com/BerriAI/litellm/main/"
       "model_prices_and_context_window.json")
OUT = Path(__file__).resolve().parents[1] / "src" / "xskill" / "data" / "model_prices.json"


def _per_1m(per_token) -> float | None:
    return round(float(per_token) * 1_000_000, 6) if isinstance(per_token, (int, float)) else None


def main() -> int:
    try:
        with urllib.request.urlopen(SRC, timeout=30) as r:  # noqa: S310
            raw = json.loads(r.read().decode("utf-8"))
    except Exception as e:  # pylint: disable=broad-exception-caught
        print(f"FATAL: 拉取价格表失败: {e}\n  源: {SRC}", file=sys.stderr)
        return 1

    out: dict = {"_meta": {
        "source": SRC,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "schema": "model -> {input_per_1m, output_per_1m, cache_hit_per_1m?, embed_per_1m?}",
    }}
    n_chat = n_embed = 0
    for model, d in raw.items():
        if not isinstance(d, dict):
            continue
        mode = d.get("mode")
        if mode == "embedding":
            ep = _per_1m(d.get("input_cost_per_token"))
            if ep is not None:
                out[model] = {"embed_per_1m": ep}
                n_embed += 1
        elif mode in (None, "chat", "completion", "responses"):
            ip = _per_1m(d.get("input_cost_per_token"))
            op = _per_1m(d.get("output_cost_per_token"))
            if ip is None and op is None:
                continue
            entry = {"input_per_1m": ip or 0.0, "output_per_1m": op or 0.0}
            ch = _per_1m(d.get("cache_read_input_token_cost"))
            if ch is not None:
                entry["cache_hit_per_1m"] = ch
                entry["cache_miss_per_1m"] = entry["input_per_1m"]
            out[model] = entry
            n_chat += 1

    if n_chat + n_embed < 50:
        print(f"FATAL: 解析到的条目过少({n_chat}+{n_embed}),疑似源格式变更",
              file=sys.stderr)
        return 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"OK: wrote {OUT}  ({n_chat} chat + {n_embed} embedding models)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
