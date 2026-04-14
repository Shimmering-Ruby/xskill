import React, { useMemo, useRef, useState } from "react";
import { submitTrajectory, sseStream } from "../api";
import SSELog from "../components/SSELog";

const MD_TEMPLATE = `# Trajectory: fix django migration error

## Intent
Resolve a failing Django migration on the staging database.

## Steps
1. Inspect \`manage.py showmigrations\` output
2. Identify the conflicting migration
3. Manually edit \`django_migrations\` table and re-run

## Outcome
Migrations applied cleanly on staging.
`;

const METADATA_PLACEHOLDER = `{
  "success": true,
  "tags": ["django", "migration"],
  "intent": "fix django migration error"
}`;

export default function PageIngest({ desc }) {
  // form state
  const [format, setFormat] = useState("markdown");
  const [trajId, setTrajId] = useState("");
  const [content, setContent] = useState("");
  const [metadataText, setMetadataText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState(null);
  const [lastSubmit, setLastSubmit] = useState(null); // { traj_id, md_path, ... }

  // index-SSE state
  const [datasetDir, setDatasetDir] = useState("");
  const [concurrency, setConcurrency] = useState(10);
  const [indexing, setIndexing] = useState(false);
  const [events, setEvents] = useState([]);
  const [progressPct, setProgressPct] = useState(null);
  const [indexResult, setIndexResult] = useState(null);
  const [indexError, setIndexError] = useState(null);
  const abortRef = useRef(null);

  const fileInputRef = useRef(null);

  const metadataParsed = useMemo(() => {
    if (!metadataText.trim()) return { ok: true, value: null };
    try {
      const v = JSON.parse(metadataText);
      if (v && typeof v === "object" && !Array.isArray(v)) {
        return { ok: true, value: v };
      }
      return { ok: false, error: "metadata must be a JSON object" };
    } catch (e) {
      return { ok: false, error: String(e?.message || e) };
    }
  }, [metadataText]);

  const canSubmit =
    !submitting && content.trim().length > 0 && metadataParsed.ok;

  const onPickFile = () => {
    fileInputRef.current?.click();
  };
  const onFileChosen = async (e) => {
    const f = e.target.files?.[0];
    if (!f) return;
    const text = await f.text();
    setContent(text);
    const lower = f.name.toLowerCase();
    if (lower.endsWith(".json")) setFormat("json");
    else if (lower.endsWith(".md")) setFormat("markdown");
    // reset input so same-file reselect works
    e.target.value = "";
  };

  const onSubmit = async () => {
    setSubmitError(null);
    setSubmitting(true);
    try {
      const body = {
        content,
        format,
        metadata: metadataParsed.value || undefined,
      };
      if (trajId.trim()) body.traj_id = trajId.trim();
      const r = await submitTrajectory(body);
      setLastSubmit(r);
      try {
        if (r?.traj_id) localStorage.setItem("t2s:lastTrajId", r.traj_id);
        if (r?.md_path) localStorage.setItem("t2s:lastTrajPath", r.md_path);
      } catch {}
    } catch (e) {
      setSubmitError(String(e?.message || e));
    } finally {
      setSubmitting(false);
    }
  };

  const startIndex = async () => {
    setIndexing(true);
    setIndexError(null);
    setIndexResult(null);
    setEvents([]);
    setProgressPct(null);
    const ctrl = new AbortController();
    abortRef.current = ctrl;

    const body = { concurrency };
    if (datasetDir.trim()) body.path = datasetDir.trim();

    try {
      await sseStream("/api/v1/trajectories/index", body, {
        signal: ctrl.signal,
        onEvent: (type, data) => {
          setEvents((xs) => [...xs, { type, data, ts: Date.now() }]);
          if (type === "progress") {
            const pct =
              typeof data?.pct === "number"
                ? data.pct
                : typeof data?.percent === "number"
                ? data.percent
                : typeof data?.current === "number" &&
                  typeof data?.total === "number" &&
                  data.total > 0
                ? Math.round((data.current / data.total) * 100)
                : null;
            if (pct !== null) setProgressPct(pct);
          }
          if (type === "result") {
            setIndexResult(data || {});
          }
          if (type === "error") {
            setIndexError(data?.error || data?.message || "unknown error");
          }
        },
      });
    } catch (e) {
      if (!ctrl.signal.aborted) {
        setIndexError(String(e?.message || e));
      }
    } finally {
      setIndexing(false);
      abortRef.current = null;
    }
  };

  const stopIndex = () => {
    abortRef.current?.abort();
  };

  return (
    <div className="p-8 max-w-6xl mx-auto">
      <h1 className="text-[20px] font-semibold text-gray-900 mb-1">Ingest</h1>
      <h2 className="text-[13px] text-gray-500 mb-6">
        {desc || "Submit a trajectory and build the vector index."}
      </h2>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* LEFT — form */}
        <div className="bg-white rounded-lg border border-gray-200 p-5">
          <div className="text-[14px] font-semibold text-gray-800 mb-3">
            Submit trajectory
          </div>

          <div className="flex items-center gap-3 mb-3">
            <div className="flex items-center gap-2">
              <label className="text-[12px] text-gray-500">Format</label>
              <select
                value={format}
                onChange={(e) => setFormat(e.target.value)}
                className="h-8 rounded-md border border-gray-200 text-[13px] px-2 bg-white"
              >
                <option value="markdown">markdown</option>
                <option value="json">json</option>
              </select>
            </div>
            <div className="flex items-center gap-2 flex-1">
              <label className="text-[12px] text-gray-500">traj_id</label>
              <input
                type="text"
                value={trajId}
                onChange={(e) => setTrajId(e.target.value)}
                placeholder="(auto)"
                className="h-8 flex-1 rounded-md border border-gray-200 text-[13px] px-2"
              />
            </div>
            <button
              type="button"
              onClick={onPickFile}
              className="h-8 px-3 rounded-md border border-gray-200 text-[12px] text-gray-700 hover:bg-gray-50"
            >
              ⬆ Load file…
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept=".md,.json,.txt"
              className="hidden"
              onChange={onFileChosen}
            />
          </div>

          <label className="block text-[12px] text-gray-500 mb-1">
            Content
          </label>
          <textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            placeholder={MD_TEMPLATE}
            className="w-full h-60 font-mono text-[12px] rounded-md border border-gray-200 p-3 resize-none focus:outline-none focus:border-[#4361ee]"
          />

          <label className="block text-[12px] text-gray-500 mt-3 mb-1">
            Metadata (JSON, optional)
          </label>
          <textarea
            value={metadataText}
            onChange={(e) => setMetadataText(e.target.value)}
            placeholder={METADATA_PLACEHOLDER}
            className={`w-full h-24 font-mono text-[11px] rounded-md border p-3 resize-none focus:outline-none ${
              metadataParsed.ok
                ? "border-gray-200 focus:border-[#4361ee]"
                : "border-red-300 focus:border-red-500"
            }`}
          />
          {!metadataParsed.ok && (
            <div className="text-[11px] text-red-600 mt-1">
              {metadataParsed.error}
            </div>
          )}

          <div className="mt-4 flex items-center gap-3">
            <button
              type="button"
              disabled={!canSubmit}
              onClick={onSubmit}
              className="bg-[#4361ee] text-white text-[13px] h-8 px-4 rounded-md hover:bg-[#3651dc] disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {submitting ? "Submitting…" : "Submit"}
            </button>
            {submitError && (
              <span className="text-[12px] text-red-600 break-all">
                {submitError}
              </span>
            )}
            {lastSubmit && !submitError && (
              <span className="text-[12px] text-green-700">
                submitted as{" "}
                <span className="font-mono">{lastSubmit.traj_id}</span>
              </span>
            )}
          </div>
        </div>

        {/* RIGHT — activity */}
        <div className="bg-white rounded-lg border border-gray-200 p-5">
          <div className="flex items-center justify-between mb-3">
            <div className="text-[14px] font-semibold text-gray-800">
              Activity
            </div>
            {indexing ? (
              <button
                type="button"
                onClick={stopIndex}
                className="h-7 px-3 rounded-md border border-red-200 bg-red-50 text-red-700 text-[12px] hover:bg-red-100"
              >
                Stop
              </button>
            ) : null}
          </div>

          {lastSubmit && (
            <div className="mb-3 p-3 rounded-md border border-gray-200 bg-[#f8f9fb] flex items-center gap-3">
              <div className="flex-1 min-w-0">
                <div className="text-[12px] text-gray-500">Submitted</div>
                <div className="font-mono text-[12px] text-gray-800 truncate">
                  {lastSubmit.traj_id}
                </div>
                {lastSubmit.md_path && (
                  <div className="font-mono text-[10px] text-gray-400 truncate">
                    {lastSubmit.md_path}
                  </div>
                )}
              </div>
              <button
                type="button"
                disabled={indexing}
                onClick={startIndex}
                className="bg-[#4361ee] text-white text-[12px] h-7 px-3 rounded-md hover:bg-[#3651dc] disabled:opacity-50 disabled:cursor-not-allowed"
              >
                Build index
              </button>
            </div>
          )}

          <div className="flex items-center gap-2 mb-3">
            <input
              type="text"
              value={datasetDir}
              onChange={(e) => setDatasetDir(e.target.value)}
              placeholder="Index dataset dir (optional, server default if blank)"
              className="h-8 flex-1 rounded-md border border-gray-200 text-[12px] px-2 font-mono"
            />
            <label className="text-[11px] text-gray-500">concurrency</label>
            <input
              type="number"
              min={1}
              max={64}
              value={concurrency}
              onChange={(e) =>
                setConcurrency(Math.max(1, Number(e.target.value) || 1))
              }
              className="h-8 w-16 rounded-md border border-gray-200 text-[12px] px-2"
            />
            <button
              type="button"
              disabled={indexing}
              onClick={startIndex}
              className="bg-[#4361ee] text-white text-[12px] h-8 px-3 rounded-md hover:bg-[#3651dc] disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {indexing ? "Indexing…" : "Index"}
            </button>
          </div>

          {progressPct !== null && (
            <div className="mb-2">
              <div className="h-1.5 w-full rounded-full bg-gray-100 overflow-hidden">
                <div
                  className="h-full"
                  style={{
                    width: `${Math.min(100, Math.max(0, progressPct))}%`,
                    background: "#4361ee",
                    transition: "width 120ms linear",
                  }}
                />
              </div>
              <div className="text-[10px] text-gray-400 mt-1 font-mono">
                {progressPct}%
              </div>
            </div>
          )}

          <SSELog events={events} />

          {indexError && (
            <div className="mt-2 text-[12px] text-red-600 break-all">
              {indexError}
            </div>
          )}

          {indexResult && (
            <div className="mt-3 p-3 rounded-md border border-green-200 bg-green-50">
              <div className="text-[12px] font-semibold text-green-800 mb-1">
                Result
              </div>
              <div className="text-[12px] text-green-900 space-y-0.5 font-mono">
                {indexResult.status && (
                  <div>status: {indexResult.status}</div>
                )}
                {typeof indexResult.trajectories === "number" && (
                  <div>trajectories: {indexResult.trajectories}</div>
                )}
                {typeof indexResult.indexed === "number" && (
                  <div>indexed: {indexResult.indexed}</div>
                )}
                {typeof indexResult.skipped === "number" && (
                  <div>skipped: {indexResult.skipped}</div>
                )}
                {indexResult.dataset && (
                  <div className="truncate">dataset: {indexResult.dataset}</div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
