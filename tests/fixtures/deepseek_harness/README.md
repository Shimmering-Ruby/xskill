# DeepSeek Harness fixture provenance

`session.jsonl` was **not** hand-written. It was produced by DeepSeek Harness's
own serializer functions (`toHeaderLine`, `eventLines`, `logPath`, extracted
verbatim from `@deepseek-ai/dsh-session-persistence-jsonl` 0.1.0-rc.7 and
executed under Node), with `compression: 'none'` and `packChunks: true`. The
header object, per-record `JSON.stringify`, and the project-directory /
session-directory path derivation are therefore byte-for-byte what a live dsh
run writes for the same events.

The event list deliberately covers every record type the adapter must handle:
session header, `turn/start` / `turn/end`, `user/message`, `assistant/chunk`
(streaming fragments that must be skipped), `tool/call`, `tool/result`
(skipped), and the assembled `assistant/message`.

Regenerate with the same event list if the upstream format version changes;
the adapter tests assert against this file.
