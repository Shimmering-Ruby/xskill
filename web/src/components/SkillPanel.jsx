import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { listSkills, getCanaryOverview } from "../api";

/* ─── status derivation ─────────────────────────────────────── */
function deriveStatus(skill, stagingSet) {
  if (stagingSet.has(skill.name)) return "staging";
  if (skill.frozen) return "frozen";
  if ((skill.version ?? 0) < 2 || skill.eval_score == null) return "draft";
  return "active";
}

/* ─── eval score pill color ─────────────────────────────────── */
function evalPillClass(score) {
  if (typeof score !== "number") return "bg-gray-100 text-gray-400 border-gray-200";
  if (score >= 8) return "bg-emerald-50 text-emerald-700 border-emerald-200";
  if (score >= 6) return "bg-blue-50 text-blue-700 border-blue-200";
  if (score >= 4) return "bg-amber-50 text-amber-700 border-amber-200";
  return "bg-red-50 text-red-700 border-red-200";
}

/* ─── status pill color ─────────────────────────────────────── */
const STATUS_CLS = {
  active:  "bg-emerald-50 text-emerald-700 border-emerald-200",
  staging: "bg-amber-50 text-amber-700 border-amber-200",
  draft:   "bg-gray-100 text-gray-500 border-gray-200",
  frozen:  "bg-sky-50 text-sky-700 border-sky-200",
};

/* ─── filter pills ──────────────────────────────────────────── */
const FILTERS = ["all", "active", "staging", "draft", "frozen"];

/* ─── skill card ────────────────────────────────────────────── */
function SkillCard({ skill, status, isSelected, hasStaging, onClick }) {
  const score = skill.eval_score;
  const version = skill.version ?? "—";
  const trajCount = skill.source_trajs_count ?? skill.source_trajs?.length ?? 0;
  const candidateCount = skill.candidates_count ?? 0;

  return (
    <button
      type="button"
      onClick={onClick}
      className={`w-full text-left px-2.5 py-1.5 border-b border-gray-100 hover:bg-gray-50/80 transition-colors ${
        isSelected ? "border-l-2 border-l-[#4361ee] bg-[#f8f9ff]" : "border-l-2 border-l-transparent"
      }`}
      style={{ minHeight: 52 }}
    >
      {/* row 1: name + status pill */}
      <div className="flex items-center gap-1.5">
        <span className="font-mono text-[12px] text-gray-800 truncate flex-1">
          {skill.name}
        </span>
        <span
          className={`text-[9px] font-mono uppercase px-1.5 py-0.5 rounded border leading-none shrink-0 ${
            STATUS_CLS[status] || STATUS_CLS.active
          }`}
        >
          {status}
        </span>
      </div>

      {/* row 2: eval + version + trajs + candidates + staging tag */}
      <div className="flex items-center gap-1.5 mt-0.5 text-[10px]">
        {/* eval score */}
        <span
          className={`font-mono tabular-nums px-1 py-0 rounded border leading-tight ${evalPillClass(score)}`}
        >
          {typeof score === "number" ? `eval:${score.toFixed(1)}` : "eval:—"}
        </span>

        <span className="text-gray-400 font-mono">v{version}</span>

        <span className="text-gray-400 font-mono">{trajCount}t</span>

        <span className="text-gray-400 font-mono">{candidateCount}c</span>

        {hasStaging && (
          <span className="text-amber-600 font-mono font-medium ml-auto">
            staging
          </span>
        )}
      </div>
    </button>
  );
}

/* ─── main panel ────────────────────────────────────────────── */
export default function SkillPanel({ onSelect, selected, className }) {
  const [skills, setSkills] = useState([]);
  const [canaryMap, setCanaryMap] = useState(new Map());
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const mountedRef = useRef(true);

  /* ── fetch ── */
  const refresh = useCallback(() => {
    Promise.all([
      listSkills(),
      getCanaryOverview().catch(() => null),
    ]).then(([skillRes, canaryRes]) => {
      if (!mountedRef.current) return;
      setSkills(skillRes.skills || []);

      // Build name -> canary entry map
      const m = new Map();
      if (canaryRes) {
        const entries = canaryRes.skills || canaryRes.entries || canaryRes || [];
        if (Array.isArray(entries)) {
          entries.forEach((e) => {
            if (e.name || e.skill_name) m.set(e.name || e.skill_name, e);
          });
        }
      }
      setCanaryMap(m);
    })
      .catch(() => {})
      .finally(() => {
        if (mountedRef.current) setLoading(false);
      });
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    refresh();
    const timer = setInterval(refresh, 10_000);
    return () => {
      mountedRef.current = false;
      clearInterval(timer);
    };
  }, [refresh]);

  /* ── staging set (names with active staging branch) ── */
  const stagingSet = useMemo(() => {
    const s = new Set();
    canaryMap.forEach((entry, name) => {
      if (entry.has_staging) s.add(name);
    });
    return s;
  }, [canaryMap]);

  /* ── enrich skills with derived status ── */
  const enriched = useMemo(() => {
    return skills.map((sk) => ({
      ...sk,
      _status: deriveStatus(sk, stagingSet),
      _hasStaging: stagingSet.has(sk.name),
      _candidates: canaryMap.get(sk.name)?.candidates_count ?? sk.candidates_count ?? 0,
    }));
  }, [skills, stagingSet, canaryMap]);

  /* ── filter ── */
  const filtered = useMemo(() => {
    let list = enriched;

    if (statusFilter !== "all") {
      list = list.filter((s) => s._status === statusFilter);
    }

    if (search.trim()) {
      const q = search.toLowerCase();
      list = list.filter((s) => s.name.toLowerCase().includes(q));
    }

    return list;
  }, [enriched, statusFilter, search]);

  /* ── status counts ── */
  const counts = useMemo(() => {
    const c = { all: enriched.length, active: 0, staging: 0, draft: 0, frozen: 0 };
    enriched.forEach((s) => {
      c[s._status] = (c[s._status] || 0) + 1;
    });
    return c;
  }, [enriched]);

  return (
    <div className={`flex flex-col h-full bg-white ${className || ""}`}>
      {/* header */}
      <div className="px-3 py-2 border-b border-gray-200 flex items-center gap-2 shrink-0">
        <span className="text-[13px] font-semibold text-gray-800">Skills</span>
        <span className="text-[10px] font-mono text-gray-500 bg-gray-100 border border-gray-200 rounded-full px-1.5 py-0.5">
          {skills.length}
        </span>
        <button
          onClick={() => {
            setLoading(true);
            refresh();
          }}
          className="ml-auto text-[10px] px-2 py-0.5 rounded border border-gray-200 text-gray-500 hover:bg-gray-50"
          title="Refresh"
        >
          ↻
        </button>
      </div>

      {/* search + status filter pills */}
      <div className="px-3 py-2 border-b border-gray-200 space-y-1.5 shrink-0">
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search skills..."
          className="w-full h-7 px-2 rounded border border-gray-200 text-[11px] font-mono focus:outline-none focus:border-[#4361ee] focus:ring-1 focus:ring-[#4361ee]/30"
        />
        <div className="flex gap-1 flex-wrap">
          {FILTERS.map((f) => {
            const active = statusFilter === f;
            return (
              <button
                key={f}
                onClick={() => setStatusFilter(f)}
                className={`text-[10px] px-1.5 py-0.5 rounded-full border transition-colors ${
                  active
                    ? "border-[#4361ee] bg-[#eef2ff] text-[#4361ee] font-medium"
                    : "border-gray-200 text-gray-500 hover:border-gray-300"
                }`}
              >
                {f}{counts[f] > 0 ? ` ${counts[f]}` : ""}
              </button>
            );
          })}
        </div>
      </div>

      {/* skill card list */}
      <div className="flex-1 overflow-y-auto">
        {loading && skills.length === 0 && (
          <div className="text-[11px] text-gray-400 py-6 text-center">loading...</div>
        )}

        {!loading && skills.length === 0 && (
          <div className="text-[11px] text-gray-400 py-6 text-center">(no skills yet)</div>
        )}

        {!loading && skills.length > 0 && filtered.length === 0 && (
          <div className="text-[11px] text-gray-400 py-6 text-center">(no match)</div>
        )}

        {filtered.map((sk) => (
          <SkillCard
            key={sk.name}
            skill={{ ...sk, candidates_count: sk._candidates }}
            status={sk._status}
            isSelected={sk.name === selected}
            hasStaging={sk._hasStaging}
            onClick={() => onSelect && onSelect(sk.name)}
          />
        ))}
      </div>
    </div>
  );
}
