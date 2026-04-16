import React, { useEffect, useState } from "react";
import {
  getSkill,
  getSkillCanary,
  getSkillCandidates,
  getTrajectoryContent,
  getTrajectoryLogs,
} from "../api";

/* ─── shared helpers ────────────────────────────────────────── */

function Row({ k, v }) {
  if (v === undefined || v === null) return null;
  return (
    <div className="flex gap-2 text-[11px] text-gray-700 py-1 border-b border-gray-50">
      <span className="text-gray-400 w-28 shrink-0">{k}</span>
      <span className="font-mono text-[10px] break-all">{String(v)}</span>
    </div>
  );
}

function ScoreBar({ label, value, max = 10, color }) {
  const pct = value != null ? Math.min((value / max) * 100, 100) : 0;
  return (
    <div className="flex items-center gap-2 text-[11px]">
      <span className="w-[150px] text-right text-gray-500 shrink-0 truncate">
        {label}
      </span>
      <div className="flex-1 h-4 bg-gray-100 rounded overflow-hidden">
        {value != null && (
          <div
            className="h-full rounded transition-all"
            style={{ width: `${pct}%`, background: color }}
          />
        )}
      </div>
      <span className="w-10 text-right font-mono text-gray-700">
        {value != null ? value.toFixed(1) : "\u2014"}
      </span>
    </div>
  );
}

function StatusPill({ status, large }) {
  const styles = {
    done: "bg-emerald-100 text-emerald-700 border-emerald-200",
    meta_done: "bg-blue-100 text-blue-700 border-blue-200",
    pending: "bg-amber-100 text-amber-700 border-amber-200",
    active: "bg-emerald-100 text-emerald-700 border-emerald-200",
    frozen: "bg-sky-100 text-sky-700 border-sky-200",
  };
  const cls =
    styles[status] || "bg-gray-100 text-gray-600 border-gray-200";
  return (
    <span
      className={`inline-block font-mono border rounded ${
        large
          ? "text-[13px] px-3 py-1"
          : "text-[10px] px-1.5 py-0.5"
      } ${cls}`}
    >
      {status || "unknown"}
    </span>
  );
}

function uxScoreColor(score) {
  if (typeof score !== "number") return "#6b7280";
  if (score >= 8) return "#15803d";
  if (score >= 6) return "#b45309";
  if (score >= 4) return "#d97706";
  return "#dc2626";
}

/* ─── tab bar (same style as Canary.jsx) ────────────────────── */
function TabBar({ tabs, active, onChange }) {
  return (
    <div className="flex border-b border-gray-200 px-4 gap-4">
      {tabs.map((t) => (
        <button
          key={t.id}
          onClick={() => onChange(t.id)}
          className={`py-2 text-[12px] border-b-2 transition-colors ${
            active === t.id
              ? "border-[#4361ee] text-[#4361ee] font-medium"
              : "border-transparent text-gray-500 hover:text-gray-700"
          }`}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

/* ================================================================
   TRAJECTORY INSPECTOR
   ================================================================ */

function TrajInspector({ traj }) {
  const [tab, setTab] = useState("overview");
  const [fileData, setFileData] = useState(null);
  const [fileLoading, setFileLoading] = useState(false);
  const [fileErr, setFileErr] = useState(null);
  const [logData, setLogData] = useState(null);
  const [logLoading, setLogLoading] = useState(false);
  const [logErr, setLogErr] = useState(null);

  const tabs = [
    { id: "overview", label: "Overview" },
    { id: "logs", label: "Logs" },
    { id: "raw", label: "Raw" },
    { id: "meta", label: "Meta" },
  ];

  const filePath = [traj.dir_path || traj.dir || "", traj.filename]
    .filter(Boolean)
    .join("/");

  // Reset when traj changes
  useEffect(() => {
    setTab("overview");
    setFileData(null);
    setFileErr(null);
    setLogData(null);
    setLogErr(null);
  }, [traj?.traj_id, traj?.filename]);

  // Fetch process logs when switching to logs tab
  useEffect(() => {
    if (tab !== "logs") return;
    if (logData) return;
    if (!traj?.filename) return;

    setLogLoading(true);
    setLogErr(null);
    getTrajectoryLogs(traj.filename, traj.dir_path || traj.dir || "")
      .then((data) => setLogData(data?.logs || []))
      .catch((e) => setLogErr(String(e?.message || e)))
      .finally(() => setLogLoading(false));
  }, [tab, traj?.filename, traj?.dir_path, traj?.dir, logData]);

  // Fetch file content + meta when switching to raw or meta tab
  useEffect(() => {
    if (tab !== "raw" && tab !== "meta") return;
    if (fileData) return;
    if (!filePath) return;

    setFileLoading(true);
    setFileErr(null);
    getTrajectoryContent(filePath)
      .then((data) => setFileData(data))
      .catch((e) => setFileErr(String(e?.message || e)))
      .finally(() => setFileLoading(false));
  }, [tab, filePath, fileData]);

  const meta = fileData?.meta || null;
  const metaLoading = fileLoading;
  const metaErr = fileErr;

  return (
    <div className="flex flex-col h-full">
      {/* header */}
      <div className="px-4 py-3 border-b border-gray-200 bg-white">
        <div className="font-mono text-[14px] font-semibold text-gray-900 truncate">
          {traj.filename || traj.traj_id || "Trajectory"}
        </div>
        <div className="text-[11px] text-gray-400 truncate mt-0.5">
          {traj.dir_path || traj.dir_label || ""}
        </div>
      </div>

      <TabBar tabs={tabs} active={tab} onChange={setTab} />

      <div className="flex-1 overflow-auto p-4">
        {/* ── Overview tab ── */}
        {tab === "overview" && (
          <div className="space-y-4">
            {/* status pill large */}
            <div className="flex items-center gap-3">
              <StatusPill status={traj.status} large />
              {traj.process_action && (
                <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-purple-50 text-purple-700 border border-purple-200">
                  {traj.process_action}
                </span>
              )}
            </div>

            {/* key-value table */}
            <div className="rounded-md border border-gray-100 bg-gray-50/50 p-3">
              <Row k="filename" v={traj.filename} />
              <Row k="directory" v={traj.dir_path || traj.dir_label} />
              <Row k="traj_id" v={traj.traj_id} />
              <Row k="discovered_at" v={traj.discovered_at} />
              <Row k="indexed_at" v={traj.indexed_at} />
              <Row k="updated_at" v={traj.updated_at} />
              <Row k="retry_count" v={traj.retry_count} />
            </div>

            {/* skill generated */}
            {traj.skill_used && (
              <div className="rounded-md border border-gray-100 bg-gray-50/50 p-3">
                <div className="text-[11px] text-gray-400 uppercase tracking-wider mb-1">
                  Skill Generated
                </div>
                <span className="font-mono text-[12px] text-[#4361ee] cursor-pointer hover:underline">
                  {traj.skill_used}
                </span>
                {traj.canary_side && (
                  <span
                    className={`ml-2 text-[9px] px-1 py-0.5 rounded ${
                      traj.canary_side === "staging"
                        ? "bg-amber-50 text-amber-700"
                        : "bg-emerald-50 text-emerald-700"
                    }`}
                  >
                    {traj.canary_side}
                  </span>
                )}
              </div>
            )}

            {/* UX Score */}
            {traj.ux_score != null && (
              <div className="rounded-md border border-gray-100 bg-gray-50/50 p-3">
                <div className="text-[11px] text-gray-400 uppercase tracking-wider mb-1">
                  UX Score
                </div>
                <span
                  className="text-[28px] font-bold font-mono tabular-nums"
                  style={{ color: uxScoreColor(traj.ux_score) }}
                >
                  {typeof traj.ux_score === "number"
                    ? traj.ux_score.toFixed(1)
                    : traj.ux_score}
                </span>
              </div>
            )}

            {/* Error message */}
            {traj.error && (
              <div className="rounded-md border border-red-200 bg-red-50 p-3">
                <div className="text-[11px] text-red-400 uppercase tracking-wider mb-1">
                  Error
                </div>
                <pre className="text-[11px] font-mono text-red-700 whitespace-pre-wrap break-words">
                  {traj.error}
                </pre>
              </div>
            )}
          </div>
        )}

        {/* ── Logs tab ── */}
        {tab === "logs" && (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <div className="text-[11px] text-gray-400 uppercase tracking-wider">
                Process Logs
              </div>
              <button
                onClick={() => { setLogData(null); setLogErr(null); }}
                className="text-[10px] px-2 py-0.5 rounded border border-gray-200 text-gray-500 hover:bg-gray-50"
                title="Refresh logs"
              >
                Refresh
              </button>
            </div>
            {logLoading && <div className="text-[12px] text-gray-400">Loading...</div>}
            {logErr && <div className="text-[12px] text-red-500">{logErr}</div>}
            {logData && logData.length === 0 && (
              <div className="text-[12px] text-gray-400 py-6 text-center">
                No process logs available for this trajectory.
                <br />
                <span className="text-[10px]">Logs are generated during skill processing.</span>
              </div>
            )}
            {logData && logData.length > 0 && (
              <div className="rounded-md border border-gray-200 overflow-hidden bg-[#1e1e2e]">
                {logData.map((entry, i) => {
                  const tagColors = {
                    step: "text-blue-400",
                    ok: "text-emerald-400",
                    error: "text-red-400",
                    git: "text-purple-400",
                    eval: "text-amber-400",
                    decision: "text-cyan-400",
                    tool: "text-indigo-400",
                    info: "text-gray-400",
                  };
                  const tagIcons = {
                    step: ">",
                    ok: "+",
                    error: "!",
                    git: "G",
                    eval: "E",
                    decision: "D",
                    tool: "T",
                    info: " ",
                  };
                  const tagCls = tagColors[entry.tag] || "text-gray-400";
                  const icon = tagIcons[entry.tag] || " ";
                  return (
                    <div
                      key={i}
                      className={`px-3 py-1 text-[11px] font-mono border-b border-gray-800/50 flex gap-2 items-start ${
                        entry.tag === "error" ? "bg-red-950/30" : ""
                      }`}
                    >
                      <span className="text-gray-600 shrink-0 w-[130px] text-[10px]">
                        {entry.t?.slice(11) || ""}
                      </span>
                      <span className={`shrink-0 w-4 text-center font-bold ${tagCls}`}>
                        {icon}
                      </span>
                      <span className={`shrink-0 w-16 ${tagCls}`}>
                        [{entry.tag}]
                      </span>
                      <span className="text-[#cdd6f4] break-all flex-1">
                        {entry.msg}
                      </span>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}

        {/* ── Raw tab ── */}
        {tab === "raw" && (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <div className="text-[11px] text-gray-400 uppercase tracking-wider">
                Trajectory File
              </div>
              <span className="text-[10px] font-mono text-gray-400 truncate">{filePath}</span>
            </div>
            {fileLoading && <div className="text-[12px] text-gray-400">Loading...</div>}
            {fileErr && <div className="text-[12px] text-red-500">{fileErr}</div>}
            {fileData?.content && (
              <pre className="rounded-md border border-gray-200 bg-[#1e1e2e] p-4 text-[11px] text-[#cdd6f4] font-mono whitespace-pre-wrap break-words max-h-[600px] overflow-auto leading-relaxed">
                {fileData.content}
              </pre>
            )}
          </div>
        )}

        {/* ── Meta tab ── */}
        {tab === "meta" && (
          <div className="space-y-3">
            <div className="text-[11px] text-gray-400 uppercase tracking-wider">
              Trajectory Metadata (.meta)
            </div>
            {!traj.has_meta && (
              <div className="text-[12px] text-gray-400 py-6 text-center">
                No .meta file for this trajectory (has_meta=false)
              </div>
            )}
            {traj.has_meta && metaLoading && (
              <div className="text-[12px] text-gray-400 py-6 text-center">
                loading...
              </div>
            )}
            {traj.has_meta && metaErr && !meta && (
              <div className="text-[12px] text-gray-400 py-6 text-center">
                {metaErr}
              </div>
            )}
            {meta && (
              <pre className="rounded-md border border-gray-700 bg-[#1e1e2e] text-[#cdd6f4] p-4 text-[11px] font-mono whitespace-pre-wrap break-words overflow-auto max-h-[calc(100vh-18rem)]">
                {typeof meta === "string" ? meta : JSON.stringify(meta, null, 2)}
              </pre>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/* ================================================================
   SKILL INSPECTOR
   ================================================================ */

function SkillInspector({ name }) {
  const [tab, setTab] = useState("overview");
  const [detail, setDetail] = useState(null);
  const [canary, setCanary] = useState(null);
  const [candidates, setCandidates] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState(null);

  const tabs = [
    { id: "overview", label: "Overview" },
    { id: "skillmd", label: "SKILL.md" },
    { id: "candidates", label: "Candidates" },
    { id: "canary", label: "Canary" },
  ];

  useEffect(() => {
    setLoading(true);
    setErr(null);
    setTab("overview");
    Promise.all([
      getSkill(name),
      getSkillCanary(name).catch(() => null),
      getSkillCandidates(name).catch(() => null),
    ])
      .then(([d, c, cand]) => {
        setDetail(d);
        setCanary(c);
        setCandidates(cand);
      })
      .catch((e) => setErr(String(e?.message || e)))
      .finally(() => setLoading(false));
  }, [name]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full text-[12px] text-gray-400">
        loading skill...
      </div>
    );
  }

  if (err) {
    return (
      <div className="flex items-center justify-center h-full text-[12px] text-red-600 font-mono">
        {err}
      </div>
    );
  }

  const meta = detail?.metadata || {};
  const evalData = meta.eval || detail?.eval || {};
  const tags = meta.tags || detail?.abstract?.tags || [];
  const version = meta.version || detail?.version || "\u2014";
  const description = meta.description || detail?.description || detail?.abstract?.description || "";
  const sourceTrajs = meta.source_trajs || detail?.source_trajs || [];
  const status = meta.status || detail?.status || "active";
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

  const cands = candidates?.candidates || [];

  return (
    <div className="flex flex-col h-full">
      {/* header */}
      <div className="px-4 py-3 border-b border-gray-200 bg-white">
        <div className="flex items-center gap-2">
          <span className="font-mono text-[15px] font-semibold text-gray-900 truncate">
            {name}
          </span>
          <StatusPill status={status} />
          <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-gray-100 text-gray-600 border border-gray-200">
            v{version}
          </span>
        </div>
        {description && (
          <p className="text-[11px] text-gray-500 mt-1 line-clamp-2">
            {description}
          </p>
        )}
      </div>

      <TabBar tabs={tabs} active={tab} onChange={setTab} />

      <div className="flex-1 overflow-auto p-4">
        {/* ── Overview tab ── */}
        {tab === "overview" && (
          <div className="space-y-4">
            {/* staging banner */}
            {hasStaging && (
              <div className="flex items-center gap-2 px-3 py-2 rounded-md bg-amber-50 border border-amber-200">
                <span className="text-amber-700 text-[12px] font-semibold">
                  Staging Active
                </span>
                <span className="text-[10px] font-mono text-amber-600 ml-auto">
                  {canary?.staging_sha?.slice(0, 8) || "\u2014"}
                </span>
              </div>
            )}

            {/* tags */}
            {tags.length > 0 && (
              <div className="flex flex-wrap gap-1">
                {tags.map((t) => (
                  <span
                    key={t}
                    className="font-mono text-[10px] px-2 py-0.5 rounded-full bg-gray-50 border border-gray-200 text-gray-600"
                  >
                    {t}
                  </span>
                ))}
              </div>
            )}

            {/* source trajectories */}
            {Array.isArray(sourceTrajs) && sourceTrajs.length > 0 && (
              <div className="rounded-md border border-gray-100 bg-gray-50/50 p-3">
                <div className="text-[11px] text-gray-400 uppercase tracking-wider mb-2">
                  Source Trajectories ({sourceTrajs.length})
                </div>
                <div className="space-y-1 max-h-32 overflow-auto">
                  {sourceTrajs.map((t, i) => (
                    <div
                      key={i}
                      className="text-[10px] font-mono text-gray-600 truncate"
                    >
                      {typeof t === "string" ? t : t.filename || t.traj_id || JSON.stringify(t)}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* eval dimension scores */}
            {evalData.scores && (
              <div className="rounded-md border border-gray-100 bg-white p-3 space-y-1.5">
                <div className="text-[11px] text-gray-400 uppercase tracking-wider mb-2">
                  Eval Dimensions
                </div>
                {Object.entries(evalData.scores).map(([dim, val]) => (
                  <ScoreBar
                    key={dim}
                    label={dim}
                    value={typeof val === "number" ? val : null}
                    color="#4361ee"
                  />
                ))}
              </div>
            )}

            {/* eval metadata */}
            <div className="rounded-md border border-gray-100 bg-gray-50/50 p-3">
              <Row k="version" v={version} />
              <Row
                k="eval_score"
                v={
                  typeof evalData.eval_score === "number"
                    ? evalData.eval_score.toFixed(2)
                    : "\u2014"
                }
              />
              <Row k="eval_tier" v={evalData.tier || "\u2014"} />
              <Row
                k="eval_runs"
                v={evalData.runs != null ? evalData.runs : "\u2014"}
              />
              <Row
                k="last_updated"
                v={meta.last_updated || detail?.last_updated || "\u2014"}
              />
            </div>
          </div>
        )}

        {/* ── SKILL.md tab ── */}
        {tab === "skillmd" && (
          <div className="space-y-3">
            <div className="text-[11px] text-gray-400 uppercase tracking-wider">
              Full SKILL.md
            </div>
            {skillMdRaw ? (
              <pre className="rounded-md border border-gray-700 bg-[#1e1e2e] text-[#cdd6f4] p-4 text-[10px] font-mono leading-relaxed whitespace-pre-wrap break-words overflow-auto max-h-[calc(100vh-16rem)]">
                {skillMdRaw}
              </pre>
            ) : (
              <div className="text-[12px] text-gray-400 py-6 text-center">
                (no SKILL.md content)
              </div>
            )}
          </div>
        )}

        {/* ── Candidates tab ── */}
        {tab === "candidates" && (
          <div className="space-y-1">
            <div className="text-[11px] text-gray-400 uppercase tracking-wider mb-2">
              Candidates ({cands.length})
            </div>
            {cands.length === 0 ? (
              <div className="text-[12px] text-gray-400 py-6 text-center">
                No candidates
              </div>
            ) : (
              <div className="rounded-md border border-gray-100 overflow-hidden">
                {cands.map((c, i) => {
                  const supporters = c.supporting_trajs?.length ?? 0;
                  const promoted = c.promoted;
                  return (
                    <div
                      key={i}
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
                        <span className="flex-1 text-gray-800 break-all">
                          {c.pattern}
                        </span>
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
                })}
              </div>
            )}
          </div>
        )}

        {/* ── Canary tab ── */}
        {tab === "canary" && (
          <div className="space-y-4">
            {!canary ? (
              <div className="text-[12px] text-gray-400 py-6 text-center">
                No canary data available.
              </div>
            ) : (
              <>
                {/* UX score comparison */}
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

                {/* config info */}
                <div className="space-y-1 text-[11px]">
                  <div className="text-gray-400 uppercase tracking-wider">
                    Config
                  </div>
                  <Row k="main sha" v={canary.main_sha?.slice(0, 8)} />
                  <Row k="staging sha" v={canary.staging_sha?.slice(0, 8)} />
                  <Row
                    k="staging since"
                    v={canary.staging_created_at?.slice(0, 10)}
                  />
                  <Row k="probability" v={canary.config?.probability} />
                  <Row k="min samples" v={canary.config?.min_samples} />
                  <Row k="max days" v={canary.config?.max_days_hold} />
                </div>

                {/* staging body preview */}
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
      </div>
    </div>
  );
}

/* ================================================================
   MAIN EXPORT
   ================================================================ */

export default function PageInspector({ selectedTraj, selectedSkill }) {
  // Trajectory takes priority
  if (selectedTraj) {
    return <TrajInspector key={selectedTraj.traj_id || selectedTraj.filename} traj={selectedTraj} />;
  }

  if (selectedSkill) {
    return <SkillInspector key={selectedSkill} name={selectedSkill} />;
  }

  // Empty state
  return (
    <div className="flex flex-col items-center justify-center h-full text-center px-6">
      <div className="text-gray-300 text-4xl mb-3">~</div>
      <p className="text-[13px] text-gray-500 font-medium">
        Select a trajectory or skill to inspect
      </p>
      <p className="text-[11px] text-gray-400 mt-1 max-w-xs">
        Click an item in the Trajectories or Skills panel to view its details
        here.
      </p>
    </div>
  );
}
