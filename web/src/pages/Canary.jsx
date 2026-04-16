import React, { useCallback, useEffect, useState } from "react";
import {
  getCanaryOverview,
  getSkillCanary,
  getSkillCandidates,
} from "../api";

/* ─── tiny bar chart (pure CSS) ───────────────────────────────── */
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

/* ─── candidate row ───────────────────────────────────────────── */
function CandidateRow({ c }) {
  const supporters = c.supporting_trajs?.length ?? 0;
  const promoted = c.promoted;
  return (
    <div
      className={`px-3 py-2 text-[12px] border-b border-gray-100 ${
        promoted ? "bg-emerald-50/40" : ""
      }`}
    >
      <div className="flex items-start gap-2">
        <span
          className={`shrink-0 mt-0.5 inline-block text-[10px] font-mono px-1.5 py-0.5 rounded border ${
            c.type === "warning"
              ? "bg-amber-50 text-amber-700 border-amber-200"
              : c.type === "decision_branch"
              ? "bg-purple-50 text-purple-700 border-purple-200"
              : "bg-blue-50 text-blue-700 border-blue-200"
          }`}
        >
          {c.type || "step"}
        </span>
        <span className="flex-1 text-gray-800 break-all">{c.pattern}</span>
        <span
          className={`shrink-0 font-mono text-[10px] px-1.5 py-0.5 rounded ${
            promoted
              ? "bg-emerald-100 text-emerald-700"
              : supporters >= 3
              ? "bg-green-100 text-green-700"
              : "bg-gray-100 text-gray-600"
          }`}
        >
          {promoted ? "promoted" : `${supporters} traj`}
        </span>
      </div>
      {c.attach_to && (
        <div className="ml-16 mt-0.5 text-[10px] text-gray-400">
          attach_to: {c.attach_to}
        </div>
      )}
    </div>
  );
}

/* ─── UX score timeline row ───────────────────────────────────── */
function UxScoreRow({ s }) {
  const isStaging = s.side === "staging";
  return (
    <div className="flex items-center gap-2 text-[11px] py-1 px-3 border-b border-gray-50">
      <span className="w-12 shrink-0 text-gray-400 font-mono">
        {s.scored_at?.slice(5, 16).replace("T", " ") || "—"}
      </span>
      <span
        className={`w-14 shrink-0 text-center text-[10px] font-mono px-1 py-0.5 rounded ${
          isStaging
            ? "bg-amber-50 text-amber-700"
            : "bg-emerald-50 text-emerald-700"
        }`}
      >
        {s.side}
      </span>
      <span className="w-6 shrink-0 text-right font-bold text-gray-800">
        {s.score}
      </span>
      <span className="flex-1 text-gray-500 truncate">{s.reasons}</span>
      <span className="shrink-0 text-[10px] text-gray-400 font-mono">
        {s.traj_id}
      </span>
    </div>
  );
}

/* ─── detail panel for one skill ──────────────────────────────── */
function SkillDetail({ name, onClose }) {
  const [canary, setCanary] = useState(null);
  const [candidates, setCandidates] = useState(null);
  const [tab, setTab] = useState("canary");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    Promise.all([getSkillCanary(name), getSkillCandidates(name)])
      .then(([c, cand]) => {
        setCanary(c);
        setCandidates(cand);
      })
      .finally(() => setLoading(false));
  }, [name]);

  if (loading) return <div className="p-6 text-gray-400 text-sm">loading...</div>;

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
  const allScores = [...mainScores, ...stgScores].sort(
    (a, b) => (a.scored_at || "").localeCompare(b.scored_at || "")
  );

  const cands = candidates?.candidates || [];

  const tabs = [
    { id: "canary", label: "Canary" },
    { id: "buffer", label: `Buffer (${cands.length})` },
    { id: "scores", label: `UX Scores (${allScores.length})` },
  ];

  return (
    <div className="border-l border-gray-200 bg-white flex flex-col w-[520px] shrink-0">
      {/* header */}
      <div className="px-4 py-3 border-b border-gray-200 flex items-center gap-2">
        <button
          onClick={onClose}
          className="text-gray-400 hover:text-gray-700 text-lg leading-none"
        >
          &times;
        </button>
        <span className="font-semibold text-sm text-gray-800 truncate">
          {name}
        </span>
        {canary?.has_staging && (
          <span className="ml-auto text-[10px] bg-amber-100 text-amber-800 px-2 py-0.5 rounded-full font-medium">
            staging active
          </span>
        )}
      </div>

      {/* tabs */}
      <div className="flex border-b border-gray-200 px-4 gap-4">
        {tabs.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`py-2 text-[12px] border-b-2 transition-colors ${
              tab === t.id
                ? "border-[#4361ee] text-[#4361ee] font-medium"
                : "border-transparent text-gray-500 hover:text-gray-700"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* tab body */}
      <div className="flex-1 overflow-auto">
        {tab === "canary" && (
          <div className="p-4 space-y-4">
            {/* score comparison */}
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

            {/* meta info */}
            <div className="space-y-1 text-[11px]">
              <div className="text-gray-400 uppercase tracking-wider">Info</div>
              <Row k="main sha" v={canary?.main_sha?.slice(0, 8)} />
              <Row k="staging sha" v={canary?.staging_sha?.slice(0, 8)} />
              <Row k="staging since" v={canary?.staging_created_at?.slice(0, 10)} />
              <Row k="probability" v={canary?.config?.probability} />
              <Row k="min samples" v={canary?.config?.min_samples} />
              <Row k="max days" v={canary?.config?.max_days_hold} />
            </div>

            {/* body previews */}
            {canary?.has_staging && (
              <div className="space-y-2">
                <div className="text-[11px] text-gray-400 uppercase tracking-wider">
                  Body Preview (staging)
                </div>
                <pre className="text-[10px] bg-gray-50 border border-gray-200 rounded p-2 max-h-40 overflow-auto whitespace-pre-wrap text-gray-700">
                  {canary.staging_body_preview || "(empty)"}
                </pre>
              </div>
            )}
          </div>
        )}

        {tab === "buffer" && (
          <div>
            {cands.length === 0 ? (
              <div className="p-6 text-center text-gray-400 text-sm">
                No candidates
              </div>
            ) : (
              cands.map((c, i) => <CandidateRow key={i} c={c} />)
            )}
          </div>
        )}

        {tab === "scores" && (
          <div>
            {allScores.length === 0 ? (
              <div className="p-6 text-center text-gray-400 text-sm">
                No UX scores yet
              </div>
            ) : (
              allScores.map((s, i) => <UxScoreRow key={i} s={s} />)
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function Row({ k, v }) {
  if (v === undefined || v === null) return null;
  return (
    <div className="flex gap-2 text-gray-700">
      <span className="text-gray-400 w-20 shrink-0">{k}</span>
      <span className="font-mono">{String(v)}</span>
    </div>
  );
}

/* ─── main page ───────────────────────────────────────────────── */
export default function PageCanary({ desc }) {
  const [skills, setSkills] = useState([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState(null);
  const [selected, setSelected] = useState(null);

  const refresh = useCallback(() => {
    setLoading(true);
    setErr(null);
    getCanaryOverview()
      .then((d) => setSkills(d.skills || []))
      .catch((e) => setErr(String(e?.message || e)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return (
    <div className="flex h-full">
      {/* list */}
      <div className="flex-1 flex flex-col overflow-hidden">
        <div className="px-6 py-4 border-b border-gray-200 bg-white flex items-center gap-3">
          <div>
            <h2 className="text-base font-semibold text-gray-900">
              Canary Dashboard
            </h2>
            {desc && <p className="text-xs text-gray-500 mt-0.5">{desc}</p>}
          </div>
          <button
            onClick={refresh}
            className="ml-auto text-xs px-3 py-1 rounded border border-gray-300 text-gray-600 hover:bg-gray-50"
          >
            Refresh
          </button>
        </div>

        <div className="flex-1 overflow-auto px-6 py-4 bg-[#f8f9fb]">
          {loading && <p className="text-sm text-gray-400">loading...</p>}
          {err && <p className="text-sm text-red-600">{err}</p>}

          {!loading && skills.length === 0 && (
            <p className="text-sm text-gray-400 mt-10 text-center">
              No skills found
            </p>
          )}

          <div className="space-y-2">
            {skills.map((s) => (
              <button
                key={s.skill_name}
                onClick={() => setSelected(s.skill_name)}
                className={`w-full text-left rounded-lg border p-3 transition-colors ${
                  selected === s.skill_name
                    ? "border-[#4361ee] bg-[#eef2ff]"
                    : "border-gray-200 bg-white hover:border-gray-300"
                }`}
              >
                <div className="flex items-center gap-2">
                  <span className="font-medium text-sm text-gray-800">
                    {s.skill_name}
                  </span>
                  {s.has_staging && (
                    <span className="text-[10px] bg-amber-100 text-amber-800 px-1.5 py-0.5 rounded-full">
                      staging
                    </span>
                  )}
                </div>
                <div className="flex gap-4 mt-1 text-[11px] text-gray-500">
                  <span>
                    main: {s.main_avg != null ? s.main_avg.toFixed(1) : "—"} ({s.main_n})
                  </span>
                  <span>
                    staging: {s.staging_avg != null ? s.staging_avg.toFixed(1) : "—"} ({s.staging_n})
                  </span>
                </div>
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* detail panel */}
      {selected && (
        <SkillDetail name={selected} onClose={() => setSelected(null)} />
      )}
    </div>
  );
}
