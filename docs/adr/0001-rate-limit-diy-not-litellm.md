# ADR-0001: 自实现 Token Bucket 限流，不引入 LiteLLM

**状态:** Accepted
**日期:** 2026-05-25
**关联 issue:** SkillNerds/xskill#32

## 背景

issue #32 揭示 xskill 默认 30 并发对云端 plan（OpenAI Tier-1 / Azure 60 RPM
/ OneAPI 等）用户会瞬时打满配额触发 429。需要在 LLM 调用层加限流。

可选路径：

1. 引入 litellm，用其内置 Router 的限流功能
2. 自实现 token bucket

## 决策

**自实现** TokenBucket（RPM + TPM 双桶 + 字符粗估自校准）。

## 否决 litellm 的理由

### 1. 强依赖 tiktoken，触发中国用户 Azure Blob 下载灾难

litellm 1.86 的 requires_dist 含 `tiktoken<1.0,>=0.8.0` 和
`tokenizers<1.0,>=0.21.0`。tiktoken 首次 import 时会从
`https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken`
下载 BPE 文件，该 URL 在中国大陆经常超时或不通。`TIKTOKEN_CACHE_DIR`
只能复用已缓存文件，首次跑机器必须有网。

xskill 定位"任意 OpenAI 兼容 endpoint"，大量目标用户在中国大陆。
`pip install xskill && xskill serve` 第一次跑卡 5 分钟然后报
ReadTimeout 是产品级灾难。

### 2. 砍掉 Python 3.9 支持

litellm 1.86 `requires_python: <3.14,>=3.10`。xskill `pyproject.toml`
显式支持 3.9-3.12。3.9 是 RHEL 9 / Debian 11 自带 Python，砍掉
即砍企业内部部署用户。

### 3. agno 框架硬约束

xskill 的 cluster/edit agent 通过 agno 框架运行，agno 直连
api.deepseek.com 时**必须**用 `agno.models.deepseek.DeepSeek` 子类
（原因：DeepSeek thinking 模型 multi-turn 强制要求 reasoning_content
原样回传）。litellm proxy 模式下 base_url 改为 localhost:4000 后，
xskill 自己的 base_url 路由判断失效，会走 `OpenAIChat` 通用类，
reasoning_content 透传断裂 —— multi-turn tool calling 必崩。

agno 虽提供 `agno.models.litellm.LiteLLM`（2025-03 引入），但接 LiteLLM
后同样要靠 litellm SDK，仍然踩前两条 dealbreaker。

### 4. 依赖体量

litellm 主包 + tiktoken（Rust）+ tokenizers（Rust）+ aiohttp +
jinja2 + jsonschema ≈ 50MB，`pip install xskill` 从十几秒涨到一分钟以上。
OSS 友好度下降。

## DIY 方案的取舍

- **不引 tiktoken**：字符粗估（英文 4 字符/token，中文 1.5 字符/token，
  × 1.2 余量），response.usage 存在时 reconcile 自校准，缺失则保留估算。
  误差 ±30% 但限流场景"宁多算不少算"。
- **不引 asyncio**：用 threading + monotonic clock，与 xskill 现有
  ThreadPoolExecutor 模型一致。
- **按 base_url 共享桶**：utils/llm 通路和 agno 通路同 base_url 共享同一
  TokenBucket，避免双重扣额。
- **monkey-patch agno model.invoke 而非 subclass**：保留 agno DeepSeek
  子类的 reasoning_content 处理逻辑，只在 invoke 前后插
  acquire/reconcile，与 agno 版本升级解耦。

## PR review 红线

- 任何 `import tiktoken` / `import litellm` / `import tokenizers` 直接拒
- 任何把 response.usage 当必有字段处理的代码直接拒（必须 fallback 估算）
- 任何新加的 agno model 子类破坏 base_url 路由判断的直接拒

## 后续

- Batch 2: anthropic 原生 + OneAPI 形态测试
- Batch 2: streaming 模式 usage 处理（末尾 chunk）
- 长期：如果 xskill 增加 budget tracking / observability dashboard 等
  功能且代码量超过 500 行，再评估"自建 vs litellm SDK 模式"取舍
