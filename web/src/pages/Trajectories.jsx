import React, { useCallback, useEffect, useMemo, useState, useRef } from "react";
import { getWatcherStatus, listRegistryDirs, listTrajectories } from "../api";

/* ─── status pill colors ─────────────────────────────────────── */
const STATUS_STYLE = {
  done: "bg-emerald-100 text-emerald-700 border-emerald-200",
  meta_done: "bg-blue-100 text-blue-700 border-blue-200",
  pending: "bg-amber-100 text-amber-700 border-amber-200 animate-pulse",
};

function statusStyle(s) {
  return STATUS_STYLE[s] || "bg-gray-100 text-gray-600 border-gray-200";
}

/* ─── watcher status bar ─────────────────────────────────────── */
function WatcherBar({ watcher, dirCount }) {
  const running = watcher?.running ?? false;
  const stats = watcher?.stats || {};
  const lastPoll = watcher?.last_poll_at;

  return (
    <div className="px-4 py-2 bg-white border-b border-gray-200 flex items-center gap-4 text-[11px] text-gray-600 flex-wrap">
      {/* running indicator */}
      <div className="flex items-center gap-1.5">
        <span
          className={`inline-block w-2 h-2 rounded-full ${
            running ? "bg-emerald-500" : "bg-red-400"
          }`}
        />
        <span className="font-medium text-gray-800">
          Watcher {running ? "running" : "stopped"}
        </span>
      </div>

      {/* last poll */}
      {lastPoll && (
        <span className="text-gray-400">
          last poll{" "}
          <span className="font-mono text-gray-500">
            {lastPoll.slice(11, 19)}
          </span>
        </span>
      )}

      {/* stats */}
      {stats.polls != null && <Stat label="polls" value={stats.polls} />}
      {stats.new_trajs != null && <Stat label="new_trajs" value={stats.new_trajs} />}
      {stats.skills_generated != null && (
        <Stat label="skills" value={stats.skills_generated} />
      )}
      {stats.scores != null && <Stat label="scores" value={stats.scores} />}

      {/* registered dirs */}
      <span className="ml-auto text-gray-400">
        <span className="font-mono text-gray-500">{dirCount}</span> registered
        dir{dirCount !== 1 ? "s" : ""}
      </span>
    </div>
  );
}

function Stat({ label, value }) {
  return (
    <span className="text-gray-400">
      {label}{" "}
      <span className="font-mono text-gray-700 font-medium">{value}</span>
    </span>
  );
}

/* ─── filter bar ─────────────────────────────────────────────── */
const FILTER_OPTIONS = ["All", "done", "pending", "meta_done"];

function FilterBar({ search, onSearch, statusFilter, onStatusFilter, total, filtered }) {
  return (
    <div className="px-4 py-2 bg-white border-b border-gray-200 flex items-center gap-3 flex-wrap">
      {/* text search */}
      <input
        type="text"
        value={search}
        onChange={(e) => onSearch(e.target.value)}
        placeholder="Search filename, dir, skill..."
        className="text-xs border border-gray-300 rounded px-2 py-1 w-56 focus:outline-none focus:border-[#4361ee] focus:ring-1 focus:ring-[#4361ee]/30"
      />

      {/* status pills */}
      <div className="flex gap-1">
        {FILTER_OPTIONS.map((f) => {
          const active = statusFilter === f;
          return (
            <button
              key={f}
              onClick={() => onStatusFilter(f)}
              className={`text-[11px] px-2 py-0.5 rounded-full border transition-colors ${
                active
                  ? "border-[#4361ee] bg-[#eef2ff] text-[#4361ee] font-medium"
                  : "border-gray-200 text-gray-500 hover:border-gray-300"
              }`}
            >
              {f === "All" ? "All" : f}
            </button>
          );
        })}
      </div>

      {/* count badge */}
      <span className="ml-auto text-[11px] text-gray-400">
        {filtered === total ? (
          <span>
            <span className="font-mono text-gray-600">{total}</span> trajectories
          </span>
        ) : (
          <span>
            <span className="font-mono text-gray-600">{filtered}</span> /{" "}
            <span className="font-mono">{total}</span> trajectories
          </span>
        )}
      </span>
    </div>
  );
}

/* ─── trajectory row ─────────────────────────────────────────── */
function TrajRow({ t }) {
  const ts = t.discovered_at;
  const displayTime = ts
    ? ts.slice(5, 16).replace("T", " ")
    : "";

  return (
    <div className="flex items-center gap-2 px-4 py-1.5 text-[12px] border-b border-gray-100 hover:bg-gray-50/60">
      {/* filename */}
      <span className="w-56 shrink-0 font-mono text-gray-800 truncate" title={t.filename}>
        {t.filename}
      </span>

      {/* dir */}
      <span className="w-36 shrink-0 text-gray-500 truncate" title={t.dir_path || t.dir_label}>
        {t.dir_label || truncDir(t.dir_path)}
      </span>

      {/* status pill */}
      <span
        className={`shrink-0 inline-block text-[10px] font-mono px-1.5 py-0.5 rounded border ${statusStyle(
          t.status
        )}`}
      >
        {t.status || "unknown"}
      </span>

      {/* skill_used + canary badge */}
      <span className="flex-1 truncate text-gray-600">
        {t.skill_used && (
          <>
            <span className="font-mono text-[11px]">{t.skill_used}</span>
            {t.canary_side && (
              <span
                className={`ml-1 text-[9px] px-1 py-0.5 rounded ${
                  t.canary_side === "staging"
                    ? "bg-amber-50 text-amber-700"
                    : "bg-emerald-50 text-emerald-700"
                }`}
              >
                {t.canary_side}
              </span>
            )}
          </>
        )}
      </span>

      {/* timestamp */}
      <span className="w-24 shrink-0 text-right text-gray-400 font-mono text-[10px]">
        {displayTime}
      </span>
    </div>
  );
}

function truncDir(p) {
  if (!p) return "";
  const parts = p.replace(/\/$/, "").split("/");
  if (parts.length <= 3) return p;
  return ".../" + parts.slice(-2).join("/");
}

/* ─── empty state ────────────────────────────────────────────── */
function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center py-20 text-center">
      <div className="text-gray-300 text-4xl mb-3">~</div>
      <p className="text-sm text-gray-500 font-medium">No trajectories found</p>
      <p className="text-xs text-gray-400 mt-1 max-w-xs">
        Register a directory with the watcher to start discovering trajectories
        automatically, or submit one via the Ingest tab.
      </p>
    </div>
  );
}

/* ─── main page ──────────────────────────────────────────────── */
export default function PageTrajectories({ desc }) {
  const [trajs, setTrajs] = useState([]);
  const [watcher, setWatcher] = useState(null);
  const [dirCount, setDirCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState(null);

  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("All");

  // Refs to avoid stale closure in intervals
  const mountedRef = useRef(true);

  // ── fetch trajectories ──
  const fetchTrajs = useCallback(() => {
    listTrajectories()
      .then((d) => {
        if (!mountedRef.current) return;
        setTrajs(d.trajectories || d || []);
        setErr(null);
      })
      .catch((e) => {
        if (!mountedRef.current) return;
        setErr(String(e?.message || e));
      })
      .finally(() => {
        if (mountedRef.current) setLoading(false);
      });
  }, []);

  // ── fetch watcher + dirs ──
  const fetchWatcher = useCallback(() => {
    Promise.all([getWatcherStatus(), listRegistryDirs()])
      .then(([w, dirs]) => {
        if (!mountedRef.current) return;
        setWatcher(w);
        const count = Array.isArray(dirs?.dirs) ? dirs.dirs.length : Array.isArray(dirs) ? dirs.length : 0;
        setDirCount(count);
      })
      .catch(() => {}); // silent — secondary info
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    fetchTrajs();
    fetchWatcher();

    const trajTimer = setInterval(fetchTrajs, 5000);
    const watcherTimer = setInterval(fetchWatcher, 3000);

    return () => {
      mountedRef.current = false;
      clearInterval(trajTimer);
      clearInterval(watcherTimer);
    };
  }, [fetchTrajs, fetchWatcher]);

  // ── derived: filter + sort ──
  const filtered = useMemo(() => {
    let list = Array.isArray(trajs) ? trajs : [];

    // status filter
    if (statusFilter !== "All") {
      list = list.filter((t) => t.status === statusFilter);
    }

    // text search
    if (search.trim()) {
      const q = search.toLowerCase();
      list = list.filter(
        (t) =>
          (t.filename || "").toLowerCase().includes(q) ||
          (t.dir_label || "").toLowerCase().includes(q) ||
          (t.dir_path || "").toLowerCase().includes(q) ||
          (t.skill_used || "").toLowerCase().includes(q)
      );
    }

    // sort by discovered_at descending
    list = [...list].sort((a, b) =>
      (b.discovered_at || "").localeCompare(a.discovered_at || "")
    );

    return list;
  }, [trajs, statusFilter, search]);

  const totalCount = Array.isArray(trajs) ? trajs.length : 0;

  return (
    <div className="flex flex-col h-full">
      {/* header */}
      <div className="px-6 py-4 border-b border-gray-200 bg-white flex items-center gap-3">
        <div>
          <h2 className="text-base font-semibold text-gray-900">Trajectories</h2>
          {desc && <p className="text-xs text-gray-500 mt-0.5">{desc}</p>}
        </div>
        <button
          onClick={() => {
            setLoading(true);
            fetchTrajs();
            fetchWatcher();
          }}
          className="ml-auto text-xs px-3 py-1 rounded border border-gray-300 text-gray-600 hover:bg-gray-50"
        >
          Refresh
        </button>
      </div>

      {/* watcher bar */}
      <WatcherBar watcher={watcher} dirCount={dirCount} />

      {/* filter bar */}
      <FilterBar
        search={search}
        onSearch={setSearch}
        statusFilter={statusFilter}
        onStatusFilter={setStatusFilter}
        total={totalCount}
        filtered={filtered.length}
      />

      {/* table header */}
      <div className="flex items-center gap-2 px-4 py-1 text-[10px] uppercase tracking-wider text-gray-400 bg-gray-50 border-b border-gray-200">
        <span className="w-56 shrink-0">Filename</span>
        <span className="w-36 shrink-0">Directory</span>
        <span className="w-16 shrink-0">Status</span>
        <span className="flex-1">Skill</span>
        <span className="w-24 shrink-0 text-right">Discovered</span>
      </div>

      {/* body */}
      <div className="flex-1 overflow-auto bg-[#f8f9fb]">
        {loading && totalCount === 0 && (
          <p className="p-6 text-sm text-gray-400">loading...</p>
        )}
        {err && <p className="p-6 text-sm text-red-600">{err}</p>}

        {!loading && totalCount === 0 && !err && <EmptyState />}

        {filtered.length > 0 &&
          filtered.map((t, i) => (
            <TrajRow key={t.traj_id || t.filename || i} t={t} />
          ))}

        {totalCount > 0 && filtered.length === 0 && !loading && (
          <p className="p-6 text-sm text-gray-400 text-center">
            No trajectories match the current filters.
          </p>
        )}
      </div>
    </div>
  );
}
