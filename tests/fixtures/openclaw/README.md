# OpenClaw fixtures

## `sample_session.trajectory.jsonl`

OpenClaw `~/.openclaw/agents/<agent>/sessions/<sid>.trajectory.jsonl` 真实样本的
精简 + 脱敏版本，用于 `tests/test_openclaw_adapter.py`。

来源：本机 `openclaw@2026.5.7` 跑 weixin channel 的真 session（采集时点
2026-05-19）。脱敏处理：

- `sessionId / traceId` → `11111111-2222-3333-4444-555555555555`
- `workspaceDir` → `/tmp/openclaw-test-workspace`
- `runId` → `run-aaaaaaaa-...`
- `messagesSnapshot` 里的用户文本和 media 路径 → `[redacted ...]`

事件类型覆盖（7 种 / OpenClaw 全部）：

```
session.started / trace.metadata / context.compiled / prompt.submitted
/ model.completed / trace.artifacts / session.ended
```

**未覆盖**：`tool_use` / `tool_result` content blocks（本机 session 是纯文本
对话，没触发工具）。`scripts/capture_openclaw_fixture/` 用 docker 跑一次带
exec tool 的 prompt 来补这块覆盖，产物落到
`sample_session_with_tools.trajectory.jsonl`。
