import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import TopBar from "./components/TopBar";
import PageIngest from "./pages/Ingest";
import PageSearch from "./pages/Search";
import PageGenerate from "./pages/Generate";
import PageEval from "./pages/Eval";
import PageChat from "./pages/Chat";
import PageCanary from "./pages/Canary";
import PageInspector from "./pages/Inspector";
import TrajPanel from "./components/TrajectoryPanel";
import SkillPanelComp from "./components/SkillPanel";
import { defaultConfig, loadUiConfig } from "./ui-config";

/* ================================================================
   Tab ID -> Page component mapping (center workspace)
   ================================================================ */
const PAGE_BY_ID = {
  inspector: null, // rendered inline below
  ingest: PageIngest,
  search: PageSearch,
  generate: PageGenerate,
  evaluate: PageEval,
  chat: PageChat,
  canary: PageCanary,
};

/* IDs that live in side panels, not in center tabs */
const SIDE_PANEL_IDS = new Set(["skills", "trajectories"]);

/* ================================================================
   Inspector — delegates to the full PageInspector component
   ================================================================ */
function InspectorTab({ selectedTraj, selectedSkill }) {
  return <PageInspector selectedTraj={selectedTraj} selectedSkill={selectedSkill} />;
}

/* ================================================================
   Sidebar wrappers — delegate to dedicated panel components
   ================================================================ */
function TrajectoryPanel({ onSelectTraj, selectedTraj }) {
  return (
    <TrajPanel
      className="h-full"
      onSelect={onSelectTraj}
      selected={selectedTraj}
    />
  );
}

function SkillPanel({ onSelectSkill, selectedSkill }) {
  return (
    <SkillPanelComp
      className="h-full"
      onSelect={onSelectSkill}
      selected={selectedSkill}
    />
  );
}

/* ================================================================
   Resizable divider handle
   ================================================================ */
function DividerHandle({ onMouseDown }) {
  return (
    <div
      className="w-1 shrink-0 cursor-col-resize hover:bg-gray-300 bg-gray-200 transition-colors duration-150"
      onMouseDown={onMouseDown}
    />
  );
}

/* ================================================================
   Collapsed side rail
   ================================================================ */
function CollapsedRail({ label, position, onExpand }) {
  const isLeft = position === "left";
  const arrow = isLeft ? "\u25B6" : "\u25C0";

  return (
    <div className="w-7 shrink-0 bg-gray-50 border-gray-200 flex flex-col items-center pt-2 select-none"
      style={{ borderRight: isLeft ? "1px solid #e5e7eb" : "none", borderLeft: isLeft ? "none" : "1px solid #e5e7eb" }}
    >
      <button
        onClick={onExpand}
        className="w-5 h-5 flex items-center justify-center text-[10px] text-gray-500 hover:text-gray-800 hover:bg-gray-200 rounded transition-colors"
        title={`Expand ${label}`}
      >
        {arrow}
      </button>
      <span
        className="text-[10px] text-gray-400 tracking-wider mt-3"
        style={{ writingMode: "vertical-rl", textOrientation: "mixed" }}
      >
        {label}
      </span>
    </div>
  );
}

/* ================================================================
   Main App
   ================================================================ */
export default function App() {
  const [config, setConfig] = useState(defaultConfig);

  /* ── workspace tabs: filter out side-panel tabs, prepend inspector ── */
  const centerTabs = useMemo(() => {
    const fromConfig = config.tabs.filter((t) => !SIDE_PANEL_IDS.has(t.id));
    const hasInspector = fromConfig.some((t) => t.id === "inspector");
    if (!hasInspector) {
      return [
        { id: "inspector", label: "Inspector", dot: "#64748b", desc: "Inspect selected trajectory or skill" },
        ...fromConfig,
      ];
    }
    return fromConfig;
  }, [config.tabs]);

  const [activeTab, setActiveTab] = useState("inspector");

  /* ── selection state ── */
  const [selectedTraj, setSelectedTraj] = useState(null);
  const [selectedSkill, setSelectedSkill] = useState(null);

  /* ── panel collapse state ── */
  const [leftCollapsed, setLeftCollapsed] = useState(false);
  const [rightCollapsed, setRightCollapsed] = useState(false);

  /* ── panel widths ── */
  const [leftWidth, setLeftWidth] = useState(280);
  const [rightWidth, setRightWidth] = useState(280);

  /* ── drag state refs ── */
  const dragging = useRef(null); // "left" | "right" | null
  const dragStartX = useRef(0);
  const dragStartWidth = useRef(0);

  /* ── load remote ui-config ── */
  useEffect(() => {
    let alive = true;
    loadUiConfig().then((cfg) => {
      if (!alive) return;
      setConfig(cfg);
    });
    return () => { alive = false; };
  }, []);

  /* ── auto-switch to inspector on selection ── */
  const handleSelectTraj = useCallback((traj) => {
    setSelectedTraj(traj);
    setSelectedSkill(null);
    setActiveTab("inspector");
  }, []);

  const handleSelectSkill = useCallback((name) => {
    setSelectedSkill(name);
    setSelectedTraj(null);
    setActiveTab("inspector");
  }, []);

  /* ── drag resize logic ── */
  const onDividerMouseDown = useCallback((side) => (e) => {
    e.preventDefault();
    dragging.current = side;
    dragStartX.current = e.clientX;
    dragStartWidth.current = side === "left" ? leftWidth : rightWidth;

    const onMouseMove = (ev) => {
      if (!dragging.current) return;
      const delta = ev.clientX - dragStartX.current;
      const newW =
        dragging.current === "left"
          ? dragStartWidth.current + delta
          : dragStartWidth.current - delta;
      const clamped = Math.max(180, Math.min(500, newW));
      if (dragging.current === "left") setLeftWidth(clamped);
      else setRightWidth(clamped);
    };

    const onMouseUp = () => {
      dragging.current = null;
      document.removeEventListener("mousemove", onMouseMove);
      document.removeEventListener("mouseup", onMouseUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };

    document.addEventListener("mousemove", onMouseMove);
    document.addEventListener("mouseup", onMouseUp);
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  }, [leftWidth, rightWidth]);

  /* ── resolve active page component ── */
  const activeTabDef = useMemo(
    () => centerTabs.find((t) => t.id === activeTab) ?? centerTabs[0],
    [centerTabs, activeTab],
  );

  const PageComp = PAGE_BY_ID[activeTabDef?.id] || null;

  return (
    <div className="h-screen flex flex-col overflow-hidden bg-[#f8f9fb]">
      {/* ── TopBar ── */}
      <TopBar title={config.title} />

      {/* ── Three-column body ── */}
      <div className="flex-1 flex overflow-hidden min-h-0">

        {/* ── Left panel (Trajectories) ── */}
        {leftCollapsed ? (
          <CollapsedRail
            label="Trajectories"
            position="left"
            onExpand={() => setLeftCollapsed(false)}
          />
        ) : (
          <>
            <div
              className="shrink-0 overflow-hidden bg-white border-r border-gray-200 flex flex-col transition-all duration-200"
              style={{ width: leftWidth }}
            >
              {/* collapse toggle */}
              <div className="h-8 shrink-0 flex items-center justify-between px-2 border-b border-gray-100 bg-gray-50/80">
                <span className="text-[11px] font-semibold text-gray-600 uppercase tracking-wider">
                  Trajectories
                </span>
                <button
                  onClick={() => setLeftCollapsed(true)}
                  className="w-5 h-5 flex items-center justify-center text-[10px] text-gray-400 hover:text-gray-800 hover:bg-gray-200 rounded transition-colors"
                  title="Collapse"
                >
                  &#9664;
                </button>
              </div>
              <div className="flex-1 overflow-auto">
                <TrajectoryPanel onSelectTraj={handleSelectTraj} selectedTraj={selectedTraj} />
              </div>
            </div>

            {/* Left divider */}
            <DividerHandle onMouseDown={onDividerMouseDown("left")} />
          </>
        )}

        {/* ── Center workspace ── */}
        <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
          {/* Tab bar */}
          <div className="h-9 shrink-0 flex items-end px-2 gap-0.5 bg-gray-100 border-b border-gray-200 overflow-x-auto">
            {centerTabs.map((t) => {
              const isActive = t.id === activeTabDef?.id;
              return (
                <button
                  key={t.id}
                  onClick={() => setActiveTab(t.id)}
                  className={`h-8 px-3 text-[12px] font-medium rounded-t-md border border-b-0 transition-colors whitespace-nowrap ${
                    isActive
                      ? "bg-white border-gray-200 text-gray-900"
                      : "bg-transparent border-transparent text-gray-500 hover:text-gray-700 hover:bg-gray-50"
                  }`}
                >
                  {t.dot && (
                    <span
                      className="inline-block w-2 h-2 rounded-full mr-1.5"
                      style={{ background: t.dot }}
                    />
                  )}
                  {t.label}
                </button>
              );
            })}
          </div>

          {/* Tab content */}
          <div className="flex-1 overflow-auto">
            {activeTabDef?.id === "inspector" ? (
              <InspectorTab
                selectedTraj={selectedTraj}
                selectedSkill={selectedSkill}
                onClearTraj={() => setSelectedTraj(null)}
                onClearSkill={() => setSelectedSkill(null)}
              />
            ) : PageComp ? (
              <PageComp desc={activeTabDef?.desc} />
            ) : (
              <div className="p-8 text-gray-400 text-sm">Unknown tab</div>
            )}
          </div>
        </div>

        {/* ── Right panel (Skills) ── */}
        {rightCollapsed ? (
          <CollapsedRail
            label="Skills"
            position="right"
            onExpand={() => setRightCollapsed(false)}
          />
        ) : (
          <>
            {/* Right divider */}
            <DividerHandle onMouseDown={onDividerMouseDown("right")} />

            <div
              className="shrink-0 overflow-hidden bg-white border-l border-gray-200 flex flex-col transition-all duration-200"
              style={{ width: rightWidth }}
            >
              {/* collapse toggle */}
              <div className="h-8 shrink-0 flex items-center justify-between px-2 border-b border-gray-100 bg-gray-50/80">
                <span className="text-[11px] font-semibold text-gray-600 uppercase tracking-wider">
                  Skills
                </span>
                <button
                  onClick={() => setRightCollapsed(true)}
                  className="w-5 h-5 flex items-center justify-center text-[10px] text-gray-400 hover:text-gray-800 hover:bg-gray-200 rounded transition-colors"
                  title="Collapse"
                >
                  &#9654;
                </button>
              </div>
              <div className="flex-1 overflow-auto">
                <SkillPanel onSelectSkill={handleSelectSkill} selectedSkill={selectedSkill} />
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
