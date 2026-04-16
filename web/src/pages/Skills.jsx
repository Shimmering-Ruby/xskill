import React, { useCallback, useEffect, useMemo, useState } from "react";
import { listSkills, getSkill, getSkillCanary } from "../api";

/* ─── score helpers ──────────────────────────────────────────── */
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

/* ─── ScoreBar (reused from Canary pattern) ──────────────────── */
function ScoreBar({ label, value, max = 10, color }) {
  const pct = value != null ? Math.min((value / max) * 100, 100) : 0;
  return (
    <div className="flex items-center gap-2 text-[11px]">
      <span className="w-14 text-right text-gray-500 shrink-0">{label}</span>
      <div className="flex-1 h-4 bg-gray-100 rounded overflow-hidden">
        {value != null && (
          <div
            className="h-full rounded transition-all"
            style={{ width: `${pct}%`, background: color }}
          />
        )}
      </div>
      <span className="w-10 text-right font-mono text-gray-700">
        {value != null ? value.toFixed(1) : "—"}
      </span>
    </div>
  );
}

/* ─── metadata row ───────────────────────────────────────────── */
function Row({ k, v }) {
  if (v === undefined || v === null) return null;
  return (
    <div className="flex gap-2 text-[11px] text-gray-700 py-1 border-b border-gray-50">
      <span className="text-gray-400 w-28 shrink-0">{k}</span>
      <span className="font-mono text-[10px]">{String(v)}</span>
    </div>
  );
}

/* ─── detail panel ───────────────────────────────────────────── */
function DetailPanel({ name, onClose }) {
  const [detail, setDetail] = useState(null);
  const [canary, setCanary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState("overview");

  useEffect(() => {
    setLoading(true);
    setTab("overview");
    Promise.all([
      getSkill(name),
      getSkillCanary(name).catch(() => null),
    ])
      .then(([d, c]) => {
        setDetail(d);
        setCanary(c);
      })
      .catch(() => setDetail({ error: "Failed to load skill" }))
      .finally(() => setLoading(false));
  }, [name]);

  if (loading) {
    return (
      <div className="text-[12px] text-gray-400 py-12 text-center">
        loading skill...
      </div>
    );
  }

  if (detail?.error) {
    return (
      <div className="text-[12px] text-red-600 font-mono py-6 text-center">
        {detail.error}
      </div>
    );
  }

  const meta = detail?.metadata || {};
  const evalData = meta.eval || detail?.eval || {};
  const tags = meta.tags || detail?.abstract?.tags || [];
  const version = meta.version || detail?.version || "—";
  const sourceTrajs = meta.source_trajs?.length ?? detail?.source_trajs?.length ?? "—";
  const lastUpdated = meta.last_updated || detail?.last_updated || "—";
  const skillMdRaw = detail?.skill_md_raw || detail?.skill_md_body || detail?.skill_md || "";

  const hasStaging = canary?.has_staging;
  const mainScores = canary?.ux_scores?.main || [];
  const stgScores = canary?.ux_scores?.staging || [];
  const mainAvg =
    mainScores.length > 0
      ? mainScores.reduce((a, s) => a + s.score, 0) / mainScores.length
      : null;
  const stgAvg =
    stgScores.length > 0
      ? stgScores.reduce((a, s) => a + s.score, 0) / stgScores.length
      : null;

  const tabs = [
    { id: "overview", label: "Overview" },
    { id: "skillmd", label: "SKILL.md" },
    { id: "canary", label: "Canary" },
  ];

  return (
    <>
      {/* header */}
      <div className="flex items-center gap-2 mb-3 pb-3 border-b border-gray-100">
        <div className="font-mono text-[15px] font-semibold text-gray-900 truncate flex-1">
          {name}
        </div>
        <button
          onClick={onClose}
          className="text-gray-400 hover:text-gray-700 text-lg leading-none px-1"
        >
          &times;
        </button>
      </div>

      {/* tabs */}
      <div className="flex items-center gap-1 mb-3 text-[12px] font-mono">
        {tabs.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setTab(t.id)}
            className={`h-7 px-3 rounded-md border transition ${
              tab === t.id
                ? "border-[#4361ee] text-[#4361ee] bg-[#eef2ff]"
                : "border-gray-200 text-gray-600 hover:bg-gray-50"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Overview tab */}
      {tab === "overview" && (
        <div className="space-y-4">
          {/* staging banner */}
          {hasStaging && (
            <div className="flex items-center gap-2 px-3 py-2 rounded-md bg-amber-50 border border-amber-200">
              <span className="text-amber-700 text-[12px] font-semibold">
                Staging Active
              </span>
              <span className="text-[10px] font-mono text-amber-600 ml-auto">
                {canary?.staging_sha?.slice(0, 8) || "—"}
              </span>
            </div>
          )}

          {/* metadata table */}
          <div className="rounded-md border border-gray-100 bg-gray-50/50 p-3">
            <Row k="version" v={version} />
            <Row
              k="tags"
              v={tags.length > 0 ? tags.join(", ") : "—"}
            />
            <Row k="source_trajs" v={sourceTrajs} />
            <Row k="last_updated" v={lastUpdated} />
            <Row
              k="eval_score"
              v={
                typeof evalData.eval_score === "number"
                  ? evalData.eval_score.toFixed(2)
                  : "—"
              }
            />
            <Row k="eval_tier" v={evalData.tier || "—"} />
            <Row
              k="eval_runs"
              v={evalData.runs != null ? evalData.runs : "—"}
            />
          </div>

          {/* eval dimension scores */}
          {evalData.scores && (
            <div className="rounded-md border border-gray-100 bg-white p-3 space-y-1.5">
              <div className="text-[11px] text-gray-400 uppercase tracking-wider mb-2">
                Eval Dimensions
              </div>
              {Object.entries(evalData.scores).map(([dim, val]) => (
                <div key={dim} className="flex items-center gap-3 text-[11px]">
                  <span className="text-gray-600 w-[150px] truncate shrink-0">
                    {dim}
                  </span>
                  <div className="flex-1 h-2 rounded-full bg-gray-100 overflow-hidden">
                    <div
                      className="h-full"
                      style={{
                        width: `${Math.max(0, Math.min(100, ((val ?? 0) / 10) * 100))}%`,
                        background: "#4361ee",
                        transition: "width 200ms linear",
                      }}
                    />
                  </div>
                  <span className="font-mono text-[10px] text-gray-700 tabular-nums w-8 text-right">
                    {typeof val === "number" ? val.toFixed(1) : "—"}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* SKILL.md tab */}
      {tab === "skillmd" && (
        <div className="rounded-md border border-gray-200 bg-gray-50 overflow-auto max-h-[calc(100vh-16rem)]">
          <pre
            className="font-mono text-[10px] leading-relaxed p-4 whitespace-pre-wrap break-words text-gray-700"
            style={{ tabSize: 2 }}
          >
            {skillMdRaw || "(no SKILL.md content)"}
          </pre>
        </div>
      )}

      {/* Canary tab */}
      {tab === "canary" && (
        <div className="space-y-4">
          {!canary ? (
            <div className="text-[12px] text-gray-400 py-6 text-center">
              No canary data available.
            </div>
          ) : (
            <>
              <div className="space-y-1.5">
                <div className="text-[11px] text-gray-400 uppercase tracking-wider">
                  UX Score Comparison
                </div>
                <ScoreBar
                  label={`main (${mainScores.length})`}
                  value={mainAvg}
                  color="#10b981"
                />
                <ScoreBar
                  label={`staging (${stgScores.length})`}
                  value={stgAvg}
                  color="#f59e0b"
                />
              </div>

              <div className="space-y-1 text-[11px]">
                <div className="text-gray-400 uppercase tracking-wider">Info</div>
                <Row k="main sha" v={canary.main_sha?.slice(0, 8)} />
                <Row k="staging sha" v={canary.staging_sha?.slice(0, 8)} />
                <Row k="staging since" v={canary.staging_created_at?.slice(0, 10)} />
                <Row k="probability" v={canary.config?.probability} />
                <Row k="min samples" v={canary.config?.min_samples} />
                <Row k="max days" v={canary.config?.max_days_hold} />
              </div>

              {hasStaging && canary.staging_body_preview && (
                <div className="space-y-2">
                  <div className="text-[11px] text-gray-400 uppercase tracking-wider">
                    Body Preview (staging)
                  </div>
                  <pre className="text-[10px] bg-gray-50 border border-gray-200 rounded p-2 max-h-40 overflow-auto whitespace-pre-wrap text-gray-700">
                    {canary.staging_body_preview}
                  </pre>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </>
  );
}

/* ─── main page ──────────────────────────────────────────────── */
export default function PageSkills({ desc }) {
  const [skills, setSkills] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filterQ, setFilterQ] = useState("");
  const [selected, setSelected] = useState(null);

  const refresh = useCallback(() => {
    setLoading(true);
    listSkills()
      .then((r) => setSkills(r.skills || []))
      .catch(() => setSkills([]))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const filtered = useMemo(() => {
    const q = filterQ.trim().toLowerCase();
    if (!q) return skills;
    return skills.filter((s) => s.name.toLowerCase().includes(q));
  }, [skills, filterQ]);

  return (
    <div className="p-8 max-w-[1400px] mx-auto">
      <h1 className="text-[20px] font-semibold text-gray-900 mb-1">Skills</h1>
      <h2 className="text-[13px] text-gray-500 mb-6">
        {desc || "Browse and inspect extracted skills."}
      </h2>

      <div className="grid lg:grid-cols-[320px_1fr] gap-4">
        {/* LEFT — skill list */}
        <aside className="bg-white rounded-lg border border-gray-200 p-4 sticky top-4 max-h-[calc(100vh-7rem)] overflow-y-auto">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <div className="text-[14px] font-semibold text-gray-800">
                Skills
              </div>
              <span className="text-[11px] font-mono text-gray-500 bg-gray-100 border border-gray-200 rounded-full px-2 py-0.5">
                {skills.length}
              </span>
            </div>
            <button
              onClick={refresh}
              className="text-[11px] px-2.5 py-1 rounded-md border border-gray-200 text-gray-600 hover:bg-gray-50"
            >
              Refresh
            </button>
          </div>

          <input
            type="text"
            value={filterQ}
            onChange={(e) => setFilterQ(e.target.value)}
            placeholder="filter by name..."
            className="w-full h-8 px-2 mb-3 rounded-md border border-gray-200 text-[12px] font-mono focus:outline-none focus:border-[#4361ee]"
          />

          {loading && (
            <div className="text-[12px] text-gray-400 py-4 text-center">
              loading...
            </div>
          )}
          {!loading && filtered.length === 0 && (
            <div className="text-[12px] text-gray-400 py-4 text-center">
              {skills.length === 0 ? "(no skills yet)" : "(no match)"}
            </div>
          )}

          <div className="space-y-1">
            {filtered.map((s) => {
              const isSel = s.name === selected;
              const tags = s.tags || [];
              return (
                <button
                  key={s.name}
                  type="button"
                  onClick={() => setSelected(s.name)}
                  className={`w-full text-left rounded-md border px-2.5 py-2 transition ${
                    isSel
                      ? "bg-[#eef2ff] border-[#c7d2fe]"
                      : "bg-white border-gray-200 hover:bg-gray-50"
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-[12.5px] text-gray-800 truncate flex-1">
                      {s.name}
                    </span>
                    {s.frozen && (
                      <span className="text-[9px] font-mono uppercase px-1 py-0.5 rounded bg-sky-50 border border-sky-200 text-sky-700">
                        frozen
                      </span>
                    )}
                    <ScorePill score={s.eval_score} compact />
                  </div>
                  {tags.length > 0 && (
                    <div className="flex items-center gap-1 mt-1 flex-wrap">
                      {tags.slice(0, 4).map((t) => (
                        <span
                          key={t}
                          className="font-mono text-[9px] px-1 py-0.5 rounded-full bg-gray-50 border border-gray-200 text-gray-500"
                        >
                          {t}
                        </span>
                      ))}
                    </div>
                  )}
                </button>
              );
            })}
          </div>
        </aside>

        {/* RIGHT — detail */}
        <section className="bg-white rounded-lg border border-gray-200 p-5 min-w-0">
          {!selected && (
            <div className="text-[13px] text-gray-400 py-12 text-center">
              Select a skill on the left to view details.
            </div>
          )}

          {selected && (
            <DetailPanel
              key={selected}
              name={selected}
              onClose={() => setSelected(null)}
            />
          )}
        </section>
      </div>
    </div>
  );
}
