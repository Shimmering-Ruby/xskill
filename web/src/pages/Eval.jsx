import React, { useEffect, useMemo, useRef, useState } from "react";
import { listSkills, getSkill, evalSkill } from "../api";
import SSELog from "../components/SSELog";
import EventCard from "../components/EventCard";
import ToolRow, { parseToolMessage } from "../components/ToolRow";

// ─────────────────────────────────────────────────────────────────────────────
// Markdown (compact subset — adapted from frontend_exp/App.jsx Prose/Inline)
// ─────────────────────────────────────────────────────────────────────────────
function Inline({ text = "" }) {
  const re = /(\*\*(.+?)\*\*|`([^`]+)`|\[([^\]]+)\]\(([^)]+)\))/g;
  const out = [];
  let last = 0,
    m,
    k = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index));
    if (m[2])
      out.push(
        <strong key={k++} className="font-semibold text-gray-900">
          {m[2]}
        </strong>
      );
    else if (m[3])
      out.push(
        <code
          key={k++}
          className="font-mono text-[11px] bg-gray-100 border border-gray-200 text-blue-700 px-1 py-0.5 rounded"
        >
          {m[3]}
        </code>
      );
    else if (m[4])
      out.push(
        <a
          key={k++}
          href={m[5]}
          target="_blank"
          rel="noreferrer"
          className="text-indigo-600 underline underline-offset-2"
        >
          {m[4]}
        </a>
      );
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return <>{out}</>;
}

function Prose({ text = "" }) {
  const lines = text.split("\n");
  const nodes = [];
  let i = 0,
    k = 0;
  while (i < lines.length) {
    const l = lines[i];
    if (l.startsWith("```")) {
      const lang = l.slice(3).trim();
      const code = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) {
        code.push(lines[i]);
        i++;
      }
      nodes.push(
        <pre
          key={k++}
          className="font-mono text-[11px] rounded-lg my-3 p-4 overflow-x-auto"
          style={{ background: "#1e1e2e", color: "#cdd6f4", lineHeight: 1.55 }}
        >
          {lang && (
            <div className="text-[10px] mb-2" style={{ color: "#7f849c" }}>
              {lang}
            </div>
          )}
          {code.join("\n")}
        </pre>
      );
      i++;
      continue;
    }
    const hm = l.match(/^(#{1,3})\s+(.+)/);
    if (hm) {
      const cls = [
        "text-[16px] font-semibold mt-4 mb-1.5",
        "text-[14px] font-semibold mt-3 mb-1",
        "text-[13px] font-semibold mt-2 mb-1",
      ][hm[1].length - 1];
      nodes.push(
        <div key={k++} className={`text-gray-900 ${cls}`}>
          <Inline text={hm[2]} />
        </div>
      );
      i++;
      continue;
    }
    if (/^[-*•]\s/.test(l)) {
      const items = [];
      while (i < lines.length && /^[-*•]\s/.test(lines[i])) {
        items.push(lines[i].replace(/^[-*•]\s/, ""));
        i++;
      }
      nodes.push(
        <div key={k++} className="my-1.5">
          {items.map((it, j) => (
            <div
              key={j}
              className="flex gap-2.5 text-[12.5px] leading-relaxed text-gray-600 mb-0.5"
            >
              <span className="text-gray-400 mt-0.5 shrink-0">—</span>
              <span>
                <Inline text={it} />
              </span>
            </div>
          ))}
        </div>
      );
      continue;
    }
    if (/^\d+\.\s/.test(l)) {
      const items = [];
      while (i < lines.length && /^\d+\.\s/.test(lines[i])) {
        items.push(lines[i].replace(/^\d+\.\s/, ""));
        i++;
      }
      nodes.push(
        <div key={k++} className="my-1.5 pl-1">
          {items.map((it, j) => (
            <div
              key={j}
              className="flex gap-2.5 text-[12.5px] leading-relaxed text-gray-600 mb-0.5"
            >
              <span className="text-gray-400 shrink-0 tabular-nums">
                {j + 1}.
              </span>
              <span>
                <Inline text={it} />
              </span>
            </div>
          ))}
        </div>
      );
      continue;
    }
    if (l.startsWith("> ")) {
      nodes.push(
        <div
          key={k++}
          className="border-l-2 border-gray-200 pl-3 text-gray-500 italic text-[12.5px] leading-relaxed my-1.5"
        >
          <Inline text={l.slice(2)} />
        </div>
      );
      i++;
      continue;
    }
    if (/^---+$/.test(l.trim())) {
      nodes.push(<hr key={k++} className="border-gray-200 my-4" />);
      i++;
      continue;
    }
    if (l.trim() === "") {
      nodes.push(<div key={k++} className="h-2" />);
      i++;
      continue;
    }
    nodes.push(
      <div
        key={k++}
        className="text-[12.5px] leading-relaxed text-gray-600 mb-0.5"
      >
        <Inline text={l} />
      </div>
    );
    i++;
  }
  return <div>{nodes}</div>;
}

// ─────────────────────────────────────────────────────────────────────────────
// Eval helpers
// ─────────────────────────────────────────────────────────────────────────────

const DIMENSIONS = [
  [
    "description_precision",
    "Description 精度 — frontmatter.description 是否列出典型用户表述 + 工具依赖，且边界清晰（不误触）",
  ],
  ["step_actionability", "步骤可执行性 — 每步都具体到工具、命令、文件路径与函数名"],
  ["granularity", "粒度合理 — 覆盖一类相似问题（3-5 个场景），既不过粗也不过细"],
  ["generalizability", "可泛化 — 模式可迁移到同类项目，不硬编码特定路径/库名"],
  [
    "warning_coverage",
    "Warning 覆盖度 — 关键 step 都有内联 `> ⚠️` 警告，且引用轨迹证据（N/M trajs）",
  ],
  ["faithfulness", "忠实性 — 步骤与源轨迹一致，未臆造不存在的操作"],
  [
    "structural_quality",
    "结构质量 — frontmatter 完整（name/description/compatibility/metadata）、`##` 阶段分节、步骤编号",
  ],
];

// Read legacy score keys as a fallback, since skills migrated or written
// before the v2 rename still carry `trigger_precision` / `pitfall_quality`
// under metadata.eval.scores.
const LEGACY_DIMENSION_ALIASES = {
  description_precision: "trigger_precision",
  warning_coverage: "pitfall_quality",
};

function readDim(scores, dim) {
  if (!scores) return undefined;
  if (scores[dim] != null) return scores[dim];
  const legacy = LEGACY_DIMENSION_ALIASES[dim];
  if (legacy && scores[legacy] != null) return scores[legacy];
  return undefined;
}

function scoreTier(score) {
  if (typeof score !== "number") return null;
  if (score >= 8) return "A";
  if (score >= 6) return "B";
  return "C";
}

function scoreColor(score) {
  if (typeof score !== "number")
    return { fg: "#6b7280", bg: "#f3f4f6", border: "#e5e7eb" };
  if (score >= 8)
    return { fg: "#15803d", bg: "#f0fdf4", border: "#bbf7d0" };
  if (score >= 6)
    return { fg: "#b45309", bg: "#fffbeb", border: "#fde68a" };
  return { fg: "#6b7280", bg: "#f3f4f6", border: "#e5e7eb" };
}

function ScorePill({ score, compact = false }) {
  if (typeof score !== "number") {
    return (
      <span className="text-[10px] font-mono text-gray-400 px-1.5 py-0.5 rounded bg-gray-50 border border-gray-200">
        —
      </span>
    );
  }
  const c = scoreColor(score);
  return (
    <span
      className="font-mono tabular-nums rounded"
      style={{
        color: c.fg,
        background: c.bg,
        border: `1px solid ${c.border}`,
        fontSize: compact ? 10 : 11,
        padding: compact ? "1px 5px" : "2px 6px",
      }}
    >
      {score.toFixed(1)}
    </span>
  );
}

function TierBadge({ tier }) {
  if (!tier) return null;
  const map = {
    A: { fg: "#15803d", bg: "#f0fdf4", border: "#bbf7d0" },
    B: { fg: "#b45309", bg: "#fffbeb", border: "#fde68a" },
    C: { fg: "#6b7280", bg: "#f3f4f6", border: "#e5e7eb" },
  };
  const c = map[tier] || map.C;
  return (
    <span
      className="font-mono text-[10px] font-semibold rounded px-1.5 py-0.5"
      style={{ color: c.fg, background: c.bg, border: `1px solid ${c.border}` }}
    >
      Tier {tier}
    </span>
  );
}

function DimensionBars({ scores }) {
  if (!scores) return null;
  return (
    <div className="space-y-2">
      {DIMENSIONS.map(([dim, tip]) => {
        const v = readDim(scores, dim);
        const hasScore = typeof v === "number";
        const pct = hasScore ? Math.max(0, Math.min(100, (v / 10) * 100)) : 0;
        return (
          <div
            key={dim}
            className="flex items-center gap-3"
            title={hasScore ? `${dim}: ${v}/10 — ${tip}` : tip}
          >
            <div className="text-[12px] text-gray-700 w-[160px] truncate shrink-0">
              {dim}
            </div>
            <div className="flex-1 h-2 rounded-full bg-gray-100 overflow-hidden">
              <div
                className="h-full"
                style={{
                  width: `${pct}%`,
                  background: "#4361ee",
                  transition: "width 200ms linear",
                }}
              />
            </div>
            <div className="font-mono text-[11px] text-gray-700 tabular-nums w-10 text-right">
              {hasScore ? v.toFixed(1) : "—"}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// Classify a log event for rich stream rendering (same rules as Generate page)
function classifyLog(data) {
  const msg = String(data?.msg ?? "");
  const tag = String(data?.tag ?? "info");
  if (tag === "tool" || msg.startsWith("TOOL:")) {
    const stripped = msg.replace(/^TOOL:\s*/, "");
    const parsed = parseToolMessage(stripped);
    return { ...parsed, kind: "tool", toolKind: parsed.kind };
  }
  if (tag === "eval") return { kind: "milestone", tag, text: msg };
  if (tag === "error") return { kind: "error", text: msg };
  if (tag === "ok") return { kind: "milestone", tag, text: msg };
  return { kind: "log", tag, text: msg };
}

// ─────────────────────────────────────────────────────────────────────────────
// Page
// ─────────────────────────────────────────────────────────────────────────────

export default function PageEval({ desc }) {
  const [skills, setSkills] = useState([]);
  const [loadingList, setLoadingList] = useState(true);
  const [filterQ, setFilterQ] = useState("");
  const [selected, setSelected] = useState(null);
  const [detail, setDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [tab, setTab] = useState("preview");

  const [nRuns, setNRuns] = useState(3);
  const [sandbox, setSandbox] = useState(false);

  const [running, setRunning] = useState(false);
  const [events, setEvents] = useState([]);
  const [renderItems, setRenderItems] = useState([]);
  const [evalResult, setEvalResult] = useState(null);
  const [evalError, setEvalError] = useState(null);
  const [showRaw, setShowRaw] = useState(false);
  const abortRef = useRef(null);

  // Initial load
  useEffect(() => {
    (async () => {
      setLoadingList(true);
      try {
        const r = await listSkills();
        setSkills(r.skills || []);
        if ((r.skills || []).length > 0 && !selected) {
          setSelected(r.skills[0].name);
        }
      } catch (e) {
        // surface an error; still show empty state
        setSkills([]);
      } finally {
        setLoadingList(false);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Load detail when selected changes
  useEffect(() => {
    if (!selected) {
      setDetail(null);
      return;
    }
    let alive = true;
    setDetailLoading(true);
    (async () => {
      try {
        const d = await getSkill(selected);
        if (alive) {
          setDetail(d);
          // reset eval state when switching skill
          setEvents([]);
          setRenderItems([]);
          setEvalResult(null);
          setEvalError(null);
        }
      } catch (e) {
        if (alive) setDetail({ error: String(e?.message || e) });
      } finally {
        if (alive) setDetailLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [selected]);

  const filtered = useMemo(() => {
    const q = filterQ.trim().toLowerCase();
    if (!q) return skills;
    return skills.filter((s) => s.name.toLowerCase().includes(q));
  }, [skills, filterQ]);

  const start = async () => {
    if (!selected) return;
    setRunning(true);
    setEvents([]);
    setRenderItems([]);
    setEvalResult(null);
    setEvalError(null);
    setTab("eval");

    const ctrl = new AbortController();
    abortRef.current = ctrl;

    try {
      await evalSkill(
        selected,
        { n_runs: nRuns, sandbox },
        {
          signal: ctrl.signal,
          onEvent: (type, data) => {
            setEvents((xs) => [...xs, { type, data, ts: Date.now() }]);

            if (type === "log") {
              const cls = classifyLog(data || {});
              setRenderItems((xs) => {
                if (cls.kind === "tool") {
                  const isResult =
                    "preview" in cls && !("args" in cls);
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
              setEvalResult(data || {});
              setRenderItems((xs) =>
                xs.map((x) =>
                  x.kind === "tool" && x.live ? { ...x, live: false } : x
                )
              );
              // Refresh skill list to reflect new eval_score
              listSkills()
                .then((r) => setSkills(r.skills || []))
                .catch(() => {});
            }

            if (type === "error") {
              setEvalError(data?.error || data?.message || "unknown error");
            }
          },
        }
      );
    } catch (e) {
      if (!ctrl.signal.aborted) {
        setEvalError(String(e?.message || e));
      }
    } finally {
      setRunning(false);
      abortRef.current = null;
    }
  };

  const stop = () => abortRef.current?.abort();

  // v2 shape: detail.metadata.eval.{scores,eval_score,tier,runs}
  // Legacy shape: detail.eval.* and detail.abstract.eval_result.*
  const detailEval =
    detail?.metadata?.eval || detail?.eval || detail?.abstract?.eval_result || null;
  const detailTags =
    detail?.metadata?.tags || detail?.abstract?.tags || [];
  const detailBody =
    detail?.skill_md_body || detail?.skill_md || "";
  const finalScores =
    evalResult?.scores || detailEval?.scores || null;
  const finalMedian =
    (typeof evalResult?.eval_score === "number" && evalResult.eval_score) ||
    (typeof detailEval?.eval_score === "number" && detailEval.eval_score) ||
    null;
  const finalTier = scoreTier(finalMedian);

  return (
    <div className="p-8 max-w-[1400px] mx-auto">
      <h1 className="text-[20px] font-semibold text-gray-900 mb-1">Evaluate</h1>
      <h2 className="text-[13px] text-gray-500 mb-6">
        {desc || "Review existing skills and run the 7-dimension LLM evaluation."}
      </h2>

      <div className="grid lg:grid-cols-[320px_1fr] gap-4">
        {/* LEFT — skill list */}
        <aside className="bg-white rounded-lg border border-gray-200 p-4 sticky top-4 max-h-[calc(100vh-7rem)] overflow-y-auto">
          <div className="flex items-center justify-between mb-3">
            <div className="text-[14px] font-semibold text-gray-800">Skills</div>
            <span className="text-[11px] font-mono text-gray-500 bg-gray-100 border border-gray-200 rounded-full px-2 py-0.5">
              {skills.length}
            </span>
          </div>
          <input
            type="text"
            value={filterQ}
            onChange={(e) => setFilterQ(e.target.value)}
            placeholder="filter by name…"
            className="w-full h-8 px-2 mb-3 rounded-md border border-gray-200 text-[12px] font-mono focus:outline-none focus:border-[#4361ee]"
          />
          {loadingList && (
            <div className="text-[12px] text-gray-400 py-4 text-center">
              loading…
            </div>
          )}
          {!loadingList && filtered.length === 0 && (
            <div className="text-[12px] text-gray-400 py-4 text-center">
              {skills.length === 0 ? "(no skills yet)" : "(no match)"}
            </div>
          )}
          <div className="space-y-1">
            {filtered.map((s) => {
              const isSel = s.name === selected;
              const tier = scoreTier(s.eval_score);
              return (
                <button
                  key={s.name}
                  type="button"
                  onClick={() => setSelected(s.name)}
                  className={`w-full text-left rounded-md border px-2.5 py-2 flex items-center gap-2 transition ${
                    isSel
                      ? "bg-[#eef2ff] border-[#c7d2fe]"
                      : "bg-white border-gray-200 hover:bg-gray-50"
                  }`}
                >
                  <span className="font-mono text-[12.5px] text-gray-800 truncate flex-1">
                    {s.name}
                  </span>
                  {s.frozen && (
                    <span className="text-[9px] font-mono uppercase px-1 py-0.5 rounded bg-sky-50 border border-sky-200 text-sky-700">
                      frozen
                    </span>
                  )}
                  {tier && (
                    <span className="text-[10px] font-mono text-gray-500">
                      {tier}
                    </span>
                  )}
                  <ScorePill score={s.eval_score} compact />
                </button>
              );
            })}
          </div>
          <div className="mt-3 text-[11px] text-gray-400 leading-snug">
            Click a skill → review + evaluate
          </div>
        </aside>

        {/* RIGHT — detail + eval */}
        <section className="bg-white rounded-lg border border-gray-200 p-5 min-w-0">
          {!selected && (
            <div className="text-[13px] text-gray-400 py-12 text-center">
              Select a skill on the left to begin.
            </div>
          )}

          {selected && (
            <>
              {/* Top strip */}
              <div className="flex flex-wrap items-center gap-3 mb-3">
                <div className="font-mono text-[15px] font-semibold text-gray-900">
                  {selected}
                </div>
                {detailTags.length > 0 && (
                  <div className="flex items-center gap-1 flex-wrap">
                    {detailTags.slice(0, 6).map((t) => (
                      <span
                        key={t}
                        className="font-mono text-[10px] px-1.5 py-0.5 rounded-full bg-gray-50 border border-gray-200 text-gray-600"
                      >
                        {t}
                      </span>
                    ))}
                  </div>
                )}
                <div className="flex-1" />
                {typeof detailEval?.eval_score === "number" && (
                  <div className="flex items-center gap-2">
                    <span className="text-[11px] text-gray-400 font-mono">
                      score
                    </span>
                    <ScorePill score={detailEval.eval_score} />
                    <TierBadge tier={scoreTier(detailEval.eval_score)} />
                  </div>
                )}
              </div>

              {/* Controls row */}
              <div className="flex flex-wrap items-center gap-3 mb-4 pb-3 border-b border-gray-100">
                <label className="text-[12px] text-gray-600 flex items-center gap-2">
                  n_runs
                  <select
                    value={nRuns}
                    onChange={(e) => setNRuns(Number(e.target.value))}
                    disabled={running}
                    className="h-7 rounded-md border border-gray-200 text-[12px] font-mono px-1 focus:outline-none focus:border-[#4361ee]"
                  >
                    <option value={1}>1</option>
                    <option value={3}>3</option>
                    <option value={5}>5</option>
                  </select>
                </label>
                <label className="flex items-center gap-2 text-[12px] text-gray-600 select-none">
                  <input
                    type="checkbox"
                    checked={sandbox}
                    onChange={(e) => setSandbox(e.target.checked)}
                    disabled={running}
                    className="accent-[#4361ee]"
                  />
                  sandbox
                  <span className="text-[11px] text-gray-400">(SWE-bench)</span>
                </label>
                <div className="flex-1" />
                <button
                  type="button"
                  disabled={running || !selected}
                  onClick={start}
                  className="bg-[#4361ee] text-white text-[13px] h-8 px-4 rounded-md hover:bg-[#3651dc] disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {running ? "Evaluating…" : "Run evaluation"}
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

              {/* Tabs */}
              <div className="flex items-center gap-1 mb-3 text-[12px] font-mono">
                {[
                  ["preview", "Preview"],
                  ["eval", "Evaluation"],
                ].map(([key, label]) => (
                  <button
                    key={key}
                    type="button"
                    onClick={() => setTab(key)}
                    className={`h-7 px-3 rounded-md border transition ${
                      tab === key
                        ? "border-[#4361ee] text-[#4361ee] bg-[#eef2ff]"
                        : "border-gray-200 text-gray-600 hover:bg-gray-50"
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>

              {tab === "preview" && (
                <div className="min-w-0">
                  {detailLoading && (
                    <div className="text-[12px] text-gray-400 py-6 text-center">
                      loading skill…
                    </div>
                  )}
                  {!detailLoading && detail?.error && (
                    <div className="text-[12px] text-red-600 font-mono">
                      {detail.error}
                    </div>
                  )}
                  {!detailLoading && detail && !detail.error && (
                    <>
                      <details className="mb-3 rounded-md border border-gray-200 bg-gray-50">
                        <summary className="cursor-pointer select-none text-[11px] font-mono text-gray-600 px-3 py-1.5 hover:text-gray-900">
                          frontmatter
                        </summary>
                        <pre
                          className="font-mono text-[11px] rounded-b-md p-3 overflow-x-auto whitespace-pre-wrap break-all"
                          style={{
                            background: "#1e1e2e",
                            color: "#cdd6f4",
                            lineHeight: 1.55,
                          }}
                        >
                          {JSON.stringify(
                            {
                              description: detail.description,
                              ...(detail.compatibility
                                ? { compatibility: detail.compatibility }
                                : {}),
                              metadata: detail.metadata || {},
                            },
                            null,
                            2
                          )}
                        </pre>
                      </details>
                      <div className="rounded-md border border-gray-100 bg-gray-50/50 p-4 max-h-[620px] overflow-auto">
                        <Prose text={detailBody || "(empty)"} />
                      </div>
                    </>
                  )}
                </div>
              )}

              {tab === "eval" && (
                <div className="space-y-4 min-w-0">
                  {/* Summary header */}
                  <div className="flex items-center gap-3 flex-wrap">
                    <div className="flex items-baseline gap-2">
                      <span className="text-[11px] text-gray-400 font-mono">
                        median
                      </span>
                      <span
                        className="font-mono tabular-nums text-[28px] font-semibold"
                        style={{
                          color:
                            typeof finalMedian === "number"
                              ? scoreColor(finalMedian).fg
                              : "#9ca3af",
                        }}
                      >
                        {typeof finalMedian === "number"
                          ? finalMedian.toFixed(2)
                          : "—"}
                      </span>
                      <span className="text-[11px] text-gray-400 font-mono">
                        / 10
                      </span>
                    </div>
                    <TierBadge tier={finalTier} />
                    {evalResult?.runs != null && (
                      <span className="text-[11px] text-gray-500 font-mono">
                        runs: {evalResult.runs}
                      </span>
                    )}
                    {evalResult?.tier && (
                      <span className="text-[11px] text-gray-500 font-mono">
                        eval_tier: {evalResult.tier}
                      </span>
                    )}
                  </div>

                  {/* Dimension bars */}
                  <div className="rounded-md border border-gray-100 bg-white p-4">
                    <DimensionBars scores={finalScores} />
                    {!finalScores && (
                      <div className="text-[12px] text-gray-400 text-center py-2">
                        No scored dimensions yet — press{" "}
                        <span className="font-mono">Run evaluation</span>.
                      </div>
                    )}
                  </div>

                  {/* Stream */}
                  <div>
                    <div className="flex items-center justify-between mb-2">
                      <div className="text-[12px] font-semibold text-gray-700">
                        Stream
                      </div>
                      <button
                        type="button"
                        onClick={() => setShowRaw((r) => !r)}
                        className={`h-7 px-2.5 rounded-md text-[11px] border font-mono ${
                          showRaw
                            ? "border-[#4361ee] text-[#4361ee] bg-[#eef2ff]"
                            : "border-gray-200 text-gray-600 hover:bg-gray-50"
                        }`}
                      >
                        raw log
                      </button>
                    </div>

                    {showRaw ? (
                      <SSELog events={events} />
                    ) : (
                      <div className="max-h-[360px] overflow-auto pr-1 rounded-md border border-gray-100 bg-gray-50/60 p-3">
                        {renderItems.length === 0 &&
                          !running &&
                          !evalResult &&
                          !evalError && (
                            <div className="text-[12px] text-gray-400 py-6 text-center">
                              press{" "}
                              <span className="font-mono">Run evaluation</span>{" "}
                              to stream live scores.
                            </div>
                          )}
                        {renderItems.map((it, i) => (
                          <StreamRow key={i} item={it} />
                        ))}
                        {evalError && (
                          <EventCard
                            accent="#dc2626"
                            label="error"
                            tone="error"
                          >
                            <pre className="whitespace-pre-wrap break-all font-mono text-[11px] text-red-700">
                              {evalError}
                            </pre>
                          </EventCard>
                        )}
                      </div>
                    )}
                  </div>

                  {/* Raw JSON */}
                  {evalResult && (
                    <details>
                      <summary className="text-[11px] text-gray-400 cursor-pointer hover:text-gray-600 font-mono">
                        raw result JSON
                      </summary>
                      <pre className="mt-2 p-3 bg-gray-50 border border-gray-200 rounded-md text-[11px] font-mono text-gray-600 whitespace-pre-wrap break-all max-h-[260px] overflow-auto">
                        {JSON.stringify(evalResult, null, 2)}
                      </pre>
                    </details>
                  )}
                </div>
              )}
            </>
          )}
        </section>
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
  if (item.kind === "milestone") {
    const accent =
      item.tag === "eval"
        ? "#0f766e"
        : item.tag === "ok"
        ? "#15803d"
        : "#4361ee";
    return (
      <EventCard accent={accent} label={item.tag} tag={item.tag}>
        <div className="font-mono text-[12px] text-gray-800 break-all whitespace-pre-wrap">
          {item.text}
        </div>
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
  return (
    <div className="flex gap-2 text-[12px] text-gray-600 leading-relaxed my-1">
      <span className="text-gray-300 font-mono">›</span>
      <span className="break-all whitespace-pre-wrap">
        {item.tag && (
          <span className="text-gray-400 font-mono mr-1.5">[{item.tag}]</span>
        )}
        {item.text}
      </span>
    </div>
  );
}
