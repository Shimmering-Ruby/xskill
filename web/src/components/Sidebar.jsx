import React, { useState } from "react";
import { getStatus } from "../api";

function TabRow({ tab, active, onClick }) {
  const base =
    "w-full flex items-center gap-2 px-4 h-8 text-[13px] text-left transition-colors";
  const activeCls =
    "bg-[#eef2ff] text-[#4361ee] border-l-2 border-[#4361ee] pl-[14px] font-medium";
  const inactiveCls =
    "text-gray-600 hover:bg-gray-50 border-l-2 border-transparent";
  return (
    <button
      type="button"
      onClick={onClick}
      className={`${base} ${active ? activeCls : inactiveCls}`}
    >
      <span
        aria-hidden
        className="w-1.5 h-1.5 rounded-full shrink-0"
        style={{ background: tab.dot }}
      />
      <span className="truncate">{tab.label}</span>
    </button>
  );
}

export default function Sidebar({ tabs, activeTab, onSelect }) {
  const [showStatus, setShowStatus] = useState(false);
  const [statusData, setStatusData] = useState(null);
  const [statusErr, setStatusErr] = useState(null);
  const [loadingStatus, setLoadingStatus] = useState(false);

  const toggleStatus = async () => {
    const next = !showStatus;
    setShowStatus(next);
    if (next && !statusData && !loadingStatus) {
      setLoadingStatus(true);
      setStatusErr(null);
      try {
        const d = await getStatus();
        setStatusData(d);
      } catch (e) {
        setStatusErr(String(e?.message || e));
      } finally {
        setLoadingStatus(false);
      }
    }
  };

  return (
    <aside className="w-56 shrink-0 bg-white border-r border-gray-200 flex flex-col">
      <div className="text-[9px] tracking-widest text-gray-400 uppercase px-4 pt-3 pb-1">
        Flows
      </div>
      <nav className="flex flex-col">
        {tabs.map((t) => (
          <TabRow
            key={t.id}
            tab={t}
            active={activeTab === t.id}
            onClick={() => onSelect(t.id)}
          />
        ))}
      </nav>

      <div className="flex-1" />

      <div className="text-[9px] tracking-widest text-gray-400 uppercase px-4 pt-3 pb-1">
        System
      </div>
      <button
        type="button"
        onClick={toggleStatus}
        className={`w-full flex items-center gap-2 px-4 h-8 text-[13px] text-left transition-colors ${
          showStatus
            ? "bg-gray-50 text-gray-900"
            : "text-gray-600 hover:bg-gray-50"
        }`}
      >
        <span
          aria-hidden
          className="w-1.5 h-1.5 rounded-full shrink-0 bg-gray-400"
        />
        <span>Status</span>
        <span className="ml-auto text-gray-400 text-[10px]">
          {showStatus ? "▾" : "▸"}
        </span>
      </button>

      {showStatus && (
        <div className="mx-3 mb-3 mt-1 rounded-md border border-gray-200 bg-[#f8f9fb] p-2.5 text-[11px] leading-snug">
          {loadingStatus && (
            <div className="text-gray-500">loading…</div>
          )}
          {statusErr && (
            <div className="text-red-600 break-all">{statusErr}</div>
          )}
          {statusData && (
            <div className="space-y-1 text-gray-700">
              <StatusRow k="version" v={statusData.version} />
              <StatusRow k="branch" v={statusData.git_branch || statusData.branch} />
              <StatusRow
                k="skills"
                v={
                  statusData.skills_count ??
                  statusData.skill_count ??
                  statusData.skills
                }
              />
              {statusData.index_ready !== undefined && (
                <StatusRow k="index" v={String(statusData.index_ready)} />
              )}
            </div>
          )}
        </div>
      )}
    </aside>
  );
}

function StatusRow({ k, v }) {
  if (v === undefined || v === null) return null;
  return (
    <div className="flex gap-2">
      <span className="text-gray-400 w-14 shrink-0">{k}</span>
      <span className="font-mono text-gray-800 truncate">{String(v)}</span>
    </div>
  );
}
