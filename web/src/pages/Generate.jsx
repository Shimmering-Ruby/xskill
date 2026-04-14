import React, { useEffect, useMemo, useRef, useState } from "react";
import { processTrajectory } from "../api";
import ToolRow, { parseToolMessage } from "../components/ToolRow";
import EventCard from "../components/EventCard";
import ActionBadge from "../components/ActionBadge";
import SSELog from "../components/SSELog";

// STEP_COL palette (from frontend_exp)
const STEP_COL = {
  intent: "#9ca3af",
  vector: "#4361ee",
  directory: "#b45309",
  refine: "#0f766e",
  result: "#15803d",
};

// Map backend log tags → colored left-border accent
const TAG_ACCENT = {
  step: STEP_COL.intent,
  tool: "#6366f1",
  decision: STEP_COL.directory,
  git: "#64748b",
  eval: STEP_COL.refine,
  ok: STEP_COL.result,
  error: "#dc2626",
  batch: STEP_COL.vector,
  info: "#9ca3af",
};

// Derive phase badge from the incoming stream
function derivePhase(events) {
  // Walk backwards, find most recent meaningful step
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i];
    if (e.type === "result") return "done";
    if (e.type === "error") return "error";
    if (e.type === "progress") {
      const s = e.data?.step || "";
      if (s) return s;
    }
    if (e.type === "log") {
      const msg = (e.data?.msg || "").toLowerCase();
      const tag = e.data?.tag;
      if (tag === "eval" || msg.includes("eval")) return "eval";
      if (tag === "git" || msg.includes("提交") || msg.includes("commit"))
        return "commit";
      if (msg.includes("启动 agent") || tag === "tool") return "agent";
      if (msg.includes("读取轨迹")) return "reading";
    }
  }
  return "idle";
}

const PHASE_COLORS = {
  idle: { bg: "bg-gray-50", text: "text-gray-500", border: "border-gray-200" },
  reading: {
    bg: "bg-amber-50",
    text: "text-amber-700",
    border: "border-amber-200",
  },
  init: {
    bg: "bg-amber-50",
    text: "text-amber-700",
    border: "border-amber-200",
  },
  agent: {
    bg: "bg-indigo-50",
    text: "text-indigo-700",
    border: "border-indigo-200",
  },
  eval: {
    bg: "bg-teal-50",
    text: "text-teal-700",
    border: "border-teal-200",
  },
  commit: {
    bg: "bg-sky-50",
    text: "text-sky-700",
    border: "border-sky-200",
  },
  done: {
    bg: "bg-green-50",
    text: "text-green-700",
    border: "border-green-200",
  },
  error: {
    bg: "bg-red-50",
    text: "text-red-700",
    border: "border-red-200",
  },
};

function PhaseBadge({ phase }) {
  const c = PHASE_COLORS[phase] || PHASE_COLORS.idle;
  return (
    <span
      className={`inline-flex items-center gap-1.5 text-[11px] px-2.5 py-1 rounded-full border font-mono ${c.bg} ${c.text} ${c.border}`}
    >
      <span
        className={`w-1.5 h-1.5 rounded-full`}
        style={{ background: "currentColor", opacity: 0.6 }}
      />
      {phase}
    </span>
  );
}

function EvalBar({ score, max = 10 }) {
  const pct = Math.max(0, Math.min(100, (Number(score) / max) * 100));
  const color =
    score >= 7 ? "#15803d" : score >= 5 ? "#b45309" : "#dc2626";
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 flex-1 rounded-full bg-gray-100 overflow-hidden">
        <div
          className="h-full"
          style={{ width: `${pct}%`, background: color }}
        />
      </div>
      <span className="font-mono text-[11px] text-gray-600 tabular-nums">
        {Number(score).toFixed(1)}
      </span>
    </div>
  );
}

function EvalDimensionBars({ evalObj }) {
  // Look for common 7-dim shapes: scores:{dim:val}, dimensions:{...}
  const scores =
    evalObj?.scores ||
    evalObj?.dimensions ||
    evalObj?.per_dimension ||
    null;
  if (!scores || typeof scores !== "object") return null;
  const entries = Object.entries(scores).filter(
    ([, v]) => typeof v === "number"
  );
  if (entries.length === 0) return null;
  return (
    <div className="mt-2 space-y-1">
      {entries.map(([dim, v]) => (
        <div key={dim} className="flex items-center gap-2">
          <span className="text-[10px] font-mono text-gray-500 w-24 truncate">
            {dim}
          </span>
          <div className="flex-1">
            <EvalBar score={v} />
          </div>
        </div>
      ))}
    </div>
  );
}

// Classify a single log event into a render shape
function classifyLog(data) {
  const msg = String(data?.msg ?? "");
  const tag = String(data?.tag ?? "info");
  // Tool — either tagged "tool" or message starts with "TOOL:"
  if (tag === "tool" || msg.startsWith("TOOL:")) {
    const stripped = msg.replace(/^TOOL:\s*/, "");
    const parsed = parseToolMessage(stripped);
    return { ...parsed, kind: "tool", toolKind: parsed.kind };
  }
  if (tag === "reasoning" || msg.startsWith("REASONING:")) {
    return { kind: "reasoning", text: msg.replace(/^REASONING:\s*/, "") };
  }
  if (tag === "error") return { kind: "error", text: msg };
  if (
    tag === "decision" ||
    msg.startsWith("META:") ||
    msg.startsWith("DECISION:") ||
    msg.startsWith("EVAL:") ||
    msg.startsWith("COMMIT:")
  ) {
    return { kind: "milestone", tag, text: msg };
  }
  if (tag === "eval") return { kind: "milestone", tag, text: msg };
  if (tag === "git") return { kind: "milestone", tag, text: msg };
  if (tag === "ok") return { kind: "milestone", tag, text: msg };
  if (tag === "step") return { kind: "step", text: msg };
  return { kind: "log", tag, text: msg };
}

export default function PageGenerate({ desc }) {
  const [trajPath, setTrajPath] = useState(() => {
    try {
      return localStorage.getItem("t2s:lastTrajPath") || "";
    } catch {
      return "";
    }
  });
  const [dryRun, setDryRun] = useState(false);
  const [rawMode, setRawMode] = useState(false);
  const [running, setRunning] = useState(false);
  const [events, setEvents] = useState([]); // raw SSE events for the raw viewer
  const [renderItems, setRenderItems] = useState([]); // classified for rich viewer
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [progressPct, setProgressPct] = useState(null);
  const abortRef = useRef(null);
  const scrollRef = useRef(null);
  const stickyBottomRef = useRef(true);

  // Auto-suggest from Ingest's last submitted traj_id
  const suggested = useMemo(() => {
    try {
      const id = localStorage.getItem("t2s:lastTrajId");
      return id || "";
    } catch {
      return "";
    }
  }, []);

  // Auto-scroll unless user scrolled up
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    if (stickyBottomRef.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [renderItems, result, error]);

  const onStreamScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const nearBottom =
      el.scrollTop + el.clientHeight >= el.scrollHeight - 40;
    stickyBottomRef.current = nearBottom;
  };

  const phase = useMemo(() => derivePhase(events), [events]);

  const canRun = !running && trajPath.trim().length > 0;

  const start = async () => {
    setRunning(true);
    setEvents([]);
    setRenderItems([]);
    setResult(null);
    setError(null);
    setProgressPct(null);
    stickyBottomRef.current = true;

    try {
      localStorage.setItem("t2s:lastTrajPath", trajPath.trim());
    } catch {}

    const ctrl = new AbortController();
    abortRef.current = ctrl;

    try {
      await processTrajectory(
        { traj_path: trajPath.trim(), dry_run: dryRun },
        {
          signal: ctrl.signal,
          onEvent: (type, data) => {
            setEvents((xs) => [...xs, { type, data, ts: Date.now() }]);

            if (type === "progress") {
              const cur = data?.current;
              const tot = data?.total;
              if (
                typeof cur === "number" &&
                typeof tot === "number" &&
                tot > 0
              ) {
                setProgressPct(Math.round((cur / tot) * 100));
              }
              // Also push a muted row so user sees high-level phase step
              const step = data?.step || "progress";
              const detail = data?.detail || "";
              setRenderItems((xs) => [
                ...xs,
                {
                  kind: "progress",
                  text: `${step}${detail ? " · " + detail : ""}`,
                  ts: Date.now(),
                },
              ]);
            }

            if (type === "log") {
              const cls = classifyLog(data || {});
              setRenderItems((xs) => {
                if (cls.kind === "tool") {
                  // If this is a result variant, try to attach to last live tool
                  // We kept cls.args (for call) OR cls.preview (for result).
                  const isResult = "preview" in cls && !("args" in cls);
                  if (isResult) {
                    for (let i = xs.length - 1; i >= 0; i--) {
                      const prev = xs[i];
                      if (
                        prev.kind === "tool" &&
                        prev.name === cls.name &&
                        prev.live
                      ) {
                        const copy = xs.slice();
                        copy[i] = {
                          ...prev,
                          live: false,
                          result: cls.preview || "",
                        };
                        return copy;
                      }
                    }
                    // No matching live tool — render as orphan result
                    return [
                      ...xs,
                      {
                        kind: "tool",
                        name: cls.name,
                        args: "",
                        result: cls.preview || "",
                        live: false,
                        ts: Date.now(),
                      },
                    ];
                  }
                  return [
                    ...xs,
                    {
                      kind: "tool",
                      name: cls.name,
                      args: cls.args || "",
                      result: "",
                      live: true,
                      ts: Date.now(),
                    },
                  ];
                }
                return [...xs, { ...cls, ts: Date.now() }];
              });
            }

            if (type === "result") {
              setResult(data || {});
              // When done, flip any still-live tools to done
              setRenderItems((xs) =>
                xs.map((x) =>
                  x.kind === "tool" && x.live ? { ...x, live: false } : x
                )
              );
            }

            if (type === "error") {
              setError(data?.error || data?.message || "unknown error");
            }
          },
        }
      );
    } catch (e) {
      if (!ctrl.signal.aborted) {
        setError(String(e?.message || e));
      }
    } finally {
      setRunning(false);
      abortRef.current = null;
    }
  };

  const stop = () => abortRef.current?.abort();

  return (
    <div className="p-8 max-w-[1400px] mx-auto">
      <h1 className="text-[20px] font-semibold text-gray-900 mb-1">Generate</h1>
      <h2 className="text-[13px] text-gray-500 mb-6">
        {desc || "Agent analyzes a trajectory and decides create / update / skip."}
      </h2>

      <div className="grid lg:grid-cols-[360px_1fr] gap-4">
        {/* LEFT — controls */}
        <div className="bg-white rounded-lg border border-gray-200 p-5 sticky top-4 self-start">
          <div className="text-[14px] font-semibold text-gray-800 mb-3">
            Source trajectory
          </div>

          <label className="block text-[12px] text-gray-500 mb-1">
            traj_path
          </label>
          <input
            type="text"
            value={trajPath}
            onChange={(e) => setTrajPath(e.target.value)}
            placeholder="e.g. data/traj_0001.md"
            className="w-full h-8 px-2 rounded-md border border-gray-200 text-[12px] font-mono focus:outline-none focus:border-[#4361ee]"
          />
          <div className="text-[10px] text-gray-400 mt-1 leading-snug">
            Relative to server cwd or absolute path. Also accepts the full
            <code className="font-mono"> data/&lt;id&gt;.md </code> produced by
            Ingest.
          </div>

          {suggested && (
            <button
              type="button"
              className="mt-2 text-[11px] text-[#4361ee] hover:underline font-mono"
              onClick={() => setTrajPath(`data/${suggested}.md`)}
            >
              use last ingested: {suggested}
            </button>
          )}

          <label className="flex items-center gap-2 mt-4 text-[12px] text-gray-700 select-none">
            <input
              type="checkbox"
              checked={dryRun}
              onChange={(e) => setDryRun(e.target.checked)}
              className="accent-[#4361ee]"
            />
            dry_run
            <span className="text-[11px] text-gray-400">(no git commit)</span>
          </label>

          <div className="mt-4 flex items-center gap-2">
            <button
              type="button"
              disabled={!canRun}
              onClick={start}
              className="bg-[#4361ee] text-white text-[13px] h-8 px-4 rounded-md hover:bg-[#3651dc] disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {running ? "Running…" : "Run"}
            </button>
            {running && (
              <button
                type="button"
                onClick={stop}
                className="h-8 px-3 rounded-md border border-red-200 bg-red-50 text-red-700 text-[12px] hover:bg-red-100"
              >
                Stop
              </button>
            )}
          </div>

          <div className="mt-4 text-[11px] text-gray-500 leading-relaxed">
            Agent will analyze the trajectory and decide whether to create /
            update / skip a Skill.
          </div>
        </div>

        {/* RIGHT — stream */}
        <div className="bg-white rounded-lg border border-gray-200 p-5 min-w-0">
          <div className="flex items-center gap-3 mb-3">
            <PhaseBadge phase={phase} />
            {progressPct !== null && (
              <div className="flex-1 min-w-0">
                <div className="h-1.5 w-full rounded-full bg-gray-100 overflow-hidden">
                  <div
                    className="h-full"
                    style={{
                      width: `${Math.max(0, Math.min(100, progressPct))}%`,
                      background: "#4361ee",
                      transition: "width 120ms linear",
                    }}
                  />
                </div>
              </div>
            )}
            <div className="flex-1" />
            <button
              type="button"
              onClick={() => setRawMode((m) => !m)}
              className={`h-7 px-2.5 rounded-md text-[11px] border ${
                rawMode
                  ? "border-[#4361ee] text-[#4361ee] bg-[#eef2ff]"
                  : "border-gray-200 text-gray-600 hover:bg-gray-50"
              }`}
              title="Toggle raw log viewer"
            >
              raw log
            </button>
          </div>

          {rawMode ? (
            <SSELog events={events} />
          ) : (
            <div
              ref={scrollRef}
              onScroll={onStreamScroll}
              className="max-h-[620px] overflow-auto pr-1"
            >
              {renderItems.length === 0 && !running && !result && !error && (
                <div className="text-[12px] text-gray-400 py-8 text-center">
                  Pick a trajectory on the left and press Run. The agent's
                  reasoning, tool calls, and final decision will stream here.
                </div>
              )}

              {renderItems.map((it, i) => (
                <StreamRow key={i} item={it} />
              ))}

              {error && (
                <EventCard
                  accent="#dc2626"
                  label="error"
                  tag="error"
                  tone="error"
                >
                  <pre className="whitespace-pre-wrap break-all font-mono text-[11px] text-red-700">
                    {error}
                  </pre>
                </EventCard>
              )}

              {result && <FinalResult result={result} />}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function StreamRow({ item }) {
  if (item.kind === "tool") {
    return (
      <ToolRow
        name={item.name}
        args={item.args || ""}
        result={item.result || ""}
        live={!!item.live}
      />
    );
  }
  if (item.kind === "reasoning") {
    return (
      <div className="border-l-2 border-gray-200 pl-3 my-2 text-[12px] italic text-gray-500 leading-relaxed whitespace-pre-wrap">
        {item.text}
      </div>
    );
  }
  if (item.kind === "milestone") {
    const tag = item.tag || "decision";
    const accent =
      TAG_ACCENT[tag] ||
      (item.text?.startsWith("META:")
        ? STEP_COL.intent
        : item.text?.startsWith("EVAL:")
        ? STEP_COL.refine
        : item.text?.startsWith("COMMIT:")
        ? STEP_COL.directory
        : STEP_COL.directory);
    return (
      <EventCard accent={accent} label={tag} tag={tag}>
        <div className="font-mono text-[12px] text-gray-800 break-all whitespace-pre-wrap">
          {item.text}
        </div>
      </EventCard>
    );
  }
  if (item.kind === "step") {
    return (
      <EventCard accent={STEP_COL.intent} label="step" tone="muted">
        <div className="text-[12px] text-gray-700">{item.text}</div>
      </EventCard>
    );
  }
  if (item.kind === "error") {
    return (
      <EventCard accent="#dc2626" label="error" tone="error">
        <div className="font-mono text-[11px] text-red-700 whitespace-pre-wrap break-all">
          {item.text}
        </div>
      </EventCard>
    );
  }
  if (item.kind === "progress") {
    return (
      <div className="flex items-center gap-2 text-[11px] text-gray-400 my-1 font-mono">
        <span className="text-gray-300">›</span>
        <span>{item.text}</span>
      </div>
    );
  }
  // Generic / log / fallback
  return (
    <div className="flex gap-2 text-[12px] text-gray-600 leading-relaxed my-1">
      <span className="text-gray-300 font-mono">›</span>
      <span className="break-all whitespace-pre-wrap">
        {item.tag && (
          <span className="text-gray-400 font-mono mr-1.5">[{item.tag}]</span>
        )}
        {item.text || JSON.stringify(item)}
      </span>
    </div>
  );
}

function FinalResult({ result }) {
  const action = result?.action || "unknown";
  const skills = Array.isArray(result?.skills) ? result.skills : [];
  const skill = result?.skill || null;
  const evalMap = result?.eval || {};
  const agent = result?.agent_result;

  // Accent by action
  const accent =
    action === "merged"
      ? "#15803d"
      : action === "rejected"
      ? "#dc2626"
      : action === "error"
      ? "#dc2626"
      : "#4361ee";

  return (
    <div
      className="rounded-lg mt-4 overflow-hidden"
      style={{
        border: `1px solid ${accent}33`,
        background: action === "merged" ? "#f0fdf4" : "#f8f9fb",
      }}
    >
      <div
        className="px-4 py-3 flex items-center gap-3"
        style={{ borderBottom: `1px solid ${accent}22` }}
      >
        <ActionBadge action={action} />
        <div className="flex-1 min-w-0">
          {skills.length > 0 && (
            <div className="flex items-center gap-1.5 flex-wrap">
              {skills.map((s) => (
                <span
                  key={s}
                  className="font-mono text-[11px] px-2 py-0.5 rounded-full bg-white border border-gray-200 text-gray-700"
                >
                  {s}
                </span>
              ))}
            </div>
          )}
          {skill && !skills.length && (
            <span className="font-mono text-[12px] text-gray-700">{skill}</span>
          )}
        </div>
        <span className="text-[10px] font-mono text-gray-400 uppercase">
          result
        </span>
      </div>

      <div className="px-4 py-3 space-y-2">
        {result?.reason && (
          <div className="text-[12px] text-gray-600">
            <span className="text-gray-400">reason: </span>
            {result.reason}
          </div>
        )}

        {Object.keys(evalMap).length > 0 && (
          <div className="space-y-2">
            {Object.entries(evalMap).map(([name, er]) => (
              <div
                key={name}
                className="rounded-md border border-gray-200 bg-white p-3"
              >
                <div className="flex items-center justify-between gap-2 mb-1">
                  <span className="font-mono text-[12px] text-gray-800">
                    {name}
                  </span>
                  {er?.tier && (
                    <span className="text-[10px] font-mono text-gray-500 uppercase">
                      tier: {er.tier}
                    </span>
                  )}
                </div>
                {typeof er?.eval_score === "number" && (
                  <EvalBar score={er.eval_score} />
                )}
                <EvalDimensionBars evalObj={er} />
                {er?.note && (
                  <div className="text-[11px] text-gray-500 mt-1">
                    {er.note}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

        {agent?.content && (
          <details className="mt-2">
            <summary className="text-[11px] text-gray-500 cursor-pointer hover:text-gray-700 font-mono">
              agent output ({agent.framework || "agent"})
            </summary>
            <pre className="mt-2 p-3 bg-gray-50 border border-gray-200 rounded-md text-[11px] font-mono text-gray-700 whitespace-pre-wrap break-all max-h-[280px] overflow-auto">
              {agent.content}
            </pre>
          </details>
        )}

        <details>
          <summary className="text-[11px] text-gray-400 cursor-pointer hover:text-gray-600 font-mono">
            raw result
          </summary>
          <pre className="mt-2 p-3 bg-gray-50 border border-gray-200 rounded-md text-[11px] font-mono text-gray-600 whitespace-pre-wrap break-all max-h-[260px] overflow-auto">
            {JSON.stringify(result, null, 2)}
          </pre>
        </details>
      </div>
    </div>
  );
}
