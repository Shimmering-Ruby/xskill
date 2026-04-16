import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getWatcherStatus, listTrajectories } from "../api";

/* ─── status config ─────────────────────────────────────────── */
/* Pipeline order: discovered → meta_extracting → meta_done → indexed → processing → outcomes */
const ALL_STATUSES = [
  "discovered",
  "meta_extracting",
  "meta_done",
  "indexed",
  "processing",
  "in_candidate",
  "in_skill",
  "staged",
  "staging_queued",
  "rejected",
  "filtered",
  "error",
];

const STATUS_PILL = {
  discovered:      "bg-gray-100 text-gray-600 border-gray-200",
  meta_extracting: "bg-blue-100 text-blue-600 border-blue-200 animate-pulse",
  meta_done:       "bg-blue-100 text-blue-700 border-blue-200",
  indexed:         "bg-indigo-100 text-indigo-700 border-indigo-200",
  processing:      "bg-purple-100 text-purple-600 border-purple-200 animate-pulse",
  in_candidate:    "bg-amber-100 text-amber-700 border-amber-200",
  in_skill:        "bg-emerald-100 text-emerald-700 border-emerald-200",
  staged:          "bg-amber-50 text-amber-700 border-amber-400 ring-1 ring-amber-300",
  rejected:        "bg-red-100 text-red-600 border-red-200",
  filtered:        "bg-gray-100 text-gray-400 border-gray-200 line-through",
  error:           "bg-red-100 text-red-600 border-red-200",
};

function pillClass(status) {
  return STATUS_PILL[status] || "bg-gray-100 text-gray-500 border-gray-200";
}

/* tiny colored dot for stats bar */
const STATUS_DOT = {
  discovered: "bg-gray-400",
  meta_extracting: "bg-blue-400",
  meta_done: "bg-blue-500",
  indexed: "bg-indigo-500",
  processing: "bg-purple-500",
  in_candidate: "bg-amber-500",
  in_skill: "bg-emerald-500",
  staged: "bg-amber-400",
  rejected: "bg-red-500",
  filtered: "bg-gray-300",
  error: "bg-red-600",
};

function uxColor(score) {
  if (score == null) return "text-gray-400";
  if (score >= 8) return "text-emerald-600";
  if (score >= 6) return "text-blue-600";
  if (score >= 4) return "text-amber-600";
  return "text-red-600";
}

/* ─── watcher indicator ─────────────────────────────────────── */
function WatcherIndicator({ watcher }) {
  const running = watcher?.running ?? false;
  return (
    <div className="px-3 py-1.5 border-b border-gray-200 flex items-center gap-1.5 text-[11px] text-gray-500">
      <span
        className={`inline-block w-1.5 h-1.5 rounded-full ${
          running ? "bg-emerald-500" : "bg-red-400"
        }`}
      />
      <span className="font-medium text-gray-700">
        Watcher {running ? "running" : "stopped"}
      </span>
    </div>
  );
}

/* ─── stats bar ─────────────────────────────────────────────── */
function StatsBar({ trajs }) {
  const counts = useMemo(() => {
    const m = {};
    for (const t of trajs) {
      const s = t.status || "unknown";
      m[s] = (m[s] || 0) + 1;
    }
    return m;
  }, [trajs]);

  return (
    <div className="px-3 py-1 border-b border-gray-200 flex items-center gap-2 text-[10px] text-gray-500 flex-wrap">
      <span className="font-mono text-gray-700 font-medium">{trajs.length}</span>
      <span>total</span>
      <span className="text-gray-300">|</span>
      {Object.entries(counts).map(([status, count]) => (
        <span key={status} className="flex items-center gap-0.5">
          <span className={`inline-block w-1.5 h-1.5 rounded-full ${STATUS_DOT[status] || "bg-gray-400"}`} />
          <span className="font-mono">{count}</span>
        </span>
      ))}
    </div>
  );
}

/* ─── search + filter ───────────────────────────────────────── */
function SearchFilter({ search, onSearch, statusFilter, onStatusFilter, statusCounts }) {
  return (
    <div className="px-3 py-1.5 border-b border-gray-200 space-y-1.5">
      <input
        type="text"
        value={search}
        onChange={(e) => onSearch(e.target.value)}
        placeholder="Search filename, dir, skill..."
        className="w-full text-[11px] border border-gray-300 rounded px-2 py-0.5 h-7 focus:outline-none focus:border-blue-400 focus:ring-1 focus:ring-blue-300/40"
      />
      <div className="flex gap-1 flex-wrap">
        {["All", ...ALL_STATUSES].map((f) => {
          const active = statusFilter === f;
          const count = f === "All" ? null : statusCounts[f] || 0;
          return (
            <button
              key={f}
              onClick={() => onStatusFilter(f)}
              className={`text-[10px] px-1.5 py-0.5 rounded-full border transition-colors leading-tight ${
                active
                  ? "border-blue-500 bg-blue-50 text-blue-600 font-medium"
                  : "border-gray-200 text-gray-500 hover:border-gray-300"
              }`}
            >
              {f}
              {count != null && count > 0 && (
                <span className="ml-0.5 font-mono">{count}</span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}

/* ─── trajectory card ───────────────────────────────────────── */
function TrajCard({ t, isSelected, onSelect }) {
  const ux = t.ux_score;
  const skill = t.skill_used || t.skill_name || "";

  return (
    <button
      type="button"
      onClick={() => onSelect(t)}
      className={`w-full text-left px-3 py-1.5 border-b border-gray-100 hover:bg-gray-50/70 transition-colors cursor-pointer ${
        isSelected ? "border-l-2 border-l-blue-500 bg-blue-50/30" : "border-l-2 border-l-transparent"
      }`}
      style={{ minHeight: 48 }}
    >
      {/* row 1: filename + status pill */}
      <div className="flex items-center gap-1.5">
        <span className="font-mono text-[11px] text-gray-800 truncate flex-1" title={t.filename}>
          {t.filename}
        </span>
        <span
          className={`shrink-0 text-[9px] font-mono px-1.5 py-0.5 rounded border leading-none ${pillClass(
            t.status
          )}`}
        >
          {t.status || "unknown"}
        </span>
      </div>

      {/* row 2: dir_label + ux_score + skill */}
      <div className="flex items-center gap-1.5 mt-0.5 text-[10px]">
        <span className="text-gray-500 truncate" title={t.dir_path || t.dir_label}>
          {t.dir_label || truncDir(t.dir_path)}
        </span>
        {ux != null && (
          <span className={`font-mono font-medium ${uxColor(ux)}`}>
            ux:{ux}
          </span>
        )}
        {skill && (
          <>
            <span className="text-gray-300">&rarr;</span>
            <span className="font-mono text-gray-600 truncate" title={skill}>
              {skill}
            </span>
          </>
        )}
      </div>
    </button>
  );
}

function truncDir(p) {
  if (!p) return "";
  const parts = p.replace(/\/$/, "").split("/");
  if (parts.length <= 2) return p;
  return ".../" + parts.slice(-2).join("/");
}

/* ─── main component ────────────────────────────────────────── */
export default function TrajectoryPanel({ onSelect, selected, className }) {
  const [trajs, setTrajs] = useState([]);
  const [watcher, setWatcher] = useState(null);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("All");
  const mountedRef = useRef(true);
  const trajCountRef = useRef(0);

  // fetch trajectories — only update state if count changed
  const fetchTrajs = useCallback(() => {
    listTrajectories()
      .then((d) => {
        if (!mountedRef.current) return;
        const list = d.trajectories || d || [];
        if (list.length !== trajCountRef.current) {
          trajCountRef.current = list.length;
          setTrajs(list);
        }
      })
      .catch(() => {}); // silent in sidebar
  }, []);

  // fetch watcher status
  const fetchWatcher = useCallback(() => {
    getWatcherStatus()
      .then((w) => {
        if (mountedRef.current) setWatcher(w);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    fetchTrajs();
    fetchWatcher();

    const trajTimer = setInterval(fetchTrajs, 5000);
    const watcherTimer = setInterval(fetchWatcher, 5000);

    return () => {
      mountedRef.current = false;
      clearInterval(trajTimer);
      clearInterval(watcherTimer);
    };
  }, [fetchTrajs, fetchWatcher]);

  // status counts
  const statusCounts = useMemo(() => {
    const m = {};
    for (const t of trajs) {
      const s = t.status || "unknown";
      m[s] = (m[s] || 0) + 1;
    }
    return m;
  }, [trajs]);

  // filtered list
  const filtered = useMemo(() => {
    let list = Array.isArray(trajs) ? trajs : [];

    if (statusFilter !== "All") {
      list = list.filter((t) => t.status === statusFilter);
    }

    if (search.trim()) {
      const q = search.toLowerCase();
      list = list.filter(
        (t) =>
          (t.filename || "").toLowerCase().includes(q) ||
          (t.dir_label || "").toLowerCase().includes(q) ||
          (t.dir_path || "").toLowerCase().includes(q) ||
          (t.skill_used || "").toLowerCase().includes(q) ||
          (t.skill_name || "").toLowerCase().includes(q)
      );
    }

    // newest first
    list = [...list].sort((a, b) =>
      (b.discovered_at || "").localeCompare(a.discovered_at || "")
    );

    return list;
  }, [trajs, statusFilter, search]);

  const selectedId = selected?.traj_id || selected?.filename;

  return (
    <div className={`flex flex-col h-full bg-white ${className || ""}`}>
      {/* watcher indicator */}
      <WatcherIndicator watcher={watcher} />

      {/* stats bar */}
      <StatsBar trajs={trajs} />

      {/* search + filter */}
      <SearchFilter
        search={search}
        onSearch={setSearch}
        statusFilter={statusFilter}
        onStatusFilter={setStatusFilter}
        statusCounts={statusCounts}
      />

      {/* scrollable card list */}
      <div className="flex-1 overflow-y-auto">
        {filtered.length === 0 && (
          <p className="px-3 py-6 text-[11px] text-gray-400 text-center">
            {trajs.length === 0 ? "No trajectories yet" : "No match"}
          </p>
        )}
        {filtered.map((t, i) => (
          <TrajCard
            key={t.traj_id || t.filename || i}
            t={t}
            isSelected={(t.traj_id || t.filename) === selectedId}
            onSelect={onSelect}
          />
        ))}
      </div>
    </div>
  );
}
