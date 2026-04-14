import React, { useState } from "react";
import { searchTrajectories } from "../api";
import SimilarityPill from "../components/SimilarityPill";

const FILTERS = [
  { id: "all", label: "All" },
  { id: "success", label: "Success" },
  { id: "failure", label: "Failure" },
];

function FilterPill({ active, onClick, children }) {
  const cls = active
    ? "bg-[#eef2ff] text-[#4361ee] border border-[#c7d2fe]"
    : "bg-white text-gray-600 border border-gray-200 hover:bg-gray-50";
  return (
    <button
      type="button"
      onClick={onClick}
      className={`h-8 px-3 rounded-md text-[12px] ${cls}`}
    >
      {children}
    </button>
  );
}

function truncate(s, n = 180) {
  if (!s) return "";
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}

function SuccessBadge({ meta }) {
  // Accept a range of shapes: meta.success (bool), meta.status, meta.result
  let ok = null;
  if (typeof meta?.success === "boolean") ok = meta.success;
  else if (meta?.status === "success") ok = true;
  else if (meta?.status === "failure" || meta?.status === "failed") ok = false;
  else if (meta?.result === "success") ok = true;
  else if (meta?.result === "failure") ok = false;
  if (ok === null) return null;
  return (
    <span
      className={`inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full border ${
        ok
          ? "bg-green-50 text-green-700 border-green-200"
          : "bg-red-50 text-red-700 border-red-200"
      }`}
    >
      <span
        className={`w-1.5 h-1.5 rounded-full ${
          ok ? "bg-green-500" : "bg-red-500"
        }`}
      />
      {ok ? "success" : "failure"}
    </span>
  );
}

function TagChip({ text }) {
  return (
    <span className="inline-flex items-center text-[10px] px-2 py-0.5 rounded-full bg-gray-50 text-gray-600 border border-gray-200 font-mono">
      {text}
    </span>
  );
}

function ResultCard({ item, expanded, onToggle }) {
  const meta = item.meta || {};
  const intent = meta.intent || meta.summary || meta.task || "";
  const tags = Array.isArray(meta.tags) ? meta.tags.slice(0, 5) : [];

  return (
    <div
      onClick={onToggle}
      className="bg-white rounded-lg border border-gray-200 p-4 mb-2 hover:border-[#c7d2fe] transition-colors cursor-pointer"
    >
      <div className="flex items-center justify-between gap-3">
        <div className="font-mono text-[13px] text-gray-800 truncate">
          {item.traj_id}
        </div>
        <SimilarityPill score={item.similarity} />
      </div>
      {intent && (
        <div className="text-[12px] text-gray-500 mt-1 truncate">
          {truncate(intent, 200)}
        </div>
      )}
      <div className="flex items-center gap-2 mt-2 flex-wrap">
        <SuccessBadge meta={meta} />
        {tags.map((t, i) => (
          <TagChip key={i} text={t} />
        ))}
      </div>

      {expanded && (
        <div className="mt-3 border-t border-gray-100 pt-3" onClick={(e) => e.stopPropagation()}>
          {item.md_path && (
            <div className="flex items-center gap-2 mb-2 text-[11px]">
              <span className="text-gray-400">md_path</span>
              <span className="font-mono text-gray-700 truncate">
                {item.md_path}
              </span>
              <a
                href="#"
                onClick={(e) => e.preventDefault()}
                className="text-[#4361ee] hover:underline"
              >
                open
              </a>
            </div>
          )}
          <pre className="font-mono text-[11px] bg-gray-50 rounded p-3 overflow-auto max-h-[320px] text-gray-700 border border-gray-100">
{JSON.stringify(meta, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}

export default function PageSearch({ desc }) {
  const [query, setQuery] = useState("");
  const [topK, setTopK] = useState(10);
  const [filter, setFilter] = useState("all");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [results, setResults] = useState(null); // null = not run, [] = empty
  const [expandedId, setExpandedId] = useState(null);

  const doSearch = async () => {
    if (!query.trim()) return;
    setLoading(true);
    setError(null);
    setExpandedId(null);
    try {
      const r = await searchTrajectories({
        query: query.trim(),
        top_k: Math.max(1, Math.min(50, Number(topK) || 10)),
        filter,
      });
      setResults(r?.results || []);
    } catch (e) {
      setError(String(e?.message || e));
      setResults([]);
    } finally {
      setLoading(false);
    }
  };

  const clear = () => {
    setQuery("");
    setResults(null);
    setError(null);
    setExpandedId(null);
  };

  const onKey = (e) => {
    if (e.key === "Enter") doSearch();
  };

  return (
    <div className="p-8 max-w-5xl mx-auto">
      <h1 className="text-[20px] font-semibold text-gray-900 mb-1">Search</h1>
      <h2 className="text-[13px] text-gray-500 mb-6">
        {desc || "Semantic search over indexed trajectories."}
      </h2>

      <div className="bg-white rounded-lg border border-gray-200 p-5 mb-4">
        <div className="flex items-center gap-2 mb-3">
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onKey}
            placeholder="e.g. 'fix django migration error'"
            className="w-full h-9 px-3 rounded-md border border-gray-200 text-[13px] focus:outline-none focus:border-[#4361ee]"
          />
          <input
            type="number"
            min={1}
            max={50}
            value={topK}
            onChange={(e) => setTopK(e.target.value)}
            className="h-9 w-16 rounded-md border border-gray-200 text-[13px] px-2"
            title="top_k"
          />
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {FILTERS.map((f) => (
            <FilterPill
              key={f.id}
              active={filter === f.id}
              onClick={() => setFilter(f.id)}
            >
              {f.label}
            </FilterPill>
          ))}
          <div className="flex-1" />
          <button
            type="button"
            onClick={clear}
            className="h-8 px-3 rounded-md text-[12px] text-gray-600 hover:bg-gray-50"
          >
            Clear
          </button>
          <button
            type="button"
            onClick={doSearch}
            disabled={loading || !query.trim()}
            className="bg-[#4361ee] text-white text-[13px] h-8 px-4 rounded-md hover:bg-[#3651dc] disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {loading ? "Searching…" : "Search"}
          </button>
        </div>
      </div>

      {loading && (
        <div className="flex items-center gap-2 text-[12px] text-gray-500 px-1 py-2">
          <span
            className="inline-block w-3 h-3 rounded-full border-2 border-gray-300 border-t-[#4361ee] animate-spin"
            aria-hidden
          />
          Searching…
        </div>
      )}

      {error && !loading && (
        <div className="p-3 rounded-md bg-red-50 border border-red-200 text-[12px] text-red-700 break-all mb-2">
          {error}
        </div>
      )}

      {!loading && results !== null && results.length === 0 && !error && (
        <div className="text-center text-[13px] text-gray-400 py-10">
          No results
        </div>
      )}

      {!loading &&
        results &&
        results.length > 0 &&
        results.map((item) => (
          <ResultCard
            key={item.traj_id}
            item={item}
            expanded={expandedId === item.traj_id}
            onToggle={() =>
              setExpandedId((cur) =>
                cur === item.traj_id ? null : item.traj_id
              )
            }
          />
        ))}
    </div>
  );
}
